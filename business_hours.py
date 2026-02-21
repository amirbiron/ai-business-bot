"""
Business Hours Service — context-aware responses based on operating hours and holidays.

Resolution order:
1. Special days (one-time exceptions, holidays with custom hours)
2. Israeli holiday calendar (auto-calculated via `holidays` library)
3. Regular weekly business hours

All times are in the Asia/Jerusalem timezone.
"""

import logging
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

import holidays as holidays_lib

from ai_chatbot import database as db

logger = logging.getLogger(__name__)

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Hebrew day names (0=Sunday .. 6=Saturday, matching Israeli convention)
DAY_NAMES_HE = {
    0: "ראשון",
    1: "שני",
    2: "שלישי",
    3: "רביעי",
    4: "חמישי",
    5: "שישי",
    6: "שבת",
}

# Map Python weekday (0=Monday) to Israeli day-of-week (0=Sunday)
def _python_weekday_to_israeli(py_weekday: int) -> int:
    """Convert Python's weekday (0=Mon..6=Sun) to Israeli (0=Sun..6=Sat)."""
    return (py_weekday + 1) % 7


def _now_israel() -> datetime:
    """Current datetime in Israel timezone."""
    return datetime.now(ISRAEL_TZ)


def _today_israel() -> date:
    """Current date in Israel timezone."""
    return _now_israel().date()


def _get_israeli_holidays(*years: int) -> dict[date, str]:
    """Get Israeli holidays for one or more years.

    Returns a dict mapping date -> holiday name (in Hebrew where available).
    """
    il_holidays = holidays_lib.Israel(years=list(years), language="he")
    return dict(il_holidays)


def get_status_for_date(target_date: date = None) -> dict:
    """Determine the business status for a given date.

    Resolution: special_days table -> Israeli holiday calendar -> regular hours.

    Returns a dict with:
        - is_open (bool): Whether the business is open that day
        - open_time (str|None): Opening time e.g. "09:00"
        - close_time (str|None): Closing time e.g. "19:00"
        - reason (str): Why the business is open/closed
        - source (str): "special_day" | "holiday" | "regular"
        - day_name (str): Hebrew day name
    """
    if target_date is None:
        target_date = _today_israel()

    il_day = _python_weekday_to_israeli(target_date.weekday())
    day_name = DAY_NAMES_HE[il_day]
    date_str = target_date.strftime("%Y-%m-%d")

    # 1. Check special_days table (highest priority)
    special = db.get_special_day_by_date(date_str)
    if special:
        if special["is_closed"]:
            return {
                "is_open": False,
                "open_time": None,
                "close_time": None,
                "reason": special["name"],
                "notes": special.get("notes", ""),
                "source": "special_day",
                "day_name": day_name,
            }
        return {
            "is_open": True,
            "open_time": special["open_time"],
            "close_time": special["close_time"],
            "reason": f'{special["name"]} (שעות מיוחדות)',
            "notes": special.get("notes", ""),
            "source": "special_day",
            "day_name": day_name,
        }

    # 2. Check Israeli holiday calendar
    # Include next year to handle year-boundary erev chag (e.g. Dec 31 → Jan 1)
    holiday_years = {target_date.year}
    tomorrow = target_date + timedelta(days=1)
    holiday_years.add(tomorrow.year)
    il_holidays = _get_israeli_holidays(*holiday_years)

    if target_date in il_holidays:
        holiday_name = il_holidays[target_date]
        return {
            "is_open": False,
            "open_time": None,
            "close_time": None,
            "reason": holiday_name,
            "notes": "",
            "source": "holiday",
            "day_name": day_name,
        }

    # Check regular hours first — needed for both erev chag and step 3
    hours = db.get_business_hours_for_day(il_day)
    is_regularly_closed = not hours or hours["is_closed"]

    # Erev chag: only flag if the business is normally open on this day
    if tomorrow in il_holidays and not is_regularly_closed:
        tomorrow_name = il_holidays[tomorrow]
        return {
            "is_open": True,
            "open_time": hours["open_time"],
            "close_time": hours["close_time"],
            "reason": f"ערב {tomorrow_name}",
            "notes": "ייתכן שעות מקוצרות — מומלץ לבדוק מראש",
            "source": "erev_chag",
            "day_name": day_name,
        }

    # 3. Regular business hours
    if is_regularly_closed:
        return {
            "is_open": False,
            "open_time": None,
            "close_time": None,
            "reason": "סגור ביום זה",
            "notes": "",
            "source": "regular",
            "day_name": day_name,
        }

    return {
        "is_open": True,
        "open_time": hours["open_time"],
        "close_time": hours["close_time"],
        "reason": "",
        "notes": "",
        "source": "regular",
        "day_name": day_name,
    }


