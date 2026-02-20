"""
Main Entry Point — Starts both the Telegram bot and the Admin panel.

Usage:
    python -m ai_chatbot.main              # Start both bot and admin
    python -m ai_chatbot.main --bot        # Start only the Telegram bot
    python -m ai_chatbot.main --admin      # Start only the admin panel
    python -m ai_chatbot.main --seed       # Seed database and build index
"""

import argparse
import logging
import threading
import sys

from ai_chatbot import database as db
from ai_chatbot.config import TELEGRAM_BOT_TOKEN, ADMIN_HOST, ADMIN_PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def run_seed():
    """Seed the database with demo data and build the RAG index."""
    from ai_chatbot.seed_data import seed_and_index
    seed_and_index()


def run_admin_panel():
    """Start the Flask admin panel in a thread."""
    from ai_chatbot.admin.app import run_admin
    logger.info("Starting Admin Panel at http://%s:%s", ADMIN_HOST, ADMIN_PORT)
    run_admin()


def run_telegram_bot():
    """Start the Telegram bot."""
    from ai_chatbot.bot.telegram_bot import run_bot
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN is not set! "
            "Please set it in your .env file. "
            "Starting admin panel only..."
        )
        return
    
    logger.info("Starting Telegram Bot...")
    run_bot()


def main():
    parser = argparse.ArgumentParser(description="AI Business Chatbot")
    parser.add_argument("--bot", action="store_true", help="Start only the Telegram bot")
    parser.add_argument("--admin", action="store_true", help="Start only the admin panel")
    parser.add_argument("--seed", action="store_true", help="Seed database and build RAG index")
    args = parser.parse_args()
    
    # Always initialize the database
    logger.info("Initializing database...")
    db.init_db()

    if args.seed:
        run_seed()
        return

    # Auto-seed on first run: if the knowledge base is empty, populate it with
    # demo data and build the FAISS index so the bot can answer questions
    # immediately without requiring a manual --seed step.
    if db.count_kb_entries(active_only=False) == 0:
        logger.info("Knowledge base is empty — auto-seeding with demo data...")
        try:
            run_seed()
        except Exception:
            logger.exception("Auto-seed failed. Continuing without demo data.")

    if args.bot:
        run_telegram_bot()
        return

    if args.admin:
        run_admin_panel()
        return
    
    # Default: run both
    logger.info("Starting AI Business Chatbot (Bot + Admin Panel)...")
    
    # Start admin panel in a background thread
    admin_thread = threading.Thread(target=run_admin_panel, daemon=True)
    admin_thread.start()
    logger.info("Admin panel started at http://%s:%s", ADMIN_HOST, ADMIN_PORT)
    
    # Start the Telegram bot in the main thread (it uses asyncio)
    if TELEGRAM_BOT_TOKEN:
        run_telegram_bot()
    else:
        logger.warning(
            "TELEGRAM_BOT_TOKEN not set. Running admin panel only. "
            "Set TELEGRAM_BOT_TOKEN in .env to enable the Telegram bot."
        )
        # Keep the main thread alive for the admin panel
        try:
            admin_thread.join()
        except KeyboardInterrupt:
            logger.info("Shutting down...")


if __name__ == "__main__":
    main()

