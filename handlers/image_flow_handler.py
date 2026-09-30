"""Image generation and review flow handlers."""
import asyncio
import html
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.logger import logger
from core.states import BotState, JobType, set_user_job_active
from handlers.menu_handler import cmd_cancel, get_cancel_keyboard, is_authorized
from services.gemini_web_image import GeminiWebImageService
from utils.telegram_io import edit_or_reply, send_review_photo

img_service = GeminiWebImageService()


def get_image_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Duyệt ảnh → Tạo video", callback_data="btn_approve_image")],
        [InlineKeyboardButton("✏️ Góp ý chỉnh ảnh", callback_data="btn_feedback_image")],
        [InlineKeyboardButton("❌ Hủy", callback_data="btn_cancel")]
    ])


def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Quay lại", callback_data="btn_back_to_review_img")]
    ])


def get_error_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Thử lại", callback_data="btn_retry_job")],
        [InlineKeyboardButton("❌ Hủy", callback_data="btn_cancel")]
    ])


async def trigger_image_generation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Initiates image try-on generation."""
    user = update.effective_user
    if not is_authorized(user.id):
        return BotState.IDLE

    context.user_data["user_id"] = user.id
    context.user_data["last_job"] = JobType.IMAGE_INITIAL
    set_user_job_active(user.id, True)
    model_img: Path = context.user_data.get("model_image_path")
    cloth_img: Path = context.user_data.get("cloth_image_path")
    session_dir: Path = context.user_data.get("session_dir")

    version = len(context.user_data.get("image_versions", [])) + 1
    cancel_event = asyncio.Event()
    context.user_data["cancel_event"] = cancel_event
    context.user_data["cancel_requested"] = False

    target = update.message or (update.callback_query.message if update.callback_query else None)
    status_msg = await target.reply_text(
        "⏳ Đang thay đồ trên Gemini… 00:00",
        reply_markup=get_cancel_keyboard()
    )

    async def update_progress(progress_text: str):
        try:
            if progress_text.startswith("⏳"):
                text = progress_text
            else:
                text = f"⏳ Đang thay đồ trên Gemini… {progress_text}"
            await status_msg.edit_text(text, reply_markup=get_cancel_keyboard())
        except Exception:
            pass

    try:
        img_path, error_msg, chat_url = await img_service.generate_tryon_image(
            model_image_path=model_img,
            cloth_image_path=cloth_img,
            session_dir=session_dir,
            version=version,
            progress_callback=update_progress,
            cancel_event=cancel_event
        )
        context.user_data["image_chat_url"] = chat_url

        if context.user_data.get("cancel_requested"):
            await edit_or_reply(status_msg, target, "🛑 Đã hủy quá trình tạo ảnh.")
            return BotState.IDLE

        if error_msg or not img_path or not img_path.exists():
            escaped_err = html.escape(error_msg or "Không tải được ảnh kết quả.")
            await edit_or_reply(
                status_msg, target,
                f"❌ <b>Lỗi khi tạo ảnh:</b>\n{escaped_err}\n\nBạn muốn thử lại hay hủy?",
                parse_mode="HTML",
                reply_markup=get_error_keyboard()
            )
            return BotState.ERROR

        context.user_data["image_versions"].append(img_path)
        context.user_data["approved_image_path"] = img_path

        try:
            await status_msg.delete()
        except Exception:
            pass

        # The image exists on disk from here on, so the session must reach REVIEW_IMAGE even if
        # delivering the preview to Telegram is slow (send_review_photo never raises on network errors).
        await send_review_photo(
            target, img_path,
            f"✨ <b>Ảnh thử đồ hoàn tất (Phiên bản #{version})</b>\n\nBạn có thể duyệt để chuyển sang làm video, hoặc góp ý để chỉnh sửa tiếp.",
            get_image_review_keyboard()
        )
        return BotState.REVIEW_IMAGE

    except Exception as e:
        logger.exception(f"Unhandled error in trigger_image_generation: {e}")
        await edit_or_reply(
            status_msg, target,
            f"❌ <b>Lỗi hệ thống:</b> {html.escape(str(e))}\n\nBạn muốn thử lại hay hủy?",
            parse_mode="HTML",
            reply_markup=get_error_keyboard()
        )
        return BotState.ERROR
    finally:
        set_user_job_active(user.id, False)


async def handle_image_review_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Handles buttons on the image review message (also registered as a fallback safety net)."""
    query = update.callback_query
    data = query.data
    if data in ("btn_approve_image", "btn_feedback_image") and not context.user_data.get("image_versions"):
        await query.answer("Phiên làm việc này đã kết thúc. Vui lòng bấm 'Tạo mới' để bắt đầu phiên mới.", show_alert=True)
        return None
    await query.answer()

    if data == "btn_approve_image":
        from handlers.video_flow_handler import trigger_video_generation
        return await trigger_video_generation(update, context)

    elif data == "btn_feedback_image":
        await query.message.reply_text(
            "✏️ <b>Nhập góp ý chỉnh sửa ảnh:</b>\n\n"
            "Ví dụ: <i>'cho váy ôm hơn'</i>, <i>'giữ màu áo tối hơn'</i>, <i>'chỉnh lại cổ áo'</i>\n"
            "Bot sẽ yêu cầu Gemini chỉnh sửa trực tiếp trên ảnh này.",
            parse_mode="HTML",
            reply_markup=get_back_keyboard()
        )
        return BotState.WAITING_IMG_FEEDBACK

    elif data == "btn_cancel":
        return await cmd_cancel(update, context)

    return BotState.REVIEW_IMAGE


