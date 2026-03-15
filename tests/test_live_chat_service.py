"""
טסטים ל-LiveChatService — שכבת השירות (service layer) ודקורטורים.

מוקים: DB, send_telegram_message, Update/Context של טלגרם.
"""

import os
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


@pytest.fixture
def db(tmp_path):
    """מאתחל DB בקובץ זמני ומחזיר את מודול database מוכן לשימוש."""
    db_path = tmp_path / "test.db"
    with patch("ai_chatbot.config.DB_PATH", db_path):
        import importlib
        import database
        importlib.reload(database)
        database.init_db()
        yield database


@pytest.fixture
def service(db):
    """מחזיר LiveChatService עם DB מוכן + mock לטלגרם."""
    with patch("live_chat_service.send_telegram_message", return_value=True) as mock_send:
        from live_chat_service import LiveChatService
        yield LiveChatService, mock_send


# ── Service: start ──────────────────────────────────────────────────────────


class TestStart:
    def test_start_new_session(self, service, db):
        svc, mock_send = service
        sent, status = svc.start("123")
        assert status == "started"
        assert sent is True
        assert db.is_live_chat_active("123")
        mock_send.assert_called_once()

    def test_start_idempotent(self, service, db):
        svc, mock_send = service
        db.start_live_chat("123", "אבי")
        sent, status = svc.start("123")
        assert status == "already_active"
        # לא שולח הודעה נוספת
        mock_send.assert_not_called()

    def test_start_telegram_failure(self, db):
        with patch("live_chat_service.send_telegram_message", return_value=False):
            from live_chat_service import LiveChatService
            sent, status = LiveChatService.start("123")
            assert status == "telegram_failed"
            assert sent is False
            # השיחה עדיין נפתחה ב-DB
            assert db.is_live_chat_active("123")

    def test_start_saves_notification_message(self, service, db):
        svc, mock_send = service
        svc.start("123")
        # ההודעה נשמרת ב-DB
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE user_id = '123' AND role = 'assistant'"
            ).fetchone()
            assert row is not None
            assert "נציג אנושי" in row["message"]


# ── Service: end ────────────────────────────────────────────────────────────


class TestEnd:
    def test_end_active_session(self, service, db):
        svc, mock_send = service
        db.start_live_chat("123", "אבי")
        sent, status = svc.end("123")
        assert status == "ended"
        assert not db.is_live_chat_active("123")

    def test_end_idempotent(self, service, db):
        svc, mock_send = service
        sent, status = svc.end("999")
        assert status == "already_ended"
        mock_send.assert_not_called()

    def test_end_telegram_failure(self, db):
        with patch("live_chat_service.send_telegram_message", return_value=False):
            from live_chat_service import LiveChatService
            db.start_live_chat("123", "אבי")
            sent, status = LiveChatService.end("123")
            assert status == "telegram_failed"
            # השיחה עדיין נסגרת ב-DB (חשוב!)
            assert not db.is_live_chat_active("123")


# ── Service: send ───────────────────────────────────────────────────────────


class TestSend:
    def test_send_message(self, service, db):
        svc, mock_send = service
        db.start_live_chat("123", "אבי")
        ok, status = svc.send("123", "שלום לקוח!")
        assert status == "sent"
        assert ok is True
        mock_send.assert_called_once_with("123", "שלום לקוח!")

    def test_send_no_active_session(self, service, db):
        svc, _ = service
        ok, status = svc.send("123", "שלום")
        assert status == "session_ended"
        assert ok is False

    def test_send_empty_message(self, service, db):
        svc, _ = service
        db.start_live_chat("123", "אבי")
        ok, status = svc.send("123", "")
        assert status == "empty_message"
        assert ok is False

    def test_send_whitespace_only(self, service, db):
        svc, _ = service
        db.start_live_chat("123", "אבי")
        ok, status = svc.send("123", "   ")
        assert status == "empty_message"
        assert ok is False

    def test_send_telegram_failure(self, db):
        with patch("live_chat_service.send_telegram_message", return_value=False):
            from live_chat_service import LiveChatService
            db.start_live_chat("123", "אבי")
            ok, status = LiveChatService.send("123", "שלום")
            assert status == "telegram_failed"

    def test_send_touches_session(self, service, db):
        """שליחת הודעה מנציג מעדכנת את updated_at."""
        svc, _ = service
        db.start_live_chat("123", "אבי")
        session_before = db.get_active_live_chat("123")
        svc.send("123", "שלום")
        session_after = db.get_active_live_chat("123")
        assert session_after["updated_at"] >= session_before["updated_at"]


