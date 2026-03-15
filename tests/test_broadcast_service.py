"""
טסטים ל-BroadcastService — send_broadcast, error handling, progress updates.

כל הטסטים async (pytest-asyncio) עם mock לבוט וDB.
telegram mock מגיע מ-conftest.py.
"""

import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from telegram.error import Forbidden, RetryAfter, TimedOut, BadRequest


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("ai_chatbot.config.DB_PATH", db_path):
        import importlib
        import database
        importlib.reload(database)
        database.init_db()
        yield database


@pytest.fixture
def mock_bot():
    """מחזיר mock Bot עם send_message async."""
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.initialize = AsyncMock()
    bot.shutdown = AsyncMock()
    return bot


class TestSendBroadcast:
    @pytest.mark.asyncio
    async def test_successful_broadcast(self, db, mock_bot):
        from broadcast_service import send_broadcast
        bc_id = db.create_broadcast("שלום לכולם!", "all", 3)
        await send_broadcast(mock_bot, bc_id, "שלום לכולם!", ["111", "222", "333"])

        assert mock_bot.send_message.call_count == 3

    @pytest.mark.asyncio
    async def test_message_too_long(self, db, mock_bot):
        from broadcast_service import send_broadcast, _MAX_MESSAGE_LENGTH
        long_msg = "x" * (_MAX_MESSAGE_LENGTH + 1)
        bc_id = db.create_broadcast(long_msg, "all", 1)
        await send_broadcast(mock_bot, bc_id, long_msg, ["111"])

        # לא נשלחת אף הודעה
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_forbidden_unsubscribes(self, db, mock_bot):
        """משתמש שחסם את הבוט — נרשם כ-failed + unsubscribe."""
        from broadcast_service import send_broadcast
        mock_bot.send_message.side_effect = Forbidden("blocked")
        bc_id = db.create_broadcast("הודעה", "all", 1)

        with patch("broadcast_service.db.unsubscribe_user") as mock_unsub:
            await send_broadcast(mock_bot, bc_id, "הודעה", ["111"])
            mock_unsub.assert_called_once_with("111")

    @pytest.mark.asyncio
    async def test_retry_after_waits_and_retries(self, db, mock_bot):
        """RetryAfter — ממתין ומנסה שוב."""
        from broadcast_service import send_broadcast

        retry_err = RetryAfter(retry_after=0)
        mock_bot.send_message.side_effect = [retry_err, None]

        bc_id = db.create_broadcast("הודעה", "all", 1)
        await send_broadcast(mock_bot, bc_id, "הודעה", ["111"])

        assert mock_bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_timed_out_counts_as_failed(self, db, mock_bot):
        """TimedOut — נספר ככשלון."""
        from broadcast_service import send_broadcast
        mock_bot.send_message.side_effect = TimedOut()
        bc_id = db.create_broadcast("הודעה", "all", 1)
        await send_broadcast(mock_bot, bc_id, "הודעה", ["111"])
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_mixed_results(self, db, mock_bot):
        """חלק מצליחים, חלק נכשלים — כולם מעובדים."""
        from broadcast_service import send_broadcast
        mock_bot.send_message.side_effect = [
            None,
            Forbidden("blocked"),
            None,
        ]
        bc_id = db.create_broadcast("הודעה", "all", 3)

        with patch("broadcast_service.db.unsubscribe_user"):
            await send_broadcast(mock_bot, bc_id, "הודעה", ["111", "222", "333"])

        assert mock_bot.send_message.call_count == 3

    @pytest.mark.asyncio
    async def test_needs_init_initializes_and_shuts_down(self, db, mock_bot):
        """needs_init=True — מאתחל את ה-Bot וסוגר בסוף."""
        from broadcast_service import send_broadcast
        bc_id = db.create_broadcast("הודעה", "all", 1)
        await send_broadcast(mock_bot, bc_id, "הודעה", ["111"], needs_init=True)

        mock_bot.initialize.assert_awaited_once()
        mock_bot.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_recipients(self, db, mock_bot):
        """רשימת נמענים ריקה — לא קורסת."""
        from broadcast_service import send_broadcast
        bc_id = db.create_broadcast("הודעה", "all", 0)
        await send_broadcast(mock_bot, bc_id, "הודעה", [])
        mock_bot.send_message.assert_not_called()


class TestHandleFutureError:
    def test_cancelled_future(self, db):
        from broadcast_service import _handle_future_error
        bc_id = db.create_broadcast("הודעה", "all", 1)
        future = MagicMock()
        future.cancelled.return_value = True
        _handle_future_error(future, bc_id)

    def test_exception_future(self, db):
        from broadcast_service import _handle_future_error
        bc_id = db.create_broadcast("הודעה", "all", 1)
        future = MagicMock()
        future.cancelled.return_value = False
        future.exception.return_value = RuntimeError("boom")
        _handle_future_error(future, bc_id)

    def test_successful_future(self, db):
        from broadcast_service import _handle_future_error
        bc_id = db.create_broadcast("הודעה", "all", 1)
        future = MagicMock()
        future.cancelled.return_value = False
        future.exception.return_value = None
        _handle_future_error(future, bc_id)
