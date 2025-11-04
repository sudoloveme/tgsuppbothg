"""
Main bot file - initialization and handler registration.
"""
import logging
import threading
from typing import Dict, Tuple

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import (
    TELEGRAM_BOT_TOKEN,
    SUPPORT_CHAT_ID,
    OWNER_ID,
    ARCHIVE_AFTER_HOURS,
)
from helpers import set_config
from commands import (
    cmd_start,
    cmd_id,
    cmd_panel,
    cmd_linkmail,
    cmd_info,
    cmd_stats,
    cmd_diag,
    cmd_subscription,
)
from handlers import (
    handle_callback_buttons,
    handle_incoming_from_user,
    handle_owner_reply,
    archive_inactive_topics_job,
)
from utils import init_mappings
from config import MINIAPP_URL, MINIAPP_PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("support-bot")


# Maps message ids in the owner's chat to (original_chat_id, original_message_id)
# This is in-memory and will reset on restart.
owner_msg_id_to_origin: Dict[int, Tuple[int, int]] = {}

# Forum mode mappings
user_id_to_thread_id: Dict[int, int] = {}
support_msg_id_to_origin: Dict[int, Tuple[int, int]] = {}


def main() -> None:
    """Main function to start the bot."""
    # Log startup mode
    if SUPPORT_CHAT_ID is not None:
        logger.info("Starting in FORUM mode. SUPPORT_CHAT_ID=%s", str(SUPPORT_CHAT_ID))
    else:
        logger.info("Starting in OWNER DM mode. OWNER_ID=%s", str(OWNER_ID))
    
    # Initialize helpers with config
    set_config(SUPPORT_CHAT_ID, OWNER_ID)
    
    # Initialize global mappings
    init_mappings(support_msg_id_to_origin, owner_msg_id_to_origin, user_id_to_thread_id)

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("id", cmd_id))
    # Subscription mini-app command
    application.add_handler(CommandHandler("subscription", cmd_subscription))
    # Diagnostics command
    application.add_handler(CommandHandler("diag", cmd_diag))
    # Statistics command
    application.add_handler(CommandHandler("stats", cmd_stats))
    if SUPPORT_CHAT_ID is not None:
        application.add_handler(CommandHandler("panel", cmd_panel))
        application.add_handler(CommandHandler("linkmail", cmd_linkmail))
        application.add_handler(CommandHandler("info", cmd_info))

    # Reply handlers: restrict to specific chats to avoid intercepting all messages
    if SUPPORT_CHAT_ID is not None:
        application.add_handler(
            MessageHandler(
                filters.Chat(SUPPORT_CHAT_ID) & filters.REPLY,
                handle_owner_reply,
            )
        )
    if OWNER_ID is not None:
        application.add_handler(
            MessageHandler(
                filters.Chat(OWNER_ID) & filters.REPLY,
                handle_owner_reply,
            )
        )

    # Generic incoming messages from users
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_incoming_from_user,
        )
    )

    # Buttons handler
    application.add_handler(CallbackQueryHandler(handle_callback_buttons))

    # Auto-archive job (only if JobQueue available)
    if SUPPORT_CHAT_ID is not None and ARCHIVE_AFTER_HOURS > 0 and getattr(application, "job_queue", None) is not None:
        application.job_queue.run_repeating(archive_inactive_topics_job, interval=3600, first=60)
    elif SUPPORT_CHAT_ID is not None and ARCHIVE_AFTER_HOURS > 0:
        logger.warning("JobQueue not available. Install PTB with job-queue extras: pip install 'python-telegram-bot[job-queue]==21.5'")

    # Start mini-app server if URL is configured
    if MINIAPP_URL:
        def run_miniapp_server():
            import asyncio
            try:
                from miniapp_server import run_server
                logger.info(f"Starting mini-app server in thread on port {MINIAPP_PORT}...")
                # Create new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(run_server(host="0.0.0.0", port=MINIAPP_PORT))
                except Exception as e:
                    logger.exception(f"Error in mini-app server: {e}")
                finally:
                    loop.close()
            except Exception as e:
                logger.exception(f"Failed to start mini-app server: {e}")
        
        miniapp_thread = threading.Thread(target=run_miniapp_server, daemon=True, name="MiniAppServer")
        miniapp_thread.start()
        logger.info(f"Mini-app server thread started (thread name: {miniapp_thread.name})")
    else:
        logger.warning("MINIAPP_URL not configured. Mini-app functionality disabled.")

    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
