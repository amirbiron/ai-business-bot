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
        "ספרו לי על השירותים",
        "what services do you offer?",
        "",
        "   ",
    ])
    def test_general_detected(self, msg):
        assert detect_intent(msg) == Intent.GENERAL

    def test_general_no_direct_response(self):
        assert get_direct_response(Intent.GENERAL) is None


# ── בקשת נציג ────────────────────────────────────────────────────────────

class TestHumanAgent:
    @pytest.mark.parametrize("msg", [
        # עברית
        "נציג",
        "תעביר אותי לנציג",
        "תעבירו אותי לנציג",
        "אני רוצה לדבר עם בנאדם",
        "אפשר נציג",
        "אפשר לדבר עם מישהו",
        "לדבר עם מישהו",
        "רוצה נציג",
        "תן לי נציג",
        "תני לי נציג",
        "אדם אמיתי",
        "אני רוצה נציג",
        # אנגלית
        "talk to a human",
        "I need an agent",
        "transfer me to a representative",
        "can I speak to a person",
    ])
    def test_human_agent_detected(self, msg):
        assert detect_intent(msg) == Intent.HUMAN_AGENT

    def test_human_agent_no_direct_response(self):
        """בקשת נציג מטופלת ב-handler ייעודי, לא תגובה ישירה."""
        assert get_direct_response(Intent.HUMAN_AGENT) is None

    def test_complaint_not_triggered_by_agent_request(self):
        """ביטוי של בקשת נציג לא צריך להיתפס כתלונה."""
        assert detect_intent("תעביר לנציג") != Intent.COMPLAINT
        assert detect_intent("רוצה נציג") != Intent.COMPLAINT


# ── תלונה ──────────────────────────────────────────────────────────────────

class TestComplaint:
    @pytest.mark.parametrize("msg", [
        # עברית — תלונות בסיסיות
        "אני לא מרוצה",
        "רוצה להתלונן",
        "שירות גרוע",
        "יש לי בעיה",
        "שירות נוראי",
        "מאוכזב",
        "מאוכזבת",
        "חוויה רעה",
        # עברית — סלנג ותסכול
        "אוי נו",
        "באסה",
        "דבילי",
        "שירות על הפנים",
        "לא עונה על השאלה",
        "בושה",
        "בושה וחרפה",
        "איזה זלזול",
        "שירות פח",
        "עושים צחוק",
        "עושה צחוק",
        # עברית — בקשות זיכוי/נטישה
        "תבטלו את ההזמנה",
        "רוצה זיכוי",
        "תחזירו לי את הכסף",
        "אני עוזב",
        "לא קונה אצלכם יותר",
        "לא קונה פה יותר",
        # עברית — המתנה ואי-מענה
        "מחכה כבר שעות",
        "אף אחד לא עונה",
        # אנגלית
        "i want to complain",
        "terrible service",
        "i want a refund",
        "give me my money back",
    ])
    def test_complaint_detected(self, msg):
        assert detect_intent(msg) == Intent.COMPLAINT

    def test_complaint_no_direct_response(self):
        """תלונות עוברות ל-handler ייעודי, לא תגובה ישירה."""
        assert get_direct_response(Intent.COMPLAINT) is None


# ── מיקום ──────────────────────────────────────────────────────────────────

class TestLocation:
    @pytest.mark.parametrize("msg", [
        "מה הכתובת שלכם?",
        "איפה אתם?",
        "איך מגיעים אליכם?",
        "where are you?",
        "what is your address?",
    ])
    def test_location_detected(self, msg):
        assert detect_intent(msg) == Intent.LOCATION

    def test_location_no_direct_response(self):
        """שאלות מיקום עוברות דרך RAG, לא תגובה ישירה."""
        assert get_direct_response(Intent.LOCATION) is None
