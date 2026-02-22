"""
טסטים למודול שעות פעילות — business_hours.py

משתמש ב-mock על DB ועל שעון הזמן כדי לבדוק
תרחישי שעות פתיחה, חגים, ימים מיוחדים וערבי חג.
"""

from datetime import date, time, datetime
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest

from business_hours import (
    _python_weekday_to_israeli,
    get_status_for_date,
    is_currently_open,
    get_weekly_schedule_text,
    _format_closed_message,
    _find_next_opening,
    ISRAEL_TZ,
    DAY_NAMES_HE,
)


class TestWeekdayConversion:
    @pytest.mark.parametrize("py_day,il_day", [
        (0, 1),  # Monday → שני
        (1, 2),  # Tuesday → שלישי
        (2, 3),  # Wednesday → רביעי
        (3, 4),  # Thursday → חמישי
        (4, 5),  # Friday → שישי
        (5, 6),  # Saturday → שבת
        (6, 0),  # Sunday → ראשון
    ])
    def test_conversion(self, py_day, il_day):
        assert _python_weekday_to_israeli(py_day) == il_day


class TestGetStatusForDate:
    """בדיקת סדר עדיפויות: special_day → חג → שעות רגילות."""

    @patch("business_hours.db")
    def test_special_day_closed(self, mock_db):
        """יום מיוחד סגור מנצח הכל."""
        mock_db.get_special_day_by_date.return_value = {
            "is_closed": True, "name": "שיפוץ", "notes": ""
        }
        # Sunday 2026-03-01
        result = get_status_for_date(date(2026, 3, 1))
        assert result["is_open"] is False
        assert result["reason"] == "שיפוץ"
        assert result["source"] == "special_day"

    @patch("business_hours.db")
    def test_special_day_custom_hours(self, mock_db):
        """יום מיוחד עם שעות מותאמות."""
        mock_db.get_special_day_by_date.return_value = {
            "is_closed": False, "name": "ערב חג", "open_time": "09:00",
            "close_time": "14:00", "notes": "סוגרים מוקדם"
        }
        result = get_status_for_date(date(2026, 3, 1))
        assert result["is_open"] is True
        assert result["close_time"] == "14:00"
        assert result["source"] == "special_day"

    @patch("business_hours.db")
    @patch("business_hours._get_israeli_holidays")
    def test_holiday_closed(self, mock_holidays, mock_db):
        """חג ישראלי — סגור."""
        mock_db.get_special_day_by_date.return_value = None
        target = date(2026, 4, 15)
        mock_holidays.return_value = {target: "פסח"}
        result = get_status_for_date(target)
        assert result["is_open"] is False
        assert result["reason"] == "פסח"
        assert result["source"] == "holiday"

    @patch("business_hours.db")
    @patch("business_hours._get_israeli_holidays")
    def test_regular_open(self, mock_holidays, mock_db):
        """יום רגיל פתוח — אין חג, אין יום מיוחד."""
        mock_db.get_special_day_by_date.return_value = None
        mock_holidays.return_value = {}
        mock_db.get_business_hours_for_day.return_value = {
            "is_closed": False, "open_time": "09:00", "close_time": "19:00"
        }
        result = get_status_for_date(date(2026, 3, 1))
        assert result["is_open"] is True
        assert result["open_time"] == "09:00"
        assert result["source"] == "regular"

    @patch("business_hours.db")
    @patch("business_hours._get_israeli_holidays")
    def test_regular_closed_day(self, mock_holidays, mock_db):
        """יום שבדרך כלל סגור (למשל שבת)."""
        mock_db.get_special_day_by_date.return_value = None
        mock_holidays.return_value = {}
        mock_db.get_business_hours_for_day.return_value = {
            "is_closed": True, "open_time": None, "close_time": None
        }
        result = get_status_for_date(date(2026, 2, 28))  # Saturday
        assert result["is_open"] is False
        assert result["source"] == "regular"

    @patch("business_hours.db")
    @patch("business_hours._get_israeli_holidays")
    def test_erev_chag(self, mock_holidays, mock_db):
        """ערב חג — פתוח עם הערה."""
        target = date(2026, 4, 14)
        tomorrow = date(2026, 4, 15)
        mock_db.get_special_day_by_date.return_value = None
        mock_holidays.return_value = {tomorrow: "פסח"}
        mock_db.get_business_hours_for_day.return_value = {
            "is_closed": False, "open_time": "09:00", "close_time": "19:00"
        }
        result = get_status_for_date(target)
        assert result["is_open"] is True
        assert "ערב" in result["reason"]
        assert result["source"] == "erev_chag"

    @patch("business_hours.db")
    @patch("business_hours._get_israeli_holidays")
    def test_erev_chag_on_closed_day_stays_closed(self, mock_holidays, mock_db):
        """ערב חג ביום שבדרך כלל סגור — נשאר סגור."""
        target = date(2026, 4, 14)
        tomorrow = date(2026, 4, 15)
        mock_db.get_special_day_by_date.return_value = None
        mock_holidays.return_value = {tomorrow: "פסח"}
        mock_db.get_business_hours_for_day.return_value = {
            "is_closed": True, "open_time": None, "close_time": None
        }
        result = get_status_for_date(target)
        assert result["is_open"] is False
        assert result["source"] == "regular"


class TestFormatClosedMessage:
    def test_holiday_message(self):
        msg = _format_closed_message(
            {"reason": "יום כיפור", "source": "holiday", "day_name": "שבת", "notes": ""},
            None,
        )
        assert "יום כיפור" in msg

    def test_with_next_opening(self):
        msg = _format_closed_message(
            {"reason": "", "source": "regular", "day_name": "שבת", "notes": ""},
            "מחר (ראשון) בשעה 09:00",
        )
        assert "09:00" in msg

    def test_regular_closed_no_reason(self):
        msg = _format_closed_message(
            {"reason": "", "source": "regular", "day_name": "שבת", "notes": ""},
            None,
        )
        assert "סגור" in msg


class TestWeeklySchedule:
    @patch("business_hours.db")
    def test_empty_schedule(self, mock_db):
        mock_db.get_all_business_hours.return_value = []
        text = get_weekly_schedule_text()
        assert "לא הוגדרו" in text

    @patch("business_hours.db")
    def test_full_schedule(self, mock_db):
        mock_db.get_all_business_hours.return_value = [
            {"day_of_week": 0, "is_closed": False, "open_time": "09:00", "close_time": "17:00"},
            {"day_of_week": 6, "is_closed": True, "open_time": None, "close_time": None},
        ]
        text = get_weekly_schedule_text()
        assert "ראשון" in text
        assert "09:00" in text
        assert "סגור" in text
