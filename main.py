"""Main entry point for the Virtual Try-On & TikTok Affiliate Telegram Bot."""
import asyncio
import html
import warnings
from pathlib import Path
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    PicklePersistence,
    filters,
    ContextTypes,
)
from telegram.warnings import PTBUserWarning

# Filter expected PTB reminder warnings
warnings.filterwarnings("ignore", category=PTBUserWarning)

from core.config import get_config_manager, PROJECT_ROOT
from core.logger import logger, setup_logger
from core.states import BotState
from handlers.image_flow_handler import (
    handle_back_to_review_image,
    handle_image_feedback_text,
    handle_image_review_action,
)
from handlers.menu_handler import (
    busy_guard,
    cmd_busy_notice,
    cmd_cancel,
    cmd_help,
    cmd_new,
    cmd_start,
    cmd_status,
    get_start_keyboard,
    handle_stale_callback,
)
from handlers.model_library_handler import (
    handle_confirm_delete_model,
    handle_delete_saved_model,
    handle_pick_saved_model,
    handle_picker_back,
    handle_save_current_model,
    handle_show_saved_models,
)
from handlers.publish_handler import (
    handle_product_input,
    handle_retry_job,
    handle_skip_product,
)
from handlers.upload_handler import handle_image_upload, handle_waiting_media
from handlers.video_flow_handler import (
    handle_back_to_review_video,
    handle_video_feedback_text,
    handle_video_review_action,
)
from services.browser import get_browser_service
from utils.cleanup import cleanup_old_debug_files, cleanup_old_sessions


async def post_init(app: Application) -> None:
    """Runs after the application is initialized."""
    cm = get_config_manager()
    if cm.env.ALLOWED_USER_ID:
        logger.info(f"Bot initialized in PRIVATE mode for user: {cm.env.ALLOWED_USER_ID}")
    else:
        logger.info("Bot initialized in PUBLIC mode (open to all users).")

    # Register command menu in Telegram UI
    try:
        commands = [
            BotCommand("start", "Khởi động bot & về trang chủ"),
            BotCommand("new", "Tạo ảnh thử đồ mới"),
            BotCommand("status", "Kiểm tra kết nối Chrome & Gemini"),
            BotCommand("help", "Xem hướng dẫn sử dụng & mẹo"),
            BotCommand("cancel", "Hủy tác vụ đang chạy"),
        ]
        await app.bot.set_my_commands(commands)
        logger.info("Telegram command menu (set_my_commands) registered successfully.")
    except Exception as e:
        logger.warning(f"Could not register command menu: {e}")

    # Run cleanup of expired sessions and debug files
    keep_days = cm.app_config.app.keep_session_files_days
    cleanup_old_sessions(PROJECT_ROOT / "workspace" / "sessions", keep_days=keep_days)
    cleanup_old_debug_files(PROJECT_ROOT / "workspace" / "debug", keep_days=7)

    # Optional warm up of browser
    try:
        browser_svc = get_browser_service()
        await browser_svc.connect()
        logger.info("Chrome CDP browser connection warmed up successfully.")
    except Exception as e:
        logger.warning(f"Browser CDP not active yet: {e}. Bot will launch Chrome on first job.")


