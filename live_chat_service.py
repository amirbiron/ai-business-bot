"""
LiveChatService — centralized service for managing live chat sessions.

Consolidates all live chat logic (start, end, send, is_active) that was
previously scattered across bot handlers and admin endpoints.  Enforces
the BOT_ACTIVE ↔ LIVE_CHAT state transitions with idempotent guards.

See: https://github.com/amirbiron/ai-business-bot/issues/49
"""

import logging
from functools import wraps
from typing import Optional

import requests as http_requests
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ai_chatbot import database as db
from ai_chatbot.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)


# ── Telegram & Username Helpers ──────────────────────────────────────────────


def send_telegram_message(chat_id: str, text: str) -> bool:
    """Send a message to a Telegram user via the Bot HTTP API."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        resp = http_requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return resp.ok
    except Exception as e:
        logger.error("Failed to send Telegram message to %s: %s", chat_id, e)
        return False


def _get_customer_username(user_id: str) -> str:
    """Look up the customer's display name for a given user_id."""
    return db.get_username_for_user(user_id) or user_id


# ── Service Layer ────────────────────────────────────────────────────────────


class LiveChatService:
    """Centralized service for all live chat operations.

    All state queries and transitions go through this class so that
    edge cases (duplicate starts, Telegram failures, username lookups)
    are handled in a single place.
    """

    # ── State Queries ────────────────────────────────────────────────

    @staticmethod
    def is_active(user_id: str) -> bool:
        """Check whether the user is currently in a live chat session."""
        return db.is_live_chat_active(user_id)

    @staticmethod
    def get_session(user_id: str) -> Optional[dict]:
        """Return the active session record, or None."""
        return db.get_active_live_chat(user_id)

    @staticmethod
    def get_all_active() -> list[dict]:
        """Return all currently active live chat sessions."""
        return db.get_all_active_live_chats()

    @staticmethod
    def count_active() -> int:
        """Count currently active live chat sessions."""
        return db.count_active_live_chats()

    @staticmethod
    def get_customer_username(user_id: str) -> str:
        """Look up the customer's display name."""
        return _get_customer_username(user_id)

    # ── State Transitions ────────────────────────────────────────────

    @staticmethod
    def start(user_id: str) -> tuple[bool, str]:
        """Transition BOT_ACTIVE → LIVE_CHAT.

        Idempotent — returns early if already active.
        Handles duplicate starts, username lookup, Telegram notification.

        Returns:
            (telegram_sent, status) where status is one of:
            "already_active", "started", "telegram_failed".
        """
        if db.is_live_chat_active(user_id):
            return True, "already_active"

        username = _get_customer_username(user_id)
        db.start_live_chat(user_id, username)

        notify_msg = "👤 נציג אנושי הצטרף לשיחה. כעת תקבלו מענה ישיר."
        sent = send_telegram_message(user_id, notify_msg)
        if sent:
            db.save_message(user_id, username, "assistant", notify_msg)

        return sent, "started" if sent else "telegram_failed"

    @staticmethod
    def end(user_id: str) -> tuple[bool, str]:
        """Transition LIVE_CHAT → BOT_ACTIVE.

        Idempotent — returns early if not active.
        Sends the "bot is back" notification *before* deactivating so the
        bot stays suspended until the customer receives the message.

        Returns:
            (telegram_sent, status) where status is one of:
            "already_ended", "ended", "telegram_failed".
        """
        if not db.is_live_chat_active(user_id):
            return True, "already_ended"

        username = _get_customer_username(user_id)
        end_msg = (
            "🤖 הבוט חזר לנהל את השיחה. "
            "אם תרצו לדבר עם נציג שוב, לחצו על 'דברו עם נציג'."
        )
        sent = send_telegram_message(user_id, end_msg)
        if sent:
            db.save_message(user_id, username, "assistant", end_msg)

        # Deactivate AFTER sending notification
        db.end_live_chat(user_id)

        return sent, "ended" if sent else "telegram_failed"

    @staticmethod
    def send(user_id: str, message_text: str) -> tuple[bool, str]:
        """Send a message from the human agent to the customer.

        Guards: session must be active, message must be non-empty.

        Returns:
            (success, status) where status is one of:
            "session_ended", "empty_message", "sent", "telegram_failed".
        """
        if not db.is_live_chat_active(user_id):
            return False, "session_ended"

        if not message_text or not message_text.strip():
            return False, "empty_message"

        sent = send_telegram_message(user_id, message_text)
        if not sent:
            return False, "telegram_failed"

        username = _get_customer_username(user_id)
        db.save_message(user_id, username, "assistant", message_text)

        return True, "sent"

    # ── Lifecycle ────────────────────────────────────────────────────

    @staticmethod
    def cleanup_stale():
        """Deactivate sessions left over from a previous bot run."""
        db.cleanup_stale_live_chats()


# ── Bot-Layer Decorators (Middleware) ────────────────────────────────────────


def _get_user_info(update: Update) -> tuple[str, str]:
    """Extract user_id and display_name from an Update."""
    user = update.effective_user
    user_id = str(user.id)
    display_name = user.full_name or (
        f"@{user.username}" if user.username else f"User {user.id}"
    )
    return user_id, display_name


def live_chat_guard(handler):
    """Bot middleware decorator: if live chat is active, save the message and return silently.

    Use on regular bot handlers (commands, button handlers, message handler).
    The bot "goes silent" and lets the human agent handle the conversation.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id, display_name = _get_user_info(update)
        if LiveChatService.is_active(user_id):
            # Save the user's message for the human agent's context
            if update.message and update.message.text:
                db.save_message(user_id, display_name, "user", update.message.text)
            return
        return await handler(update, context)

    return wrapper


def live_chat_guard_booking(handler):
    """Bot middleware decorator for booking conversation handlers.

    Like ``live_chat_guard`` but returns ``ConversationHandler.END`` and
    clears booking state so the conversation handler exits cleanly.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id, display_name = _get_user_info(update)
        if LiveChatService.is_active(user_id):
            if update.message and update.message.text:
                db.save_message(user_id, display_name, "user", update.message.text)
            context.user_data.clear()
            return ConversationHandler.END
        return await handler(update, context)

    return wrapper
