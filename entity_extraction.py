"""
חילוץ ישויות ישראליות מטקסט חופשי.

מזהה טלפונים ישראליים, סכומים בשקלים, תאריכים בפורמט ישראלי,
ומספרי תעודת זהות. שימושי לאימות קלט בתהליכי הזמנה ולניתוח שיחות.
"""

import re
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# ─── טלפונים ישראליים ────────────────────────────────────────────────────

# רגקס אחד שמכסה את כל הפורמטים הישראליים, מסודר לפי עדיפות (בינלאומי ← נייד ← קווי)
_PHONE_PATTERN = re.compile(
    r'(?<!\d)'
    r'(?:'
    r'\+972[\s-]?\d{1,2}[\s-]?\d{3}[\s-]?\d{4}'   # בינלאומי: +972-50-1234567
    r'|05\d[\s-]?\d{3}[\s-]?\d{4}'                  # נייד: 050-1234567
    r'|0[2-9][\s-]?\d{3}[\s-]?\d{4}'                # קווי: 02-1234567
    r')'
    r'(?!\d)',
)


def extract_phone_numbers(text: str) -> list[str]:
    """חילוץ מספרי טלפון ישראליים מטקסט."""
    return _PHONE_PATTERN.findall(text)


# ─── סכומים בשקלים ───────────────────────────────────────────────────────

_NIS_PATTERNS = [
    # ₪150, ₪ 1,500.00
    re.compile(r'₪\s?[\d,]+(?:\.\d{1,2})?'),
    # 150 שקלים, 200 שקל, 300 ש"ח, 400 שח
    re.compile(r'[\d,]+(?:\.\d{1,2})?\s?(?:שקלים|שקל|ש"ח|שח)'),
]


def extract_nis_amounts(text: str) -> list[str]:
    """חילוץ סכומים בשקלים מטקסט."""
    results = []
    for pattern in _NIS_PATTERNS:
        results.extend(pattern.findall(text))
    return results


# ─── תאריכים ─────────────────────────────────────────────────────────────

_HEBREW_MONTHS = (
    "ינואר|פברואר|מרץ|אפריל|מאי|יוני|יולי|אוגוסט|ספטמבר|אוקטובר|נובמבר|דצמבר"
)

_DATE_PATTERNS = [
    # DD/MM/YYYY, DD.MM.YYYY, DD-MM-YYYY (שנה מלאה או קצרה)
    re.compile(r'\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}'),
    # DD/MM, DD.MM (בלי שנה — נפוץ בשיחות: "15/03", "3.7")
    # lookbehind מונע תפיסת "03/26" מתוך "15/03/26"
    re.compile(r'(?<!\d[/.\-])(?<!\d)\d{1,2}[/.\-]\d{1,2}(?![/.\-\d])'),
    # "14 במרץ", "3 בינואר", "14 מרץ"
    re.compile(rf'\d{{1,2}}\s*ב?(?:{_HEBREW_MONTHS})'),
]


def extract_dates(text: str) -> list[str]:
    """חילוץ תאריכים בפורמט ישראלי מטקסט."""
    results = []
    for pattern in _DATE_PATTERNS:
        results.extend(pattern.findall(text))
    return results


# ─── נורמליזציית תאריך ──────────────────────────────────────────────────

_HEBREW_MONTH_MAP = {
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "אפריל": 4,
    "מאי": 5, "יוני": 6, "יולי": 7, "אוגוסט": 8,
    "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
}

# ימים בשבוע — יום ראשון = 6 ב-Python (weekday() מחזיר 0=Monday)
_HEBREW_DAY_MAP = {
    "ראשון": 6, "שני": 0, "שלישי": 1, "רביעי": 2,
    "חמישי": 3, "שישי": 4, "שבת": 5,
}

