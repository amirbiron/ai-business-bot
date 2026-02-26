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
    sanitize_telegram_html,
    _build_messages,
)
from config import (
    FALLBACK_RESPONSE, build_system_prompt, TONE_DEFINITIONS, BUSINESS_NAME,
    _AGENT_IDENTITY, _AGENT_DESCRIPTOR, _CONVERSATION_GUIDELINES,
    _RESPONSE_STRUCTURE,
)


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


class TestBuildSystemPrompt:
    def test_default_friendly_tone(self):
        """ברירת מחדל — טון ידידותי."""
        prompt = build_system_prompt()
        assert BUSINESS_NAME in prompt
        assert "ידידותי" in prompt or "חברי" in prompt
        # מוודאים שהכללים המקוריים נמצאים
        assert "ענה רק על סמך המידע" in prompt
        assert "מקור:" in prompt

    def test_formal_tone(self):
        """טון רשמי."""
        prompt = build_system_prompt(tone="formal")
        assert "רשמי" in prompt
        assert "הימנע מסלנג" in prompt

    def test_sales_tone(self):
        """טון מכירתי."""
        prompt = build_system_prompt(tone="sales")
        assert "מכירות" in prompt or "מוכוון" in prompt

    def test_luxury_tone(self):
        """טון יוקרתי."""
        prompt = build_system_prompt(tone="luxury")
        assert "יוקרתי" in prompt or "מעודן" in prompt

    def test_custom_phrases_included(self):
        """ביטויים מותאמים אישית מוזרקים לפרומפט."""
        prompt = build_system_prompt(custom_phrases="אהלן, בשמחה, בכיף")
        assert "אהלן, בשמחה, בכיף" in prompt
        assert "ביטויים אופייניים" in prompt

    def test_empty_custom_phrases_omitted(self):
        """ביטויים ריקים לא יוצרים סקשן מיותר."""
        prompt = build_system_prompt(custom_phrases="")
        assert "ביטויים אופייניים" not in prompt

    def test_invalid_tone_falls_back(self):
        """טון לא מוכר — חוזר ל-friendly."""
        prompt = build_system_prompt(tone="nonexistent")
        # צריך להכיל את הטון הידידותי כ-fallback
        friendly_text = TONE_DEFINITIONS["friendly"]
        assert friendly_text in prompt

    def test_constraints_section(self):
        """סקשן מגבלות — לא לצאת מהדמות."""
        prompt = build_system_prompt()
        assert "לעולם אל תצא מהדמות" in prompt
        assert "ז'רגון תאגידי" in prompt

    def test_output_structure_friendly(self):
        """סקשן מבנה התשובה — פתיחה חמה, תשובה, סגירה (טון ידידותי)."""
        prompt = build_system_prompt()
        assert "פתיחה חמה" in prompt
        assert "סגירה טבעית" in prompt

    def test_output_structure_per_tone(self):
        """כל טון מקבל מבנה תשובה ייחודי."""
        for tone in TONE_DEFINITIONS:
            prompt = build_system_prompt(tone=tone)
            assert _RESPONSE_STRUCTURE[tone].split("\n")[0] in prompt

    def test_all_tones_defined(self):
        """כל ארבעת הטונים מוגדרים בכל המילונים."""
        expected = {"friendly", "formal", "sales", "luxury"}
        assert set(TONE_DEFINITIONS.keys()) == expected
        assert set(_AGENT_IDENTITY.keys()) == expected
        assert set(_AGENT_DESCRIPTOR.keys()) == expected
        assert set(_CONVERSATION_GUIDELINES.keys()) == expected
        assert set(_RESPONSE_STRUCTURE.keys()) == expected

    def test_identity_section_present(self):
        """פסקת הזהות מוזרקת לפרומפט בכל הטונים."""
        for tone in TONE_DEFINITIONS:
            prompt = build_system_prompt(tone=tone)
            # כל הטונים מכילים את המשפט "אתה לא בינה מלאכותית"
            assert 'אתה לא "בינה מלאכותית"' in prompt

    def test_identity_formal_no_casual_language(self):
        """פסקת זהות רשמית — ללא ניסוחים חמים כמו '100% אנושית' או 'עסק קטן'."""
        prompt = build_system_prompt(tone="formal")
        assert "100% אנושית" not in prompt
        assert "עסק קטן" not in prompt

    def test_formal_tone_no_warm_casual_language(self):
        """טון רשמי — אין שפה חמה/שיחתית שסותרת את הטון."""
        prompt = build_system_prompt(tone="formal")
        assert "שיחתית וחמה" not in prompt
        assert "פתיחה חמה" not in prompt
        assert "חבר צוות" not in prompt

    def test_luxury_tone_no_warm_casual_language(self):
        """טון יוקרתי — אין שפה חמה/שיחתית שסותרת את הטון."""
        prompt = build_system_prompt(tone="luxury")
        assert "שיחתית וחמה" not in prompt
        assert "פתיחה חמה" not in prompt
        assert "חבר צוות" not in prompt

    def test_follow_up_rule_placement(self):
        """כשהפיצ'ר שאלות המשך פעיל — כלל 11 מופיע אחרי כלל 10, לפני סקשן המגבלות."""
        prompt = build_system_prompt(follow_up_enabled=True)
        pos_rule_10 = prompt.index("10. ענה באותה שפה")
        pos_rule_11 = prompt.index("11. בסוף כל תשובה")
        pos_constraints = prompt.index("── מגבלות ──")
        assert pos_rule_10 < pos_rule_11 < pos_constraints

    def test_follow_up_rule_absent_by_default(self):
        """ברירת מחדל — כלל 11 לא מופיע."""
        prompt = build_system_prompt()
        assert "11." not in prompt
        assert "שאלות_המשך" not in prompt


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


