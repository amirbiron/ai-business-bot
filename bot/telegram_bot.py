"""
Telegram Bot Runner — sets up and starts the Telegram bot with all handlers.
"""

import logging
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

from ai_chatbot.config import TELEGRAM_BOT_TOKEN
from ai_chatbot.bot.handlers import (
    start_command,
    help_command,
    message_handler,
    booking_start,
    booking_service,
    booking_date,
    booking_time,
    booking_confirm,
    booking_cancel,
    error_handler,
    BOOKING_SERVICE,
    BOOKING_DATE,
    BOOKING_TIME,
    BOOKING_CONFIRM,
)

logger = logging.getLogger(__name__)


def create_bot_application():
    """
    Create and configure the Telegram bot application with all handlers.
    
    Returns:
        Configured Application instance ready to run.
    """
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Please set it in your .env file or environment variables."
        )
    
    # Build the application
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # ─── Conversation handler for appointment booking ─────────────────────
    booking_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^📅 Book Appointment$"), booking_start),
            CommandHandler("book", booking_start),
        ],
        states={
            BOOKING_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, booking_service)],
            BOOKING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, booking_date)],
            BOOKING_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, booking_time)],
            BOOKING_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, booking_confirm)],
        },
        fallbacks=[CommandHandler("cancel", booking_cancel)],
    )
    
    # ─── Register handlers (order matters!) ───────────────────────────────
    
    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Booking conversation (must be before the general message handler)
    app.add_handler(booking_handler)
    
    # General text messages (catch-all)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    logger.info("Telegram bot application configured successfully")
    return app


def run_bot():
    """Start the Telegram bot (blocking call)."""
    logger.info("Starting Telegram bot...")
    app = create_bot_application()
    app.run_polling(drop_pending_updates=True)
