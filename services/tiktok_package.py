"""TikTok package builder: generates caption, checklist and archives approved media."""
import html
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from core.config import PROJECT_ROOT
from core.logger import logger


class TikTokPackageService:
    def __init__(self, output_base_dir: Optional[Path] = None):
        self.output_base_dir = output_base_dir or (PROJECT_ROOT / "workspace" / "output")

    def build_caption(self, tiktok_config, product_info: Optional[str] = None) -> str:
        """
        Builds TikTok caption.
        If product_info is a URL, avoids dumping raw URL into caption (TikTok captions don't support clickable links).
        """
        hashtags = tiktok_config.hashtags
        if product_info and product_info.strip():
            clean_product = product_info.strip()
            # If user provided a URL, use a general phrase in caption and keep URL for checklist
            if clean_product.startswith(("http://", "https://")):
                caption = tiktok_config.caption_without_product.format(
                    hashtags=hashtags
                )
            else:
                caption = tiktok_config.caption_template.format(
                    product_name=clean_product,
                    hashtags=hashtags
                )
        else:
            caption = tiktok_config.caption_without_product.format(
                hashtags=hashtags
            )
        return caption.strip()

    def format_publish_package_messages(
        self,
        caption: str,
        product_info: Optional[str] = None
    ) -> Tuple[str, str]:
        """
        Returns:
        1. Caption message formatted in HTML with pre/code for 1-tap copy
        2. Instructions & checklist message in HTML
        """
        escaped_caption = html.escape(caption)
        caption_msg = (
            "📋 <b>Caption (chạm để sao chép):</b>\n\n"
            f"<pre><code>{escaped_caption}</code></pre>"
        )

        escaped_product = html.escape(product_info.strip()) if product_info else "(Không có - gắn trực tiếp từ Showcase)"

        checklist_lines = [
            "📦 <b>Gói đăng TikTok Affiliate sẵn sàng!</b>",
            "",
            f"🛒 <b>Sản phẩm cần gắn:</b> {escaped_product}",
            "",
            "📝 <b>Checklist khi đăng bài trên TikTok:</b>",
            "  ☐ 1. Lưu video gốc (file đính kèm ở trên) vào điện thoại",
            "  ☐ 2. Mở app TikTok, chọn video vừa lưu",
            "  ☐ 3. Bật tùy chọn: <b>'Nội dung do AI tạo'</b> (AI-generated content)",
            "  ☐ 4. Dán caption đã sao chép ở trên",
            "  ☐ 5. Bấm 'Thêm liên kết' → 'Sản phẩm' → Gắn sản phẩm tương ứng từ Showcase",
            "  ☐ 6. Kiểm tra lại và bấm Đăng! ✨"
        ]
        checklist_msg = "\n".join(checklist_lines)

        return caption_msg, checklist_msg

    def archive_approved_media(
        self,
        session_id: str,
        approved_image_path: Optional[Path],
        approved_video_path: Optional[Path]
    ) -> Path:
        """
        Copies approved image and video to workspace/output/<YYYY-MM-DD>/<session_id>/
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        dest_dir = self.output_base_dir / date_str / session_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        if approved_image_path and Path(approved_image_path).exists():
            img_src = Path(approved_image_path)
            shutil.copy2(img_src, dest_dir / f"approved_{img_src.name}")
            logger.info(f"Archived image to {dest_dir}")

        if approved_video_path and Path(approved_video_path).exists():
            vid_src = Path(approved_video_path)
            shutil.copy2(vid_src, dest_dir / f"approved_{vid_src.name}")
            logger.info(f"Archived video to {dest_dir}")

        return dest_dir


_tiktok_service: Optional[TikTokPackageService] = None


def get_tiktok_service() -> TikTokPackageService:
    global _tiktok_service
    if _tiktok_service is None:
        _tiktok_service = TikTokPackageService()
    return _tiktok_service

