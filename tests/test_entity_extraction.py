"""
טסטים למודול חילוץ ישויות ישראליות — entity_extraction.py

בודק זיהוי טלפונים, סכומי שקלים, תאריכים ותעודות זהות מטקסט חופשי.
"""

import pytest
from datetime import date
from entity_extraction import (
    extract_phone_numbers,
    extract_nis_amounts,
    extract_dates,
    extract_teudat_zehut,
    extract_all,
    normalize_date,
)


# ── טלפונים ישראליים ─────────────────────────────────────────────────────

class TestPhoneNumbers:
    @pytest.mark.parametrize("text,expected", [
        ("הטלפון שלי 050-1234567", ["050-1234567"]),
        ("התקשרו ל-0501234567 בבקשה", ["0501234567"]),
        ("050 123 4567", ["050 123 4567"]),
        ("+972-50-1234567", ["+972-50-1234567"]),
        ("+972501234567", ["+972501234567"]),
        ("קווי: 02-6231111", ["02-6231111"]),
        ("03-5551234", ["03-5551234"]),
    ])
    def test_phone_detected(self, text, expected):
        result = extract_phone_numbers(text)
        assert result == expected

    def test_no_phone_in_text(self):
        assert extract_phone_numbers("שלום, מה קורה?") == []

    def test_multiple_phones(self):
        text = "נייד: 050-1111111, בית: 02-2222222"
        result = extract_phone_numbers(text)
        assert len(result) == 2


# ── סכומים בשקלים ────────────────────────────────────────────────────────

class TestNisAmounts:
    @pytest.mark.parametrize("text,expected", [
        ("המחיר הוא ₪150", ["₪150"]),
        ("עולה 200 שקלים", ["200 שקלים"]),
        ("עלות: 300 ש\"ח", ['300 ש"ח']),
        ("₪1,500.00", ["₪1,500.00"]),
        ("50 שקל", ["50 שקל"]),
    ])
    def test_amount_detected(self, text, expected):
        result = extract_nis_amounts(text)
        assert result == expected

    def test_no_amount_in_text(self):
        assert extract_nis_amounts("שלום, מה קורה?") == []


# ── תאריכים ──────────────────────────────────────────────────────────────

class TestDates:
    @pytest.mark.parametrize("text,expected", [
        ("15/03/2026", ["15/03/2026"]),
        ("15.03.2026", ["15.03.2026"]),
        ("15-03-2026", ["15-03-2026"]),
        ("15/03/26", ["15/03/26"]),
        ("14 במרץ", ["14 במרץ"]),
        ("3 בינואר", ["3 בינואר"]),
        ("14 מרץ", ["14 מרץ"]),
        # DD/MM בלי שנה
        ("15/03", ["15/03"]),
        ("3.7", ["3.7"]),
    ])
    def test_date_detected(self, text, expected):
        result = extract_dates(text)
        assert result == expected

    def test_no_date_in_text(self):
        assert extract_dates("אני רוצה תור בבקשה") == []


# ── נורמליזציית תאריך ───────────────────────────────────────────────────

