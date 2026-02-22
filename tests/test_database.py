"""
טסטים למודול בסיס הנתונים — database.py

משתמש ב-SQLite בקובץ זמני. מאתחל את הסכימה המלאה דרך init_db().
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
        importlib.reload(database)  # כדי שישתמש בנתיב החדש
        database.init_db()
        yield database


class TestKBEntries:
    def test_add_and_get(self, db):
        entry_id = db.add_kb_entry("שירותים", "תספורות", "תספורת גברים 50 ש\"ח")
        assert entry_id > 0

        entry = db.get_kb_entry(entry_id)
        assert entry is not None
        assert entry["category"] == "שירותים"
        assert entry["title"] == "תספורות"

    def test_update(self, db):
        entry_id = db.add_kb_entry("א", "ב", "תוכן ישן")
        db.update_kb_entry(entry_id, "א", "ב", "תוכן חדש")
        entry = db.get_kb_entry(entry_id)
        assert entry["content"] == "תוכן חדש"

    def test_delete(self, db):
        entry_id = db.add_kb_entry("א", "ב", "ג")
        db.delete_kb_entry(entry_id)
        assert db.get_kb_entry(entry_id) is None

    def test_get_all_entries(self, db):
        db.add_kb_entry("א", "כותרת1", "תוכן1")
        db.add_kb_entry("ב", "כותרת2", "תוכן2")
        entries = db.get_all_kb_entries()
        assert len(entries) == 2

    def test_get_by_category(self, db):
        db.add_kb_entry("שירותים", "שירות1", "...")
        db.add_kb_entry("מידע", "מידע1", "...")
        services = db.get_all_kb_entries(category="שירותים")
        assert len(services) == 1
        assert services[0]["category"] == "שירותים"

    def test_count_entries(self, db):
        assert db.count_kb_entries() == 0
        db.add_kb_entry("א", "ב", "ג")
        assert db.count_kb_entries() == 1

    def test_get_categories(self, db):
        db.add_kb_entry("קטגוריה_א", "כ1", "ת1")
        db.add_kb_entry("קטגוריה_ב", "כ2", "ת2")
        cats = db.get_kb_categories()
        assert "קטגוריה_א" in cats
        assert "קטגוריה_ב" in cats

    def test_count_categories(self, db):
        db.add_kb_entry("א", "כ1", "ת1")
        db.add_kb_entry("ב", "כ2", "ת2")
        assert db.count_kb_categories() == 2


class TestConversations:
    def test_save_and_get(self, db):
        db.save_message("u1", "ישראל", "user", "שלום")
        db.save_message("u1", "ישראל", "assistant", "היי!")
        history = db.get_conversation_history("u1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    def test_limit(self, db):
        for i in range(30):
            db.save_message("u2", "יוסי", "user", f"הודעה {i}")
        history = db.get_conversation_history("u2", limit=10)
        assert len(history) == 10

    def test_unique_users(self, db):
        db.save_message("u1", "ישראל", "user", "שלום")
        db.save_message("u2", "יוסי", "user", "היי")
        users = db.get_unique_users()
        assert len(users) == 2

    def test_count_unique_users(self, db):
        db.save_message("u1", "א", "user", "1")
        db.save_message("u2", "ב", "user", "2")
        assert db.count_unique_users() == 2

    def test_get_username_for_user(self, db):
        db.save_message("u5", "דנה", "user", "שלום")
        assert db.get_username_for_user("u5") == "דנה"
        assert db.get_username_for_user("nonexistent") is None


class TestConversationSummaries:
    def test_save_and_get_summary(self, db):
        db.save_conversation_summary("u1", "סיכום שיחה", 5, last_summarized_message_id=10)
        summary = db.get_latest_summary("u1")
        assert summary is not None
        assert summary["summary_text"] == "סיכום שיחה"
        assert summary["message_count"] == 5

    def test_summary_replaces_previous(self, db):
        db.save_conversation_summary("u1", "סיכום ראשון", 5, last_summarized_message_id=5)
        db.save_conversation_summary("u1", "סיכום שני", 3, last_summarized_message_id=8)
        summary = db.get_latest_summary("u1")
        assert summary["summary_text"] == "סיכום שני"
        # message_count מצטבר
        assert summary["message_count"] == 8

    def test_unsummarized_count(self, db):
        for i in range(15):
            db.save_message("u3", "test", "user", f"msg {i}")
        assert db.get_unsummarized_message_count("u3") == 15

    def test_no_summary_returns_none(self, db):
        assert db.get_latest_summary("nonexistent") is None


class TestAgentRequests:
    def test_create_and_get(self, db):
        req_id = db.create_agent_request("u1", "ישראל", "עזרה")
        req = db.get_agent_request(req_id)
        assert req is not None
        assert req["status"] == "pending"

    def test_update_status(self, db):
        req_id = db.create_agent_request("u1", "ישראל")
        db.update_agent_request_status(req_id, "handled")
        req = db.get_agent_request(req_id)
        assert req["status"] == "handled"

    def test_count_by_status(self, db):
        db.create_agent_request("u1", "א")
        db.create_agent_request("u2", "ב")
        assert db.count_agent_requests("pending") == 2
        assert db.count_agent_requests("handled") == 0


class TestAppointments:
    def test_create_and_get(self, db):
        appt_id = db.create_appointment("u1", "ישראל", service="תספורת")
        appt = db.get_appointment(appt_id)
        assert appt is not None
        assert appt["service"] == "תספורת"
        assert appt["status"] == "pending"

    def test_update_status(self, db):
        appt_id = db.create_appointment("u1", "ישראל")
        db.update_appointment_status(appt_id, "confirmed")
        assert db.get_appointment(appt_id)["status"] == "confirmed"

    def test_count(self, db):
        db.create_appointment("u1", "א")
        db.create_appointment("u2", "ב")
        assert db.count_appointments() == 2
        assert db.count_appointments("pending") == 2


class TestBusinessHours:
    def test_upsert_and_get(self, db):
        db.upsert_business_hours(0, "09:00", "17:00", False)
        hours = db.get_business_hours_for_day(0)
        assert hours is not None
        assert hours["open_time"] == "09:00"
        assert hours["close_time"] == "17:00"
        assert hours["is_closed"] == 0

    def test_upsert_update(self, db):
        db.upsert_business_hours(1, "09:00", "17:00", False)
        db.upsert_business_hours(1, "10:00", "18:00", False)
        hours = db.get_business_hours_for_day(1)
        assert hours["open_time"] == "10:00"

    def test_get_all(self, db):
        for day in range(7):
            db.upsert_business_hours(day, "09:00", "17:00", day == 6)
        all_hours = db.get_all_business_hours()
        assert len(all_hours) == 7

    def test_seed_defaults(self, db):
        db.seed_default_business_hours()
        all_hours = db.get_all_business_hours()
        assert len(all_hours) == 7
        # שבת סגור
        saturday = [h for h in all_hours if h["day_of_week"] == 6][0]
        assert saturday["is_closed"] == 1


class TestSpecialDays:
    def test_add_and_get(self, db):
        sd_id = db.add_special_day("2026-03-01", "יום מיוחד", is_closed=True)
        sd = db.get_special_day_by_date("2026-03-01")
        assert sd is not None
        assert sd["name"] == "יום מיוחד"

    def test_replace_on_same_date(self, db):
        db.add_special_day("2026-03-01", "ישן")
        db.add_special_day("2026-03-01", "חדש")
        sd = db.get_special_day_by_date("2026-03-01")
        assert sd["name"] == "חדש"

    def test_delete(self, db):
        sd_id = db.add_special_day("2026-04-01", "חג")
        db.delete_special_day(sd_id)
        assert db.get_special_day_by_date("2026-04-01") is None


class TestVacationMode:
    def test_default_inactive(self, db):
        vacation = db.get_vacation_mode()
        assert vacation["is_active"] == 0

    def test_activate_and_deactivate(self, db):
        db.update_vacation_mode(True, "2026-04-01", "אנחנו בחופשה")
        vacation = db.get_vacation_mode()
        assert vacation["is_active"] == 1
        assert vacation["vacation_end_date"] == "2026-04-01"

        db.update_vacation_mode(False)
        vacation = db.get_vacation_mode()
        assert vacation["is_active"] == 0


class TestLiveChats:
    def test_start_and_check(self, db):
        db.start_live_chat("u1", "ישראל")
        assert db.is_live_chat_active("u1") is True
        assert db.count_active_live_chats() == 1

    def test_end_chat(self, db):
        db.start_live_chat("u1")
        db.end_live_chat("u1")
        assert db.is_live_chat_active("u1") is False

    def test_start_closes_previous(self, db):
        """פתיחת צ'אט חדש סוגרת את הקודם."""
        db.start_live_chat("u1")
        db.start_live_chat("u1")
        assert db.count_active_live_chats() == 1


