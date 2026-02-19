"""
Embeddings module — generates text embeddings using OpenAI's embedding API.

Supports two modes:
1. OpenAI API (production) — uses text-embedding-3-small (1536 dimensions)
2. Local fallback (testing) — uses a simple hash-based embedding for offline testing

The mode is selected automatically: if the OpenAI API call fails, it falls back
to local embeddings and logs a warning.
"""

import hashlib
import logging
import numpy as np
from typing import Optional

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

from ai_chatbot.config import EMBEDDING_MODEL

logger = logging.getLogger(__name__)

_client: Optional[object] = None


def _get_client():
    global _client
    if _client is None:
        if OpenAI is None:
            raise RuntimeError("OpenAI client is unavailable (openai package not installed).")
        _client = OpenAI()
    return _client

# Dimension for local fallback embeddings (matches text-embedding-3-small)
LOCAL_EMBEDDING_DIM = 1536

# Track whether we've already warned about fallback mode
_warned_fallback = False


def _local_embedding(text: str) -> np.ndarray:
    """
    Generate a deterministic pseudo-embedding from text using hashing.
    This is NOT semantically meaningful — it's a fallback for testing
    when the OpenAI API is unavailable.
    """
    global _warned_fallback
    if not _warned_fallback:
        logger.warning(
            "Using LOCAL fallback embeddings. These are NOT semantically meaningful. "
            "For production use, ensure OPENAI_API_KEY is set and the API is reachable."
        )
        _warned_fallback = True
    
    # Create a deterministic hash-based vector
    text_bytes = text.encode("utf-8")
    # Use multiple hash rounds to fill the vector
    vector = []
    for i in range(LOCAL_EMBEDDING_DIM // 16 + 1):
        h = hashlib.md5(text_bytes + i.to_bytes(4, "big")).digest()
        for byte in h:
            vector.append((byte / 255.0) * 2 - 1)  # Normalize to [-1, 1]
    
    vec = np.array(vector[:LOCAL_EMBEDDING_DIM], dtype=np.float32)
    # Normalize to unit length
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def get_embedding(text: str) -> np.ndarray:
    """
    Generate an embedding vector for a single text string.
    
    Tries the OpenAI API first; falls back to local embeddings on failure.
    
    Args:
        text: The text to embed.
    
    Returns:
        A numpy array of the embedding vector.
    """
    text = text.replace("\n", " ").strip()
    if not text:
        text = "empty"
    
    try:
        client = _get_client()
        response = client.embeddings.create(
            input=[text],
            model=EMBEDDING_MODEL
        )
        return np.array(response.data[0].embedding, dtype=np.float32)
    except Exception as e:
        logger.debug(f"OpenAI embedding API failed: {e}. Using local fallback.")
        return _local_embedding(text)


def get_embeddings_batch(texts: list[str]) -> np.ndarray:
    """
    Generate embeddings for a batch of texts.
    
    Tries the OpenAI API first; falls back to local embeddings on failure.
    
    Args:
        texts: List of text strings to embed.
    
    Returns:
        A numpy array of shape (len(texts), embedding_dim).
    """
    cleaned = [t.replace("\n", " ").strip() or "empty" for t in texts]
    
    try:
        # OpenAI supports batching — process in chunks of 100
        all_embeddings = []
        batch_size = 100
        
        for i in range(0, len(cleaned), batch_size):
            batch = cleaned[i:i + batch_size]
            client = _get_client()
            response = client.embeddings.create(
                input=batch,
                model=EMBEDDING_MODEL
            )
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)
        
        return np.array(all_embeddings, dtype=np.float32)
    
    except Exception as e:
        logger.debug(f"OpenAI batch embedding API failed: {e}. Using local fallback.")
        embeddings = [_local_embedding(t) for t in cleaned]
        return np.array(embeddings, dtype=np.float32)
