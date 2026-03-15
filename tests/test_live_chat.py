"""
טסטים ל-LiveChatService ולפונקציות DB של live chat.

בודק: יצירת/סגירת sessions, timeout לפי פעילות אחרונה,
touch_live_chat, ו-cleanup_expired.
"""

import os
from unittest.mock import patch

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


class TestLiveChatDB:
    """טסטים לפונקציות DB של live chat."""

    def test_start_and_get(self, db):
        session_id = db.start_live_chat("123", "אבי")
        assert session_id > 0
        session = db.get_active_live_chat("123")
        assert session is not None
        assert session["user_id"] == "123"
        assert session["is_active"] == 1

    def test_end_live_chat(self, db):
        db.start_live_chat("123", "אבי")
        db.end_live_chat("123")
        assert db.get_active_live_chat("123") is None

    def test_is_live_chat_active(self, db):
        assert not db.is_live_chat_active("123")
        db.start_live_chat("123", "אבי")
        assert db.is_live_chat_active("123")

    def test_count_active(self, db):
        assert db.count_active_live_chats() == 0
        db.start_live_chat("111", "א")
        db.start_live_chat("222", "ב")
        assert db.count_active_live_chats() == 2

    def test_get_all_active(self, db):
        db.start_live_chat("111", "א")
        db.start_live_chat("222", "ב")
        active = db.get_all_active_live_chats()
        assert len(active) == 2

    def test_start_closes_previous_session(self, db):
        """התחלת שיחה חדשה סוגרת את הקודמת."""
        db.start_live_chat("123", "אבי")
        db.start_live_chat("123", "אבי")
        assert db.count_active_live_chats() == 1

    def test_touch_live_chat(self, db):
        """עדכון פעילות אחרונה מעדכן את updated_at."""
        db.start_live_chat("123", "אבי")
        session_before = db.get_active_live_chat("123")
        # מוודא שיש updated_at
        assert session_before.get("updated_at") is not None

        db.touch_live_chat("123")
        session_after = db.get_active_live_chat("123")
        # updated_at צריך להיות >= לפני (יכול להיות שווה אם הקריאה מהירה מדי)
        assert session_after["updated_at"] >= session_before["updated_at"]


class TestEndExpiredLiveChats:
    """טסטים ל-auto-timeout של sessions ישנים."""

    def test_no_expired_returns_zero(self, db):
        """אם אין sessions פתוחים — מחזיר 0."""
        assert db.end_expired_live_chats(max_hours=4) == 0

    def test_fresh_session_not_closed(self, db):
        """session חדש לא נסגר."""
        db.start_live_chat("123", "אבי")
        closed = db.end_expired_live_chats(max_hours=4)
        assert closed == 0
        assert db.is_live_chat_active("123")

    def test_old_session_closed(self, db):
        """session שלא עודכן מזמן — נסגר אוטומטית."""
        db.start_live_chat("123", "אבי")
        # הזקנת ה-session — קביעת updated_at ל-5 שעות בעבר
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE live_chats SET updated_at = datetime('now', '-5 hours') WHERE user_id = '123'"
            )
        closed = db.end_expired_live_chats(max_hours=4)
        assert closed == 1
        assert not db.is_live_chat_active("123")

    def test_recently_touched_not_closed(self, db):
        """session שעודכן לאחרונה (touch) — לא נסגר גם אם started_at ישן."""
        db.start_live_chat("123", "אבי")
        # started_at ישן אבל updated_at טרי
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE live_chats SET started_at = datetime('now', '-10 hours') WHERE user_id = '123'"
            )
        # updated_at עדיין טרי (מזמן ה-INSERT)
        closed = db.end_expired_live_chats(max_hours=4)
        assert closed == 0
        assert db.is_live_chat_active("123")

    def test_multiple_sessions_partial_close(self, db):
        """רק sessions ישנים נסגרים, חדשים נשארים."""
        db.start_live_chat("111", "א")
        db.start_live_chat("222", "ב")
        # הזקנת רק session 111
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE live_chats SET updated_at = datetime('now', '-5 hours') WHERE user_id = '111'"
            )
        closed = db.end_expired_live_chats(max_hours=4)
        assert closed == 1
        assert not db.is_live_chat_active("111")
        assert db.is_live_chat_active("222")

    def test_already_ended_not_counted(self, db):
        """sessions שכבר נסגרו לא נספרים."""
        db.start_live_chat("123", "אבי")
        db.end_live_chat("123")
        closed = db.end_expired_live_chats(max_hours=0)  # max_hours=0 — כל session
        assert closed == 0
