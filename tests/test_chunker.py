"""
טסטים למודול החלוקה לצ'אנקים — rag/chunker.py

בודק שהטקסט מתחלק נכון לפי פסקאות, משפטים ומילים,
ושכל צ'אנק לא חורג ממגבלת הטוקנים.
"""

import pytest
from rag.chunker import chunk_text, estimate_tokens, create_chunks_for_entry


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_nonempty_string_positive(self):
        assert estimate_tokens("hello world") > 0

    def test_hebrew_text(self):
        """טקסט בעברית צריך להחזיר ערך חיובי."""
        assert estimate_tokens("שלום עולם, מה נשמע?") > 0


class TestChunkText:
    def test_short_text_single_chunk(self):
        """טקסט קצר שנכנס בצ'אנק אחד לא צריך להתחלק."""
        text = "שלום עולם."
        chunks = chunk_text(text, max_tokens=100)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_empty_text_returns_empty(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_paragraph_splitting(self):
        """שתי פסקאות ארוכות צריכות להתחלק לשני צ'אנקים לפחות."""
        para1 = "מילה " * 200
        para2 = "משפט " * 200
        text = f"{para1}\n\n{para2}"
        chunks = chunk_text(text, max_tokens=50)
        assert len(chunks) >= 2

    def test_chunks_within_token_limit(self):
        """כל צ'אנק חייב להיות בגבולות הטוקנים."""
        max_tokens = 50
        text = ("זהו משפט ארוך. " * 100).strip()
        chunks = chunk_text(text, max_tokens=max_tokens)
        for chunk in chunks:
            tokens = estimate_tokens(chunk)
            # נותנים מרווח סביר כי ההערכה לא מדויקת ב-100%
            assert tokens <= max_tokens * 1.5, f"Chunk too long: {tokens} tokens"

    def test_no_empty_chunks(self):
        """אסור שיווצרו צ'אנקים ריקים."""
        text = "פסקה ראשונה.\n\n\n\nפסקה שנייה.\n\n\n\n"
        chunks = chunk_text(text, max_tokens=100)
        for chunk in chunks:
            assert chunk.strip() != ""

    def test_sentence_splitting(self):
        """כשפסקה ארוכה מדי — צריך לפצל לפי משפטים."""
        text = "זהו משפט ראשון. " * 100  # פסקה אחת ארוכה
        chunks = chunk_text(text, max_tokens=30)
        assert len(chunks) > 1


class TestCreateChunksForEntry:
    def test_basic_chunking(self):
        chunks = create_chunks_for_entry(
            entry_id=1,
            category="שירותים",
            title="תספורות",
            content="תספורת גברים 50 ש\"ח. תספורת נשים 80 ש\"ח.",
        )
        assert len(chunks) >= 1
        assert chunks[0]["entry_id"] == 1
        assert chunks[0]["index"] == 0
        assert chunks[0]["category"] == "שירותים"
        assert chunks[0]["title"] == "תספורות"

    def test_context_prefix_added(self):
        """כל צ'אנק צריך להתחיל עם קידומת הקשר [קטגוריה — כותרת]."""
        chunks = create_chunks_for_entry(
            entry_id=5, category="מידע", title="כתובת", content="רחוב הרצל 15"
        )
        assert chunks[0]["text"].startswith("[מידע — כתובת]")

    def test_empty_content(self):
        chunks = create_chunks_for_entry(
            entry_id=1, category="א", title="ב", content=""
        )
        assert chunks == []
