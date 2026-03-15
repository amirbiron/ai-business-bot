"""
Intent Detection Module — classifies user messages to optimize routing.

Supported intents (checked in priority order):
  GREETING              — "Hi", "Hello", "שלום"           → Direct response (no RAG)
  FAREWELL              — "Thanks", "Bye", "תודה"         → Direct response + feedback
  BUSINESS_HOURS        — "Are you open?", "שעות פתיחה"   → Direct response (hours status)
  PRICING               — "How much?", "כמה עולה?"       → Targeted RAG (pricing)
  APPOINTMENT_BOOKING   — "Want appointment", "רוצה תור"  → Trigger booking flow
  APPOINTMENT_CANCEL    — "Want to cancel", "לבטל תור"    → Trigger cancellation flow
  HUMAN_AGENT           — "תעביר לנציג", "talk to agent"  → Direct handoff to human
  GENERAL               — Everything else                 → Full RAG (current behavior)

Uses keyword matching for speed — no LLM call needed for classification.
"""

import re
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class Intent(Enum):
    GREETING = "greeting"
    FAREWELL = "farewell"
    BUSINESS_HOURS = "business_hours"
    APPOINTMENT_BOOKING = "appointment_booking"
    APPOINTMENT_CANCEL = "appointment_cancel"
    PRICING = "pricing"
    COMPLAINT = "complaint"
    HUMAN_AGENT = "human_agent"
    LOCATION = "location"
    GENERAL = "general"


# ─── Keyword patterns per intent ─────────────────────────────────────────────
# Each pattern is compiled as case-insensitive. Hebrew keywords are included
# alongside English so the bot handles bilingual input naturally.

