"""
טסטים למודול זיהוי כוונות — intent.py

בודק שכל סוג כוונה מזוהה נכון על סמך מילות מפתח
בעברית ובאנגלית, וש-edge cases מטופלים כראוי.
"""

import pytest
from intent import Intent, detect_intent, get_direct_response


# ── ברכות ──────────────────────────────────────────────────────────────────

class TestGreeting:
    @pytest.mark.parametrize("msg", [
        "שלום", "היי", "הי", "בוקר טוב", "ערב טוב", "מה נשמע",
        "אהלן", "הלו",
        "hi", "hello", "hey", "Hi!", "Hello.",
        "good morning", "good evening",
    ])
    def test_greeting_detected(self, msg):
        assert detect_intent(msg) == Intent.GREETING

    @pytest.mark.parametrize("msg", [
        "שלום, כמה עולה תספורת?",
        "hi how much is a haircut",
        "hello I want to book an appointment",
    ])
    def test_greeting_with_follow_up_not_greeting(self, msg):
        """ברכה עם שאלה נוספת לא צריכה להסתווג כברכה."""
        assert detect_intent(msg) != Intent.GREETING

    def test_greeting_has_direct_response(self):
        resp = get_direct_response(Intent.GREETING)
        assert resp is not None
        assert len(resp) > 0


# ── פרידה ──────────────────────────────────────────────────────────────────

class TestFarewell:
    @pytest.mark.parametrize("msg", [
        "תודה", "תודה רבה", "ביי", "להתראות", "יום טוב",
        "thanks", "thank you", "bye", "goodbye",
    ])
    def test_farewell_detected(self, msg):
        assert detect_intent(msg) == Intent.FAREWELL

    def test_farewell_has_direct_response(self):
        resp = get_direct_response(Intent.FAREWELL)
        assert resp is not None


# ── שעות פעילות ────────────────────────────────────────────────────────────

class TestBusinessHours:
    @pytest.mark.parametrize("msg", [
        "שעות פתיחה", "מתי אתם פותחים?", "אתם פתוחים?",
        "פתוח היום?", "פתוחים עכשיו?", "עד מתי פתוחים?",
        "are you open", "what are your hours", "business hours",
        "is the salon open",
    ])
    def test_business_hours_detected(self, msg):
        assert detect_intent(msg) == Intent.BUSINESS_HOURS

    def test_business_hours_no_direct_response(self):
        """שעות פעילות עוברות דרך RAG — אין תשובה ישירה."""
        assert get_direct_response(Intent.BUSINESS_HOURS) is None


# ── מחיר ───────────────────────────────────────────────────────────────────

class TestPricing:
    @pytest.mark.parametrize("msg", [
        "כמה עולה תספורת?", "מה המחיר?", "מחירון",
        "how much is a haircut?", "what's the price?", "pricing",
    ])
    def test_pricing_detected(self, msg):
        assert detect_intent(msg) == Intent.PRICING

    def test_pricing_before_booking(self):
        """'כמה עולה לקבוע תור' — מחיר מנצח את קביעת תור."""
        assert detect_intent("כמה עולה לקבוע תור?") == Intent.PRICING


# ── קביעת תור ──────────────────────────────────────────────────────────────

class TestAppointmentBooking:
    @pytest.mark.parametrize("msg", [
        "רוצה תור", "רוצה לקבוע תור", "אפשר תור?",
        "book an appointment", "I want to book",
    ])
    def test_booking_detected(self, msg):
        assert detect_intent(msg) == Intent.APPOINTMENT_BOOKING


# ── ביטול תור ──────────────────────────────────────────────────────────────

class TestAppointmentCancel:
    @pytest.mark.parametrize("msg", [
        "לבטל תור", "ביטול תור", "רוצה לבטל את התור",
        "cancel my appointment", "I want to cancel my booking",
    ])
    def test_cancel_detected(self, msg):
        assert detect_intent(msg) == Intent.APPOINTMENT_CANCEL


# ── כללי ───────────────────────────────────────────────────────────────────

class TestGeneral:
    @pytest.mark.parametrize("msg", [
        "מה הכתובת שלכם?",
        "ספרו לי על השירותים",
        "what services do you offer?",
        "",
        "   ",
    ])
    def test_general_detected(self, msg):
        assert detect_intent(msg) == Intent.GENERAL

    def test_general_no_direct_response(self):
        assert get_direct_response(Intent.GENERAL) is None
