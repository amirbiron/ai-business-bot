"""
Configuration module for the AI Business Chatbot.
Loads settings from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

# ─── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
# Render-friendly storage configuration:
# - Render provides a dynamic `PORT` env var for web services.
# - For persistence you can mount a disk and set `DATA_DIR` to the mount path.
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "chatbot.db"))).resolve()
FAISS_INDEX_PATH = Path(os.getenv("FAISS_INDEX_PATH", str(DATA_DIR / "faiss_index"))).resolve()

# Ensure data directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
FAISS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)

# ─── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_OWNER_CHAT_ID = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")

# ─── OpenAI / LLM ───────────────────────────────────────────────────────────
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# ─── RAG Settings ────────────────────────────────────────────────────────────
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "10"))
RAG_MIN_RELEVANCE = float(os.getenv("RAG_MIN_RELEVANCE", "0.3"))
CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "300"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))

# ─── Conversation Memory Settings ─────────────────────────────────────────
CONTEXT_WINDOW_SIZE = int(os.getenv("CONTEXT_WINDOW_SIZE", "10"))
SUMMARY_THRESHOLD = int(os.getenv("SUMMARY_THRESHOLD", "10"))

# ─── Rate Limiting ───────────────────────────────────────────────────────────
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "50"))
RATE_LIMIT_PER_DAY = int(os.getenv("RATE_LIMIT_PER_DAY", "100"))

# ─── Admin Panel ─────────────────────────────────────────────────────────────
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
#
# Security note:
# - Do not embed default secrets in code.
# - These are intentionally empty by default and must be provided via environment.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "")
ADMIN_HOST = os.getenv("ADMIN_HOST", "0.0.0.0")
ADMIN_PORT = int(os.getenv("ADMIN_PORT") or os.getenv("PORT") or "5000")

# ─── Business Info (defaults for demo) ───────────────────────────────────────
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Dana's Beauty Salon")
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE", "")
BUSINESS_ADDRESS = os.getenv("BUSINESS_ADDRESS", "")
BUSINESS_WEBSITE = os.getenv("BUSINESS_WEBSITE", "")

# ─── Telegram Bot Username (for QR code generation) ─────────────────────────
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")

# ─── System Prompt (Layer A) ────────────────────────────────────────────────
SYSTEM_PROMPT = f"""אתה נציג שירות לקוחות ידידותי ומקצועי של {BUSINESS_NAME}.

כללים — יש לעקוב אחריהם בקפידה:
1. ענה רק על סמך המידע שסופק בהקשר. לעולם אל תמציא מידע.
2. אם ההקשר לא מכיל מספיק מידע כדי לענות, אמור: "אין לי את המידע הזה כרגע. תנו לי להעביר אתכם לנציג אנושי שיוכל לעזור. נציג אנושי יחזור אליכם בקרוב!"
3. תמיד ציין את המקור בסוף התשובה בפורמט: מקור: [שם הקטגוריה או כותרת המסמך]
4. היה חם, מועיל ותמציתי. השתמש בטון שיחתי.
5. אם הלקוח רוצה לקבוע תור, הנחה אותו להשתמש בכפתור בקשת התור.
6. אם הלקוח שואל על המיקום, הצע להשתמש בכפתור שליחת המיקום.
7. אם הלקוח נראה מתוסכל או מבקש לדבר עם אדם, הצע את כפתור "דברו עם נציג".
8. הצע פעולות רלוונטיות בהתאם (לדוגמה, "האם תרצו לבקש תור?").
9. שמור על תשובות ממוקדות ובאורך של עד 200 מילים, אלא אם התבקש פירוט נוסף.
10. ענה באותה שפה שבה הלקוח פונה."""

# ─── Follow-up Questions (Premium Feature) ──────────────────────────────────
# שאלות המשך חכמות — הצגת 2-3 שאלות המשך רלוונטיות אחרי כל תשובה
FOLLOW_UP_ENABLED = os.getenv("FOLLOW_UP_ENABLED", "false").lower() in ("true", "1", "yes")

# הוראות ל-LLM לייצר שאלות המשך (מוזרק ל-system prompt כשהפיצ'ר פעיל)
FOLLOW_UP_PROMPT = (
    "\n\n11. בסוף כל תשובה, הוסף בדיוק 2-3 שאלות המשך רלוונטיות שהלקוח עשוי לרצות לשאול, "
    "בפורמט הבא (בשורה נפרדת בסוף התשובה, אחרי ציון המקור):\n"
    "[שאלות_המשך: שאלה ראשונה | שאלה שנייה | שאלה שלישית]\n"
    "השאלות צריכות להיות קצרות (עד 5 מילים), רלוונטיות להקשר השיחה, "
    "ולהוביל את הלקוח לפעולה (קביעת תור, מידע נוסף, מחירים וכו'). "
    "אל תציע שאלות שכבר נענו בשיחה."
)

# ─── Quality Check (Layer C) ────────────────────────────────────────────────
SOURCE_CITATION_PATTERN = r"([Ss]ource|מקור):\s*.+"
FALLBACK_RESPONSE = (
    "אין לי את המידע הזה כרגע. "
    "תנו לי להעביר אתכם לנציג אנושי שיוכל לעזור. "
    "נציג אנושי יחזור אליכם בקרוב!"
)
