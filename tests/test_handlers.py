"""
טסטים ל-bot/handlers.py — פונקציות עזר, ניתוב intent, ו-handlers עיקריים.

מוקים: telegram Update/Context, DB, LLM, config values.
"""

import asyncio
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_update(user_id: int = 100, text: str = "שלום", username: str = "testuser"):
    """יוצר mock Update עם effective_user ו-message."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.full_name = "Test User"
    update.effective_user.username = username
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = user_id
    update.effective_message = update.message
    update.callback_query = None
    return update


def _make_context(args=None):
    """יוצר mock Context עם bot, user_data, bot_data."""
    context = MagicMock()
    context.user_data = {}
    context.bot_data = {}
    context.args = args or []
    context.bot = AsyncMock()
    context.bot.send_message = AsyncMock()
    context.bot.send_chat_action = AsyncMock()
    context.application = MagicMock()
    context.application.create_task = MagicMock()
    return context


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("ai_chatbot.config.DB_PATH", db_path):
        import importlib
        import database
        importlib.reload(database)
        database.init_db()
        yield database


# ── Pure / semi-pure helpers ────────────────────────────────────────────────


class TestGetUserInfo:
    def test_extracts_user_info(self):
        from bot.handlers import _get_user_info
        update = _make_update(user_id=42, username="moshe")
        update.effective_user.full_name = "Moshe Cohen"
        uid, name, uname = _get_user_info(update)
        assert uid == "42"
        assert name == "Moshe Cohen"
        assert uname == "moshe"

    def test_fallback_when_no_full_name(self):
        from bot.handlers import _get_user_info
        update = _make_update(user_id=7, username="dani")
        update.effective_user.full_name = ""
        _, name, _ = _get_user_info(update)
        assert "@dani" in name

    def test_fallback_when_no_username(self):
        from bot.handlers import _get_user_info
        update = _make_update(user_id=7, username="")
        update.effective_user.full_name = ""
        update.effective_user.username = ""
        _, name, _ = _get_user_info(update)
        assert "7" in name


class TestTgHandle:
    def test_with_username(self):
        from bot.handlers import _tg_handle
        assert _tg_handle("moshe") == "@moshe"

    def test_without_username(self):
        from bot.handlers import _tg_handle
        assert _tg_handle("") == ""


class TestShouldHandoffToHuman:
    def test_empty_text(self):
        from bot.handlers import _should_handoff_to_human
        assert not _should_handoff_to_human("")
        assert not _should_handoff_to_human(None)

    def test_fallback_response(self):
        from bot.handlers import _should_handoff_to_human, FALLBACK_RESPONSE
        assert _should_handoff_to_human(FALLBACK_RESPONSE)

    def test_handoff_phrase(self):
        from bot.handlers import _should_handoff_to_human
        assert _should_handoff_to_human("תנו לי להעביר אתכם לנציג אנושי שיוכל לעזור")

    def test_normal_text(self):
        from bot.handlers import _should_handoff_to_human
        assert not _should_handoff_to_human("שעות הפתיחה שלנו הן 9-17")


class TestVcardEscape:
    def test_escapes_special_chars(self):
        from bot.handlers import _vcard_escape
        assert _vcard_escape("a;b,c\\d") == "a\\;b\\,c\\\\d"

    def test_plain_text(self):
        from bot.handlers import _vcard_escape
        assert _vcard_escape("hello") == "hello"


class TestGenerateVcardText:
    def test_generates_valid_vcard(self, db):
        from bot.handlers import _generate_vcard_text
        vcard = _generate_vcard_text()
        assert vcard.startswith("BEGIN:VCARD")
        assert vcard.endswith("END:VCARD")
        assert "VERSION:3.0" in vcard


# ── Follow-up questions helpers ──────────────────────────────────────────────


class TestCleanupStaleFollowUps:
    def test_removes_old_entries(self):
        from bot.handlers import _cleanup_stale_follow_ups, FOLLOW_UP_CB_PREFIX
        old_ts = int(time.time()) - 7200  # שעתיים לפני
        bot_data = {
            f"{FOLLOW_UP_CB_PREFIX}123_{old_ts}_0": "שאלה ישנה",
            f"{FOLLOW_UP_CB_PREFIX}123_{old_ts}_1": "שאלה ישנה 2",
            "some_other_key": "value",
        }
        _cleanup_stale_follow_ups(bot_data)
        assert "some_other_key" in bot_data
        assert len(bot_data) == 1  # רק some_other_key נשאר

    def test_keeps_fresh_entries(self):
        from bot.handlers import _cleanup_stale_follow_ups, FOLLOW_UP_CB_PREFIX
        now_ts = int(time.time())
        bot_data = {
            f"{FOLLOW_UP_CB_PREFIX}123_{now_ts}_0": "שאלה חדשה",
        }
        _cleanup_stale_follow_ups(bot_data)
        assert len(bot_data) == 1


class TestBuildFollowUpKeyboard:
    def test_creates_keyboard(self):
        from bot.handlers import _build_follow_up_keyboard
        bot_data = {}
        kb = _build_follow_up_keyboard(["שאלה 1", "שאלה 2"], bot_data, "42")
        assert kb is not None
        assert len(bot_data) == 2  # שתי שאלות נשמרו

    def test_empty_questions_returns_none(self):
        from bot.handlers import _build_follow_up_keyboard
        assert _build_follow_up_keyboard([], {}, "42") is None


# ── _reply_html_safe ────────────────────────────────────────────────────────


class TestReplyHtmlSafe:
    @pytest.mark.asyncio
    async def test_sends_html(self):
        from bot.handlers import _reply_html_safe
        message = AsyncMock()
        await _reply_html_safe(message, "<b>test</b>")
        message.reply_text.assert_awaited_once_with("<b>test</b>", parse_mode="HTML")

    @pytest.mark.asyncio
    async def test_falls_back_on_bad_request(self):
        from bot.handlers import _reply_html_safe
        from telegram.error import BadRequest
        message = AsyncMock()
        message.reply_text.side_effect = [BadRequest("bad html"), None]
        await _reply_html_safe(message, "<bad>")
        assert message.reply_text.call_count == 2

    @pytest.mark.asyncio
    async def test_none_message(self):
        from bot.handlers import _reply_html_safe
        result = await _reply_html_safe(None, "text")
        assert result is None


# ── _notify_owner ────────────────────────────────────────────────────────────


class TestNotifyOwner:
    @pytest.mark.asyncio
    async def test_success(self):
        from bot.handlers import _notify_owner
        context = _make_context()
        with patch("bot.handlers.TELEGRAM_OWNER_CHAT_ID", "999"):
            result = await _notify_owner(context, "test notification")
        assert result is True
        context.bot.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_owner_id(self):
        from bot.handlers import _notify_owner
        context = _make_context()
        with patch("bot.handlers.TELEGRAM_OWNER_CHAT_ID", ""):
            result = await _notify_owner(context, "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_retries_on_network_error(self):
        from bot.handlers import _notify_owner
        from telegram.error import TimedOut
        context = _make_context()
        context.bot.send_message.side_effect = [TimedOut(), None]
        with patch("bot.handlers.TELEGRAM_OWNER_CHAT_ID", "999"):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await _notify_owner(context, "test", max_retries=2)
        assert result is True
        assert context.bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self):
        from bot.handlers import _notify_owner
        from telegram.error import TimedOut
        context = _make_context()
        context.bot.send_message.side_effect = TimedOut()
        with patch("bot.handlers.TELEGRAM_OWNER_CHAT_ID", "999"):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await _notify_owner(context, "test", max_retries=2)
        assert result is False

    @pytest.mark.asyncio
    async def test_unexpected_error_fails_immediately(self):
        from bot.handlers import _notify_owner
        context = _make_context()
        context.bot.send_message.side_effect = RuntimeError("boom")
        with patch("bot.handlers.TELEGRAM_OWNER_CHAT_ID", "999"):
            result = await _notify_owner(context, "test")
        assert result is False
        context.bot.send_message.assert_awaited_once()


# ── Intent routing in message_handler ────────────────────────────────────────


def _handler_patches():
    """Context managers משותפים לכל טסטי handlers — מוקים לדקורטורים ולתלויות.

    הדקורטורים (rate_limit_guard, live_chat_guard) מפנים לפונקציות ב-modules
    המקוריים שלהם, לכן צריך לעשות patch שם ולא ב-bot.handlers.
    """
    return [
        patch("rate_limiter.check_rate_limit", return_value=None),
        patch("rate_limiter.record_message"),
        patch("live_chat_service.LiveChatService.is_active", return_value=False),
        patch("live_chat_service.db"),
        patch("bot.handlers.db"),
    ]


from contextlib import ExitStack


class TestMessageHandlerIntentRouting:
    """בדיקת ניתוב intent ב-message_handler — ללא RAG/LLM."""

    @pytest.mark.asyncio
    async def test_greeting_routed_directly(self, db):
        from bot.handlers import message_handler
        update = _make_update(text="שלום!")
        context = _make_context()

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            mock_db = stack.enter_context(patch("bot.handlers.db"))
            mock_db.ensure_user_subscribed = MagicMock()
            mock_db.save_message = MagicMock()
            mock_db.is_referral_code_sent = MagicMock(return_value=True)
            mock_intent = stack.enter_context(patch("bot.handlers.detect_intent"))
            stack.enter_context(patch("bot.handlers.get_direct_response", return_value="היי!"))
            from bot.handlers import Intent
            mock_intent.return_value = Intent.GREETING

            await message_handler(update, context)

        update.message.reply_text.assert_awaited()
        call_args = update.message.reply_text.call_args
        assert "היי!" in str(call_args)

    @pytest.mark.asyncio
    async def test_business_hours_routed_directly(self, db):
        from bot.handlers import message_handler
        update = _make_update(text="מתי אתם פתוחים?")
        context = _make_context()

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            mock_db = stack.enter_context(patch("bot.handlers.db"))
            mock_db.ensure_user_subscribed = MagicMock()
            mock_db.save_message = MagicMock()
            mock_db.is_referral_code_sent = MagicMock(return_value=True)
            mock_intent = stack.enter_context(patch("bot.handlers.detect_intent"))
            stack.enter_context(patch("bot.handlers.is_currently_open", return_value={"message": "פתוח"}))
            stack.enter_context(patch("bot.handlers.get_weekly_schedule_text", return_value="ראשון-חמישי 9-17"))
            from bot.handlers import Intent
            mock_intent.return_value = Intent.BUSINESS_HOURS

            await message_handler(update, context)

        update.message.reply_text.assert_awaited()
        text_sent = update.message.reply_text.call_args[0][0]
        assert "פתוח" in text_sent
        assert "ראשון-חמישי" in text_sent

    @pytest.mark.asyncio
    async def test_complaint_offers_agent(self, db):
        from bot.handlers import message_handler
        update = _make_update(text="שירות גרוע!")
        context = _make_context()

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            mock_db = stack.enter_context(patch("bot.handlers.db"))
            mock_db.ensure_user_subscribed = MagicMock()
            mock_db.save_message = MagicMock()
            mock_db.is_referral_code_sent = MagicMock(return_value=True)
            mock_intent = stack.enter_context(patch("bot.handlers.detect_intent"))
            from bot.handlers import Intent
            mock_intent.return_value = Intent.COMPLAINT

            await message_handler(update, context)

        update.message.reply_text.assert_awaited()

    @pytest.mark.asyncio
    async def test_appointment_booking_during_vacation(self, db):
        from bot.handlers import message_handler
        update = _make_update(text="רוצה לקבוע תור")
        context = _make_context()

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            mock_db = stack.enter_context(patch("bot.handlers.db"))
            mock_db.ensure_user_subscribed = MagicMock()
            mock_db.save_message = MagicMock()
            mock_db.is_referral_code_sent = MagicMock(return_value=True)
            mock_intent = stack.enter_context(patch("bot.handlers.detect_intent"))
            mock_vac = stack.enter_context(patch("bot.handlers.VacationService"))
            from bot.handlers import Intent
            mock_intent.return_value = Intent.APPOINTMENT_BOOKING
            mock_vac.is_active.return_value = True
            mock_vac.get_booking_message.return_value = "אנחנו בחופשה!"

            await message_handler(update, context)

        text_sent = update.message.reply_text.call_args[0][0]
        assert "חופשה" in text_sent


# ── Start command ────────────────────────────────────────────────────────────


class TestStartCommand:
    @pytest.mark.asyncio
    async def test_sends_welcome_message(self, db):
        from bot.handlers import start_command
        update = _make_update()
        context = _make_context()

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            mock_db = stack.enter_context(patch("bot.handlers.db"))
            mock_db.ensure_user_subscribed = MagicMock()
            mock_db.save_message = MagicMock()
            mock_db.register_referral = MagicMock(return_value=False)

            await start_command(update, context)

        update.message.reply_text.assert_awaited_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "ברוכים הבאים" in call_text

    @pytest.mark.asyncio
    async def test_referral_code_bonus_text(self, db):
        from bot.handlers import start_command
        update = _make_update()
        context = _make_context(args=["REF_ABC123"])

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            mock_db = stack.enter_context(patch("bot.handlers.db"))
            mock_db.ensure_user_subscribed = MagicMock()
            mock_db.save_message = MagicMock()
            mock_db.register_referral = MagicMock(return_value=True)

            await start_command(update, context)

        call_text = update.message.reply_text.call_args[0][0]
        assert "הפניה" in call_text


# ── Booking flow ─────────────────────────────────────────────────────────────


class TestBookingFlow:
    @pytest.mark.asyncio
    async def test_booking_service_saves_and_advances(self, db):
        from bot.handlers import booking_service, BOOKING_DATE
        update = _make_update(text="תספורת")
        context = _make_context()

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            result = await booking_service(update, context)

        assert result == BOOKING_DATE
        assert context.user_data["booking_service"] == "תספורת"

    @pytest.mark.asyncio
    async def test_booking_date_saves_and_advances(self, db):
        from bot.handlers import booking_date, BOOKING_TIME
        update = _make_update(text="יום שני")
        context = _make_context()

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            result = await booking_date(update, context)

        assert result == BOOKING_TIME
        assert context.user_data["booking_date"] == "יום שני"

    @pytest.mark.asyncio
    async def test_booking_time_saves_and_advances(self, db):
        from bot.handlers import booking_time, BOOKING_CONFIRM
        update = _make_update(text="10:00")
        context = _make_context()
        context.user_data = {
            "booking_service": "תספורת",
            "booking_date": "יום שני",
        }

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            result = await booking_time(update, context)

        assert result == BOOKING_CONFIRM
        assert context.user_data["booking_time"] == "10:00"

    @pytest.mark.asyncio
    async def test_booking_confirm_yes(self, db):
        from bot.handlers import booking_confirm
        from telegram.ext import ConversationHandler
        update = _make_update(text="כן")
        context = _make_context()
        context.user_data = {
            "booking_service": "תספורת",
            "booking_date": "יום שני",
            "booking_time": "10:00",
        }

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            mock_db = stack.enter_context(patch("bot.handlers.db"))
            stack.enter_context(patch("bot.handlers._notify_owner", new_callable=AsyncMock, return_value=True))
            mock_db.create_appointment = MagicMock(return_value=1)
            mock_db.save_message = MagicMock()
            result = await booking_confirm(update, context)

        assert result == ConversationHandler.END
        assert context.user_data == {}
        mock_db.create_appointment.assert_called_once()

    @pytest.mark.asyncio
    async def test_booking_confirm_no(self, db):
        from bot.handlers import booking_confirm
        from telegram.ext import ConversationHandler
        update = _make_update(text="לא")
        context = _make_context()
        context.user_data = {"booking_service": "x"}

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            mock_db = stack.enter_context(patch("bot.handlers.db"))
            mock_db.save_message = MagicMock()
            result = await booking_confirm(update, context)

        assert result == ConversationHandler.END
        assert "בוטלה" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_booking_cancel(self, db):
        from bot.handlers import booking_cancel
        from telegram.ext import ConversationHandler
        update = _make_update(text="/cancel")
        context = _make_context()
        context.user_data = {"booking_service": "תספורת"}

        with ExitStack() as stack:
            for p in _handler_patches():
                stack.enter_context(p)
            result = await booking_cancel(update, context)

        assert result == ConversationHandler.END
        assert context.user_data == {}


# ── Error handler ────────────────────────────────────────────────────────────


class TestErrorHandler:
    @pytest.mark.asyncio
    async def test_replies_to_user(self):
        from bot.handlers import error_handler
        update = _make_update()
        context = _make_context()
        context.error = RuntimeError("boom")
        await error_handler(update, context)
        update.effective_message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_no_update(self):
        from bot.handlers import error_handler
        context = _make_context()
        context.error = RuntimeError("boom")
        # לא צריך לקרוס
        await error_handler(None, context)


# ── format_context (rag engine — מכוסה כאן כי קל לבדוק) ─────────────────────


class TestFormatContext:
    def test_formats_chunks(self):
        from rag.engine import format_context
        chunks = [
            {"category": "שירותים", "title": "תספורת", "text": "מחיר 50 ש\"ח"},
        ]
        result = format_context(chunks)
        assert "Context 1" in result
        assert "שירותים" in result
        assert "תספורת" in result

    def test_empty_chunks(self):
        from rag.engine import format_context
        result = format_context([])
        assert "No relevant" in result