async def handle_back_to_review_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Returns to REVIEW_IMAGE without canceling the session."""
    query = update.callback_query
    await query.answer()

    approved_img: Path = context.user_data.get("approved_image_path")
    version = len(context.user_data.get("image_versions", []))
    if approved_img and approved_img.exists():
        await send_review_photo(
            query.message, approved_img,
            f"✨ <b>Ảnh thử đồ hiện tại (Phiên bản #{version})</b>",
            get_image_review_keyboard()
        )
    else:
        await query.message.reply_text(
            "✨ Quay lại bước duyệt ảnh.",
            reply_markup=get_image_review_keyboard()
        )

    return BotState.REVIEW_IMAGE


async def execute_image_refinement(
    target_message,
    context: ContextTypes.DEFAULT_TYPE,
    feedback: str,
    user_id: int = 0
) -> BotState:
    """Executes refinement of try-on image, callable from both text handler and retry button."""
    eff_user_id = user_id or context.user_data.get("user_id") or (getattr(target_message.chat, 'id', 0) if hasattr(target_message, 'chat') else 0)
    context.user_data["last_job"] = JobType.IMAGE_REFINE
    context.user_data["last_image_feedback"] = feedback
    if eff_user_id:
        set_user_job_active(eff_user_id, True)

    session_dir: Path = context.user_data.get("session_dir")
    chat_url: str = context.user_data.get("image_chat_url", "")
    version = len(context.user_data.get("image_versions", [])) + 1
    cancel_event = asyncio.Event()
    context.user_data["cancel_event"] = cancel_event
    context.user_data["cancel_requested"] = False

    escaped_fb = html.escape(feedback)
    status_msg = await target_message.reply_text(
        f"⏳ Đang chỉnh sửa ảnh theo góp ý: '<i>{escaped_fb}</i>'… 00:00",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard()
    )

    async def update_progress(progress_text: str):
        try:
            if progress_text.startswith("⏳"):
                text = progress_text
            else:
                text = f"⏳ Đang chỉnh sửa ảnh theo góp ý… {progress_text}"
            await status_msg.edit_text(text, reply_markup=get_cancel_keyboard())
        except Exception:
            pass

    try:
        refined_path, error_msg, new_chat_url = await img_service.refine_tryon_image(
            feedback=feedback,
            chat_url=chat_url,
            session_dir=session_dir,
            version=version,
            progress_callback=update_progress,
            cancel_event=cancel_event
        )

        if new_chat_url:
            context.user_data["image_chat_url"] = new_chat_url

        if context.user_data.get("cancel_requested"):
            await edit_or_reply(status_msg, target_message, "🛑 Đã hủy chỉnh sửa ảnh.")
            return BotState.IDLE

        if error_msg or not refined_path or not refined_path.exists():
            escaped_err = html.escape(error_msg or "Không tạo được ảnh mới.")
            await edit_or_reply(
                status_msg, target_message,
                f"❌ <b>Lỗi khi sửa ảnh:</b>\n{escaped_err}\n\nBạn muốn thử lại hay hủy?",
                parse_mode="HTML",
                reply_markup=get_error_keyboard()
            )
            return BotState.ERROR

        context.user_data["image_versions"].append(refined_path)
        context.user_data["approved_image_path"] = refined_path

        try:
            await status_msg.delete()
        except Exception:
            pass

        await send_review_photo(
            target_message, refined_path,
            f"✨ <b>Ảnh sau khi chỉnh sửa (Phiên bản #{version})</b>\n\nBạn có thể duyệt để tạo video, hoặc tiếp tục góp ý.",
            get_image_review_keyboard()
        )
        return BotState.REVIEW_IMAGE

    except Exception as e:
        logger.exception(f"Unhandled error in execute_image_refinement: {e}")
        await edit_or_reply(
            status_msg, target_message,
            f"❌ <b>Lỗi hệ thống khi sửa ảnh:</b> {html.escape(str(e))}\n\nBạn muốn thử lại hay hủy?",
            parse_mode="HTML",
            reply_markup=get_error_keyboard()
        )
        return BotState.ERROR
    finally:
        if eff_user_id:
            set_user_job_active(eff_user_id, False)


async def handle_image_feedback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Handles text feedback for image refinement."""
    user = update.effective_user
    if not is_authorized(user.id):
        return BotState.IDLE

    feedback = update.message.text.strip()
    return await execute_image_refinement(update.message, context, feedback, user_id=user.id)
