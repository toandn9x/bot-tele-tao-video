"""Video generation and review flow handlers."""
import asyncio
import html
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.logger import logger
from core.states import BotState, JobType, set_user_job_active
from handlers.menu_handler import cmd_cancel, get_cancel_keyboard, is_authorized
from services.gemini_web_video import GeminiWebVideoService
from utils.telegram_io import edit_or_reply, send_review_video

vid_service = GeminiWebVideoService()


def get_video_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Duyệt video", callback_data="btn_approve_video")],
        [InlineKeyboardButton("✏️ Góp ý chỉnh video", callback_data="btn_feedback_video")],
        [InlineKeyboardButton("❌ Hủy", callback_data="btn_cancel")]
    ])


def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Quay lại", callback_data="btn_back_to_review_vid")]
    ])


def get_skip_product_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ Bỏ qua", callback_data="btn_skip_product")]
    ])


def get_error_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Thử lại", callback_data="btn_retry_job")],
        [InlineKeyboardButton("❌ Hủy", callback_data="btn_cancel")]
    ])


async def trigger_video_generation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Initiates video generation from approved try-on image."""
    user = update.effective_user
    if not is_authorized(user.id):
        return BotState.IDLE

    context.user_data["user_id"] = user.id
    context.user_data["last_job"] = JobType.VIDEO
    set_user_job_active(user.id, True)
    approved_img: Path = context.user_data.get("approved_image_path")
    session_dir: Path = context.user_data.get("session_dir")
    feedback_list = context.user_data.get("video_feedback", [])

    version = len(context.user_data.get("video_versions", [])) + 1
    cancel_event = asyncio.Event()
    context.user_data["cancel_event"] = cancel_event
    context.user_data["cancel_requested"] = False

    msg_target = update.message or (update.callback_query.message if update.callback_query else None)
    status_msg = await msg_target.reply_text(
        "🎬 Đang tạo video 9:16… 00:00 (quá trình thường mất 1-3 phút)",
        reply_markup=get_cancel_keyboard()
    )

    async def update_progress(progress_text: str):
        try:
            if progress_text.startswith("⏳") or progress_text.startswith("🎬"):
                text = progress_text
            else:
                text = f"🎬 Đang tạo video 9:16… {progress_text}"
            await status_msg.edit_text(text, reply_markup=get_cancel_keyboard())
        except Exception:
            pass

    try:
        vid_path, error_msg = await vid_service.generate_video(
            approved_image_path=approved_img,
            session_dir=session_dir,
            feedback_list=feedback_list,
            version=version,
            progress_callback=update_progress,
            cancel_event=cancel_event
        )

        if context.user_data.get("cancel_requested"):
            await edit_or_reply(status_msg, msg_target, "🛑 Đã hủy quá trình tạo video.")
            return BotState.IDLE

        if error_msg or not vid_path or not vid_path.exists():
            escaped_err = html.escape(error_msg or "Không tải được video MP4.")
            await edit_or_reply(
                status_msg, msg_target,
                f"❌ <b>Lỗi khi tạo video:</b>\n{escaped_err}\n\nBạn muốn thử lại hay hủy?",
                parse_mode="HTML",
                reply_markup=get_error_keyboard()
            )
            return BotState.ERROR

        context.user_data["video_versions"].append(vid_path)
        context.user_data["approved_video_path"] = vid_path

        try:
            await status_msg.delete()
        except Exception:
            pass

        # The video exists on disk from here on: reach REVIEW_VIDEO even if the upload to Telegram is slow
        await send_review_video(
            msg_target, vid_path,
            f"🎬 <b>Video hoàn tất (Phiên bản #{version})</b>\n\nBạn có thể duyệt để nhận gói đăng bài, hoặc góp ý để tạo lại.",
            get_video_review_keyboard()
        )
        return BotState.REVIEW_VIDEO

    except Exception as e:
        logger.exception(f"Unhandled error in trigger_video_generation: {e}")
        await edit_or_reply(
            status_msg, msg_target,
            f"❌ <b>Lỗi hệ thống khi tạo video:</b> {html.escape(str(e))}\n\nBạn muốn thử lại hay hủy?",
            parse_mode="HTML",
            reply_markup=get_error_keyboard()
        )
        return BotState.ERROR
    finally:
        set_user_job_active(user.id, False)


async def handle_video_review_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Handles buttons on the video review message (also registered as a fallback safety net)."""
    query = update.callback_query
    data = query.data
    if data in ("btn_approve_video", "btn_feedback_video") and not context.user_data.get("video_versions"):
        await query.answer("Phiên làm việc này đã kết thúc. Vui lòng bấm 'Tạo mới' để bắt đầu phiên mới.", show_alert=True)
        return None
    await query.answer()

    if data == "btn_approve_video":
        await query.message.reply_text(
            "🛍️ <b>BƯỚC CUỐI: THÔNG TIN SẢN PHẨM</b>\n\n"
            "Hãy nhập <b>tên hoặc link sản phẩm</b> TikTok Shop để bot tự động tạo caption phù hợp.\n\n"
            "<i>(Hoặc bấm <b>⏭️ Bỏ qua</b> nếu muốn dùng caption mẫu chung)</i>",
            parse_mode="HTML",
            reply_markup=get_skip_product_keyboard()
        )
        return BotState.WAITING_PRODUCT

    elif data == "btn_feedback_video":
        await query.message.reply_text(
            "✏️ <b>Nhập góp ý chuyển động cho video:</b>\n\n"
            "Ví dụ: <i>'quay chậm hơn'</i>, <i>'cho người mẫu bước về phía trước'</i>, <i>'tóc bay nhẹ tự nhiên'</i>...\n"
            "<i>Bot sẽ tạo video mới từ ảnh đã duyệt kèm yêu cầu này.</i>",
            parse_mode="HTML",
            reply_markup=get_back_keyboard()
        )
        return BotState.WAITING_VID_FEEDBACK

    elif data == "btn_cancel":
        return await cmd_cancel(update, context)

    return BotState.REVIEW_VIDEO


async def handle_back_to_review_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Returns to REVIEW_VIDEO without canceling session."""
    query = update.callback_query
    await query.answer()

    approved_vid: Path = context.user_data.get("approved_video_path")
    version = len(context.user_data.get("video_versions", []))
    if approved_vid and approved_vid.exists():
        await send_review_video(
            query.message, approved_vid,
            f"🎬 <b>Video hoàn tất (Phiên bản #{version})</b>",
            get_video_review_keyboard()
        )
    else:
        await query.message.reply_text(
            "🎬 Quay lại bước duyệt video.",
            reply_markup=get_video_review_keyboard()
        )

    return BotState.REVIEW_VIDEO


async def handle_video_feedback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Handles text feedback for video refinement."""
    user = update.effective_user
    if not is_authorized(user.id):
        return BotState.IDLE

    feedback = update.message.text.strip()
    if "video_feedback" not in context.user_data:
        context.user_data["video_feedback"] = []
    context.user_data["video_feedback"].append(feedback)

    escaped_fb = html.escape(feedback)
    await update.message.reply_text(
        f"📝 Đã ghi nhận góp ý: \"<i>{escaped_fb}</i>\". Bắt đầu tạo bản video mới...",
        parse_mode="HTML"
    )
    return await trigger_video_generation(update, context)