class TestSanitizeTelegramHtml:
    """טסטים לפונקציית sanitize_telegram_html — סניטציה של פלט LLM ל-HTML בטוח לטלגרם."""

    def test_preserves_allowed_tags(self):
        text = "<b>כותרת</b> ו-<i>הערה</i> ו-<u>מודגש</u>"
        assert sanitize_telegram_html(text) == text

    def test_preserves_closing_tags(self):
        text = "<b>טקסט</b>"
        assert sanitize_telegram_html(text) == text

    def test_escapes_ampersand(self):
        text = "מחיר: 100₪ & הנחה"
        result = sanitize_telegram_html(text)
        assert "&amp;" in result
        assert "& " not in result

    def test_escapes_angle_brackets_in_text(self):
        text = "3 < 5 > 2"
        result = sanitize_telegram_html(text)
        assert "&lt;" in result
        assert "&gt;" in result

    def test_escapes_unknown_tags(self):
        text = "<script>alert('xss')</script>"
        result = sanitize_telegram_html(text)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_mixed_valid_and_invalid(self):
        text = "<b>כותרת</b> עם <div>תג לא חוקי</div>"
        result = sanitize_telegram_html(text)
        assert "<b>כותרת</b>" in result
        assert "&lt;div&gt;" in result

    def test_plain_text_unchanged(self):
        text = "שלום עולם, הכל בסדר"
        assert sanitize_telegram_html(text) == text

    def test_preserves_code_and_pre_tags(self):
        text = "<code>snippet</code> ו-<pre>block</pre>"
        assert sanitize_telegram_html(text) == text

    def test_preserves_strikethrough_tag(self):
        text = "<s>מחיק</s>"
        assert sanitize_telegram_html(text) == text

    def test_strips_attributed_opening_and_closing_tags(self):
        """תג עם מאפיינים (class וכו') נמחק יחד עם תג הסגירה שלו."""
        text = '<code class="language-python">print("hi")</code>'
        result = sanitize_telegram_html(text)
        assert result == 'print("hi")'

    def test_attributed_pre_tag_stripped(self):
        """תג pre עם מאפיינים נמחק שלם."""
        text = '<pre lang="python">code</pre>'
        result = sanitize_telegram_html(text)
        assert result == "code"

    def test_mixed_plain_and_attributed_tags(self):
        """תגים רגילים נשמרים, תגים עם מאפיינים נמחקים."""
        text = '<b>כותרת</b> ו-<code class="x">snippet</code>'
        result = sanitize_telegram_html(text)
        assert result == "<b>כותרת</b> ו-snippet"


class TestFormattingInSystemPrompt:
    """טסטים שמוודאים שהנחיות העיצוב מוזרקות נכון ל-system prompt."""

    def test_formatting_section_present(self):
        """סקשן עיצוב טקסט מופיע בפרומפט."""
        prompt = build_system_prompt()
        assert "── עיצוב טקסט ──" in prompt
        assert "<b>" in prompt
        assert "<i>" in prompt
        assert "<u>" in prompt

    def test_no_markdown_instruction(self):
        """הפרומפט מנחה לא להשתמש ב-Markdown."""
        prompt = build_system_prompt()
        assert "אל תשתמש בתחביר Markdown" in prompt

    def test_emoji_guidance_friendly(self):
        """טון ידידותי — הנחיות אימוג'ים מופיעות."""
        prompt = build_system_prompt(tone="friendly")
        assert "💇‍♀️" in prompt
        assert "💅" in prompt

    def test_emoji_guidance_sales(self):
        """טון מכירתי — הנחיות אימוג'ים מופיעות."""
        prompt = build_system_prompt(tone="sales")
        assert "💇‍♀️" in prompt

    def test_no_emoji_guidance_formal(self):
        """טון רשמי — אין הנחיות אימוג'ים ספציפיות לקטגוריות."""
        prompt = build_system_prompt(tone="formal")
        assert "💇‍♀️" not in prompt

    def test_no_emoji_guidance_luxury(self):
        """טון יוקרתי — אין הנחיות אימוג'ים ספציפיות לקטגוריות."""
        prompt = build_system_prompt(tone="luxury")
        assert "💇‍♀️" not in prompt
