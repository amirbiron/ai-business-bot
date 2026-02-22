"""
appointment_notifications — התראות סטטוס אוטומטיות לתורים.

שולח הודעת טלגרם ללקוח כשבעל העסק משנה סטטוס תור
(pending → confirmed / cancelled) דרך פאנל הניהול.

ראה: https://github.com/amirbiron/ai-business-bot/issues/80
"""

import logging

from live_chat_service import send_telegram_message
from config import BUSINESS_NAME

logger = logging.getLogger(__name__)


def _build_confirmed_message(
    service: str,
    date: str,
    time: str,
    owner_message: str = "",
) -> str:
    """בניית הודעת אישור תור."""
    lines = [
        f"התור שלך ב{BUSINESS_NAME} אושר! ✅",
        "",
        f"📋 שירות: {service}",
        f"📅 תאריך: {date}",
        f"🕐 שעה: {time}",
    ]
    if owner_message:
        lines += ["", f"💬 {owner_message}"]
    lines += ["", "נתראה! 😊"]
    return "\n".join(lines)


def _build_cancelled_message(
    service: str,
    date: str,
    time: str,
    owner_message: str = "",
) -> str:
    """בניית הודעת ביטול תור."""
    lines = [
        f"התור שלך ב{BUSINESS_NAME} בוטל ❌",
        "",
        f"📋 שירות: {service}",
        f"📅 תאריך: {date}",
        f"🕐 שעה: {time}",
    ]
    if owner_message:
        lines += ["", f"💬 {owner_message}"]
    lines += ["", "לקביעת תור חדש, שלחו /book"]
    return "\n".join(lines)


# מיפוי סטטוס → פונקציית בניית הודעה
_MESSAGE_BUILDERS = {
    "confirmed": _build_confirmed_message,
    "cancelled": _build_cancelled_message,
}


def notify_appointment_status(appt: dict, owner_message: str = "") -> bool:
    """שליחת התראת סטטוס תור ללקוח בטלגרם.

    Parameters
    ----------
    appt : dict
        רשומת התור מה-DB (חייבת לכלול user_id, status, service,
        preferred_date, preferred_time).
    owner_message : str, optional
        הודעה אישית מבעל העסק שתצורף להתראה.

    Returns
    -------
    bool
        True אם ההודעה נשלחה בהצלחה, False אחרת.
    """
    status = appt.get("status", "")
    builder = _MESSAGE_BUILDERS.get(status)
    if builder is None:
        # אין התראה לסטטוס pending — רק לשינויים
        logger.debug(
            "Skipping notification for appointment #%s — status '%s' has no template",
            appt.get("id"), status,
        )
        return False

    user_id = appt.get("user_id")
    if not user_id:
        logger.warning(
            "Cannot notify — appointment #%s has no user_id", appt.get("id"),
        )
        return False

    text = builder(
        service=appt.get("service", ""),
        date=appt.get("preferred_date", ""),
        time=appt.get("preferred_time", ""),
        owner_message=owner_message.strip(),
    )

    success = send_telegram_message(user_id, text)
    if success:
        logger.info(
            "Sent %s notification to user %s for appointment #%s",
            status, user_id, appt.get("id"),
        )
    else:
        logger.error(
            "Failed to send %s notification to user %s for appointment #%s",
            status, user_id, appt.get("id"),
        )
    return success
