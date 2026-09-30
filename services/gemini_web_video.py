"""Service for fashion video generation via Gemini Web automation."""
import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Tuple
from playwright.async_api import Page

from core.config import get_config_manager
from core.logger import logger
from core.states import DetectStatus
from services.browser import get_browser_service


class GeminiWebVideoService:
    def __init__(self):
        self.browser_service = get_browser_service()

    def build_prompt(self, feedback_list: Optional[List[str]] = None) -> str:
        """Builds video generation prompt with accumulated feedback."""
        cm = get_config_manager()
        video_cfg = cm.app_config.video
        base_prompt = video_cfg.default_prompt.strip()

        if not feedback_list:
            return base_prompt

        feedback_str = "\n".join(f"- {fb.strip()}" for fb in feedback_list if fb.strip())
        return video_cfg.retry_template.format(
            base_prompt=base_prompt,
            feedback_list=feedback_str
        ).strip()

    async def generate_video(
        self,
        approved_image_path: Path,
        session_dir: Path,
        feedback_list: Optional[List[str]] = None,
        version: int = 1,
        progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        cancel_event: Optional[asyncio.Event] = None
    ) -> Tuple[Optional[Path], Optional[str]]:
        """
        Runs fashion video generation:
        1. Opens Gemini Web
        2. Uploads approved try-on image
        3. Enters video prompt + accumulated feedbacks
        4. Waits for video generation and downloads MP4
        Returns: (saved_video_path, error_message)
        """
        cm = get_config_manager()
        cm.reload_if_changed()

        video_url = cm.app_config.gemini_web.video_url
        timeout = cm.app_config.gemini_web.video_timeout_seconds
        poll_interval = cm.app_config.gemini_web.poll_interval_seconds
        prompt = self.build_prompt(feedback_list)
        selectors = cm.selectors.video

        # Check queue
        if self.browser_service.lock.locked() and progress_callback:
            await progress_callback("⏳ Đang xếp hàng chờ đến lượt xử lý...")

        page = None
        try:
            async with self.browser_service.lock:
                if cancel_event and cancel_event.is_set():
                    return None, "❌ Đã hủy thao tác trong khi chờ hàng đợi."

                page = await self.browser_service.get_page()
                logger.info(f"Navigating to Gemini Web video: {video_url}")
                await page.goto(video_url, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                if not await self.browser_service.check_login_status(page):
                    ss, _ = await self.browser_service.capture_debug(page, "login_required")
                    return None, f"🔐 Phiên Google hết hạn. Vui lòng mở scripts/open_chrome_profile.py để đăng nhập lại. (Ảnh debug: {ss.name})"

                # Count existing responses before sending
                resp_containers = page.locator(selectors.response_container)
                initial_count = await resp_containers.count()

                # Switch Gemini to the 'Tạo video' tool (otherwise a normal chat model answers)
                if not await self._select_video_tool(page, selectors):
                    ss, _ = await self.browser_service.capture_debug(page, "video_tool_failed")
                    return None, f"Không chọn được công cụ 'Tạo video' trên Gemini. Ảnh debug: {ss.name}"

                # Default aspect ratio is 'Ngang (16:9)' -> TikTok needs 'Dọc (9:16)'
                if not await self._set_portrait_aspect(page, selectors):
                    ss, _ = await self.browser_service.capture_debug(page, "video_aspect_failed")
                    return None, f"Không chuyển được tỷ lệ khung hình sang Dọc (9:16). Ảnh debug: {ss.name}"

                # Upload approved try-on image
                logger.info(f"Uploading approved image for video generation: {approved_image_path}")
                uploaded = await self._upload_image(page, approved_image_path, selectors)
                if not uploaded:
                    ss, _ = await self.browser_service.capture_debug(page, "video_upload_failed")
                    return None, f"Không thể tải ảnh đã duyệt lên để tạo video. Ảnh debug: {ss.name}"

                await asyncio.sleep(2)

                # Check cancellation before sending prompt
                if cancel_event and cancel_event.is_set():
                    return None, "❌ Đã hủy tạo video theo yêu cầu."

                # Enter prompt
                logger.info("Entering video prompt...")
                await self._enter_prompt(page, prompt, selectors)
                await asyncio.sleep(1)

                # Click generate / send
                logger.info("Sending video generation request...")
                await self._click_generate(page, selectors)

                # Wait and download MP4
                dest_file = session_dir / f"video_v{version}.mp4"
                result_path, error_msg = await self._wait_for_video_and_download(
                    page=page,
                    dest_file=dest_file,
                    initial_response_count=initial_count,
                    timeout=timeout,
                    poll_interval=poll_interval,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event
                )

                return result_path, error_msg

        except Exception as e:
            logger.exception(f"Unexpected error in generate_video: {e}")
            if page:
                try:
                    ss, _ = await self.browser_service.capture_debug(page, "video_error")
                    return None, f"Lỗi không mong muốn khi tạo video: {str(e)} (Ảnh debug: {ss.name})"
                except Exception:
                    pass
            return None, f"Lỗi không mong muốn khi tạo video: {str(e)}"

    async def _wait_for_thumbnails(self, page: Page, expected_count: int, timeout: float = 10.0) -> bool:
        """Waits for uploaded media thumbnails to appear in the prompt box area."""
        selectors = get_config_manager().selectors.video
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
                    logger.info(f"Verified {cnt}/{expected_count} video source thumbnail(s) attached.")
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

    async def _select_video_tool(self, page: Page, selectors) -> bool:
        """Opens the '+' menu and selects the 'Tạo video' tool. Returns True once the Video chip is active."""
        active = page.locator(selectors.tool_active).filter(visible=True)
        if await active.count() > 0:
            return True
        try:
            menu_btn = page.locator(selectors.tool_menu_button).filter(visible=True).first
            await menu_btn.wait_for(state="visible", timeout=10000)
            await menu_btn.click()
            item = page.locator(selectors.tool_item).filter(visible=True).first
            await item.wait_for(state="visible", timeout=5000)
            await item.click()
            await active.first.wait_for(state="visible", timeout=5000)
            logger.info("Selected Gemini 'Tạo video' tool.")
            return True
        except Exception as e:
            logger.warning(f"Could not select 'Tạo video' tool: {e}")
            return False

    async def _set_portrait_aspect(self, page: Page, selectors) -> bool:
        """Switches the video aspect ratio to 'Dọc (9:16)'. Returns True when the button label shows 9:16."""
        try:
            btn = page.locator(selectors.aspect_button).filter(visible=True).first
            await btn.wait_for(state="visible", timeout=5000)
            label = await btn.get_attribute("aria-label") or ""
            if "9:16" in label:
                return True
            await btn.click()
            option = page.locator(selectors.aspect_portrait_option).filter(visible=True).first
            await option.wait_for(state="visible", timeout=5000)
            await option.click()
            await asyncio.sleep(0.8)
            label = await page.locator(selectors.aspect_button).filter(visible=True).first.get_attribute("aria-label") or ""
            if "9:16" in label:
                logger.info(f"Video aspect ratio set: {label}")
                return True
            logger.warning(f"Aspect ratio still '{label}' after selecting portrait.")
            return False
        except Exception as e:
            logger.warning(f"Could not set portrait aspect ratio: {e}")
            return False

    async def _upload_image(self, page: Page, image_path: Path, selectors) -> bool:
        """Uploads approved image for video generation via direct input or 2-step menu upload with thumbnail verification."""
        str_path = str(image_path.resolve())

        # 0. Video mode has a dedicated 'Tải tệp lên' button that opens the file chooser directly
        try:
            direct_btn = page.locator(selectors.upload_direct_button).filter(visible=True).first
            if await direct_btn.count() > 0:
                async with page.expect_file_chooser(timeout=5000) as fc_info:
                    await direct_btn.click()
                await (await fc_info.value).set_files(str_path)
                if await self._wait_for_thumbnails(page, 1, timeout=10.0):
                    logger.info("Uploaded video source image via video-mode 'Tải tệp lên' button")
                    return True
                logger.warning("Video-mode upload button did not produce a thumbnail. Trying other methods...")
        except Exception as e:
            logger.debug(f"Video-mode upload button failed: {e}")

        # 1. Direct file input if already in DOM (verify thumbnail to ensure not a stale element)
        try:
            file_input = page.locator(selectors.file_input)
            if await file_input.count() > 0:
                await file_input.first.set_input_files(str_path)
                logger.info("Attempted set_input_files on existing video input[type='file']. Verifying thumbnail...")
                if await self._wait_for_thumbnails(page, 1, timeout=3.0):
                    logger.info("Uploaded video source image verified via direct input[type='file']")
                    return True
                logger.info("Direct input[type='file'] did not attach thumbnail (stale element). Proceeding to menu upload...")
        except Exception as e:
            logger.debug(f"Direct video upload set_input_files failed: {e}")

        # 2. 2-Step Menu Upload: Click '+' button, then click upload menu item
        try:
            upload_btn = page.locator(selectors.upload_button).filter(visible=True).first
            await upload_btn.wait_for(state="visible", timeout=5000)
            logger.info("Clicking '+' upload button for video...")
            await upload_btn.click()
            await asyncio.sleep(0.8)

            cm = get_config_manager()
            if cm.app_config.app.debug:
                try:
                    await self.browser_service.capture_debug(page, "video_upload_menu_opened")
                except Exception:
                    pass

            menu_item = page.locator(selectors.upload_menu_item).filter(visible=True).first
            try:
                await menu_item.wait_for(state="visible", timeout=3000)
                logger.info("Clicking upload menu item for video...")
                async with page.expect_file_chooser(timeout=7000) as fc_info:
                    await menu_item.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(str_path)
                if await self._wait_for_thumbnails(page, 1, timeout=10.0):
                    logger.info(f"Uploaded video source image verified via menu item: {image_path}")
                    return True
            except Exception as me:
                logger.debug(f"Video menu item click didn't trigger file chooser: {me}")
                file_input = page.locator(selectors.file_input)
                if await file_input.count() > 0:
                    await file_input.first.set_input_files(str_path)
                    if await self._wait_for_thumbnails(page, 1, timeout=5.0):
                        logger.info("Uploaded video source image verified via dynamically added input[type='file']")
                        return True
        except Exception as e:
            logger.warning(f"Video 2-step upload failed: {e}")

        # 3. Direct file chooser fallback on upload_btn
        try:
            upload_btn = page.locator(selectors.upload_button).filter(visible=True).first
            async with page.expect_file_chooser(timeout=3000) as fc_info:
                await upload_btn.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(str_path)
            if await self._wait_for_thumbnails(page, 1, timeout=8.0):
                logger.info(f"Uploaded video source image verified via fallback file chooser.")
                return True
        except Exception:
            pass

        logger.error("Failed to verify video source upload thumbnail after all upload attempts.")
        return False

    async def _enter_prompt(self, page: Page, text: str, selectors) -> None:
        box = page.locator(selectors.prompt_box).first
        await box.wait_for(state="visible", timeout=10000)
        await box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.insert_text(text)

    async def _click_generate(self, page: Page, selectors) -> None:
        btn = page.locator(selectors.generate_button).filter(visible=True).first
        try:
            await btn.wait_for(state="visible", timeout=2000)
            await btn.click()
            return
        except Exception:
            pass
        await page.keyboard.press("Enter")

    async def _wait_for_video_and_download(
        self,
        page: Page,
        dest_file: Path,
        initial_response_count: int,
        timeout: int,
        poll_interval: int,
        progress_callback: Optional[Callable[[str], Awaitable[None]]],
        cancel_event: Optional[asyncio.Event]
    ) -> Tuple[Optional[Path], Optional[str]]:
        """Polls for video generation, downloads MP4 file from new response container."""
        start_time = time.time()
        selectors = get_config_manager().selectors.video
        last_progress_edit = 0.0
        stable_text = ""
        stable_text_first_seen = 0.0

        await asyncio.sleep(5)

        while time.time() - start_time < timeout:
            if cancel_event and cancel_event.is_set():
                logger.info("Video generation canceled by user.")
                return None, "❌ Đã hủy tạo video theo yêu cầu."

            elapsed = int(time.time() - start_time)
            mins = elapsed // 60
            secs = elapsed % 60
            elapsed_str = f"{mins:02d}:{secs:02d}"

            if progress_callback and (time.time() - last_progress_edit > 5):
                last_progress_edit = time.time()
                await progress_callback(elapsed_str)

            resp_containers = page.locator(selectors.response_container)
            current_count = await resp_containers.count()

            target_response = None
            if current_count > initial_response_count:
                target_response = resp_containers.last

            status, detail = await self.browser_service.detect_page_state(
                page=page,
                response_locator=target_response,
                result_selector=selectors.result_video
            )

            if status == DetectStatus.LOGIN_REQUIRED:
                ss, _ = await self.browser_service.capture_debug(page, "login_required")
                return None, f"🔐 Phiên Google hết hạn. Vui lòng mở scripts/open_chrome_profile.py để đăng nhập lại. (Ảnh debug: {ss.name})"

            if status == DetectStatus.QUOTA_EXCEEDED:
                ss, _ = await self.browser_service.capture_debug(page, "video_quota")
                return None, f"{detail} (Ảnh debug: {ss.name})"

            if status == DetectStatus.SAFETY_REFUSED:
                ss, _ = await self.browser_service.capture_debug(page, "video_refused")
                return None, f"{detail} (Ảnh debug: {ss.name})"

            # If video is ready
            if status == DetectStatus.SUCCESS and target_response is not None:
                stable_text = ""
                stable_text_first_seen = 0.0
                last_vid = await self.browser_service.find_ready_media(target_response, selectors.result_video)
                if last_vid is not None:
                    logger.info("Found generated video element. Attempting download...")
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    success = await self._download_video(page, last_vid, dest_file)
                    if success and dest_file.exists():
                        return dest_file, None
                    ss, _ = await self.browser_service.capture_debug(page, "video_download_failed")
                    return None, f"Không thể tải file video MP4. Ảnh debug: {ss.name}"

            elif status == DetectStatus.TEXT_RESPONSE and target_response is not None:
                # Require text to be non-empty and stable across at least 60 seconds
                if detail == stable_text and len(detail.strip()) > 0:
                    if time.time() - stable_text_first_seen >= 60.0:
                        logger.info(f"Gemini responded with text instead of video: {detail[:100]}...")
                        return None, f"Gemini phản hồi bằng văn bản thay vì tạo video:\n\n\"{detail}\""
                else:
                    stable_text = detail
                    stable_text_first_seen = time.time()
            else:
                stable_text = ""
                stable_text_first_seen = 0.0

            await asyncio.sleep(poll_interval)

        ss, _ = await self.browser_service.capture_debug(page, "video_timeout")
        return None, f"⏳ Quá thời gian chờ tạo video ({timeout}s). Ảnh debug: {ss.name}"

    @staticmethod
    def _looks_like_mp4(body: bytes, content_type: str) -> bool:
        return len(body) > 1024 and (content_type.startswith("video/") or body[4:8] == b"ftyp")

    async def _download_video(self, page: Page, vid_elem, dest_file: Path) -> bool:
        """
        Downloads the generated MP4:
        1. GET the <video> src with the browser's cookies (APIRequestContext). No clicking, so it is not
           affected by overlays or a small window; it is the same file as the 'Tải video xuống' button.
        2. Click the 'Tải video xuống' button of the same response (normal -> forced -> JS click, no hover).
        3. fetch() the src inside the page with credentials (also covers blob: URLs).
        """
        selectors = get_config_manager().selectors.video

        # Absolute URL (the attribute may be relative); <source> child as a fallback
        try:
            src = await vid_elem.evaluate(
                "el => el.currentSrc || el.src || el.href || (el.querySelector && el.querySelector('source') ? el.querySelector('source').src : '')"
            )
        except Exception:
            src = await vid_elem.get_attribute("src")

        # 1. Direct request with the browser's cookies
        if src and src.startswith("http"):
            try:
                resp = await page.context.request.get(src, timeout=120000)
                body = await resp.body()
                content_type = resp.headers.get("content-type", "")
                if resp.ok and self._looks_like_mp4(body, content_type):
                    dest_file.write_bytes(body)
                    logger.info(f"Downloaded video via src request ({len(body) / 1e6:.2f} MB): {dest_file}")
                    return True
                logger.warning(f"Video src request returned {resp.status} '{content_type}' ({len(body)} bytes)")
            except Exception as e:
                logger.warning(f"Video src request failed: {e}")

        # 2. Download button of the same response
        try:
            scope = vid_elem.locator("xpath=ancestor::model-response[1]")
            if await scope.count() == 0:
                scope = page
            if await self.browser_service.click_download(page, scope.locator(selectors.download_button), dest_file, timeout_ms=60000):
                return True
        except Exception as e:
            logger.warning(f"Video download button failed: {e}")

        # 3. In-page fetch with cookies
        try:
            if src and src.startswith(("http", "blob:")):
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
                    src
                )
                if buffer and "," in buffer:
                    import base64
                    data = base64.b64decode(buffer.split(",")[1])
                    dest_file.write_bytes(data)
                    logger.info(f"Saved video via page fetch: {dest_file}")
                    return True
        except Exception as e:
            logger.warning(f"Video evaluated fetch failed: {e}")

        return False
