"""
טסטים למודול הגבלת קצב — rate_limiter.py

בודק את הלוגיקה הבסיסית של חלונות הזמן (דקה, שעה, יום)
ואת פונקציית הניקוי.

מוק על telegram כי הוא לא נחוץ ללוגיקת ה-rate limiting עצמה.
"""

import sys
import time
from collections import deque
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# מוק ל-telegram לפני ייבוא rate_limiter — מונע בעיות תלויות
_telegram_mock = MagicMock()
sys.modules.setdefault("telegram", _telegram_mock)
sys.modules.setdefault("telegram.ext", _telegram_mock)

# מוק ל-live_chat_service שתלוי אף הוא ב-telegram
_lcs_mock = MagicMock()
sys.modules.setdefault("ai_chatbot.live_chat_service", _lcs_mock)

import rate_limiter
from rate_limiter import check_rate_limit, record_message, _prune, _user_timestamps


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """מאפס את ה-state הפנימי בין טסטים."""
    _user_timestamps.clear()
    yield
    _user_timestamps.clear()


class TestPrune:
    def test_removes_old_timestamps(self):
        now = time.time()
        ts = deque([now - 100000, now - 90000, now - 10])
        _prune(ts, now)
        # רק הטיימסטמפ האחרון (10 שניות אחורה) צריך להישאר
        assert len(ts) == 1

    def test_keeps_recent_timestamps(self):
        now = time.time()
        ts = deque([now - 100, now - 50, now - 1])
        _prune(ts, now)
        assert len(ts) == 3

    def test_empty_deque(self):
        _prune(deque(), time.time())  # לא צריך לזרוק שגיאה


class TestCheckRateLimit:
    def test_no_messages_returns_none(self):
        """משתמש בלי הודעות — לא חסום."""
        assert check_rate_limit("user1") is None

    def test_under_limit_returns_none(self):
        """3 הודעות — הרבה מתחת למגבלה."""
        for _ in range(3):
            record_message("user2")
        assert check_rate_limit("user2") is None

    def test_minute_limit_triggers(self):
        """מגבלת הדקה (ברירת מחדל 10) — אחרי 10 הודעות צריך לחסום."""
        per_minute = rate_limiter.RATE_LIMIT_PER_MINUTE
        for _ in range(per_minute):
            record_message("user3")
        result = check_rate_limit("user3")
        assert result is not None
        assert "קצב" in result or "מהיר" in result  # הודעת חסימה בעברית

    def test_different_users_independent(self):
        """מגבלות לכל משתמש בנפרד."""
        per_minute = rate_limiter.RATE_LIMIT_PER_MINUTE
        for _ in range(per_minute):
            record_message("spammer")
        # spammer חסום
        assert check_rate_limit("spammer") is not None
        # משתמש אחר — לא חסום
        assert check_rate_limit("innocent") is None


class TestRecordMessage:
    def test_records_timestamp(self):
        record_message("user_x")
        assert len(_user_timestamps["user_x"]) == 1

    def test_multiple_records(self):
        for _ in range(5):
            record_message("user_y")
        assert len(_user_timestamps["user_y"]) == 5
