"""
RAG Engine — orchestrates the full retrieval-augmented generation pipeline.

This module:
1. Indexes all KB entries (chunk → embed → store in FAISS)
2. On query: embed query → search FAISS → return relevant chunks
"""

import logging
import threading
from pathlib import Path
from contextlib import contextmanager
import numpy as np

from ai_chatbot import database as db
from ai_chatbot.config import FAISS_INDEX_PATH
from ai_chatbot.rag.chunker import create_chunks_for_entry
from ai_chatbot.rag.embeddings import get_embedding, get_embeddings_batch
from ai_chatbot.rag.vector_store import get_vector_store, reset_vector_store

logger = logging.getLogger(__name__)

_INDEX_STALE_FLAG: Path = FAISS_INDEX_PATH / ".stale"
_INDEX_STATE_LOCK_FILE: Path = FAISS_INDEX_PATH / ".index_state.lock"
_REBUILD_LOCK = threading.RLock()


@contextmanager
def _index_state_lock():
    """
    Cross-process lock for reading/writing the index state files.
    """
    FAISS_INDEX_PATH.mkdir(parents=True, exist_ok=True)
    f = _INDEX_STATE_LOCK_FILE.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl  # Linux/Unix only
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            # Best-effort: if flock isn't available, keep going with in-process lock only.
            pass
        yield
    finally:
        try:
            import fcntl  # Linux/Unix only
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


def _stale_token() -> int | None:
    try:
        return _INDEX_STALE_FLAG.stat().st_mtime_ns
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _maybe_clear_stale(start_token: int | None) -> None:
    """
    Clear the stale flag only if it was not touched during the rebuild.
    """
    if start_token is None:
        # Either there was no stale flag at rebuild start, or we couldn't read it.
        # If it exists now, assume new KB changes happened during rebuild.
        return
    with _index_state_lock():
        end_token = _stale_token()
        if end_token == start_token:
            try:
                _INDEX_STALE_FLAG.unlink()
            except FileNotFoundError:
                pass


def mark_index_stale() -> None:
    with _index_state_lock():
        FAISS_INDEX_PATH.mkdir(parents=True, exist_ok=True)
        _INDEX_STALE_FLAG.touch(exist_ok=True)


def clear_index_stale() -> None:
    with _index_state_lock():
        try:
            _INDEX_STALE_FLAG.unlink()
        except FileNotFoundError:
            pass


def is_index_stale() -> bool:
    with _index_state_lock():
        return _INDEX_STALE_FLAG.exists()


def rebuild_index():
    """
    Rebuild the entire FAISS index from all active KB entries.
    
    Steps:
    1. Load all KB entries from the database.
    2. Chunk each entry.
    3. Generate embeddings for all chunks.
    4. Build the FAISS index.
    5. Save chunks to the database and index to disk.
    """
    with _REBUILD_LOCK:
        logger.info("Rebuilding RAG index...")
        with _index_state_lock():
            start_stale_token = _stale_token()

        entries = db.get_all_kb_entries(active_only=True)
        if not entries:
            # Auto-seed: if the database is completely empty, populate it with
            # demo data so the bot can answer questions out of the box.
            logger.warning("No KB entries found. Attempting to seed demo data...")
            try:
                from ai_chatbot.seed_data import seed_database
                seed_database()
                entries = db.get_all_kb_entries(active_only=True)
            except Exception:
                logger.exception("Failed to auto-seed demo data.")

        if not entries:
            logger.warning("No KB entries found after seed attempt. Creating empty index.")
            store = get_vector_store()
            store.build_index(np.array([]), [])
            store.save()
            _maybe_clear_stale(start_stale_token)
            return

        # Step 1: Create chunks for all entries
        all_chunks = []
        for entry in entries:
            chunks = create_chunks_for_entry(
                entry_id=entry["id"],
                category=entry["category"],
                title=entry["title"],
                content=entry["content"]
            )
            all_chunks.extend(chunks)

        if not all_chunks:
            logger.warning("No chunks created. Creating empty index.")
            store = get_vector_store()
            store.build_index(np.array([]), [])
            store.save()
            _maybe_clear_stale(start_stale_token)
            return

        logger.info(
            "Created %s chunks from %s entries",
            len(all_chunks),
            len(entries),
        )

        # Step 2: Generate embeddings
        chunk_texts = [c["text"] for c in all_chunks]
        embeddings = get_embeddings_batch(chunk_texts)

        logger.info("Generated %s embeddings", len(embeddings))

        # Step 3: Prepare metadata
        metadata = []
        for chunk in all_chunks:
            metadata.append({
                "entry_id": chunk["entry_id"],
                "chunk_index": chunk["index"],
                "category": chunk["category"],
                "title": chunk["title"],
                "text": chunk["text"]
            })

        # Step 4: Build and save the index
        reset_vector_store()
        store = get_vector_store()
        store.build_index(embeddings, metadata)
        store.save()

        # Step 5: Save chunks with embeddings to database
        chunks_by_entry = {}
        for i, chunk in enumerate(all_chunks):
            eid = chunk["entry_id"]
            if eid not in chunks_by_entry:
                chunks_by_entry[eid] = []
            chunks_by_entry[eid].append({
                "index": chunk["index"],
                "text": chunk["text"],
                "embedding": embeddings[i].tobytes()
            })

        for entry_id, entry_chunks in chunks_by_entry.items():
            db.save_chunks(entry_id, entry_chunks)

        _maybe_clear_stale(start_stale_token)
        logger.info("RAG index rebuild complete!")


def retrieve(query: str, top_k: int = None) -> list[dict]:
    """
    Retrieve the most relevant chunks for a user query.
    
    Args:
        query: The user's question in natural language.
        top_k: Number of chunks to retrieve (defaults to config).
    
    Returns:
        List of relevant chunk dicts with text, category, title, and score.
    """
    if is_index_stale():
        with _REBUILD_LOCK:
            if is_index_stale():
                logger.info("RAG index marked stale. Rebuilding before retrieval...")
                try:
                    rebuild_index()
                except Exception:
                    logger.exception("Failed rebuilding stale RAG index; continuing with existing index.")

    store = get_vector_store()
    
    if store.index is None or store.index.ntotal == 0:
        logger.warning("Index is empty. Attempting to rebuild...")
        rebuild_index()
        store = get_vector_store()
        if store.index is None or store.index.ntotal == 0:
            return []
    
    # Embed the query
    query_embedding = get_embedding(query)
    
    # Search
    results = store.search(query_embedding, top_k=top_k)
    
    logger.info("Retrieved %s chunks for query: '%s...'", len(results), query[:50])
    return results


def format_context(chunks: list[dict]) -> str:
    """
    Format retrieved chunks into a context string for the LLM.
    
    Args:
        chunks: List of chunk dicts from retrieve().
    
    Returns:
        Formatted context string with source labels.
    """
    if not chunks:
        return "No relevant information found in the knowledge base."
    
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        source_label = f"{chunk['category']} — {chunk['title']}"
        context_parts.append(
            f"--- Context {i} (Source: {source_label}) ---\n{chunk['text']}"
        )
    
    return "\n\n".join(context_parts)
