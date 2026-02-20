"""
Chunker module — splits knowledge base entries into smaller chunks
suitable for embedding and retrieval.
"""

import re
from ai_chatbot.config import CHUNK_MAX_TOKENS

try:
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None

_ENCODING = None  # None=unknown, False=unavailable, else=tiktoken.Encoding


def _get_encoding():
    global _ENCODING
    if _ENCODING is False:
        return None
    if _ENCODING is not None:
        return _ENCODING
    if tiktoken is None:
        _ENCODING = False
        return None
    try:
        from ai_chatbot.config import OPENAI_MODEL
        _ENCODING = tiktoken.encoding_for_model(OPENAI_MODEL)
        return _ENCODING
    except Exception:
        try:
            _ENCODING = tiktoken.get_encoding("cl100k_base")
            return _ENCODING
        except Exception:
            _ENCODING = False
            return None


def estimate_tokens(text: str) -> int:
    """
    Estimate tokens for chunking.

    Uses `tiktoken` when available for accurate counting (important for Hebrew).
    Falls back to a conservative heuristic otherwise.
    """
    if not text:
        return 0
    try:
        enc = _get_encoding()
    except Exception:
        enc = None
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Fallback heuristic: Hebrew often yields fewer chars per token than English.
    return max(1, len(text) // 3)


def chunk_text(text: str, max_tokens: int = None) -> list[str]:
    """
    Split text into chunks that fit within the token limit.
    
    Strategy:
    1. First, try to split on paragraph boundaries (double newlines).
    2. If a paragraph is too long, split on sentence boundaries.
    3. If a sentence is too long, split on word boundaries.
    
    Args:
        text: The text to chunk.
        max_tokens: Maximum tokens per chunk (defaults to config value).
    
    Returns:
        List of text chunks.
    """
    if max_tokens is None:
        max_tokens = CHUNK_MAX_TOKENS

    if estimate_tokens(text) <= max_tokens:
        return [text.strip()] if text.strip() else []
    
    # Split into paragraphs
    paragraphs = re.split(r'\n\s*\n', text)
    
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        candidate = para if not current_chunk else f"{current_chunk}\n\n{para}"
        if estimate_tokens(candidate) <= max_tokens:
            current_chunk = candidate
            continue

        # Save current chunk if non-empty
        if current_chunk:
            chunks.append(current_chunk)
            current_chunk = ""

        # If paragraph itself fits, start a new chunk with it
        if estimate_tokens(para) <= max_tokens:
            current_chunk = para
            continue

        # Paragraph too long: split by sentences, then words
        sentences = re.split(r"(?<=[.!?])\s+", para)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            candidate = sentence if not current_chunk else f"{current_chunk} {sentence}"
            if estimate_tokens(candidate) <= max_tokens:
                current_chunk = candidate
                continue

            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""

            if estimate_tokens(sentence) <= max_tokens:
                current_chunk = sentence
                continue

            # Sentence still too long: split by words
            words = sentence.split()
            for word in words:
                word = word.strip()
                if not word:
                    continue

                if estimate_tokens(word) > max_tokens:
                    if current_chunk:
                        chunks.append(current_chunk)
                        current_chunk = ""
                    chunks.append(word)
                    continue

                candidate = word if not current_chunk else f"{current_chunk} {word}"
                if estimate_tokens(candidate) <= max_tokens:
                    current_chunk = candidate
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = word
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return [c.strip() for c in chunks if c.strip()]


def create_chunks_for_entry(entry_id: int, category: str, title: str, content: str) -> list[dict]:
    """
    Create chunks for a knowledge base entry, prepending context metadata.
    
    Each chunk is prefixed with the category and title so the embedding
    captures the context of where this information comes from.
    
    Args:
        entry_id: The KB entry ID.
        category: The category of the entry.
        title: The title of the entry.
        content: The full content text.
    
    Returns:
        List of chunk dicts with 'index' and 'text' keys.
    """
    raw_chunks = chunk_text(content)
    
    result = []
    for i, chunk in enumerate(raw_chunks):
        # Prepend context: category and title
        contextualized = f"[{category} — {title}]\n{chunk}"
        result.append({
            "index": i,
            "text": contextualized,
            "entry_id": entry_id,
            "category": category,
            "title": title
        })
    
    return result