class TestSubscriptions:
    def test_default_subscribed(self, db):
        assert db.is_user_subscribed("new_user") is True

    def test_unsubscribe(self, db):
        db.ensure_user_subscribed("u1")
        db.unsubscribe_user("u1")
        assert db.is_user_subscribed("u1") is False

    def test_resubscribe(self, db):
        db.ensure_user_subscribed("u1")
        db.unsubscribe_user("u1")
        db.resubscribe_user("u1")
        assert db.is_user_subscribed("u1") is True


class TestReferrals:
    def test_generate_code(self, db):
        code = db.generate_referral_code("u1")
        assert code.startswith("REF_")
        # אותו קוד בקריאה שנייה
        assert db.generate_referral_code("u1") == code

    def test_register_referral(self, db):
        code = db.generate_referral_code("referrer")
        assert db.register_referral(code, "referred") is True

    def test_self_referral_blocked(self, db):
        code = db.generate_referral_code("u1")
        assert db.register_referral(code, "u1") is False

    def test_double_referral_blocked(self, db):
        code = db.generate_referral_code("referrer")
        db.register_referral(code, "referred")
        assert db.register_referral(code, "referred") is False

    def test_referral_stats(self, db):
        stats = db.get_referral_stats()
        assert stats["total_referrals"] == 0
        assert stats["completed_referrals"] == 0

    def test_mark_sent_atomic(self, db):
        """mark_referral_code_as_sent — רק תהליך אחד מצליח."""
        db.generate_referral_code("u1")
        assert db.mark_referral_code_as_sent("u1") is True
        # קריאה שנייה — כבר מסומן
        assert db.mark_referral_code_as_sent("u1") is False


