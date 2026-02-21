"""
Intent Detection Module — classifies user messages to optimize routing.

Supported intents:
  GREETING              — "Hi", "Hello", "שלום"           → Direct response (no RAG)
  FAREWELL              — "Thanks", "Bye", "תודה"         → Direct response + feedback
  APPOINTMENT_BOOKING   — "Want appointment", "רוצה תור"  → Trigger booking flow
  APPOINTMENT_CANCEL    — "Want to cancel", "לבטל תור"    → Trigger cancellation flow
  PRICING               — "How much?", "כמה עולה?"       → Targeted RAG (pricing)
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
    APPOINTMENT_BOOKING = "appointment_booking"
    APPOINTMENT_CANCEL = "appointment_cancel"
    PRICING = "pricing"
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
    # Pricing question
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
