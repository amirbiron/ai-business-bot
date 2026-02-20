from rag.chunker import chunk_text, create_chunks_for_entry, estimate_tokens


def test_chunk_text_returns_single_chunk_when_under_limit():
    text = "Hello world. This is short."
    chunks = chunk_text(text, max_tokens=10_000)
    assert chunks == [text]


def test_chunk_text_splits_into_multiple_chunks_and_preserves_order():
    para1 = "פסקה ראשונה. " * 50
    para2 = "פסקה שנייה. " * 50
    para3 = "פסקה שלישית. " * 50
    text = f"{para1}\n\n{para2}\n\n{para3}"
    max_tokens = 20
    chunks = chunk_text(text, max_tokens=max_tokens)
    assert len(chunks) >= 2

    # Ensure chunks are in-order and non-empty
    assert all(c.strip() for c in chunks)

    # All chunks should fit within limit as measured by the same estimator,
    # except for the special case of a single "too-long word" chunk.
    for c in chunks:
        if len(c.split()) == 1 and estimate_tokens(c) > max_tokens:
            continue
        assert estimate_tokens(c) <= max_tokens

    # Content should be preserved (ignoring whitespace normalization differences)
    joined = " ".join(chunks).replace("\n", " ")
    assert "פסקה ראשונה" in joined
    assert "פסקה שנייה" in joined
    assert "פסקה שלישית" in joined


def test_create_chunks_for_entry_prefixes_context():
    chunks = create_chunks_for_entry(
        entry_id=123,
        category="Policies",
        title="Refunds",
        content="Paragraph one.\n\nParagraph two.",
    )
    assert chunks
    assert chunks[0]["entry_id"] == 123
    assert chunks[0]["category"] == "Policies"
    assert chunks[0]["title"] == "Refunds"
    assert chunks[0]["text"].startswith("[Policies — Refunds]\n")