_INTENT_PATTERNS: list[tuple[Intent, re.Pattern]] = [
    # Greeting — short salutations only
    (
        Intent.GREETING,
        re.compile(
            r"^("
            r"hi|hello|hey|hiya|good morning|good evening|good afternoon"
            r"|שלום|היי|הי|בוקר טוב|ערב טוב|צהריים טובים|מה נשמע|מה קורה|אהלן|הלו"
            r")[.!?\s]*$",
            re.IGNORECASE,
        ),
    ),
    # Farewell — thanks / goodbye
    (
        Intent.FAREWELL,
        re.compile(
            r"^("
            r"thanks|thank you|bye|goodbye|see you|have a good day|good night"
            r"|תודה|תודה רבה|ביי|ביביי|להתראות|יום טוב|לילה טוב|שבוע טוב|יאללה ביי"
            r")[.!?\s]*$",
            re.IGNORECASE,
        ),
    ),
    # Business hours — "are you open?", "when do you close?", "שעות פתיחה"
    (
        Intent.BUSINESS_HOURS,
        re.compile(
            r"("
            r"are\s*you\s*open|when\s*(do\s*you|are\s*you)\s*(open|close)"
            r"|what\s*(are\s*)?your\s*hours|opening\s*hours|business\s*hours"
            r"|what\s*time\s*(do\s*you|are\s*you)\s*(open|close)"
            r"|is\s*(the\s*)?(store|shop|salon)\s*open"
            r"|שעות\s*פתיחה|שעות\s*פעילות|שעות\s*עבודה"
            r"|מתי\s*(אתם\s*)?(פותחים|סוגרים|פתוחים)"
            r"|אתם\s*פתוחים|פתוח\s*היום|פתוח\s*עכשיו|פתוחים\s*היום|פתוחים\s*עכשיו"
            r"|האם\s*(אתם\s*)?פתוחים|סגור\s*היום|סגורים\s*היום"
            r"|עד\s*מתי\s*(אתם\s*)?(פתוחים|פתוח)|עד\s*כמה\s*(אתם\s*)?פתוחים"
            r"|מה\s*שעות\s*(הפתיחה|הפעילות)"
            r")",
            re.IGNORECASE,
        ),
    ),
    # Pricing question — checked before appointment intents so that compound
    # queries like "כמה עולה לקבוע תור?" route to pricing, not booking.
    (
        Intent.PRICING,
        re.compile(
            r"("
            r"how\s*much|what.*price\b|what.*cost\b|pricing|price\s*list"
            r"|כמה\s*עולה|כמה\s*זה\s*עולה|מה\s*המחיר|מה\s*העלות|מחיר|מחירון|מחירים"
            r"|כמה\s*יעלה|כמה\s*כסף|עלות|תעריף|תעריפים"
            r")",
            re.IGNORECASE,
        ),
    ),
    # Appointment booking — expressed desire to book
    (
        Intent.APPOINTMENT_BOOKING,
        re.compile(
            r"("
            r"book\s*(an?\s*)?appointment|make\s*(an?\s*)?appointment"
            r"|schedule\s*(an?\s*)?appointment|set\s*up\s*(an?\s*)?appointment"
            r"|i\s*want\s*(an?\s*)?appointment|i\s*want\s*to\s*book"
            r"|רוצה\s*תור|רוצה\s*לקבוע\s*תור|לקבוע\s*תור|אפשר\s*תור|אפשר\s*לקבוע\s*תור"
            r"|קביעת\s*תור|לזמן\s*תור|אני\s*רוצה\s*לקבוע\s*תור"
            r"|בואו\s*נקבע\s*תור|יש\s*תורים\s*פנויים|מתי\s*אפשר\s*לקבוע\s*תור"
            r")",
            re.IGNORECASE,
        ),
    ),
    # Appointment cancellation
    (
        Intent.APPOINTMENT_CANCEL,
        re.compile(
            r"("
            r"cancel\s*(my\s*)?appointment|cancel\s*(my\s*)?booking"
            r"|i\s*want\s*to\s*cancel\s*(my\s*)?(appointment|booking|the\s*appointment)"
            r"|לבטל\s*(את\s*)?ה?תור|ביטול\s*(ה)?תור|רוצה\s*לבטל\s*(את\s*)?ה?תור|אני\s*מבטל\s*(את\s*)?ה?תור"
            r"|אני\s*רוצה\s*לבטל\s*את\s*התור|אני\s*צריך\s*לבטל\s*(את\s*)?ה?תור"
            r")",
            re.IGNORECASE,
        ),
    ),
    # Human agent — בקשה מפורשת לדבר עם נציג אנושי
    (
        Intent.HUMAN_AGENT,
        re.compile(
            r"("
            # אנגלית
            r"talk\s*to\s*(an?\s*)?(human|person|agent|representative|someone)"
            r"|i\s*need\s*(an?\s*)?(human|person|agent)"
            r"|transfer\s*(me\s*)?(to\s*)?(an?\s*)?(human|agent|representative)"
            r"|can\s*i\s*(speak|talk)\s*(to|with)\s*(an?\s*)?(human|person|agent|representative)"
            # עברית — בקשות נציג מפורשות
            r"|תעביר\s*(אותי\s*)?(ל)?נציג|אדם\s*אמיתי"
            r"|לדבר\s*עם\s*(מישהו|בנאדם|נציג|אדם)"
            r"|אני\s*רוצה\s*(לדבר\s*עם\s*)?(נציג|בנאדם|אדם)"
            r"|תן\s*לי\s*נציג|תני\s*לי\s*נציג"
            r"|אפשר\s*נציג|אפשר\s*לדבר\s*עם\s*(נציג|מישהו)"
            r"|תעבירו\s*(אותי\s*)?(ל)?נציג"
            r"|רוצה\s*נציג"
            r"|^נציג[.!?\s]*$"
            r")",
            re.IGNORECASE,
        ),
    ),
    # Complaint — לקוח מתוסכל, ינותב לנציג אנושי (I1)
    (
        Intent.COMPLAINT,
        re.compile(
            r"("
            # אנגלית — תלונות כלליות
            r"i\s*(want\s*to\s*)?complain|complaint|not\s*happy|not\s*satisfied|terrible\s*service"
            r"|bad\s*service|worst\s*service|awful|disgusting|unacceptable|ridiculous|rip\s*off"
            r"|i\s*want\s*a\s*refund|give\s*me\s*my\s*money\s*back|waste\s*of\s*(time|money)"
            # עברית — תלונות ותסכול כללי
            r"|אני\s*לא\s*מרוצה|לא\s*מרוצה|יש\s*לי\s*בעיה|רוצה\s*להתלונן|תלונה"
            r"|שירות\s*גרוע|שירות\s*נוראי|מאוכזב|מאוכזבת|אני\s*כועס|אני\s*כועסת"
            r"|לא\s*בסדר|חוויה\s*רעה|חוויה\s*גרועה"
            # עברית — ביטויי תסכול וסלנג
            r"|אוי\s*נו|באסה|דבילי|שירות\s*על\s*הפנים|לא\s*עונה\s*על\s*השאלה"
            r"|בושה|בושה\s*וחרפה|איזה\s*זלזול|שירות\s*פח"
            r"|עושים\s*צחוק|עושה\s*צחוק"
            # עברית — בקשות זיכוי/ביטול/נטישה
            r"|תבטלו\s*את\s*ההזמנה|רוצה\s*זיכוי|תחזירו\s*לי\s*את\s*הכסף"
            r"|אני\s*עוזב|לא\s*קונה\s*(אצלכם|פה)\s*יותר"
            # עברית — המתנה ואי-מענה
            r"|מחכה\s*כבר\s*שעות|אף\s*אחד\s*לא\s*עונה|לא\s*מגיבים"
            r"|כבר\s*שעה\s*שאני\s*מחכה|מתי\s*כבר\s*תענו"
            r")",
            re.IGNORECASE,
        ),
    ),
    # Location — שאלות על כתובת ומיקום (I3)
    (
        Intent.LOCATION,
        re.compile(
            r"("
            r"where\s*are\s*you|what.*address|how\s*(do\s*i\s*)?get\s*there|your\s*location|directions"
            r"|איפה\s*אתם|מה\s*הכתובת|כתובת|איך\s*מגיעים|איך\s*אפשר\s*להגיע|מיקום|היכן\s*אתם"
            r"|איפה\s*(ה)?(חנות|סלון|עסק|מקום)|הגעה"
            r")",
            re.IGNORECASE,
        ),
    ),
]