_RELATIVE_PATTERN = re.compile(
    r"(?:^|\s)(היום|מחר|מחרתיים)(?:\s|$)", re.IGNORECASE
)
_DAY_NAME_PATTERN = re.compile(
    r"(?:^|\s)(?:ב?יום\s+|ב)?(ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת)"
    r"(?:\s+הבא)?(?:\s|$)",
)
# DD/MM/YYYY או DD.MM.YYYY (שנה 2 או 4 ספרות)
_FULL_DATE_RE = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})")
# DD/MM בלבד
_SHORT_DATE_RE = re.compile(
    r"(?<!\d[/.\-])(?<!\d)(\d{1,2})[/.\-](\d{1,2})(?![/.\-\d])"
)
# "14 במרץ" / "14 מרץ"
_HEBREW_MONTH_RE = re.compile(
    rf"(\d{{1,2}})\s*ב?({'|'.join(_HEBREW_MONTH_MAP)})"
)


def _safe_date(year: int, month: int, day: int) -> date | None:
    """יוצר date רק אם התאריך תקין (למשל לא 31/2)."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _next_weekday(target_weekday: int, ref: date) -> date:
    """מחזיר את התאריך הקרוב ביום target_weekday (0=Mon..6=Sun), תמיד בעתיד."""
    days_ahead = (target_weekday - ref.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # אם היום זה אותו יום — הכוונה לשבוע הבא
    return ref + timedelta(days=days_ahead)


def normalize_date(text: str, ref_date: date | None = None) -> str | None:
    """ממיר טקסט חופשי לתאריך בפורמט YYYY-MM-DD.

    סדר DD/MM (ישראלי). מחזיר None אם לא זוהה תאריך תקין.
    ref_date משמש לחישוב תאריכים יחסיים (ברירת מחדל: היום).
    """
    if not text or not text.strip():
        return None

    text = text.strip()
    ref = ref_date or date.today()

    # ── תאריכים יחסיים (היום, מחר, מחרתיים) ──
    m = _RELATIVE_PATTERN.search(text)
    if m:
        word = m.group(1)
        if word == "היום":
            return ref.isoformat()
        if word == "מחר":
            return (ref + timedelta(days=1)).isoformat()
        if word == "מחרתיים":
            return (ref + timedelta(days=2)).isoformat()

    # ── שם יום (יום ראשון, ביום שני, שבת...) ──
    m = _DAY_NAME_PATTERN.search(text)
    if m:
        day_name = m.group(1)
        target_wd = _HEBREW_DAY_MAP.get(day_name)
        if target_wd is not None:
            return _next_weekday(target_wd, ref).isoformat()

    # ── DD/MM/YYYY מלא ──
    m = _FULL_DATE_RE.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3))
        if year < 100:
            year += 2000  # "26" → 2026
        d = _safe_date(year, month, day)
        return d.isoformat() if d else None

    # ── DD/MM בלי שנה ──
    m = _SHORT_DATE_RE.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        d = _safe_date(ref.year, month, day)
        if d is None:
            return None
        # אם התאריך כבר עבר (או היום) — מתכוון לשנה הבאה
        if d <= ref:
            d = _safe_date(ref.year + 1, month, day)
        return d.isoformat() if d else None

    # ── "14 במרץ" / "14 מרץ" ──
    m = _HEBREW_MONTH_RE.search(text)
    if m:
        day = int(m.group(1))
        month = _HEBREW_MONTH_MAP[m.group(2)]
        d = _safe_date(ref.year, month, day)
        if d is None:
            return None
        if d <= ref:
            d = _safe_date(ref.year + 1, month, day)
        return d.isoformat() if d else None

    return None


# ─── תעודת זהות ──────────────────────────────────────────────────────────

_TZ_PATTERN = re.compile(r'(?<!\d)\d{9}(?!\d)')


def extract_teudat_zehut(text: str) -> list[str]:
    """חילוץ מספרי תעודת זהות (9 ספרות) מטקסט."""
    return _TZ_PATTERN.findall(text)


# ─── חילוץ כולל ──────────────────────────────────────────────────────────

def extract_all(text: str) -> dict:
    """חילוץ כל סוגי הישויות הישראליות מטקסט.

    מחזיר מילון עם מפתחות רק לישויות שנמצאו.
    """
    entities = {}

    phones = extract_phone_numbers(text)
    if phones:
        entities["phone_numbers"] = phones

    amounts = extract_nis_amounts(text)
    if amounts:
        entities["amounts_nis"] = amounts

    dates = extract_dates(text)
    if dates:
        entities["dates"] = dates

    tz = extract_teudat_zehut(text)
    if tz:
        entities["teudat_zehut"] = tz

    return entities