async def post_shutdown(app: Application) -> None:
    """Runs when the bot application is shutting down."""
    logger.info("Shutting down bot. Releasing browser connection...")
    browser_svc = get_browser_service()
    await browser_svc.close_connections()
    logger.info("Bot shutdown complete.")


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catches and logs unhandled exceptions across handlers."""
    logger.error(f"Unhandled error in Telegram update: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            err_msg = html.escape(str(context.error))
            await update.effective_message.reply_text(
                f"⚠️ <b>Lỗi hệ thống:</b>\n<code>{err_msg}</code>\n\nBạn có thể gửi /start để bắt đầu lại.",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def handle_conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> BotState:
    """Politely informs the user that their session has timed out due to inactivity without failing on old query."""
    cm = get_config_manager()
    timeout_mins = cm.app_config.app.conversation_timeout_minutes
    context.user_data.clear()
    msg_text = (
        f"⏱️ <b>Phiên làm việc đã hết hạn do không có tương tác trong {timeout_mins} phút.</b>\n\n"
        "Bấm <b>📸 Tạo mới</b> hoặc gõ /start để bắt đầu lại nhé!"
    )
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg_text,
                parse_mode="HTML",
                reply_markup=get_start_keyboard()
            )
        except Exception as e:
            logger.warning(f"Could not send timeout message to chat {chat_id}: {e}")
    return BotState.IDLE


def build_application() -> Application:
    """Builds and configures Telegram application with non-blocking ConversationHandler."""
    cm = get_config_manager()
    setup_logger(debug=cm.app_config.app.debug)

    # Ensure required directories exist
    (PROJECT_ROOT / "workspace" / "sessions").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "workspace" / "output").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "workspace" / "debug").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "workspace" / "logs").mkdir(parents=True, exist_ok=True)

    # Persistence to keep session states across bot restarts
    persistence = PicklePersistence(filepath=str(PROJECT_ROOT / "workspace" / "bot_state.pickle"))

    app = (
        Application.builder()
        .token(cm.env.TELEGRAM_BOT_TOKEN)
        # PTB defaults (5s) are too tight for a home connection; media sends use longer per-call timeouts
        .read_timeout(20)
        .write_timeout(30)
        .connect_timeout(15)
        .pool_timeout(15)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    user_filter = (
        filters.User(user_id=cm.env.ALLOWED_USER_ID)
        if cm.env.ALLOWED_USER_ID
        else filters.ALL
    )
    conv_timeout = cm.app_config.app.conversation_timeout_minutes * 60

    # Register /status and /help at group=-1 so they work even when conversation is in WAITING or busy
    app.add_handler(CommandHandler("status", cmd_status, filters=user_filter), group=-1)
    app.add_handler(CommandHandler("help", cmd_help, filters=user_filter), group=-1)
    app.add_handler(CallbackQueryHandler(cmd_status, pattern="^btn_status$"), group=-1)
    app.add_handler(CallbackQueryHandler(cmd_help, pattern="^btn_help$"), group=-1)
    # '💾 Lưu người mẫu này' must also work while a job is running (conversation in WAITING)
    app.add_handler(CallbackQueryHandler(handle_save_current_model, pattern="^save_model:"), group=-1)

    # Saved model library: available while waiting for the model or the garment photo
    model_library_handlers = [
        CallbackQueryHandler(handle_show_saved_models, pattern="^pick_model_menu$"),
        CallbackQueryHandler(handle_pick_saved_model, pattern="^pick_model:"),
        CallbackQueryHandler(handle_delete_saved_model, pattern="^del_model:"),
        CallbackQueryHandler(handle_confirm_delete_model, pattern="^del_model_ok:"),
        CallbackQueryHandler(handle_picker_back, pattern="^pick_model_back$"),
    ]

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start, filters=user_filter),
            CommandHandler("new", cmd_new, filters=user_filter),
            CallbackQueryHandler(cmd_new, pattern="^btn_new_job$"),
            CallbackQueryHandler(cmd_start, pattern="^btn_home$"),
        ],
        states={
            BotState.IDLE: [
                CommandHandler("new", cmd_new, filters=user_filter),
                CommandHandler("help", cmd_help, filters=user_filter),
                CommandHandler("status", cmd_status, filters=user_filter),
                CallbackQueryHandler(cmd_new, pattern="^btn_new_job$"),
                CallbackQueryHandler(cmd_start, pattern="^btn_home$"),
                CallbackQueryHandler(cmd_help, pattern="^btn_help$"),
                CallbackQueryHandler(cmd_status, pattern="^btn_status$"),
            ],
            BotState.WAITING_MODEL_IMG: [
                MessageHandler(
                    (filters.PHOTO | (filters.Document.ALL & ~filters.COMMAND)) & user_filter,
                    handle_image_upload,
                    block=False
                ),
                CallbackQueryHandler(cmd_cancel, pattern="^btn_cancel$"),
                *model_library_handlers,
            ],
            BotState.WAITING_CLOTH_IMG: [
                MessageHandler(
                    (filters.PHOTO | (filters.Document.ALL & ~filters.COMMAND)) & user_filter,
                    handle_image_upload,
                    block=False
                ),
                CallbackQueryHandler(cmd_cancel, pattern="^btn_cancel$"),
                *model_library_handlers,
            ],
            # While async jobs run (block=False), PTB places conversation into WAITING
            ConversationHandler.WAITING: [
                # Buffer album pieces or reject single photos with friendly prompt; NEVER starts a job
                MessageHandler(
                    (filters.PHOTO | (filters.Document.ALL & ~filters.COMMAND)) & user_filter,
                    handle_waiting_media,
                    block=False
                ),
                CallbackQueryHandler(cmd_cancel, pattern="^btn_cancel$"),
                CommandHandler("cancel", cmd_cancel, filters=user_filter),
                CommandHandler(["start", "new"], cmd_busy_notice, filters=user_filter),
                CallbackQueryHandler(busy_guard),
                MessageHandler((filters.ALL & ~filters.COMMAND) & user_filter, busy_guard, block=False),
            ],
            BotState.REVIEW_IMAGE: [
                CallbackQueryHandler(handle_image_review_action, pattern="^(btn_approve_image|btn_feedback_image|btn_cancel)$", block=False),
            ],
            BotState.WAITING_IMG_FEEDBACK: [
                CallbackQueryHandler(cmd_cancel, pattern="^btn_cancel$"),
                CallbackQueryHandler(handle_back_to_review_image, pattern="^btn_back_to_review_img$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, handle_image_feedback_text, block=False),
            ],
            BotState.REVIEW_VIDEO: [
                CallbackQueryHandler(handle_video_review_action, pattern="^(btn_approve_video|btn_feedback_video|btn_cancel)$", block=False),
            ],
            BotState.WAITING_VID_FEEDBACK: [
                CallbackQueryHandler(cmd_cancel, pattern="^btn_cancel$"),
                CallbackQueryHandler(handle_back_to_review_video, pattern="^btn_back_to_review_vid$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, handle_video_feedback_text, block=False),
            ],
            BotState.WAITING_PRODUCT: [
                CallbackQueryHandler(handle_skip_product, pattern="^btn_skip_product$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, handle_product_input, block=True),
            ],
            BotState.ERROR: [
                CallbackQueryHandler(handle_retry_job, pattern="^btn_retry_job$", block=False),
                CallbackQueryHandler(cmd_cancel, pattern="^btn_cancel$"),
            ],
            ConversationHandler.TIMEOUT: [
                CallbackQueryHandler(handle_conversation_timeout),
                MessageHandler(filters.ALL & user_filter, handle_conversation_timeout)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel, filters=user_filter),
            CommandHandler("start", cmd_start, filters=user_filter),
            CommandHandler("new", cmd_new, filters=user_filter),
            CommandHandler("help", cmd_help, filters=user_filter),
            CommandHandler("status", cmd_status, filters=user_filter),
            CallbackQueryHandler(cmd_cancel, pattern="^btn_cancel$"),
            CallbackQueryHandler(cmd_new, pattern="^btn_new_job$"),
            CallbackQueryHandler(cmd_start, pattern="^btn_home$"),
            CallbackQueryHandler(cmd_help, pattern="^btn_help$"),
            CallbackQueryHandler(cmd_status, pattern="^btn_status$"),
            # Safety net: review buttons keep working if the state got out of sync (e.g. a network error
            # after the result was delivered). The handlers refuse when the session has no image/video.
            CallbackQueryHandler(handle_image_review_action, pattern="^(btn_approve_image|btn_feedback_image)$", block=False),
            CallbackQueryHandler(handle_video_review_action, pattern="^(btn_approve_video|btn_feedback_video)$", block=False),
        ],
        conversation_timeout=conv_timeout,
        persistent=True,
        per_message=False,
        per_chat=True,
        per_user=True,
        name="virtual_tryon_conversation",
    )

    app.add_handler(conv_handler)
    # Catch-all in group 0 (runs ONLY if conv_handler did not match the callback query)
    app.add_handler(CallbackQueryHandler(handle_stale_callback))
    app.add_error_handler(global_error_handler)

    return app


def main():
    try:
        app = build_application()
        logger.info("Starting Telegram bot (long polling)...")
        app.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.critical(f"Fatal error starting bot: {e}", exc_info=True)


if __name__ == "__main__":
    main()
