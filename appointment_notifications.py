"""
appointment_notifications — התראות סטטוס אוטומטיות לתורים.

שולח הודעת טלגרם ללקוח כשבעל העסק משנה סטטוס תור
(pending → confirmed / cancelled) דרך פאנל הניהול.
כולל גם תזכורות אוטומטיות יום לפני התור.

ראה: https://github.com/amirbiron/ai-business-bot/issues/80
"""

import logging
from datetime import datetime, timedelta

from live_chat_service import send_telegram_message
from config import BUSINESS_NAME
import database as db

logger = logging.getLogger(__name__)


def _build_confirmed_message(
    service: str,
    date: str,
    time: str,
    owner_message: str = "",
) -> str:
    """בניית הודעת אישור תור."""
    lines = [
        f"התור שלך ב{BUSINESS_NAME} אושר ✅",
        "",
        f"📋 <b>שירות:</b> {service}",
        f"📅 <b>תאריך:</b> {date}",
        f"🕐 <b>שעה:</b> {time}",
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
        f"😑 התור שלך ב{BUSINESS_NAME} בוטל",
        "",
        f"📋 <b>שירות:</b> {service}",
        f"📅 <b>תאריך:</b> {date}",
        f"🕐 <b>שעה:</b> {time}",
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

    success = send_telegram_message(user_id, text, parse_mode="HTML")
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


# ── תזכורות אוטומטיות ──────────────────────────────────────────────────────


def _build_reminder_message(
    service: str,
    date: str,
    time: str,
) -> str:
    """בניית הודעת תזכורת יום לפני התור."""
    lines = [
        f"🔔 תזכורת: יש לך תור מחר ב{BUSINESS_NAME}!",
        "",
        f"📋 <b>שירות:</b> {service}",
        f"📅 <b>תאריך:</b> {date}",
        f"🕐 <b>שעה:</b> {time}",
        "",
        "נתראה! 😊",
    ]
    return "\n".join(lines)


def send_appointment_reminders() -> dict:
    """שליחת תזכורות לתורים מאושרים של מחר.

    בודק הגדרות (enabled/time) ושולח רק אם:
    - תזכורות מופעלות
    - השעה הנוכחית (ישראל) >= שעת השליחה המוגדרת
    - לתור לא נשלחה תזכורת עדיין

    Returns: {"sent": int, "failed": int, "skipped": str | None}
    """
    from zoneinfo import ZoneInfo
    israel_tz = ZoneInfo("Asia/Jerusalem")

    settings = db.get_bot_settings()
    if not settings.get("reminder_enabled", 1):
        return {"sent": 0, "failed": 0, "skipped": "disabled"}

    now_il = datetime.now(israel_tz)
    reminder_time_str = settings.get("reminder_time", "10:00")
    try:
        reminder_hour, reminder_minute = map(int, reminder_time_str.split(":"))
    except (ValueError, AttributeError):
        reminder_hour, reminder_minute = 10, 0

    # שולחים רק אחרי השעה המוגדרת
    if now_il.hour < reminder_hour or (now_il.hour == reminder_hour and now_il.minute < reminder_minute):
        return {"sent": 0, "failed": 0, "skipped": "not_yet"}

    # תורים של מחר (לפי שעון ישראל)
    tomorrow = (now_il + timedelta(days=1)).strftime("%Y-%m-%d")
    appointments = db.get_appointments_for_reminder(tomorrow)

    sent = 0
    failed = 0
    for appt in appointments:
        try:
            text = _build_reminder_message(
                service=appt.get("service", ""),
                date=appt.get("preferred_date", ""),
                time=appt.get("preferred_time", ""),
            )
            success = send_telegram_message(appt["user_id"], text, parse_mode="HTML")
            if success:
                db.mark_reminder_sent(appt["id"])
                sent += 1
                logger.info("Sent reminder to user %s for appointment #%s", appt["user_id"], appt["id"])
            else:
                failed += 1
                logger.error("Failed to send reminder to user %s for appointment #%s", appt["user_id"], appt["id"])
        except Exception:
            failed += 1
            logger.error("Error sending reminder for appointment #%s", appt["id"], exc_info=True)

    if sent or failed:
        logger.info("Appointment reminders: %d sent, %d failed (target date: %s)", sent, failed, tomorrow)
    return {"sent": sent, "failed": failed, "skipped": None}
