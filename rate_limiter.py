"""
Rate Limiter — in-memory per-user message rate limiting.

Enforces three sliding-window limits:
  - Per minute: 10 messages (above this is likely not human)
  - Per hour:   50 messages (multiple conversations per day)
  - Per day:   100 messages (beyond this is anomalous)

Data is stored in-memory (defaultdict of deques) so it resets on bot
restart.  This is acceptable for a small-business bot — no persistence
overhead and abuse windows are naturally bounded.
"""

import logging
import time
from collections import defaultdict, deque
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ai_chatbot.config import (
    RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_PER_HOUR,
    RATE_LIMIT_PER_DAY,
)
from ai_chatbot.live_chat_service import LiveChatService

logger = logging.getLogger(__name__)

# Per-user deque of message timestamps (epoch seconds).
# Using deque for efficient left-pops when pruning old entries.
_user_timestamps: dict[str, deque[float]] = defaultdict(deque)

# Sliding window definitions: (window_seconds, max_messages, response_message)
_WINDOWS = [
    (
        60,
        RATE_LIMIT_PER_MINUTE,
        "קצב ההודעות מהיר מדי. אנא המתינו כחצי דקה ונסו שוב",
    ),
    (
        3600,
        RATE_LIMIT_PER_HOUR,
        "הגעתם למגבלת ההודעות לשעה הקרובה. ניתן יהיה להמשיך את השיחה בתום השעה",
    ),
    (
        86400,
        RATE_LIMIT_PER_DAY,
        "הגעתם למכסת ההודעות היומית של הבוט. "
        "ניתן להמשיך מול נציג אנושי בלחיצה על הכפתור למטה",
    ),
]


def _prune(timestamps: deque[float], now: float) -> None:
    """Remove timestamps older than the largest window (1 day)."""
    cutoff = now - 86400
    while timestamps and timestamps[0] < cutoff:
        timestamps.popleft()


def check_rate_limit(user_id: str) -> str | None:
    """Check whether *user_id* has exceeded any rate limit.

    Returns the appropriate Hebrew response message if a limit is
    exceeded, or ``None`` if the user is within all limits.

    This function does NOT record a new timestamp — call
    :func:`record_message` after confirming the message will be processed.
    """
    now = time.time()
    timestamps = _user_timestamps[user_id]
    _prune(timestamps, now)

    for window_seconds, max_messages, message in _WINDOWS:
        cutoff = now - window_seconds
        count = sum(1 for ts in timestamps if ts >= cutoff)
        if count >= max_messages:
            logger.info(
                "Rate limit hit for user %s: %d msgs in %ds (limit %d)",
                user_id, count, window_seconds, max_messages,
            )
            return message

    return None


def record_message(user_id: str) -> None:
    """Record a new message timestamp for *user_id*."""
    _user_timestamps[user_id].append(time.time())


# ── Bot-Layer Decorators ─────────────────────────────────────────────────────


def rate_limit_guard(handler):
    """Decorator for regular bot handlers.

    If the user has exceeded a rate limit, sends the limit message and
    returns without calling the wrapped handler.  Must be applied
    **before** (i.e. above) ``@live_chat_guard`` so that rate limiting
    is evaluated first.

    During an active live chat session rate limiting is bypassed so
    that the user's messages are still saved for the human agent.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None:
            return await handler(update, context)

        user_id = str(user.id)

        # Don't rate-limit during live chat — let live_chat_guard handle it.
        if LiveChatService.is_active(user_id):
            return await handler(update, context)

        limit_msg = check_rate_limit(user_id)
        if limit_msg is not None:
            if update.message:
                try:
                    await update.message.reply_text(limit_msg, parse_mode="Markdown")
                except Exception:
                    await update.message.reply_text(limit_msg)
            return

        record_message(user_id)
        return await handler(update, context)

    return wrapper


def rate_limit_guard_booking(handler):
    """Decorator for booking conversation handlers.

    Like :func:`rate_limit_guard` but returns ``ConversationHandler.END``
    so the conversation handler exits cleanly when rate-limited.

    During an active live chat session rate limiting is bypassed.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None:
            return await handler(update, context)

        user_id = str(user.id)

        # Don't rate-limit during live chat — let live_chat_guard_booking handle it.
        if LiveChatService.is_active(user_id):
            return await handler(update, context)

        limit_msg = check_rate_limit(user_id)
        if limit_msg is not None:
            if update.message:
                try:
                    await update.message.reply_text(limit_msg, parse_mode="Markdown")
                except Exception:
                    await update.message.reply_text(limit_msg)
            context.user_data.clear()
            return ConversationHandler.END

        record_message(user_id)
        return await handler(update, context)

    return wrapper