def is_currently_open() -> dict:
    """Check if the business is currently open right now.

    Returns a dict with:
        - is_open (bool)
        - message (str): Hebrew message suitable for the bot
        - status_emoji (str): Emoji for the status
        - next_opening (str|None): When the business next opens
    """
    now = _now_israel()
    today = now.date()
    current_time = now.time()

    day_status = get_status_for_date(today)

    if not day_status["is_open"]:
        next_open = _find_next_opening(today)
        return {
            "is_open": False,
            "message": _format_closed_message(day_status, next_open),
            "status_emoji": "\U0001f534",  # red circle
            "next_opening": next_open,
        }

    # Business is open today — check if we're within hours
    open_time_str = day_status.get("open_time")
    close_time_str = day_status.get("close_time")

    if not open_time_str or not close_time_str:
        # Open today but no specific hours (e.g. special day without times)
        return {
            "is_open": True,
            "message": "אנחנו פתוחים היום!",
            "status_emoji": "\u2705",
            "next_opening": None,
        }

    open_time = time.fromisoformat(open_time_str)
    close_time = time.fromisoformat(close_time_str)

    # Handle overnight hours (e.g. open_time="22:00", close_time="02:00")
    is_overnight = close_time <= open_time

    if is_overnight:
        # Overnight: open if current_time >= open_time OR current_time < close_time
        currently_within = current_time >= open_time or current_time < close_time
    else:
        # Normal: open if open_time <= current_time < close_time
        currently_within = open_time <= current_time < close_time

    if not currently_within:
        if current_time < open_time and (not is_overnight or current_time >= close_time):
            return {
                "is_open": False,
                "message": f"\U0001f534 עדיין לא פתחנו — נפתח היום בשעה {open_time_str}.",
                "status_emoji": "\U0001f534",
                "next_opening": f"היום בשעה {open_time_str}",
            }
        next_open = _find_next_opening(today)
        return {
            "is_open": False,
            "message": _format_closed_message(
                {"is_open": False, "reason": "סגרנו להיום", "source": "regular",
                 "day_name": day_status["day_name"], "notes": ""},
                next_open,
            ),
            "status_emoji": "\U0001f534",
            "next_opening": next_open,
        }

    # Currently open
    erev_note = ""
    if day_status["source"] == "erev_chag":
        erev_note = f"\n\u26a0\ufe0f {day_status['reason']} — {day_status['notes']}"

    return {
        "is_open": True,
        "message": f"\u2705 כן! אנחנו פתוחים עד {close_time_str}.{erev_note}",
        "status_emoji": "\u2705",
        "next_opening": None,
    }


def _find_next_opening(from_date: date) -> str | None:
    """Find the next day the business opens after from_date."""
    for i in range(1, 8):
        check_date = from_date + timedelta(days=i)
        status = get_status_for_date(check_date)
        if status["is_open"] and status.get("open_time"):
            day_name = status["day_name"]
            if i == 1:
                return f"מחר ({day_name}) בשעה {status['open_time']}"
            return f"יום {day_name} בשעה {status['open_time']}"
    return None


def _format_closed_message(day_status: dict, next_open: str | None) -> str:
    """Format a Hebrew message for when the business is closed."""
    reason = day_status.get("reason", "")
    source = day_status.get("source", "")

    if source == "holiday":
        msg = f"\U0001f534 סגור היום ({reason})."
    elif source == "special_day":
        msg = f"\U0001f534 סגור היום ({reason})."
    else:
        msg = "\U0001f534 סגור כעת."

    if next_open:
        msg += f"\nנפתח שוב: {next_open}"

    return msg


def get_weekly_schedule_text() -> str:
    """Generate a formatted Hebrew text of the weekly schedule."""
    all_hours = db.get_all_business_hours()
    if not all_hours:
        return "לא הוגדרו שעות פעילות."

    lines = ["שעות פעילות:"]
    for h in all_hours:
        day = DAY_NAMES_HE.get(h["day_of_week"], "?")
        if h["is_closed"]:
            lines.append(f"  {day}: סגור")
        else:
            lines.append(f"  {day}: {h['open_time']} - {h['close_time']}")

    return "\n".join(lines)


def get_hours_context_for_llm() -> str:
    """Build a context string about current business hours status for the LLM.

    This is injected into the system prompt so the LLM can give
    time-aware answers without a RAG lookup.
    """
    now = _now_israel()
    status = is_currently_open()
    schedule = get_weekly_schedule_text()

    # Upcoming special days (next 7 days)
    upcoming = []
    for i in range(7):
        d = now.date() + timedelta(days=i)
        day_status = get_status_for_date(d)
        if day_status["source"] in ("special_day", "holiday", "erev_chag"):
            label = d.strftime("%d/%m")
            upcoming.append(f"  {label} ({day_status['day_name']}): {day_status['reason']}")

    parts = [
        f"תאריך ושעה נוכחיים: {now.strftime('%d/%m/%Y %H:%M')} (יום {DAY_NAMES_HE[_python_weekday_to_israeli(now.weekday())]})",
        f"סטטוס כרגע: {status['message']}",
        "",
        schedule,
    ]

    if upcoming:
        parts.append("")
        parts.append("ימים מיוחדים קרובים:")
        parts.extend(upcoming)

    return "\n".join(parts)
