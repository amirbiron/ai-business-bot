"""
שירות הפניות — לוגיקה משותפת לשליחת קוד הפניה.

מאחד את זרימת generate→mark→build-link→send→unmark
שמשמשת גם את הבוט וגם את פאנל האדמין, עם טקסט אחיד.
"""

import logging
from typing import Optional

from ai_chatbot import database as db
from ai_chatbot.config import TELEGRAM_BOT_USERNAME

logger = logging.getLogger(__name__)


def build_referral_link(code: str) -> str:
    """בניית לינק הפניה (deep link) או קוד בלבד אם אין username."""
    if TELEGRAM_BOT_USERNAME:
        return f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={code}"
    return code


def get_referral_message_text(code: str) -> str:
    """טקסט הודעת ההפניה — מקור אמת יחיד לשני הנתיבים (בוט ואדמין)."""
    link = build_referral_link(code)
    return (
        "🎁 רוצים לשתף עם חבר/ה?\n\n"
        f"שלחו להם את הלינק הזה:\n{link}\n\n"
        "כשהם יקבעו וישלימו תור — "
        "גם אתם וגם הם תקבלו 10% הנחה לחודשיים!"
    )


def try_send_referral_code(user_id: str, send_fn) -> bool:
    """ניסיון אטומי לשלוח קוד הפניה למשתמש.

    send_fn(text: str) -> bool — פונקציית שליחה שמחזירה True בהצלחה.
    מחזיר True אם ההודעה נשלחה, False אחרת (כבר נשלח / נכשל).
    אם השליחה נכשלת — הדגל מתאפס לניסיון חוזר עתידי.
    """
    code = db.generate_referral_code(user_id)
    if not code:
        return False

    if not db.mark_referral_code_as_sent(user_id):
        return False

    text = get_referral_message_text(code)
    try:
        success = send_fn(text)
    except Exception:
        success = False
        logger.error("Exception sending referral code to user %s", user_id, exc_info=True)

    if not success:
        db.unmark_referral_code_sent(user_id)
        logger.error("Failed to send referral code to user %s, flag reset", user_id)
        return False

    return True
