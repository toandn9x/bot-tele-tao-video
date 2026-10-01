"""Browser service: manages real Chrome process, Playwright CDP connection, status detection and debug capture."""
import asyncio
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
import psutil

from core.config import get_config_manager, DetectSelectors, PROJECT_ROOT
from core.logger import logger
from core.states import DetectStatus


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Checks if a TCP port is currently open and listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


class BrowserService:
    def __init__(self):
        self.lock = asyncio.Lock()
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._chrome_proc: Optional[subprocess.Popen] = None
        self.debug_dir = PROJECT_ROOT / "workspace" / "debug"
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    async def ensure_chrome_process(self) -> None:
        """Ensures that Chrome is running with remote debugging port enabled."""
        cm = get_config_manager()
        cdp_port = cm.app_config.gemini_web.cdp_port
        profile_dir = cm.app_config.gemini_web.absolute_profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)

        if is_port_in_use(cdp_port):
            logger.info(f"CDP port {cdp_port} is already active.")
            return

        chrome_path = cm.env.CHROME_PATH
        if not Path(chrome_path).exists():
            raise FileNotFoundError(f"Chrome executable not found at: {chrome_path}")

        # If CDP port is not active, check if any Chrome processes are still holding this profile
        profile_dir_norm = str(profile_dir.resolve()).lower().replace("/", "\\")
        stray_procs = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline_str = " ".join(proc.info.get('cmdline') or []).lower().replace("/", "\\")
                if profile_dir_norm in cmdline_str:
                    stray_procs.append((proc, cmdline_str))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if stray_procs:
            manual_procs = [p for p, cmd in stray_procs if f"--remote-debugging-port={cdp_port}".lower() not in cmd]
            old_cdp_procs = [p for p, cmd in stray_procs if f"--remote-debugging-port={cdp_port}".lower() in cmd]

            if manual_procs:
                pids = ", ".join(str(p.pid) for p in manual_procs)
                err_msg = (
                    f"❌ Profile Chrome ({profile_dir.name}) đang được mở bởi một cửa sổ Chrome khác (PID: {pids}). "
                    "Để tránh mất phiên đăng nhập và cookie chưa kịp lưu xuống đĩa, bot không tự tắt cửa sổ này. "
                    "Vui lòng đóng hoàn toàn cửa sổ Chrome đó rồi thực hiện lại!"
                )
                logger.error(err_msg)
                raise RuntimeError(err_msg)

            if old_cdp_procs:
                logger.warning(
                    f"Phát hiện {len(old_cdp_procs)} tiến trình Chrome CDP cũ bị kẹt. Đang dọn dẹp để khởi động lại..."
                )
                for proc in old_cdp_procs:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                await asyncio.sleep(1.5)

        cmd = [
            chrome_path,
            f"--user-data-dir={str(profile_dir)}",
            f"--remote-debugging-port={cdp_port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
        ]

        logger.info(f"Launching Chrome via command: {' '.join(cmd)}")
        self._chrome_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Wait for CDP port to open
        start_time = time.time()
        while time.time() - start_time < 15:
            if is_port_in_use(cdp_port):
                logger.info("Chrome remote debugging port is ready.")
                await asyncio.sleep(1)  # brief settle time
                return
            await asyncio.sleep(0.5)

        raise TimeoutError(f"Chrome failed to start or open port {cdp_port} within 15 seconds.")

    async def connect(self, retry: bool = True) -> Browser:
        """Connects Playwright over CDP to the Chrome instance."""
        cm = get_config_manager()
        cdp_port = cm.app_config.gemini_web.cdp_port
        cdp_url = f"http://127.0.0.1:{cdp_port}"

        try:
            await self.ensure_chrome_process()

            if self._playwright is None:
                self._playwright = await async_playwright().start()

            if self._browser is None or not self._browser.is_connected():
                logger.info(f"Connecting Playwright over CDP to {cdp_url}...")
                self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
                if self._browser.contexts:
                    self._context = self._browser.contexts[0]
                else:
                    self._context = await self._browser.new_context()

            return self._browser
        except RuntimeError:
            # Profile conflict or configuration error - do not attempt restart retry
            raise
        except Exception as e:
            logger.error(f"CDP connection error: {e}")
            if retry:
                logger.info("Attempting to restart Chrome and reconnect once...")
                await self.restart_chrome()
                return await self.connect(retry=False)
            raise

    async def restart_chrome(self) -> None:
        """Kills any stuck Chrome instance on CDP port and restarts."""
        await self.close_connections()
        cm = get_config_manager()
        cdp_port = cm.app_config.gemini_web.cdp_port

        # Kill processes using this CDP port
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline_str = " ".join(proc.info.get('cmdline') or []).lower().replace("/", "\\")
                is_cdp_match = f"--remote-debugging-port={cdp_port}".lower() in cmdline_str

                if is_cdp_match:
                    logger.warning(f"Terminating old Chrome CDP process pid={proc.pid}")
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        await asyncio.sleep(1.5)
        await self.ensure_chrome_process()

    async def get_page(self) -> Page:
        """Gets the dedicated Gemini page or creates a new one without stealing window focus."""
        await self.connect()
        if not self._context:
            raise RuntimeError("Browser context not available.")

        pages = self._context.pages
        page = None
        # 1. Search for existing Gemini Web tab
        for p in pages:
            if "gemini.google.com" in p.url:
                page = p
                break

        # 2. Search for any valid tab
        if page is None:
            valid_pages = [p for p in pages if not p.url.startswith("devtools://")]
            if valid_pages:
                page = valid_pages[0]

        # 3. Create new page
        if page is None:
            page = await self._context.new_page()

        return page

    async def ensure_usable_window(self, page: Page, min_width: int = 1200, min_height: int = 800) -> None:
        """No-op: Disabled to avoid stealing focus and forcing fullscreen/maximized window."""
        pass

    async def click_download(self, page: Page, buttons, dest_file: Path, timeout_ms: int = 30000) -> bool:
        """
        Clicks the last matching download button and saves the resulting download to dest_file.
        Tries a normal click first, then a forced click (ignores overlays such as Gemini's fixed input box),
        then a JS click (also works on hover-only buttons that are currently hidden).
        """
        try:
            if await buttons.count() == 0:
                return False
        except Exception:
            return False

        attempts = []
        visible = buttons.filter(visible=True)
        if await visible.count() > 0:
            target = visible.last
            attempts.append(("click", lambda: target.click(timeout=5000)))
            attempts.append(("force click", lambda: target.click(timeout=5000, force=True)))
        attempts.append(("js click", lambda: buttons.last.dispatch_event("click")))

        for name, action in attempts:
            try:
                async with page.expect_download(timeout=timeout_ms) as download_info:
                    await action()
                download = await download_info.value
                await download.save_as(str(dest_file))
                logger.info(f"Downloaded via download button ({name}): {dest_file}")
                return True
            except Exception as e:
                logger.warning(f"Download button {name} failed: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}")
        return False

    async def capture_debug(self, page: Page, prefix: str = "debug") -> Tuple[Path, Path]:
        """Captures screenshot and HTML dump for troubleshooting."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ss_path = self.debug_dir / f"{prefix}_{timestamp}.png"
        html_path = self.debug_dir / f"{prefix}_{timestamp}.html"

        try:
            await page.screenshot(path=str(ss_path), full_page=True)
            content = await page.content()
            html_path.write_text(content, encoding="utf-8")
            logger.info(f"Captured debug files: {ss_path.name}, {html_path.name}")
        except Exception as e:
            logger.error(f"Failed to capture debug: {e}")

        return ss_path, html_path

    async def check_login_status(self, page: Page) -> bool:
        """Checks if current page is logged into Google without false positives from avatar."""
        current_url = page.url
        login_signals = [
            "accounts.google.com/servicelogin",
            "accounts.google.com/signin",
            "accounts.google.com/v3/signin",
            "accounts.google.com/interactive/identifier"
        ]
        if any(sig in current_url.lower() for sig in login_signals):
            logger.warning(f"Google login redirect detected: {current_url}")
            return False

        # Check for explicit Sign-in action button on page (e.g. 'Đăng nhập', 'Sign in', .signed-out)
        try:
            sign_in_elem = page.locator(".signed-out, a[href*='servicelogin' i], a[href*='signin' i], a:has-text('Đăng nhập'), button:has-text('Đăng nhập'), a:has-text('Sign in'), button:has-text('Sign in')")
            count = await sign_in_elem.count()
            for idx in range(count):
                if await sign_in_elem.nth(idx).is_visible():
                    logger.warning("Google login required: signed-out indicator or Sign-in button is visible.")
                    return False
        except Exception as e:
            logger.debug(f"Error checking sign-in elements: {e}")

        return True

    async def detect_page_state(
        self,
        page: Page,
        response_locator=None,
        result_selector: Optional[str] = None
    ) -> Tuple[DetectStatus, str]:
        """
        Periodically checks page for success, text response, refusal, quota, login, or still generating.
        Scans text and media ONLY in the newest response container, avoiding full-page false positives.
        """
        cm = get_config_manager()
        selectors = cm.selectors

        # 1. Check Login
        if not await self.check_login_status(page):
            return DetectStatus.LOGIN_REQUIRED, "Phiên đăng nhập Google đã hết hạn."

        if response_locator is None:
            return DetectStatus.GENERATING, "Đang chờ câu trả lời..."

        # The page-level 'Ngừng tạo câu trả lời' button can stay visible after Gemini has already answered
        # (seen with video refusals), so decisive signals inside the newest response are checked first.

        # 2. Ready media (image/video) inside the newest response -> SUCCESS, even if interim
        #    text like 'đang tạo' or the stop button is still shown.
        if result_selector and await self.find_ready_media(response_locator, result_selector) is not None:
            return DetectStatus.SUCCESS, "Kết quả đã sẵn sàng."

        # 3. Quota / refusal text inside the newest response (screen-reader-only labels removed)
        resp_text = await self.get_response_text(response_locator)
        resp_text_lower = resp_text.lower()

        for q_text in selectors.detect.quota_texts:
            if q_text.lower() in resp_text_lower:
                return DetectStatus.QUOTA_EXCEEDED, f"Đã chạm hạn mức Gemini:\n\"{resp_text[:400]}\""

        for r_text in selectors.detect.refused_texts:
            if r_text.lower() in resp_text_lower:
                return DetectStatus.SAFETY_REFUSED, f"Gemini từ chối yêu cầu:\n\"{resp_text[:400]}\""

        # 4. Still generating: stop button / progress indicator anywhere on the page
        try:
            gen_locator = page.locator(selectors.detect.generating)
            for idx in range(await gen_locator.count()):
                if await gen_locator.nth(idx).is_visible():
                    return DetectStatus.GENERATING, "Gemini đang xử lý..."
        except Exception:
            pass

        for g_text in selectors.detect.generating_texts:
            if g_text.lower() in resp_text_lower:
                return DetectStatus.GENERATING, f"Gemini đang xử lý ('{g_text}')..."

        # 5. An image/video placeholder is rendered but its media is not loaded yet
        #    (numbering continues after the generating checks above)
        try:
            if selectors.detect.media_placeholder and await response_locator.locator(selectors.detect.media_placeholder).count() > 0:
                return DetectStatus.GENERATING, "Đang tải kết quả..."
        except Exception as e:
            logger.debug(f"Error checking media placeholder: {e}")

        # 6. Text-only answer: only once Gemini marks the response as complete (action bar rendered)
        if resp_text:
            try:
                if selectors.detect.response_complete and await response_locator.locator(selectors.detect.response_complete).count() == 0:
                    return DetectStatus.GENERATING, "Đang chờ Gemini hoàn tất câu trả lời..."
            except Exception as e:
                logger.debug(f"Error checking response completion marker: {e}")
            return DetectStatus.TEXT_RESPONSE, resp_text

        return DetectStatus.GENERATING, "Đang chờ..."

    async def find_ready_media(self, container, selector: str):
        """
        Returns the last visible, fully loaded image (natural width >= 256px) or ready video inside
        container, or None. Scans from the end so trailing icons/favicons are skipped.
        """
        try:
            items = container.locator(selector)
            count = await items.count()
            for idx in range(count - 1, -1, -1):
                item = items.nth(idx)
                if not await item.is_visible():
                    continue
                info = await item.evaluate("""el => ({
                    tag: el.tagName.toLowerCase(),
                    complete: !!el.complete,
                    naturalWidth: el.naturalWidth || 0,
                    readyState: el.readyState || 0,
                    src: el.currentSrc || el.src || el.href
                        || (el.querySelector && el.querySelector('source') ? el.querySelector('source').src : '') || ''
                })""")
                if info["tag"] == "img":
                    if info["complete"] and info["naturalWidth"] >= 256:
                        return item
                elif info["tag"] == "video":
                    if info["readyState"] >= 1 or info["src"]:
                        return item
                else:
                    return item
        except Exception as e:
            logger.debug(f"Error finding ready media: {e}")
        return None

    async def get_response_text(self, container) -> str:
        """Visible text of a response without screen-reader-only labels ('Gemini đã nói') or the action bar."""
        try:
            text = await container.evaluate("""el => {
                let t = el.innerText || '';
                el.querySelectorAll('.cdk-visually-hidden, [class*="screen-reader"], message-actions').forEach(n => {
                    const s = (n.textContent || '').trim();
                    if (s) t = t.split(s).join('');
                });
                return t;
            }""")
            return " ".join((text or "").split())
        except Exception as e:
            logger.debug(f"Error reading response text: {e}")
            return ""

    async def close_connections(self) -> None:
        """Closes Playwright connections without necessarily killing Chrome."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.debug(f"Error closing Playwright connection: {e}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None

    async def close_all(self) -> None:
        """Closes Playwright connection and terminates Chrome."""
        await self.close_connections()
        if self._chrome_proc:
            try:
                self._chrome_proc.terminate()
            except Exception:
                pass
            self._chrome_proc = None


# Global browser service
browser_service = BrowserService()


def get_browser_service() -> BrowserService:
    return browser_service
