"""Publish handler: finalizes TikTok package, sends uncompressed video, caption and checklist."""
import html
from pathlib import Path
from typing import Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError
from telegram.ext import ContextTypes

from core.config import get_config_manager
from core.logger import logger
from core.states import BotState, JobType
from handlers.menu_handler import cmd_start, is_authorized
from services.tiktok_package import get_tiktok_service
from utils.telegram_io import MEDIA_TIMEOUTS

package_service = get_tiktok_service()


def get_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Về trang chủ", callback_data="btn_home")]
    ])


async def finish_and_send_package(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    product_info: Optional[str] = None
) -> BotState:
    """Delivers the complete TikTok affiliate posting package."""
    cm = get_config_manager()
    session_id = context.user_data.get("session_id", "session")
    approved_video: Path = context.user_data.get("approved_video_path")
    approved_image: Path = context.user_data.get("approved_image_path")

    target_msg = update.message or (update.callback_query.message if update.callback_query else None)

    # 1. Archive to output folder
    dest_dir = package_service.archive_approved_media(
        session_id=session_id,
        approved_image_path=approved_image,
        approved_video_path=approved_video
    )
    logger.info(f"Media archived to {dest_dir}")

    # 2. Build Caption & Checklist
    caption = package_service.build_caption(cm.app_config.tiktok, product_info)
    caption_msg, checklist_msg = package_service.format_publish_package_messages(caption, product_info)

    # 3. Send uncompressed video as document
    if approved_video and approved_video.exists():
        with open(approved_video, "rb") as f:
            try:
                await target_msg.reply_document(
                    document=f,
                    filename=f"TikTok_{session_id}.mp4",
                    caption="📥 <b>File video gốc (không nén) để đăng TikTok.</b> Chạm để lưu vào máy.",
                    parse_mode="HTML",
                    **MEDIA_TIMEOUTS
                )
            except NetworkError as e:
                logger.warning(f"Sending original video document failed or timed out: {e}")
                await target_msg.reply_text(
                    "⚠️ <b>Mạng gửi file chậm</b>, file video có thể đến trễ. "
                    f"Bản gốc đã lưu trên máy tại:\n<code>{html.escape(str(dest_dir))}</code>",
                    parse_mode="HTML"
                )

    # 4. Send monospace caption for 1-tap copy
    await target_msg.reply_text(caption_msg, parse_mode="HTML")

    # 5. Send checklist & guidance
    await target_msg.reply_text(
        checklist_msg,
        parse_mode="HTML",
        reply_markup=get_home_keyboard()
    )

    return BotState.IDLE


async def handle_product_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Handles text input with product name or link."""
    user = update.effective_user
    if not is_authorized(user.id):
        return BotState.IDLE

    product_info = update.message.text.strip()
    context.user_data["product_info"] = product_info
    return await finish_and_send_package(update, context, product_info=product_info)


async def handle_skip_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Handles 'Bỏ qua' button when entering product info."""
    query = update.callback_query
    await query.answer()
    return await finish_and_send_package(update, context, product_info=None)


async def handle_retry_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Retries last failed job (image, refine, or video) from ERROR state."""
    query = update.callback_query
    await query.answer()

    last_job = context.user_data.get("last_job")
    if last_job == JobType.IMAGE_INITIAL:
        from handlers.image_flow_handler import trigger_image_generation
        return await trigger_image_generation(update, context)

    elif last_job == JobType.IMAGE_REFINE:
        from handlers.image_flow_handler import execute_image_refinement, trigger_image_generation
        feedback = context.user_data.get("last_image_feedback", "")
        if feedback:
            return await execute_image_refinement(query.message, context, feedback, user_id=update.effective_user.id)
        else:
            return await trigger_image_generation(update, context)

    elif last_job == JobType.VIDEO:
        from handlers.video_flow_handler import trigger_video_generation
        return await trigger_video_generation(update, context)

    else:
        await query.message.reply_text("Không tìm thấy tác vụ gần nhất để thử lại. Bắt đầu phiên mới nhé!")
        return await cmd_start(update, context)
