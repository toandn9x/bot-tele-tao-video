"""Telegram sending helpers: generous timeouts for media and graceful fallbacks on slow networks."""
from pathlib import Path

from telegram.error import NetworkError

from core.logger import logger
from utils.media_helper import make_preview_jpeg

# Uploading photos/videos can take far longer than PTB's default 5s timeouts on a home connection
MEDIA_TIMEOUTS = {"read_timeout": 60, "write_timeout": 180, "connect_timeout": 30, "pool_timeout": 30}

SLOW_NETWORK_NOTE = "\n\n⚠️ <i>Mạng gửi file chậm nên Telegram có thể hiển thị trễ. Dùng các nút bên dưới để tiếp tục.</i>"


async def edit_or_reply(status_msg, target, text: str, **kwargs) -> None:
    """Edits the status message; if it is gone (deleted/too old), replies with a new message. Never raises."""
    if status_msg is not None:
        try:
            await status_msg.edit_text(text, **kwargs)
            return
        except Exception as e:
            logger.debug(f"Status message edit failed, replying instead: {e}")
    try:
        await target.reply_text(text, **kwargs)
    except Exception as e:
        logger.warning(f"Could not deliver message to user: {e}")


async def send_review_photo(target, image_path: Path, caption: str, keyboard) -> None:
    """
    Sends a compressed JPEG preview of image_path with the review keyboard. On network trouble the photo
    may still arrive, so we do not resend it; a text message with the same keyboard keeps the flow going.
    """
    preview = make_preview_jpeg(image_path)
    try:
        with open(preview, "rb") as f:
            await target.reply_photo(photo=f, caption=caption, parse_mode="HTML", reply_markup=keyboard, **MEDIA_TIMEOUTS)
    except NetworkError as e:
        logger.warning(f"Sending result photo failed or timed out: {e}")
        await edit_or_reply(None, target, caption + SLOW_NETWORK_NOTE, parse_mode="HTML", reply_markup=keyboard)


async def send_review_video(target, video_path: Path, caption: str, keyboard) -> None:
    """Sends the video preview with the review keyboard; falls back to a text message on network trouble."""
    try:
        with open(video_path, "rb") as f:
            await target.reply_video(video=f, caption=caption, parse_mode="HTML", reply_markup=keyboard,
                                     supports_streaming=True, **MEDIA_TIMEOUTS)
    except NetworkError as e:
        logger.warning(f"Sending result video failed or timed out: {e}")
        await edit_or_reply(None, target, caption + SLOW_NETWORK_NOTE, parse_mode="HTML", reply_markup=keyboard)
