"""
Shared fixtures — DB in-memory, מוקים לתלויות חיצוניות.
"""

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest


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
