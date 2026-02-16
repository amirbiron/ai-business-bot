"""
Chunker module — splits knowledge base entries into smaller chunks
suitable for embedding and retrieval.
"""

import re
from ai_chatbot.config import CHUNK_MAX_TOKENS


def estimate_tokens(text: str) -> int:
    """Rough token estimation: ~4 characters per token for English."""
    return len(text) // 4


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
    
    max_chars = max_tokens * 4  # rough conversion
    
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
        
        # If adding this paragraph keeps us under limit, add it
        if len(current_chunk) + len(para) + 2 <= max_chars:
            current_chunk = (current_chunk + "\n\n" + para).strip()
        else:
            # Save current chunk if non-empty
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            
            # If paragraph itself is too long, split by sentences
            if len(para) > max_chars:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) + 1 <= max_chars:
                        current_chunk = (current_chunk + " " + sentence).strip()
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        # If single sentence is too long, split by words
                        if len(sentence) > max_chars:
                            words = sentence.split()
                            current_chunk = ""
                            for word in words:
                                if len(current_chunk) + len(word) + 1 <= max_chars:
                                    current_chunk = (current_chunk + " " + word).strip()
                                else:
                                    if current_chunk:
                                        chunks.append(current_chunk)
                                    current_chunk = word
                        else:
                            current_chunk = sentence
            else:
                current_chunk = para
    
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
