"""Saved model (người mẫu) library: save the current model, pick a saved one, delete."""
import html
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from core.config import PROJECT_ROOT
from core.logger import logger
from core.states import BotState
from handlers.menu_handler import get_cancel_keyboard, is_authorized
from services.model_library import get_model_library


def get_after_model_keyboard(session_id: str, show_save: bool = True) -> InlineKeyboardMarkup:
    """Keyboard under the 'đã nhận ảnh người mẫu' message: save button (optional) + cancel."""
    rows = []
    if show_save:
        rows.append([InlineKeyboardButton("💾 Lưu người mẫu này", callback_data=f"save_model:{session_id}")])
    rows.append([InlineKeyboardButton("❌ Hủy", callback_data="btn_cancel")])
    return InlineKeyboardMarkup(rows)


def _picker_keyboard(models: list) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(f"✅ {m['name']}", callback_data=f"pick_model:{m['id']}"),
            InlineKeyboardButton("🗑", callback_data=f"del_model:{m['id']}"),
        ]
        for m in models
    ]
    rows.append([InlineKeyboardButton("❌ Hủy", callback_data="btn_cancel")])
    return InlineKeyboardMarkup(rows)


def _without_save_button(markup: Optional[InlineKeyboardMarkup]) -> Optional[InlineKeyboardMarkup]:
    if not markup:
        return None
    rows = [
        [b for b in row if not (b.callback_data or "").startswith("save_model:")]
        for row in markup.inline_keyboard
    ]
    rows = [row for row in rows if row]
    return InlineKeyboardMarkup(rows) if rows else None


def _ensure_session_dir(context: ContextTypes.DEFAULT_TYPE) -> Path:
    session_dir: Optional[Path] = context.user_data.get("session_dir")
    if not session_dir:
        session_id = context.user_data.get("session_id") or datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = PROJECT_ROOT / "workspace" / "sessions" / session_id
        context.user_data["session_dir"] = session_dir
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


async def _send_previews(message, user_id: int, models: list) -> None:
    """Sends the saved model photos (album when 2+), reusing cached Telegram file_ids when possible."""
    lib = get_model_library()
    previews = models[:10]  # Telegram albums hold at most 10 items

    async def send(use_cache: bool):
        def media_of(m):
            return m.get("tg_file_id") if use_cache and m.get("tg_file_id") else lib.model_path(user_id, m).read_bytes()

        if len(previews) == 1:
            return [await message.reply_photo(photo=media_of(previews[0]), caption=previews[0]["name"])]
        return await message.reply_media_group(
            media=[InputMediaPhoto(media=media_of(m), caption=m["name"]) for m in previews]
        )

    try:
        try:
            sent = await send(use_cache=True)
        except Exception as e:
            logger.info(f"Cached preview file_id rejected, re-uploading previews: {e}")
            sent = await send(use_cache=False)
        for m, msg in zip(previews, sent):
            if msg.photo:
                lib.set_tg_file_id(user_id, m["id"], msg.photo[-1].file_id)
    except Exception as e:
        logger.warning(f"Could not send saved model previews: {e}")


async def handle_show_saved_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'📚 Chọn người mẫu đã lưu': shows previews + a picker keyboard. Stays in the current state."""
    query = update.callback_query
    user_id = update.effective_user.id
    models = get_model_library().list_models(user_id)
    if not models:
        await query.answer("Bạn chưa lưu người mẫu nào.", show_alert=True)
        return None

    await query.answer()
    await _send_previews(query.message, user_id, models)
    await query.message.reply_text(
        "📚 <b>Chọn người mẫu đã lưu</b> (theo tên trên ảnh), hoặc gửi ảnh người mẫu mới.\n"
        "<i>Bấm 🗑 để xóa người mẫu khỏi thư viện.</i>",
        parse_mode="HTML",
        reply_markup=_picker_keyboard(models),
    )
    return None


async def handle_pick_saved_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uses a saved model as Image 1 of the current session -> WAITING_CLOTH_IMG."""
    query = update.callback_query
    user_id = update.effective_user.id
    lib = get_model_library()
    entry = lib.get_model(user_id, query.data.split(":", 1)[1])
    if not entry:
        await query.answer("Người mẫu này không còn trong thư viện.", show_alert=True)
        return None

    await query.answer()
    dest = _ensure_session_dir(context) / "model_normalized.jpg"
    shutil.copy2(lib.model_path(user_id, entry), dest)
    context.user_data["model_image_path"] = dest
    context.user_data["saved_model_id"] = entry["id"]
    context.user_data.pop("cloth_image_path", None)

    await query.message.reply_text(
        f"✅ Đã chọn người mẫu <b>{html.escape(entry['name'])}</b>.\n\n"
        "Bây giờ hãy gửi <b>ảnh trang phục</b> (áo, váy, set đồ...).",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    return BotState.WAITING_CLOTH_IMG


async def handle_delete_saved_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """First tap on 🗑: asks for confirmation by swapping the picker keyboard."""
    query = update.callback_query
    entry = get_model_library().get_model(update.effective_user.id, query.data.split(":", 1)[1])
    if not entry:
        await query.answer("Người mẫu này không còn trong thư viện.", show_alert=True)
        return None
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🗑 Xác nhận xóa '{entry['name']}'", callback_data=f"del_model_ok:{entry['id']}")],
        [InlineKeyboardButton("↩️ Quay lại", callback_data="pick_model_back")],
    ]))
    return None


async def handle_confirm_delete_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    lib = get_model_library()
    deleted = lib.delete_model(user_id, query.data.split(":", 1)[1])
    await query.answer("🗑 Đã xóa người mẫu." if deleted else "Người mẫu này đã bị xóa trước đó.")
    models = lib.list_models(user_id)
    if models:
        await query.edit_message_reply_markup(reply_markup=_picker_keyboard(models))
    else:
        await query.edit_message_text(
            "📚 Thư viện người mẫu đã trống. Hãy gửi <b>ảnh người mẫu</b> mới.",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard(),
        )
    return None


async def handle_picker_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    models = get_model_library().list_models(update.effective_user.id)
    await query.edit_message_reply_markup(reply_markup=_picker_keyboard(models) if models else get_cancel_keyboard())
    return None


async def handle_save_current_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    '💾 Lưu người mẫu này'. Registered at group=-1 so it also works while a job is running;
    raises ApplicationHandlerStop so the conversation does not answer the same callback again.
    """
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        raise ApplicationHandlerStop

    session_id = query.data.split(":", 1)[1]
    model_path = context.user_data.get("model_image_path")
    if session_id != context.user_data.get("session_id") or not model_path or not Path(model_path).exists():
        await query.answer("Phiên này đã kết thúc, không lưu được người mẫu nữa.", show_alert=True)
        raise ApplicationHandlerStop
    if context.user_data.get("saved_model_id"):
        await query.answer("Người mẫu này đã được lưu rồi.")
        raise ApplicationHandlerStop

    try:
        entry = get_model_library().save_model(user_id, Path(model_path), name=context.user_data.get("model_caption"))
    except ValueError as e:
        await query.answer(str(e), show_alert=True)
        raise ApplicationHandlerStop

    context.user_data["saved_model_id"] = entry["id"]
    await query.answer(f"💾 Đã lưu người mẫu: {entry['name']}")
    try:
        await query.edit_message_reply_markup(reply_markup=_without_save_button(query.message.reply_markup))
    except Exception as e:
        logger.debug(f"Could not remove save button: {e}")
    raise ApplicationHandlerStop
