"""Handler for receiving, downloading, and normalizing photos and documents."""
import asyncio
import html
from pathlib import Path
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes

from core.config import PROJECT_ROOT
from core.logger import logger
from core.states import BotState, is_user_job_active
from handlers.menu_handler import busy_guard, get_cancel_keyboard, is_authorized
from handlers.model_library_handler import get_after_model_keyboard
from utils.media_helper import normalize_image

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB Telegram limit


async def _download_telegram_media(update: Update, context: ContextTypes.DEFAULT_TYPE, dest_path: Path) -> Optional[Path]:
    """Downloads photo or document from Telegram message."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    file_obj = None

    if update.message.photo:
        file_obj = await update.message.photo[-1].get_file()
    elif update.message.document:
        doc = update.message.document
        if doc.file_size and doc.file_size > MAX_FILE_SIZE_BYTES:
            await update.message.reply_text("⚠️ Tệp vượt quá giới hạn 20MB của Telegram Bot API. Vui lòng gửi file ảnh nhỏ hơn 20MB.")
            return None

        mime = doc.mime_type or ""
        fname = (doc.file_name or "").lower()
        if mime.startswith("image/") or fname.endswith((".png", ".jpg", ".jpeg", ".heic", ".webp")):
            file_obj = await doc.get_file()
        else:
            await update.message.reply_text("⚠️ Định dạng tệp không được hỗ trợ. Vui lòng gửi file ảnh (PNG, JPG, HEIC, WEBP).")
            return None

    if not file_obj:
        return None

    raw_path = dest_path.with_name(f"raw_{dest_path.name}")
    await file_obj.download_to_drive(custom_path=raw_path)

    # Normalize image to clean JPEG
    try:
        normalize_image(raw_path, dest_path)
        raw_path.unlink(missing_ok=True)
        return dest_path
    except Exception as e:
        logger.error(f"Failed to normalize image: {e}")
        return raw_path


async def handle_waiting_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles media messages arriving in WAITING state (while a background job or handler is in flight).
    - If part of an active album being debounced, buffers the message for the primary task.
    - If single photo/document, prompts user to wait rather than launching a nested job.
    NEVER launches a job from WAITING state because PTB ignores state transitions in WAITING.
    """
    user = update.effective_user
    if not is_authorized(user.id):
        return

    media_group_id = update.message.media_group_id if update.message else None
    if media_group_id:
        media_groups = context.user_data.get("media_groups", {})
        if media_group_id in media_groups:
            media_groups[media_group_id]["messages"].append(update.message)
            return

    # Single photo or unexpected media while busy
    if is_user_job_active(user.id):
        text = "⏳ <b>Bot đang thực hiện tác vụ tự động hóa...</b> Vui lòng chờ hoàn tất hoặc bấm nút <b>❌ Hủy</b> bên dưới."
    else:
        text = (
            "⏳ <b>Bot đang xử lý ảnh trước đó.</b>\n\n"
            "Vui lòng đợi bot xác nhận ảnh người mẫu rồi hãy gửi tiếp ảnh trang phục nhé!"
        )

    keyboard = get_cancel_keyboard()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def handle_image_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Handles image messages in WAITING_MODEL_IMG or WAITING_CLOTH_IMG states."""
    user = update.effective_user
    if not is_authorized(user.id):
        return BotState.IDLE

    # If an individual image arrives while a heavy job is already in progress, warn busy
    media_group_id = update.message.media_group_id
    if not media_group_id and is_user_job_active(user.id):
        await busy_guard(update, context)
        return BotState.WAITING_MODEL_IMG

    session_id = context.user_data.get("session_id")
    session_dir: Path = context.user_data.get("session_dir")
    if not session_dir or not session_dir.exists():
        session_dir = PROJECT_ROOT / "workspace" / "sessions" / (session_id or "default")
        session_dir.mkdir(parents=True, exist_ok=True)
        context.user_data["session_dir"] = session_dir

    media_group_id = update.message.media_group_id

    # Handle Media Group (Album) with safe debounce
    if media_group_id:
        if "media_groups" not in context.user_data:
            context.user_data["media_groups"] = {}

        group_info = context.user_data["media_groups"].setdefault(
            media_group_id, {"messages": [], "processing": False}
        )
        group_info["messages"].append(update.message)

        # Allow album messages to arrive
        await asyncio.sleep(1.0)

        # If another task already claimed processing, exit cleanly
        if group_info["processing"]:
            return BotState.WAITING_MODEL_IMG

        if len(group_info["messages"]) >= 2:
            group_info["processing"] = True
            messages = sorted(group_info["messages"], key=lambda m: m.message_id)

            msg_model = messages[0]
            msg_cloth = messages[1]

            model_path = session_dir / "model_normalized.jpg"
            cloth_path = session_dir / "cloth_normalized.jpg"

            # Download model
            target_file1 = await (msg_model.photo[-1] if msg_model.photo else msg_model.document).get_file()
            raw1 = session_dir / "raw_model.jpg"
            await target_file1.download_to_drive(custom_path=raw1)
            normalize_image(raw1, model_path)
            raw1.unlink(missing_ok=True)

            # Download cloth
            target_file2 = await (msg_cloth.photo[-1] if msg_cloth.photo else msg_cloth.document).get_file()
            raw2 = session_dir / "raw_cloth.jpg"
            await target_file2.download_to_drive(custom_path=raw2)
            normalize_image(raw2, cloth_path)
            raw2.unlink(missing_ok=True)

            context.user_data["model_image_path"] = model_path
            context.user_data["cloth_image_path"] = cloth_path
            context.user_data["model_caption"] = msg_model.caption
            context.user_data.pop("saved_model_id", None)
            context.user_data["media_groups"].pop(media_group_id, None)

            await update.message.reply_text(
                "✅ <b>Đã nhận đủ 2 ảnh từ Album! Bắt đầu xử lý...</b>\n"
                "<i>Bấm 💾 nếu muốn lưu người mẫu này để lần sau chọn lại.</i>",
                parse_mode="HTML",
                reply_markup=get_after_model_keyboard(context.user_data.get("session_id", "")),
            )
            from handlers.image_flow_handler import trigger_image_generation
            return await trigger_image_generation(update, context)

    # Single Image upload logic
    if "model_image_path" not in context.user_data:
        target_path = session_dir / "model_normalized.jpg"
        saved = await _download_telegram_media(update, context, target_path)
        if not saved:
            return BotState.WAITING_MODEL_IMG

        context.user_data["model_image_path"] = saved
        context.user_data["model_caption"] = update.message.caption
        context.user_data.pop("saved_model_id", None)
        await update.message.reply_text(
            "✅ <b>Đã nhận ảnh người mẫu.</b>\n\nBây giờ hãy gửi tiếp <b>ảnh trang phục</b> (áo, váy, set đồ...).\n"
            "<i>Bấm 💾 để lưu người mẫu này, lần sau chỉ cần chọn lại (chú thích của ảnh sẽ được dùng làm tên).</i>",
            parse_mode="HTML",
            reply_markup=get_after_model_keyboard(context.user_data.get("session_id", ""))
        )
        return BotState.WAITING_CLOTH_IMG

    else:
        target_path = session_dir / "cloth_normalized.jpg"
        saved = await _download_telegram_media(update, context, target_path)
        if not saved:
            return BotState.WAITING_CLOTH_IMG

        context.user_data["cloth_image_path"] = saved
        await update.message.reply_text("✅ <b>Đã nhận đủ 2 ảnh! Bắt đầu xử lý thử đồ...</b>", parse_mode="HTML")

        from handlers.image_flow_handler import trigger_image_generation
        return await trigger_image_generation(update, context)
