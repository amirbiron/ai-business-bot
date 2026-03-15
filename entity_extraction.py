"""
חילוץ ישויות ישראליות מטקסט חופשי.

מזהה טלפונים ישראליים, סכומים בשקלים, תאריכים בפורמט ישראלי,
ומספרי תעודת זהות. שימושי לאימות קלט בתהליכי הזמנה ולניתוח שיחות.
"""

import re
import logging

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
