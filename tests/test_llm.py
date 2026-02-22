"""
טסטים למודול LLM — llm.py

בודק את שכבת בקרת האיכות (Layer C),
חילוץ שאלות המשך, והסרת citations.
לא קורא ל-OpenAI API — רק לוגיקה טהורה.
"""

import pytest
from llm import (
    _quality_check,
    extract_follow_up_questions,
    strip_follow_up_questions,
    strip_source_citation,
    _build_messages,
)
from config import FALLBACK_RESPONSE


class TestQualityCheck:
    def test_passes_with_hebrew_source(self):
        text = "התשובה היא X.\nמקור: מחירון שירותים"
        assert _quality_check(text) == text

    def test_passes_with_english_source(self):
        text = "The answer is X.\nSource: Price list 2025"
        assert _quality_check(text) == text

    def test_fails_without_source(self):
        text = "תשובה ללא ציון מקור"
        assert _quality_check(text) == FALLBACK_RESPONSE

    def test_source_case_insensitive(self):
        text = "Info.\nsource: services"
        assert _quality_check(text) == text

    def test_empty_response_fails(self):
        assert _quality_check("") == FALLBACK_RESPONSE


class TestExtractFollowUp:
    def test_standard_format(self):
        text = "תשובה.\n[שאלות_המשך: שאלה א | שאלה ב | שאלה ג]"
        questions = extract_follow_up_questions(text)
        assert len(questions) == 3
        assert questions[0] == "שאלה א"

    def test_space_variant(self):
        text = "תשובה.\n[שאלות המשך: שאלה א | שאלה ב]"
        questions = extract_follow_up_questions(text)
        assert len(questions) == 2

    def test_no_brackets_variant(self):
        text = "תשובה.\nשאלות_המשך: שאלה א | שאלה ב"
        questions = extract_follow_up_questions(text)
        assert len(questions) == 2

    def test_no_follow_up(self):
        text = "תשובה רגילה בלי שאלות המשך."
        assert extract_follow_up_questions(text) == []

    def test_max_three_questions(self):
        text = "[שאלות_המשך: א | ב | ג | ד | ה]"
        questions = extract_follow_up_questions(text)
        assert len(questions) == 3


class TestStripFollowUp:
    def test_strips_bracketed(self):
        text = "תשובה.\n\n[שאלות_המשך: שאלה א | שאלה ב]"
        result = strip_follow_up_questions(text)
        assert "שאלות" not in result
        assert result == "תשובה."

    def test_strips_unbracketed(self):
        text = "תשובה.\nשאלות_המשך: שאלה א | שאלה ב\n"
        result = strip_follow_up_questions(text)
        assert "שאלות" not in result

    def test_preserves_rest(self):
        text = "תשובה ארוכה.\nמקור: שירותים\n[שאלות_המשך: שאלה]"
        result = strip_follow_up_questions(text)
        assert "תשובה ארוכה." in result
        assert "מקור:" in result


class TestStripSourceCitation:
    def test_strips_hebrew_source(self):
        text = "תשובה.\nמקור: מחירון"
        result = strip_source_citation(text)
        assert result == "תשובה."

    def test_strips_english_source(self):
        text = "Answer.\nSource: Price list"
        result = strip_source_citation(text)
        assert result == "Answer."

    def test_no_source_unchanged(self):
        text = "תשובה ללא מקור."
        assert strip_source_citation(text) == text


class TestBuildMessages:
    def test_basic_structure(self):
        msgs = _build_messages("שאלה", "הקשר כלשהו")
        roles = [m["role"] for m in msgs]
        # system prompt, context, user query
        assert roles[0] == "system"
        assert roles[-1] == "user"
        assert msgs[-1]["content"] == "שאלה"

    def test_with_history(self):
        history = [
            {"role": "user", "message": "שלום"},
            {"role": "assistant", "message": "היי!"},
        ]
        msgs = _build_messages("שאלה חדשה", "הקשר", history)
        # צריך להכיל את ההיסטוריה לפני השאלה הנוכחית
        contents = [m["content"] for m in msgs]
        assert "שלום" in contents
        assert "היי!" in contents

    def test_with_summary(self):
        msgs = _build_messages("שאלה", "הקשר", conversation_summary="סיכום ישן")
        contents = " ".join(m["content"] for m in msgs)
        assert "סיכום ישן" in contents