class TestBroadcast:
    def test_create_and_get(self, db):
        bc_id = db.create_broadcast("שלום לכולם!", "all", 100)
        broadcasts = db.get_all_broadcasts()
        assert len(broadcasts) == 1
        assert broadcasts[0]["message_text"] == "שלום לכולם!"
        assert broadcasts[0]["status"] == "queued"

    def test_update_progress(self, db):
        bc_id = db.create_broadcast("הודעה", "all", 50)
        db.update_broadcast_progress(bc_id, 25, 2)
        broadcasts = db.get_all_broadcasts()
        assert broadcasts[0]["sent_count"] == 25
        assert broadcasts[0]["status"] == "sending"

    def test_complete_broadcast(self, db):
        bc_id = db.create_broadcast("הודעה", "all", 10)
        db.complete_broadcast(bc_id, 9, 1)
        broadcasts = db.get_all_broadcasts()
        assert broadcasts[0]["status"] == "completed"
        assert broadcasts[0]["sent_count"] == 9

    def test_fail_broadcast_preserves_counts(self, db):
        """כשנכשל — לא דורס מונים שכבר נכתבו."""
        bc_id = db.create_broadcast("הודעה", "all", 100)
        db.update_broadcast_progress(bc_id, 50, 3)
        db.fail_broadcast(bc_id)  # בלי מונים — שומר על הערכים מ-DB
        broadcasts = db.get_all_broadcasts()
        assert broadcasts[0]["status"] == "failed"
        assert broadcasts[0]["sent_count"] == 50
        assert broadcasts[0]["failed_count"] == 3


class TestBotSettings:
    def test_default_settings(self, db):
        """ברירת מחדל — טון ידידותי, בלי ביטויים מותאמים."""
        settings = db.get_bot_settings()
        assert settings["tone"] == "friendly"
        assert settings["custom_phrases"] == ""

    def test_update_tone(self, db):
        """עדכון טון תקשורת."""
        db.update_bot_settings("formal", "")
        settings = db.get_bot_settings()
        assert settings["tone"] == "formal"

    def test_update_custom_phrases(self, db):
        """עדכון ביטויים מותאמים אישית."""
        db.update_bot_settings("friendly", "אהלן, בשמחה, בכיף")
        settings = db.get_bot_settings()
        assert settings["custom_phrases"] == "אהלן, בשמחה, בכיף"

    def test_update_tone_and_phrases(self, db):
        """עדכון טון וביטויים ביחד."""
        db.update_bot_settings("luxury", "בוודאי, לשירותך")
        settings = db.get_bot_settings()
        assert settings["tone"] == "luxury"
        assert settings["custom_phrases"] == "בוודאי, לשירותך"

    def test_invalid_tone_ignored(self, db):
        """טון לא חוקי — לא מעדכן."""
        db.update_bot_settings("invalid_tone")
        settings = db.get_bot_settings()
        assert settings["tone"] == "friendly"  # נשאר ברירת מחדל
