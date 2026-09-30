"""Script to open Google Chrome with the dedicated bot profile for manual login."""
import os
import subprocess
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import get_config_manager
from core.logger import logger, setup_logger


def main():
    setup_logger(debug=True)
    cm = get_config_manager()

    chrome_path = cm.env.CHROME_PATH
    profile_dir = cm.app_config.gemini_web.absolute_profile_dir
    cdp_port = cm.app_config.gemini_web.cdp_port
    app_url = cm.app_config.gemini_web.app_url

    profile_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("MỞ CHROME PROFILE ĐỂ ĐĂNG NHẬP GOOGLE THỦ CÔNG")
    print("=" * 60)
    print(f"Chrome Path: {chrome_path}")
    print(f"Profile Dir: {profile_dir}")
    print(f"CDP Port   : {cdp_port}")
    print(f"URL        : {app_url}")
    print("-" * 60)
    print("HƯỚNG DẪN:")
    print("1. Trình duyệt Chrome sẽ mở ra.")
    print("2. Đăng nhập tài khoản Google (có gói Gemini Pro) của bạn.")
    print("3. Kiểm tra vào được https://gemini.google.com bình thường và thấy avatar tài khoản.")
    print("4. BẮT BUỘC: Sau khi đăng nhập xong, bạn PHẢI ĐÓNG hoàn toàn trình duyệt Chrome này trước khi chạy bot hoặc spike test!")
    print("=" * 60)

    cmd = [
        chrome_path,
        f"--user-data-dir={str(profile_dir)}",
        "--no-first-run",
        "--no-default-browser-check",
        app_url
    ]

    try:
        proc = subprocess.Popen(cmd)
        print(f"\nChrome đã được mở (PID: {proc.pid}). Nhấn Ctrl+C để kết thúc theo dõi script...")
        proc.wait()
    except KeyboardInterrupt:
        print("\nĐang thoát script...")
    except Exception as e:
        print(f"\nLỗi khi mở Chrome: {e}")


if __name__ == "__main__":
    main()
