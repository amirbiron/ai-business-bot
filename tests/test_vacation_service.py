"""
טסטים ל-VacationService — לוגיקת שירות, הודעות, ודקורטורים.
"""

import os
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("ai_chatbot.config.DB_PATH", db_path):
        import importlib
        import database
        importlib.reload(database)
        database.init_db()
        yield database


@pytest.fixture(autouse=True)
def _reset_cache():
    """איפוס cache של VacationService בין טסטים."""
    from vacation_service import VacationService
    VacationService._cache = (0.0, False)
    yield
    VacationService._cache = (0.0, False)


# ── Service Logic ───────────────────────────────────────────────────────────


class TestVacationIsActive:
    def test_inactive_by_default(self, db):
        from vacation_service import VacationService
        assert not VacationService.is_active()

    def test_active_when_enabled(self, db):
        from vacation_service import VacationService
        db.update_vacation_mode(True)
        VacationService._cache = (0.0, False)  # reset cache
        assert VacationService.is_active()

    def test_cache_prevents_repeated_db_calls(self, db):
        from vacation_service import VacationService
        VacationService.is_active()  # ממלא cache
        # שינוי ב-DB — אבל ה-cache עדיין תקף
        db.update_vacation_mode(True)
        assert not VacationService.is_active()  # cache ישן

    def test_cache_expires(self, db):
        from vacation_service import VacationService
        VacationService.is_active()  # ממלא cache
        # מזקין את ה-cache
        VacationService._cache = (time.time() - 60, False)
        db.update_vacation_mode(True)
        assert VacationService.is_active()  # cache פג — קורא DB


class TestVacationMessages:
    def test_booking_message_with_end_date(self, db):
        from vacation_service import VacationService
        db.update_vacation_mode(True, vacation_end_date="2026-04-01")
        msg = VacationService.get_booking_message()
        assert "2026-04-01" in msg
        assert "חופשה" in msg

    def test_booking_message_without_end_date(self, db):
        from vacation_service import VacationService
        db.update_vacation_mode(True)
        msg = VacationService.get_booking_message()
        assert "בקרוב" in msg

    def test_booking_message_custom(self, db):
        from vacation_service import VacationService
        db.update_vacation_mode(True, vacation_message="הודעה מותאמת!")
        msg = VacationService.get_booking_message()
        assert msg == "הודעה מותאמת!"

    def test_agent_message_with_end_date(self, db):
        from vacation_service import VacationService
        db.update_vacation_mode(True, vacation_end_date="2026-04-01")
        msg = VacationService.get_agent_message()
        assert "2026-04-01" in msg

    def test_agent_message_without_end_date(self, db):
        from vacation_service import VacationService
        db.update_vacation_mode(True)
        msg = VacationService.get_agent_message()
        assert "חופשה" in msg


# ── Decorators ──────────────────────────────────────────────────────────────


def _make_update(user_id: int = 99999):
    """יוצר mock Update — user_id ברירת מחדל גבוה כדי לא להתנגש עם live chat tests."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context():
    context = MagicMock()
    context.user_data = {"some_key": "some_value"}
    return context


class TestVacationGuardBooking:
    @pytest.mark.asyncio
    async def test_passes_through_when_not_active(self, db):
        from vacation_service import vacation_guard_booking
        inner = AsyncMock(return_value="ok")
        guarded = vacation_guard_booking(inner)

        with patch("vacation_service.LiveChatService.is_active", return_value=False):
            result = await guarded(_make_update(), _make_context())
        assert result == "ok"
        inner.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blocks_when_active(self, db):
        from vacation_service import vacation_guard_booking, VacationService
        from telegram.ext import ConversationHandler
        db.update_vacation_mode(True)
        VacationService._cache = (0.0, False)

        inner = AsyncMock()
        guarded = vacation_guard_booking(inner)
        update = _make_update()
        context = _make_context()

        with patch("vacation_service.LiveChatService.is_active", return_value=False):
            result = await guarded(update, context)
        assert result == ConversationHandler.END
        inner.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()
        assert context.user_data == {}

    @pytest.mark.asyncio
    async def test_passes_through_during_live_chat(self, db):
        """בזמן live chat — מעביר ל-handler גם אם חופשה פעילה."""
        from vacation_service import vacation_guard_booking, VacationService
        db.update_vacation_mode(True)
        VacationService._cache = (0.0, False)

        inner = AsyncMock(return_value="ok")
        guarded = vacation_guard_booking(inner)

        with patch("vacation_service.LiveChatService.is_active", return_value=True):
            result = await guarded(_make_update(user_id=123), _make_context())
        assert result == "ok"
        inner.assert_awaited_once()


class TestVacationGuardAgent:
    @pytest.mark.asyncio
    async def test_blocks_when_active(self, db):
        from vacation_service import vacation_guard_agent, VacationService
        db.update_vacation_mode(True)
        VacationService._cache = (0.0, False)

        inner = AsyncMock()
        guarded = vacation_guard_agent(inner)
        update = _make_update()

        with patch("vacation_service.LiveChatService.is_active", return_value=False):
            await guarded(update, _make_context())
        inner.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_through_when_not_active(self, db):
        from vacation_service import vacation_guard_agent
        inner = AsyncMock(return_value="ok")
        guarded = vacation_guard_agent(inner)

        with patch("vacation_service.LiveChatService.is_active", return_value=False):
            result = await guarded(_make_update(), _make_context())
        assert result == "ok"
        inner.assert_awaited_once()