def detect_intent(message: str) -> Intent:
    """
    Classify a user message into an intent using keyword matching.

    The function iterates through intent patterns in priority order.
    Greeting and farewell patterns require a full-string match (anchored)
    so that longer sentences like "Hi, how much does a haircut cost?" are
    not misclassified as a greeting.

    Args:
        message: The raw user message text.

    Returns:
        The detected Intent enum value.
    """
    text = message.strip()
    if not text:
        return Intent.GENERAL

    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(text):
            logger.info("Intent detected: %s for message: '%s'", intent.value, text[:60])
            return intent

    logger.info("Intent detected: general for message: '%s'", text[:60])
    return Intent.GENERAL


# ─── Direct responses (no RAG needed) ────────────────────────────────────────

_GREETING_RESPONSES = [
    "שלום! 👋 ברוכים הבאים. איך אפשר לעזור לכם היום?",
]

_FAREWELL_RESPONSES = [
    "תודה שפניתם אלינו! 😊 אם תצטרכו עוד משהו, אנחנו כאן.\n\n"
    "נשמח לשמוע מכם — איך הייתה החוויה שלכם?",
]


def get_direct_response(intent: Intent) -> str | None:
    """
    Return a canned response for intents that don't require RAG.

    Returns None for intents that should go through the RAG pipeline.
    """
    if intent == Intent.GREETING:
        return _GREETING_RESPONSES[0]
    if intent == Intent.FAREWELL:
        return _FAREWELL_RESPONSES[0]
    return None
