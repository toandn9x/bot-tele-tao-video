"""Menu handlers: /start, /new, /cancel, /status and common navigation."""
import asyncio
import html
from datetime import datetime
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from core.config import get_config_manager, PROJECT_ROOT
from core.logger import logger
from core.states import BotState, set_user_job_active
from services.browser import get_browser_service
from services.model_library import get_model_library


def is_authorized(user_id: int) -> bool:
    """Verifies if the sender is authorized. If ALLOWED_USER_ID is None, access is public for everyone."""
    cm = get_config_manager()
    allowed = cm.env.ALLOWED_USER_ID
    if allowed is None:
        return True
    return user_id == allowed


def get_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Tạo mới", callback_data="btn_new_job")],
        [
            InlineKeyboardButton("📊 Trạng thái", callback_data="btn_status"),
            InlineKeyboardButton("📖 Hướng dẫn", callback_data="btn_help")
        ]
    ])


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Hủy", callback_data="btn_cancel")]
    ])


def get_model_step_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Step 1 keyboard: '📚 Chọn người mẫu đã lưu (N)' when the user has saved models, plus cancel."""
    rows = []
    saved = len(get_model_library().list_models(user_id))
    if saved:
        rows.append([InlineKeyboardButton(f"📚 Chọn người mẫu đã lưu ({saved})", callback_data="pick_model_menu")])
    rows.append([InlineKeyboardButton("❌ Hủy", callback_data="btn_cancel")])
    return InlineKeyboardMarkup(rows)


async def handle_stale_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Answers unhandled or expired inline button clicks so the spinner doesn't run forever."""
    if update.callback_query:
        await update.callback_query.answer("Phiên làm việc này đã kết thúc. Vui lòng bấm 'Tạo mới' để bắt đầu phiên mới.", show_alert=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Handles /start command."""
    user = update.effective_user
    if not is_authorized(user.id):
        logger.warning(f"Unauthorized access attempt by user {user.id} ({user.username})")
        return BotState.IDLE

    name = html.escape(user.first_name or "bạn")
    text = (
        f"👋 Chào <b>{name}</b>!\n\n"
        "Chào mừng bạn đến với <b>AI Virtual Try-On & TikTok Affiliate Tool</b>.\n\n"
        "<b>Các tính năng chính:</b>\n"
        "1. Thử đồ từ ảnh người mẫu + ảnh trang phục (9:16)\n"
        "2. Góp ý chỉnh sửa chi tiết ảnh thử đồ đến khi ưng ý\n"
        "3. Tạo video chuyển động thời trang (9:16) bằng Gemini\n"
        "4. Đóng gói video gốc + caption sao chép 1 chạm + checklist TikTok Shop\n\n"
        "<b>Các lệnh nhanh:</b>\n"
        "• /new - Bắt đầu một phiên thử đồ mới\n"
        "• /status - Xem trạng thái Chrome & Gemini Pro\n"
        "• /help - Xem hướng dẫn chi tiết & mẹo sử dụng\n"
        "• /cancel - Hủy bỏ tác vụ đang xử lý\n\n"
        "Bấm <b>📸 Tạo mới</b> để bắt đầu ngay!"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=get_start_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=get_start_keyboard())

    return BotState.IDLE


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Handles /new or 'Tạo mới' button to start a session."""
    user = update.effective_user
    if not is_authorized(user.id):
        return BotState.IDLE

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = PROJECT_ROOT / "workspace" / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    context.user_data.clear()
    set_user_job_active(user.id, False)
    context.user_data["user_id"] = user.id
    context.user_data["session_id"] = session_id
    context.user_data["session_dir"] = session_dir
    context.user_data["image_versions"] = []
    context.user_data["video_versions"] = []
    context.user_data["video_feedback"] = []
    context.user_data["cancel_requested"] = False

    text = (
        "📸 <b>BƯỚC 1: TẢI ẢNH ĐẦU VÀO</b>\n\n"
        "Hãy gửi <b>ảnh người mẫu</b> (toàn thân, tỉ lệ 9:16).\n"
        "💡 <i>Khuyên dùng: Gửi dưới dạng FILE/DOCUMENT để giữ nguyên độ nét gốc.</i>\n\n"
        "<i>Hoặc gửi cùng lúc 1 Album gồm 2 ảnh (Ảnh 1: Người mẫu, Ảnh 2: Trang phục).</i>"
    )
    keyboard = get_model_step_keyboard(user.id)
    if len(keyboard.inline_keyboard) > 1:
        text += "\n\n📚 <i>Hoặc bấm nút bên dưới để chọn người mẫu đã lưu.</i>"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

    return BotState.WAITING_MODEL_IMG


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Cancels current operation and returns to IDLE."""
    user = update.effective_user
    if not is_authorized(user.id):
        return BotState.IDLE

    # Do not clear set_user_job_active here; let the running job's finally block clear it when actually halted.
    context.user_data["cancel_requested"] = True
    cancel_event = context.user_data.get("cancel_event")
    if cancel_event and isinstance(cancel_event, asyncio.Event):
        cancel_event.set()

    text = "🛑 Đã hủy phiên làm việc. Bạn có thể bắt đầu phiên mới bất kỳ lúc nào."

    if update.callback_query:
        await update.callback_query.answer("Đã gửi yêu cầu hủy")
        try:
            await update.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=get_start_keyboard())
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=get_start_keyboard())

    return BotState.IDLE


async def cmd_busy_notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Notifies user when /start or /new is sent while a job is active in WAITING state."""
    text = (
        "⏳ <b>Bot đang thực hiện tác vụ tự động hóa...</b>\n\n"
        "Nếu muốn dừng lại để bắt đầu phiên mới, vui lòng gửi /cancel hoặc bấm nút <b>❌ Hủy</b> bên dưới."
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=get_cancel_keyboard())
    elif update.callback_query:
        await update.callback_query.answer("Đang bận xử lý...")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Checks browser and login status."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    if update.callback_query:
        await update.callback_query.answer()
        msg = await update.callback_query.message.reply_text("🔍 Đang kiểm tra trạng thái Chrome và Gemini Web...")
    elif update.message:
        msg = await update.message.reply_text("🔍 Đang kiểm tra trạng thái Chrome và Gemini Web...")
    else:
        return

    browser_svc = get_browser_service()
    cm = get_config_manager()

    try:
        page = await browser_svc.get_page()
        is_logged_in = await browser_svc.check_login_status(page)
        session_id = context.user_data.get("session_id", "Không có phiên hoạt động")

        status_text = (
            "📊 <b>BÁO CÁO TRẠNG THÁI HỆ THỐNG</b>\n\n"
            f"• Chrome CDP: <code>127.0.0.1:{cm.app_config.gemini_web.cdp_port}</code>\n"
            f"• Trạng thái kết nối Chrome: ✅ Hoạt động\n"
            f"• Đăng nhập Google: {'✅ Đã đăng nhập' if is_logged_in else '❌ Chưa đăng nhập / Hết hạn'}\n"
            f"• URL hiện tại: <code>{html.escape(page.url)}</code>\n"
            f"• Phiên làm việc: <code>{html.escape(session_id)}</code>\n"
        )
    except Exception as e:
        status_text = (
            "📊 <b>BÁO CÁO TRẠNG THÁI HỆ THỐNG</b>\n\n"
            "• Trạng thái Chrome: ❌ Mất kết nối\n"
            f"• Chi tiết lỗi: <code>{html.escape(str(e))}</code>\n\n"
            "💡 <i>Chạy <code>python scripts/open_chrome_profile.py</code> để khởi động và đăng nhập.</i>"
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Tạo mới", callback_data="btn_new_job")],
        [
            InlineKeyboardButton("📖 Hướng dẫn", callback_data="btn_help"),
            InlineKeyboardButton("🏠 Trang chủ", callback_data="btn_home")
        ]
    ])

    await msg.edit_text(status_text, parse_mode="HTML", reply_markup=keyboard)
    raise ApplicationHandlerStop


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays detailed instructions, available commands, and tips."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    help_text = (
        "📖 <b>HƯỚNG DẪN SỬ DỤNG BOT THỬ ĐỒ & VIDEO TIKTOK</b>\n\n"
        "<b>1. Danh sách lệnh:</b>\n"
        "• /new - Bắt đầu một phiên thử đồ mới\n"
        "• /status - Kiểm tra trạng thái kết nối Chrome & Gemini\n"
        "• /help - Xem hướng dẫn này và mẹo tạo ảnh/video\n"
        "• /cancel - Hủy bỏ tác vụ đang xử lý để về trạng thái chờ\n"
        "• /start - Khởi động lại bot và về trang chủ\n\n"
        "<b>2. Quy trình 4 bước tạo gói nội dung:</b>\n"
        "1️⃣ <b>Gửi ảnh:</b> Gửi ảnh người mẫu (toàn thân, 9:16), sau đó gửi ảnh trang phục (hoặc gửi 1 Album gồm 2 ảnh).\n"
        "2️⃣ <b>Thử đồ (Try-On):</b> Bot tự động đưa ảnh lên Gemini Pro và trả về ảnh thử đồ độ nét cao (9:16).\n"
        "3️⃣ <b>Duyệt hoặc Góp ý:</b>\n"
        "   - Nếu chưa ưng: Bấm <i>Góp ý chỉnh ảnh</i> (ví dụ: <i>'cho váy dài qua gối', 'đổi màu áo sang xanh'</i>).\n"
        "   - Nếu đã ưng: Bấm <i>Duyệt ảnh, tạo video</i> để sinh video chuyển động thời trang (9:16).\n"
        "4️⃣ <b>Gói đăng TikTok:</b> Nhập tên hoặc link sản phẩm (hoặc bấm <i>Bỏ qua</i>). Bot sẽ gửi lại video gốc không nén + Caption sao chép 1 chạm + Checklist gắn link giỏ hàng TikTok Shop.\n\n"
        "<b>💡 Mẹo để có kết quả đẹp nhất:</b>\n"
        "• <i>Nên gửi ảnh dạng File/Document</i> để Telegram không nén giảm độ phân giải.\n"
        "• <i>Ảnh người mẫu:</i> Đứng thẳng, nhìn rõ dáng người, ánh sáng đủ, phông nền đơn giản.\n"
        "• <i>Lưu người mẫu:</i> Sau khi gửi ảnh người mẫu, bấm <b>💾 Lưu người mẫu này</b> (chú thích ảnh = tên). "
        "Lần sau bấm <b>📚 Chọn người mẫu đã lưu</b> ở bước 1, không cần gửi lại ảnh.\n"
        "• <i>Ảnh trang phục:</i> Chụp trải phẳng hoặc trên ma-nơ-canh, rõ chi tiết cổ áo, vạt áo và hoa văn.\n"
        "• <i>Góp ý chỉnh sửa:</i> Miêu tả ngắn gọn, tập trung vào điểm muốn đổi."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Bắt đầu tạo mới", callback_data="btn_new_job")],
        [
            InlineKeyboardButton("📊 Kiểm tra trạng thái", callback_data="btn_status"),
            InlineKeyboardButton("🏠 Về trang chủ", callback_data="btn_home")
        ]
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(help_text, parse_mode="HTML", reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(help_text, parse_mode="HTML", reply_markup=keyboard)

    raise ApplicationHandlerStop


async def busy_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guards processing / waiting state against concurrent inputs."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    text = "⏳ Bot đang thực hiện tác vụ tự động hóa... Vui lòng chờ hoặc bấm nút <b>❌ Hủy</b> bên dưới."
    keyboard = get_cancel_keyboard()
    if update.callback_query:
        await update.callback_query.answer("Đang bận xử lý...")
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
