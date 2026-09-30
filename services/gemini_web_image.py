"""Service for Virtual Try-On image generation via Gemini Web automation."""
import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, Tuple
from playwright.async_api import Page

from core.config import get_config_manager
from core.logger import logger
from core.states import DetectStatus
from services.browser import get_browser_service


class GeminiWebImageService:
    def __init__(self):
        self.browser_service = get_browser_service()

    async def generate_tryon_image(
        self,
        model_image_path: Path,
        cloth_image_path: Path,
        session_dir: Path,
        version: int = 1,
        progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        cancel_event: Optional[asyncio.Event] = None
    ) -> Tuple[Optional[Path], Optional[str], Optional[str]]:
        """
        Runs initial Virtual Try-On job:
        1. Opens new chat on gemini.google.com
        2. Uploads model image and garment image
        3. Pastes default try-on prompt and sends
        4. Waits for image result, downloads high-res file
        Returns: (saved_image_path, refusal_or_error_message, chat_url)
        """
        cm = get_config_manager()
        cm.reload_if_changed()

        app_url = cm.app_config.gemini_web.app_url
        timeout = cm.app_config.gemini_web.image_timeout_seconds
        poll_interval = cm.app_config.gemini_web.poll_interval_seconds
        prompt = cm.app_config.image.default_prompt
        selectors = cm.selectors.chat

        # Notify queue status if lock is currently busy
        if self.browser_service.lock.locked() and progress_callback:
            await progress_callback("⏳ Đang xếp hàng chờ đến lượt xử lý...")

        page = None
        try:
            async with self.browser_service.lock:
                # Check cancellation right after getting lock
                if cancel_event and cancel_event.is_set():
                    return None, "❌ Đã hủy thao tác trong khi chờ hàng đợi.", ""

                page = await self.browser_service.get_page()
                logger.info(f"Navigating to Gemini Web: {app_url}")
                await page.goto(app_url, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                # Check if login is required
                if not await self.browser_service.check_login_status(page):
                    ss, _ = await self.browser_service.capture_debug(page, "login_required")
                    return None, f"🔐 Phiên Google đã hết hạn. Vui lòng mở scripts/open_chrome_profile.py để đăng nhập lại. (Ảnh debug: {ss.name})", page.url

                # Count existing responses before sending
                resp_containers = page.locator(selectors.response_container)
                initial_count = await resp_containers.count()

                # Upload 2 images
                logger.info(f"Uploading model image: {model_image_path} and cloth image: {cloth_image_path}")
                uploaded = await self._upload_files(page, [model_image_path, cloth_image_path], selectors)
                if not uploaded:
                    ss, _ = await self.browser_service.capture_debug(page, "upload_failed")
                    return None, f"Không thể upload ảnh lên giao diện Gemini. Đã lưu ảnh debug: {ss.name}", page.url

                # Wait for upload thumbnails to attach
                await asyncio.sleep(2)

                # Check cancellation before sending prompt
                if cancel_event and cancel_event.is_set():
                    return None, "❌ Đã hủy thao tác theo yêu cầu.", page.url

                # Enter prompt
                logger.info("Entering prompt...")
                await self._enter_prompt(page, prompt, selectors)
                await asyncio.sleep(1)

                # Send
                logger.info("Sending message...")
                await self._click_send(page, selectors)

                # Wait for generation and download result
                chat_url = page.url
                result_path, error_msg = await self._wait_for_image_and_download(
                    page=page,
                    session_dir=session_dir,
                    version=version,
                    initial_response_count=initial_count,
                    timeout=timeout,
                    poll_interval=poll_interval,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event
                )

                chat_url = page.url
                return result_path, error_msg, chat_url

        except Exception as e:
            logger.exception(f"Unexpected error in generate_tryon_image: {e}")
            if page:
                try:
                    ss, _ = await self.browser_service.capture_debug(page, "unexpected_error")
                    return None, f"Đã xảy ra lỗi không mong muốn: {str(e)} (Ảnh debug: {ss.name})", page.url
                except Exception:
                    pass
            return None, f"Đã xảy ra lỗi: {str(e)}", app_url

    async def refine_tryon_image(
        self,
        feedback: str,
        chat_url: str,
        session_dir: Path,
        version: int = 2,
        progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        cancel_event: Optional[asyncio.Event] = None
    ) -> Tuple[Optional[Path], Optional[str], Optional[str]]:
        """
        Sends feedback in the existing Gemini chat to refine the generated image.
        """
        cm = get_config_manager()
        cm.reload_if_changed()

        timeout = cm.app_config.gemini_web.image_timeout_seconds
        poll_interval = cm.app_config.gemini_web.poll_interval_seconds
        retry_template = cm.app_config.image.retry_template
        prompt = retry_template.format(user_feedback=feedback)
        selectors = cm.selectors.chat

        # Notify queue status if lock is currently busy
        if self.browser_service.lock.locked() and progress_callback:
            await progress_callback("⏳ Đang xếp hàng chờ đến lượt xử lý...")

        page = None
        try:
            async with self.browser_service.lock:
                if cancel_event and cancel_event.is_set():
                    return None, "❌ Đã hủy thao tác trong khi chờ hàng đợi.", ""

                page = await self.browser_service.get_page()
                if chat_url and page.url != chat_url:
                    logger.info(f"Navigating back to image chat: {chat_url}")
                    await page.goto(chat_url, wait_until="domcontentloaded")
                    # Wait for prompt box to ensure chat page has loaded
                    try:
                        await page.locator(selectors.prompt_box).first.wait_for(state="visible", timeout=15000)
                    except Exception:
                        pass
                    await asyncio.sleep(2)

                if not await self.browser_service.check_login_status(page):
                    ss, _ = await self.browser_service.capture_debug(page, "login_required")
                    return None, f"🔐 Phiên Google đã hết hạn. Vui lòng mở scripts/open_chrome_profile.py để đăng nhập lại. (Ảnh debug: {ss.name})", page.url

                # Stabilize and count existing responses (wait for history to load)
                resp_containers = page.locator(selectors.response_container)
                try:
                    await resp_containers.first.wait_for(state="visible", timeout=10000)
                except Exception:
                    pass

                prev_c = await resp_containers.count()
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    c = await resp_containers.count()
                    if c > 0 and c == prev_c:
                        break
                    prev_c = c
                initial_count = prev_c

                # Check cancellation before sending refinement
                if cancel_event and cancel_event.is_set():
                    return None, "❌ Đã hủy thao tác theo yêu cầu.", page.url

                # Enter refinement prompt
                logger.info(f"Submitting refinement prompt: {prompt}")
                await self._enter_prompt(page, prompt, selectors)
                await asyncio.sleep(1)
                await self._click_send(page, selectors)

                result_path, error_msg = await self._wait_for_image_and_download(
                    page=page,
                    session_dir=session_dir,
                    version=version,
                    initial_response_count=initial_count,
                    timeout=timeout,
                    poll_interval=poll_interval,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event
                )

                return result_path, error_msg, page.url

        except Exception as e:
            logger.exception(f"Unexpected error in refine_tryon_image: {e}")
            if page:
                try:
                    ss, _ = await self.browser_service.capture_debug(page, "refine_error")
                    return None, f"Lỗi khi gửi góp ý: {str(e)} (Ảnh debug: {ss.name})", page.url
                except Exception:
                    pass
            return None, f"Lỗi khi gửi góp ý: {str(e)}", chat_url

    async def _wait_for_thumbnails(self, page: Page, expected_count: int, timeout: float = 10.0) -> bool:
        """Waits for uploaded media thumbnails to appear in the prompt box area."""
        selectors = get_config_manager().selectors.chat
        thumbnail_selector = getattr(
            selectors,
            "upload_thumbnail",
            "img[src^='blob:'], [class*='attachment'] img, [class*='preview'] img, button[aria-label*='Xóa' i], button[aria-label*='Remove' i]"
        )
        start = time.time()
        while time.time() - start < timeout:
            try:
                cnt = await page.locator(thumbnail_selector).count()
                if cnt >= expected_count:
                    logger.info(f"Verified {cnt}/{expected_count} upload thumbnail(s) attached.")
                    return True

                prompt_imgs = await page.locator(
                    "rich-textarea ~ * img, rich-textarea img, div[role='textbox'] img, [contenteditable='true'] img"
                ).count()
                if prompt_imgs >= expected_count:
                    logger.info(f"Verified {prompt_imgs}/{expected_count} prompt image(s) attached.")
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)

        logger.warning(f"Could not verify {expected_count} thumbnail(s) within {timeout}s.")
        return False

    async def _upload_files(self, page: Page, file_paths: list[Path], selectors) -> bool:
        """Uploads files via direct file input, or 2-step menu upload (+ button -> upload menu item) with thumbnail check."""
        str_paths = [str(p.resolve()) for p in file_paths]
        expected_count = len(file_paths)

        # 1. Direct file input if already in DOM (verify thumbnails to ensure not a stale element)
        try:
            file_input = page.locator(selectors.file_input)
            if await file_input.count() > 0:
                await file_input.first.set_input_files(str_paths)
                logger.info("Attempted set_input_files on existing input[type='file']. Verifying thumbnails...")
                if await self._wait_for_thumbnails(page, expected_count, timeout=3.0):
                    logger.info("Uploaded files verified via direct input[type='file']")
                    return True
                logger.info("Direct input[type='file'] did not produce thumbnails (stale input). Proceeding to menu upload...")
        except Exception as e:
            logger.debug(f"Direct set_input_files failed: {e}")

        # 2. 2-Step Menu Upload: Click '+' button, then click upload menu item
        try:
            upload_btn = page.locator(selectors.upload_button).filter(visible=True).first
            await upload_btn.wait_for(state="visible", timeout=5000)
            logger.info("Clicking '+' upload button to open tools menu...")
            await upload_btn.click()
            await asyncio.sleep(0.8)

            # Capture debug screenshot of the open menu only if debug mode enabled
            if get_config_manager().app_config.app.debug:
                try:
                    await self.browser_service.capture_debug(page, "upload_menu_opened")
                except Exception:
                    pass

            # Click menu item inside expect_file_chooser
            menu_item = page.locator(selectors.upload_menu_item).filter(visible=True).first
            try:
                await menu_item.wait_for(state="visible", timeout=3000)
                logger.info("Clicking upload menu item...")
                async with page.expect_file_chooser(timeout=7000) as fc_info:
                    await menu_item.click()
                file_chooser = await fc_info.value

                if file_chooser.is_multiple() or len(str_paths) <= 1:
                    await file_chooser.set_files(str_paths)
                else:
                    logger.info("FileChooser only accepts 1 file. Uploading files sequentially...")
                    await file_chooser.set_files(str_paths[0])
                    for sub_path in str_paths[1:]:
                        await asyncio.sleep(1)
                        btn = page.locator(selectors.upload_button).filter(visible=True).first
                        await btn.click()
                        await asyncio.sleep(0.5)
                        m_item = page.locator(selectors.upload_menu_item).filter(visible=True).first
                        async with page.expect_file_chooser(timeout=5000) as sub_fc_info:
                            await m_item.click()
                        await (await sub_fc_info.value).set_files(sub_path)

                if await self._wait_for_thumbnails(page, expected_count, timeout=10.0):
                    logger.info(f"Uploaded files verified via menu item: {file_paths}")
                    return True
            except Exception as me:
                logger.debug(f"Menu item click didn't trigger file chooser: {me}")
                # Check if clicking '+' dynamically injected input[type='file'] into the DOM
                file_input = page.locator(selectors.file_input)
                if await file_input.count() > 0:
                    await file_input.first.set_input_files(str_paths)
                    if await self._wait_for_thumbnails(page, expected_count, timeout=5.0):
                        logger.info("Uploaded files verified via dynamically added input[type='file']")
                        return True

        except Exception as e:
            logger.warning(f"2-step menu upload failed: {e}")

        # 3. Direct file chooser fallback on upload_btn
        try:
            upload_btn = page.locator(selectors.upload_button).filter(visible=True).first
            async with page.expect_file_chooser(timeout=3000) as fc_info:
                await upload_btn.click()
            file_chooser = await fc_info.value
            if file_chooser.is_multiple() or len(str_paths) <= 1:
                await file_chooser.set_files(str_paths)
            else:
                await file_chooser.set_files(str_paths[0])
                for sub_path in str_paths[1:]:
                    await asyncio.sleep(1)
                    async with page.expect_file_chooser(timeout=3000) as sub_fc_info:
                        await upload_btn.click()
                    await (await sub_fc_info.value).set_files(sub_path)

            if await self._wait_for_thumbnails(page, expected_count, timeout=8.0):
                logger.info(f"Uploaded files verified via fallback direct file chooser.")
                return True
        except Exception:
            pass

        logger.error(f"Failed to verify {expected_count} upload thumbnail(s) after all upload attempts.")
        return False

    async def _enter_prompt(self, page: Page, text: str, selectors) -> None:
        """Enters text into prompt textbox."""
        box = page.locator(selectors.prompt_box).first
        await box.wait_for(state="visible", timeout=10000)
        await box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.insert_text(text)

    async def _click_send(self, page: Page, selectors) -> None:
        """Clicks send button or presses Enter."""
        send_btn = page.locator(selectors.send_button).filter(visible=True).first
        try:
            await send_btn.wait_for(state="visible", timeout=2000)
            await send_btn.click()
            return
        except Exception:
            pass
        await page.keyboard.press("Enter")

    async def _wait_for_image_and_download(
        self,
        page: Page,
        session_dir: Path,
        version: int,
        initial_response_count: int,
        timeout: int,
        poll_interval: int,
        progress_callback: Optional[Callable[[str], Awaitable[None]]],
        cancel_event: Optional[asyncio.Event]
    ) -> Tuple[Optional[Path], Optional[str]]:
        """
        Polls for completion:
        1. Waits for a NEW response container to appear.
        2. Waits until generating stops.
        3. Scans for safety/quota ONLY inside the new response container.
        4. If image exists, downloads high-res image.
        5. If generating finished and NO image exists, extracts text response to report back.
        """
        start_time = time.time()
        selectors = get_config_manager().selectors.chat
        last_progress_edit = 0.0
        stable_text = ""
        stable_text_first_seen = 0.0

        await asyncio.sleep(4)

        while time.time() - start_time < timeout:
            if cancel_event and cancel_event.is_set():
                logger.info("Task canceled by user.")
                return None, "❌ Đã hủy thao tác theo yêu cầu."

            elapsed = int(time.time() - start_time)
            mins = elapsed // 60
            secs = elapsed % 60
            elapsed_str = f"{mins:02d}:{secs:02d}"

            if progress_callback and (time.time() - last_progress_edit > 5):
                last_progress_edit = time.time()
                await progress_callback(elapsed_str)

            resp_containers = page.locator(selectors.response_container)
            current_count = await resp_containers.count()

            # Ensure we only evaluate NEW responses
            target_response = None
            if current_count > initial_response_count:
                target_response = resp_containers.last

            # Detect state inside target container
            status, detail = await self.browser_service.detect_page_state(
                page=page,
                response_locator=target_response,
                result_selector=selectors.response_image
            )

            if status == DetectStatus.LOGIN_REQUIRED:
                ss, _ = await self.browser_service.capture_debug(page, "login_required")
                return None, f"🔐 Phiên Google hết hạn. Vui lòng mở scripts/open_chrome_profile.py để đăng nhập lại. (Ảnh debug: {ss.name})"

            if status == DetectStatus.QUOTA_EXCEEDED:
                ss, _ = await self.browser_service.capture_debug(page, "quota_exceeded")
                return None, f"{detail} (Ảnh debug: {ss.name})"

            if status == DetectStatus.SAFETY_REFUSED:
                ss, _ = await self.browser_service.capture_debug(page, "safety_refused")
                return None, f"{detail} (Ảnh debug: {ss.name})"

            # If image detected successfully in new response
            if status == DetectStatus.SUCCESS and target_response is not None:
                stable_text = ""
                stable_text_first_seen = 0.0
                last_img = await self.browser_service.find_ready_media(target_response, selectors.response_image)
                if last_img is not None:
                    logger.info("Found generated image in new response. Attempting high-res download...")
                    dest_file = session_dir / f"tryon_v{version}.png"

                    download_success = await self._download_image(page, last_img, dest_file)
                    if download_success and dest_file.exists():
                        return dest_file, None
                    ss, _ = await self.browser_service.capture_debug(page, "download_failed")
                    return None, f"Không thể tải ảnh gốc độ phân giải đầy đủ. Ảnh debug: {ss.name}"

            elif status == DetectStatus.TEXT_RESPONSE and target_response is not None:
                # Require text to be non-empty and stable across at least 15 seconds
                if detail == stable_text and len(detail.strip()) > 0:
                    if time.time() - stable_text_first_seen >= 15.0:
                        logger.info(f"Gemini responded with text instead of image: {detail[:100]}...")
                        return None, f"Gemini phản hồi bằng văn bản thay vì tạo ảnh:\n\n\"{detail}\""
                else:
                    stable_text = detail
                    stable_text_first_seen = time.time()
            else:
                stable_text = ""
                stable_text_first_seen = 0.0

            await asyncio.sleep(poll_interval)

        # Timeout reached
        ss, _ = await self.browser_service.capture_debug(page, "image_timeout")
        return None, f"⏳ Quá thời gian chờ kết quả ({timeout}s). Ảnh debug: {ss.name}"

    async def _download_image(self, page: Page, img_elem, dest_file: Path) -> bool:
        """Downloads full-resolution image using download button or high-res evaluated fetch."""
        selectors = get_config_manager().selectors.chat
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        # Method A: Hover over the image (reveals the hover-only button) and click the full-size download
        # button of the same response. Hover is capped at 3s: when Gemini's fixed input box covers the image
        # the hover is intercepted, and click_download falls back to a forced / JS click instead.
        try:
            try:
                await img_elem.hover(timeout=3000)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.info(f"Hover over image blocked ({type(e).__name__}); clicking the download button directly.")

            scope = img_elem.locator("xpath=ancestor::model-response[1]")
            if await scope.count() == 0:
                scope = page
            buttons = scope.locator(selectors.download_button)
            try:
                await buttons.filter(visible=True).last.wait_for(state="visible", timeout=3000)
            except Exception:
                pass
            # Full-size download is fetched from Google servers first, so allow extra time
            if await self.browser_service.click_download(page, buttons, dest_file, timeout_ms=30000):
                return True
            logger.warning("Full-size download button failed, falling back to the displayed image.")
        except Exception as e:
            logger.warning(f"Download button failed, falling back to displayed image: {e}")

        # Method B: Fetch the displayed image (http or blob: URL) from the page context, with cookies
        try:
            src = await img_elem.get_attribute("src")
            if src and src.startswith(("http", "blob:")):
                high_res_url = src.split("=")[0] + "=s2048" if src.startswith("http") and "=s" in src else src
                buffer = await page.evaluate(
                    """async (url) => {
                        const resp = await fetch(url, {credentials: 'include'});
                        const blob = await resp.blob();
                        const reader = new FileReader();
                        return new Promise(resolve => {
                            reader.onloadend = () => resolve(reader.result);
                            reader.readAsDataURL(blob);
                        });
                    }""",
                    high_res_url
                )
                if buffer and "," in buffer:
                    import base64
                    data = base64.b64decode(buffer.split(",")[1])
                    dest_file.write_bytes(data)
                    logger.info(f"Saved high-res image via evaluated fetch: {dest_file}")
                    return True
        except Exception as e:
            logger.warning(f"Fetch image buffer failed: {e}")

        return False
