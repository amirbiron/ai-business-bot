"""
טסטים לתזכורות תורים אוטומטיות.
"""

import os
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake-token")
os.environ.setdefault("OPENAI_API_KEY", "fake-key")

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


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


class TestSendAppointmentReminders:
    """טסטים ל-send_appointment_reminders — הלוגיקה המרכזית."""

    def _setup_confirmed_appointment(self, db, date, time="10:00"):
        """יצירת תור מאושר לצורך טסט."""
        appt_id = db.create_appointment("u1", "ישראל", service="תספורת",
                                         preferred_date=date, preferred_time=time)
        db.update_appointment_status(appt_id, "confirmed")
        return appt_id

    def test_sends_reminder_for_tomorrow(self, db):
        """שולח תזכורת לתור מאושר של מחר."""
        tomorrow = (datetime.now(ISRAEL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        appt_id = self._setup_confirmed_appointment(db, tomorrow)

        # מוודא שהתזכורות מופעלות ושהשעה כבר הגיעה
        db.update_bot_settings("friendly", "", reminder_enabled=True, reminder_time="00:00")

        with patch("appointment_notifications.send_telegram_message", return_value=True) as mock_send:
            from appointment_notifications import send_appointment_reminders
            result = send_appointment_reminders()

        assert result["sent"] == 1
        assert result["failed"] == 0
        assert result["skipped"] is None
        mock_send.assert_called_once()
        # תזכורת סומנה כנשלחה
        assert db.get_appointment(appt_id)["reminder_sent"] == 1

    def test_skips_when_disabled(self, db):
        """לא שולח כשהתזכורות מכובות."""
        tomorrow = (datetime.now(ISRAEL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        self._setup_confirmed_appointment(db, tomorrow)
        db.update_bot_settings("friendly", "", reminder_enabled=False, reminder_time="00:00")

        with patch("appointment_notifications.send_telegram_message") as mock_send:
            from appointment_notifications import send_appointment_reminders
            result = send_appointment_reminders()

        assert result["skipped"] == "disabled"
        mock_send.assert_not_called()

    def test_skips_pending_appointments(self, db):
        """לא שולח תזכורת לתורים בסטטוס pending (לא מאושרים)."""
        tomorrow = (datetime.now(ISRAEL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        db.create_appointment("u1", "ישראל", preferred_date=tomorrow, preferred_time="10:00")
        db.update_bot_settings("friendly", "", reminder_enabled=True, reminder_time="00:00")

        with patch("appointment_notifications.send_telegram_message") as mock_send:
            from appointment_notifications import send_appointment_reminders
            result = send_appointment_reminders()

        assert result["sent"] == 0
        mock_send.assert_not_called()

    def test_skips_already_reminded(self, db):
        """לא שולח תזכורת כפולה."""
        tomorrow = (datetime.now(ISRAEL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        appt_id = self._setup_confirmed_appointment(db, tomorrow)
        db.mark_reminder_sent(appt_id)
        db.update_bot_settings("friendly", "", reminder_enabled=True, reminder_time="00:00")

        with patch("appointment_notifications.send_telegram_message") as mock_send:
            from appointment_notifications import send_appointment_reminders
            result = send_appointment_reminders()

        assert result["sent"] == 0
        mock_send.assert_not_called()

    def test_telegram_failure_doesnt_mark_sent(self, db):
        """כשל בטלגרם — לא מסמן כנשלח כדי שיישלח שוב."""
        tomorrow = (datetime.now(ISRAEL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        appt_id = self._setup_confirmed_appointment(db, tomorrow)
        db.update_bot_settings("friendly", "", reminder_enabled=True, reminder_time="00:00")

        with patch("appointment_notifications.send_telegram_message", return_value=False):
            from appointment_notifications import send_appointment_reminders
            result = send_appointment_reminders()

        assert result["sent"] == 0
        assert result["failed"] == 1
        # לא סומן — ינסה שוב בריצה הבאה
        assert db.get_appointment(appt_id)["reminder_sent"] == 0
