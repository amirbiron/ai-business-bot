"""
Shared fixtures — DB in-memory, מוקים לתלויות חיצוניות.
"""

import os
import sqlite3
import sys
import tempfile
import types
from unittest.mock import patch, MagicMock

import pytest

# ── Mock telegram (לא מותקן בסביבת הטסטים) ────────────────────────────────
# חייב להתרחש לפני כל ייבוא של מודולים שתלויים ב-telegram.
if "telegram" not in sys.modules:
    _telegram = types.ModuleType("telegram")
    _telegram.Bot = MagicMock()
    _telegram.Update = MagicMock()
    _telegram.ReplyKeyboardMarkup = MagicMock()
    _telegram.KeyboardButton = MagicMock()
    _telegram.InlineKeyboardButton = MagicMock()
    _telegram.InlineKeyboardMarkup = MagicMock()
    sys.modules["telegram"] = _telegram

    _error = types.ModuleType("telegram.error")

    class _Forbidden(Exception):
        pass

    class _RetryAfter(Exception):
        def __init__(self, retry_after=0):
            self.retry_after = retry_after
            super().__init__(f"RetryAfter: {retry_after}")

    class _TimedOut(Exception):
        pass

    class _BadRequest(Exception):
        pass

    class _NetworkError(Exception):
        pass

    _error.Forbidden = _Forbidden
    _error.RetryAfter = _RetryAfter
    _error.TimedOut = _TimedOut
    _error.BadRequest = _BadRequest
    _error.NetworkError = _NetworkError
    sys.modules["telegram.error"] = _error
    _telegram.error = _error

    _ext = types.ModuleType("telegram.ext")
    _ext.ContextTypes = MagicMock()
    _ext.ConversationHandler = MagicMock()
    _ext.ConversationHandler.END = -1
    _ext.ApplicationBuilder = MagicMock()
    _ext.Application = MagicMock()
    _ext.CommandHandler = MagicMock()
    _ext.MessageHandler = MagicMock()
    _ext.CallbackQueryHandler = MagicMock()
    _ext.filters = MagicMock()
    sys.modules["telegram.ext"] = _ext
    _telegram.ext = _ext

# ── Mock requests (לא צריך HTTP אמיתי בטסטים) ─────────────────────────────
if "requests" not in sys.modules:
    sys.modules["requests"] = types.ModuleType("requests")
    sys.modules["requests"].post = MagicMock()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """מגדיר משתני סביבה בטוחים כך שייבוא config לא ייצור קבצים אמיתיים."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("FAISS_INDEX_PATH", str(tmp_path / "faiss"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("ADMIN_SECRET_KEY", "test-secret")


@pytest.fixture
def db_conn(tmp_path):
    """מחזיר חיבור SQLite in-memory עם הסכימה המלאה של הפרויקט."""
    db_path = str(tmp_path / "test.db")

    # ייבוא config ו-database חייב לקרות אחרי הגדרת סביבה (_isolate_env)
    os.environ["DB_PATH"] = db_path
    # כדי לאלץ reload של DB_PATH — patch ישירות
    with patch("ai_chatbot.config.DB_PATH", tmp_path / "test.db"):
        from database import init_db, get_connection
        init_db()
        with get_connection() as conn:
            yield conn