# ── Service: is_active + timeout ────────────────────────────────────────────


class TestIsActive:
    def test_no_session(self, service, db):
        svc, _ = service
        assert not svc.is_active("123")

    def test_active_session(self, service, db):
        svc, _ = service
        db.start_live_chat("123", "אבי")
        assert svc.is_active("123")

    def test_timeout_closes_session(self, service, db):
        """session שלא עודכן מעבר ל-timeout נסגר אוטומטית."""
        svc, _ = service
        db.start_live_chat("123", "אבי")
        # הזקנת ה-session
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE live_chats SET updated_at = datetime('now', '-3 hours') WHERE user_id = '123'"
            )
        assert not svc.is_active("123")
        assert not db.is_live_chat_active("123")


# ── Decorators ──────────────────────────────────────────────────────────────


def _make_update(user_id: int = 123, text: str = "שלום"):
    """יוצר mock Update עם effective_user ו-message."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.full_name = "Test User"
    update.effective_user.username = "testuser"
    update.message = MagicMock()
    update.message.text = text
    return update


def _make_context():
    """יוצר mock Context."""
    context = MagicMock()
    context.user_data = {}
    return context


class TestLiveChatGuard:
    @pytest.mark.asyncio
    async def test_passes_through_when_no_live_chat(self, service, db):
        """ללא שיחה חיה — ה-handler הפנימי נקרא."""
        from live_chat_service import live_chat_guard
        inner = AsyncMock(return_value="handled")
        guarded = live_chat_guard(inner)

        update = _make_update()
        context = _make_context()
        result = await guarded(update, context)
        assert result == "handled"
        inner.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blocks_during_live_chat(self, service, db):
        """בזמן שיחה חיה — ה-handler לא נקרא, ההודעה נשמרת."""
        from live_chat_service import live_chat_guard
        db.start_live_chat("123", "אבי")

        inner = AsyncMock()
        guarded = live_chat_guard(inner)

        update = _make_update(user_id=123, text="שאלה חשובה")
        context = _make_context()
        result = await guarded(update, context)
        assert result is None
        inner.assert_not_awaited()

        # ההודעה נשמרה ב-DB
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE user_id = '123' AND role = 'user'"
            ).fetchone()
            assert row is not None
            assert "שאלה חשובה" in row["message"]


class TestLiveChatGuardBooking:
    @pytest.mark.asyncio
    async def test_returns_end_during_live_chat(self, service, db):
        """בזמן שיחה חיה — מחזיר ConversationHandler.END."""
        from live_chat_service import live_chat_guard_booking
        from telegram.ext import ConversationHandler
        db.start_live_chat("123", "אבי")

        inner = AsyncMock()
        guarded = live_chat_guard_booking(inner)

        update = _make_update(user_id=123)
        context = _make_context()
        result = await guarded(update, context)
        assert result == ConversationHandler.END
        inner.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clears_user_data(self, service, db):
        """הדקורטור מנקה את user_data בזמן שיחה חיה."""
        from live_chat_service import live_chat_guard_booking
        db.start_live_chat("123", "אבי")

        inner = AsyncMock()
        guarded = live_chat_guard_booking(inner)

        update = _make_update(user_id=123)
        context = _make_context()
        context.user_data["booking_service"] = "תספורת"
        await guarded(update, context)
        assert context.user_data == {}
