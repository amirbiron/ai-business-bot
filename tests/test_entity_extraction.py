"""
טסטים למודול חילוץ ישויות ישראליות — entity_extraction.py

בודק זיהוי טלפונים, סכומי שקלים, תאריכים ותעודות זהות מטקסט חופשי.
"""

import pytest
from entity_extraction import (
    extract_phone_numbers,
    extract_nis_amounts,
    extract_dates,
    extract_teudat_zehut,
    extract_all,
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
