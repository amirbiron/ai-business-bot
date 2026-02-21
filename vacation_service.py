"""
VacationService — שירות מצב חופשה / חירום.

כשמצב חופשה פעיל:
- שאלות מידע כלליות (RAG) ממשיכות לעבוד כרגיל.
- בקשות תורים מקבלות הודעת חופשה עם תאריך חזרה.
- בקשות לנציג אנושי מקבלות הודעת חופשה.

מספק decorator (guard) שנכנס אחרי rate_limit_guard בשרשרת ה-handlers,
כך ש-__wrapped__ מדלג על rate_limit בלבד ועדיין עובר דרך vacation guard.
"""

import logging
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ai_chatbot import database as db

logger = logging.getLogger(__name__)


class VacationService:
    """שירות מרכזי לבדיקת מצב חופשה."""

    @staticmethod
    def is_active() -> bool:
        """בדיקה האם מצב חופשה פעיל."""
        vacation = db.get_vacation_mode()
        return bool(vacation["is_active"])

    @staticmethod
    def get_settings() -> dict:
        """קבלת כל הגדרות מצב חופשה."""
        return db.get_vacation_mode()

    @staticmethod
    def get_booking_message() -> str:
        """הודעה ללקוח שמנסה לקבוע תור בזמן חופשה."""
        vacation = db.get_vacation_mode()
        custom_msg = vacation.get("vacation_message", "").strip()
        end_date = vacation.get("vacation_end_date", "").strip()

        if custom_msg:
            return custom_msg

        if end_date:
            return (
                f"אנחנו בחופשה עד {end_date}.\n"
                f"ניתן לקבוע תורים החל מ-{end_date}.\n"
                "בינתיים, אתם מוזמנים לשאול אותי כל שאלה על השירותים שלנו!"
            )

        return (
            "אנחנו כרגע בחופשה.\n"
            "נחזור בקרוב — עקבו אחרי העדכונים שלנו.\n"
            "בינתיים, אתם מוזמנים לשאול אותי כל שאלה על השירותים שלנו!"
        )

    @staticmethod
    def get_agent_message() -> str:
        """הודעה ללקוח שמבקש נציג אנושי בזמן חופשה."""
        vacation = db.get_vacation_mode()
        end_date = vacation.get("vacation_end_date", "").strip()

        if end_date:
            return (
                f"אנחנו בחופשה עד {end_date}.\n"
                "ניצור קשר כשנחזור.\n"
                "בינתיים, אני יכול לענות על שאלות לגבי השירותים שלנו!"
            )

        return (
            "אנחנו כרגע בחופשה.\n"
            "ניצור קשר כשנחזור.\n"
            "בינתיים, אני יכול לענות על שאלות לגבי השירותים שלנו!"
        )


# ── Bot-Layer Decorators ─────────────────────────────────────────────────────


def vacation_guard_booking(handler):
    """Decorator עבור booking conversation handlers — מחזיר ConversationHandler.END.

    משמש על booking_start כדי שה-ConversationHandler ייסגר כראוי.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not VacationService.is_active():
            return await handler(update, context)

        # חופשה פעילה — שולח הודעת חופשה וסוגר את ה-conversation
        if update.message:
            msg = VacationService.get_booking_message()
            await update.message.reply_text(msg)
        context.user_data.clear()
        return ConversationHandler.END

    return wrapper


def vacation_guard_agent(handler):
    """Decorator עבור handler בקשת נציג — שולח הודעת חופשה ייעודית."""

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not VacationService.is_active():
            return await handler(update, context)

        # חופשה פעילה — הודעת נציג בחופשה
        if update.message:
            msg = VacationService.get_agent_message()
            await update.message.reply_text(msg)
        return

    return wrapper
