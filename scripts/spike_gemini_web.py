"""Spike script for validating Gemini Web automation directly without Telegram."""
import argparse
import asyncio
import sys
from pathlib import Path
from PIL import Image, ImageDraw

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import get_config_manager, PROJECT_ROOT
from core.logger import logger, setup_logger
from services.browser import get_browser_service
from services.gemini_web_image import GeminiWebImageService
from services.gemini_web_video import GeminiWebVideoService


def create_sample_test_images(workspace_dir: Path) -> tuple[Path, Path]:
    """Generates two 9:16 sample test images if none exist."""
    test_dir = workspace_dir / "spike_tests"
    test_dir.mkdir(parents=True, exist_ok=True)

    model_img_path = test_dir / "sample_model.jpg"
    cloth_img_path = test_dir / "sample_cloth.jpg"

    if not model_img_path.exists():
        img = Image.new("RGB", (720, 1280), color=(240, 230, 220))
        draw = ImageDraw.Draw(img)
        draw.text((200, 600), "Sample Model (Person)\n9:16 Ratio", fill=(30, 30, 30))
        img.save(model_img_path, "JPEG")

    if not cloth_img_path.exists():
        img = Image.new("RGB", (720, 1280), color=(220, 240, 255))
        draw = ImageDraw.Draw(img)
        draw.text((200, 600), "Sample Reference Garment", fill=(30, 30, 30))
        img.save(cloth_img_path, "JPEG")

    return model_img_path, cloth_img_path


async def run_spike(model_path: Path, cloth_path: Path, test_video: bool = False):
    setup_logger(debug=True)
    logger.info("--- BẮT ĐẦU SPIKE GEMINI WEB AUTOMATION ---")

    cm = get_config_manager()
    browser_svc = get_browser_service()

    try:
        # Step 1: Connect CDP & Verify Login
        logger.info("[Step 1] Kiểm tra kết nối Chrome & đăng nhập Google...")
        try:
            await browser_svc.connect()
            page = await browser_svc.get_page()
            await page.goto(cm.app_config.gemini_web.app_url, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            is_logged_in = await browser_svc.check_login_status(page)
            if not is_logged_in:
                logger.error("❌ Chưa đăng nhập Google! Hãy chạy scripts/open_chrome_profile.py để đăng nhập.")
                return
            logger.info("✅ Đã đăng nhập Google thành công!")
        except Exception as e:
            logger.error(f"❌ Lỗi kết nối Chrome: {e}")
            return

        # Step 2: Try-On Image Generation
        logger.info(f"[Step 2] Thử nghiệm tạo ảnh Thử Đồ (Model: {model_path}, Garment: {cloth_path})...")
        workspace_dir = PROJECT_ROOT / "workspace"
        spike_session_dir = workspace_dir / "spike_session"
        spike_session_dir.mkdir(parents=True, exist_ok=True)

        img_service = GeminiWebImageService()

        async def progress_cb(t):
            logger.info(f"⏳ Tiến độ xử lý: {t}")

        img_path, err, chat_url = await img_service.generate_tryon_image(
            model_image_path=model_path,
            cloth_image_path=cloth_path,
            session_dir=spike_session_dir,
            version=1,
            progress_callback=progress_cb
        )

        if err or not img_path:
            logger.warning(f"Kết quả tạo ảnh: {err}")
            return

        logger.info(f"✅ Tạo ảnh thành công: {img_path}")
        logger.info(f"Chat URL: {chat_url}")

        # Step 3: Test Refinement
        logger.info("[Step 3] Thử nghiệm góp ý chỉnh sửa ảnh trong cùng cuộc chat...")
        refined_path, ref_err, _ = await img_service.refine_tryon_image(
            feedback="Cho dáng váy ôm hơn một chút",
            chat_url=chat_url,
            session_dir=spike_session_dir,
            version=2,
            progress_callback=progress_cb
        )
        if refined_path:
            logger.info(f"✅ Góp ý sửa ảnh thành công: {refined_path}")
        else:
            logger.warning(f"Góp ý sửa ảnh: {ref_err}")

        # Step 4: Optional Video Generation
        if test_video:
            logger.info("[Step 4] Thử nghiệm tạo video thời trang từ ảnh đã tạo...")
            vid_service = GeminiWebVideoService()
            target_img = refined_path if refined_path else img_path
            vid_path, vid_err = await vid_service.generate_video(
                approved_image_path=target_img,
                session_dir=spike_session_dir,
                feedback_list=[],
                version=1,
                progress_callback=progress_cb
            )
            if vid_path:
                logger.info(f"✅ Tạo video thành công: {vid_path}")
            else:
                logger.warning(f"Tạo video: {vid_err}")

        logger.info("--- HOÀN THÀNH SPIKE ---")
    finally:
        await browser_svc.close_connections()


def main():
    parser = argparse.ArgumentParser(description="Spike Gemini Web Automation")
    parser.add_argument("--model", type=str, help="Đường dẫn file ảnh người mẫu thật (JPEG/PNG)")
    parser.add_argument("--cloth", type=str, help="Đường dẫn file ảnh trang phục thật (JPEG/PNG)")
    parser.add_argument("--video", action="store_true", help="Kiểm tra cả bước tạo video")
    args = parser.parse_args()

    workspace_dir = PROJECT_ROOT / "workspace"
    if args.model and args.cloth:
        model_path = Path(args.model).resolve()
        cloth_path = Path(args.cloth).resolve()
    else:
        print("💡 Lưu ý: Chưa truyền --model và --cloth. Đang sử dụng ảnh mẫu giả lập.")
        print("   Để kiểm tra ảnh thật, dùng lệnh:")
        print("   python scripts/spike_gemini_web.py --model path/to/model.jpg --cloth path/to/cloth.jpg --video\n")
        model_path, cloth_path = create_sample_test_images(workspace_dir)

    asyncio.run(run_spike(model_path, cloth_path, test_video=args.video))


if __name__ == "__main__":
    main()