class TestNormalizeDate:
    """כל הטסטים משתמשים ב-ref_date קבוע כדי למנוע תלות ביום ההרצה."""

    REF = date(2026, 3, 28)  # שבת

    # ── תאריכים יחסיים ──
    def test_today(self):
        assert normalize_date("היום", self.REF) == "2026-03-28"

    def test_tomorrow(self):
        assert normalize_date("מחר", self.REF) == "2026-03-29"

    def test_day_after_tomorrow(self):
        assert normalize_date("מחרתיים", self.REF) == "2026-03-30"

    # ── שמות ימים ──
    def test_day_name_sunday(self):
        # REF=שבת 28/3, יום ראשון הקרוב = 29/3
        assert normalize_date("יום ראשון", self.REF) == "2026-03-29"

    def test_day_name_with_prefix(self):
        # "ביום שני" = Monday, REF=שבת → Monday 30/3
        assert normalize_date("ביום שני", self.REF) == "2026-03-30"

    def test_day_name_wednesday(self):
        assert normalize_date("ביום רביעי", self.REF) == "2026-04-01"

    def test_day_name_shabbat(self):
        # REF=שבת → שבת הבאה = +7
        assert normalize_date("שבת", self.REF) == "2026-04-04"

    def test_day_name_next(self):
        assert normalize_date("יום חמישי הבא", self.REF) == "2026-04-02"

    # ── DD/MM/YYYY מלא ──
    def test_full_date_slash(self):
        assert normalize_date("15/03/2026", self.REF) == "2026-03-15"

    def test_full_date_dot(self):
        assert normalize_date("3.7.2026", self.REF) == "2026-07-03"

    def test_full_date_short_year(self):
        assert normalize_date("1/1/27", self.REF) == "2027-01-01"

    # ── DD/MM בלי שנה ──
    def test_short_date_future(self):
        assert normalize_date("15/04", self.REF) == "2026-04-15"

    def test_short_date_past_rolls_to_next_year(self):
        assert normalize_date("1/1", self.REF) == "2027-01-01"

    def test_short_date_today_same_day(self):
        # 28/03 = REF (היום) → היום עצמו תקף, לא מגלגל שנה
        assert normalize_date("28/3", self.REF) == "2026-03-28"

    def test_short_date_yesterday_rolls(self):
        # 27/03 < REF → כבר עבר → שנה הבאה
        assert normalize_date("27/3", self.REF) == "2027-03-27"

    # ── חודשים בעברית ──
    def test_hebrew_month_with_bet(self):
        assert normalize_date("14 במרץ", self.REF) == "2027-03-14"  # כבר עבר → 2027

    def test_hebrew_month_today(self):
        # 28 במרץ = REF (היום) → היום עצמו תקף
        assert normalize_date("28 במרץ", self.REF) == "2026-03-28"

    def test_hebrew_month_without_bet(self):
        assert normalize_date("3 אפריל", self.REF) == "2026-04-03"

    def test_hebrew_month_december(self):
        assert normalize_date("25 דצמבר", self.REF) == "2026-12-25"

    # ── תאריכים לא תקינים ──
    def test_invalid_date(self):
        assert normalize_date("31/2", self.REF) is None

    def test_no_date(self):
        assert normalize_date("שלום מה קורה", self.REF) is None

    def test_empty_string(self):
        assert normalize_date("", self.REF) is None

    def test_none_like_input(self):
        assert normalize_date("   ", self.REF) is None

    # ── טקסט עם תאריך בתוכו ──
    def test_date_in_sentence(self):
        assert normalize_date("אני רוצה תור ב-15/04", self.REF) == "2026-04-15"

    def test_relative_in_sentence(self):
        assert normalize_date("בוא נקבע מחר", self.REF) == "2026-03-29"


# ── תעודת זהות ───────────────────────────────────────────────────────────

class TestTeudatZehut:
    def test_tz_detected(self):
        assert extract_teudat_zehut("ת.ז. 123456789") == ["123456789"]

    def test_tz_not_detected_wrong_length(self):
        """8 ספרות — לא תעודת זהות."""
        assert extract_teudat_zehut("12345678") == []

    def test_tz_not_detected_in_phone(self):
        """מספר טלפון לא צריך להיתפס כת.ז."""
        assert extract_teudat_zehut("050-1234567") == []


# ── חילוץ כולל ───────────────────────────────────────────────────────────

class TestExtractAll:
    def test_mixed_entities(self):
        text = "הטלפון שלי 050-1234567, אני רוצה תור ב-15/03/2026, עלות ₪200"
        result = extract_all(text)
        assert "phone_numbers" in result
        assert "dates" in result
        assert "amounts_nis" in result

    def test_empty_text(self):
        assert extract_all("") == {}

    def test_no_entities(self):
        assert extract_all("שלום, איך אפשר לעזור?") == {}
