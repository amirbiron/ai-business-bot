"""
RAG Engine — orchestrates the full retrieval-augmented generation pipeline.

This module:
1. Indexes all KB entries (chunk → embed → store in FAISS)
2. On query: embed query → search FAISS → return relevant chunks

Supports **incremental rebuilds**: when a rebuild is triggered, only entries
whose chunk texts have changed since the last index build are re-embedded.
Unchanged entries reuse their stored embeddings from the database.
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
    Rebuild the FAISS index from all active KB entries.

    Uses **incremental embedding**: for each entry the new chunk texts are
    compared against the chunks already stored in the database.  Only entries
    whose chunk texts have changed are sent to the embedding API; unchanged
    entries reuse their stored embeddings.  This dramatically reduces API
    calls when only a small number of entries were added or edited.

    Steps:
    1. Load all active KB entries and create chunks.
    2. Load existing stored chunks from the DB.
    3. Determine which entries have changed.
    4. Generate embeddings only for changed entries.
    5. Build the FAISS index from all embeddings (reused + new).
    6. Save changed chunks to the database and index to disk.
    """
    with _REBUILD_LOCK:
        logger.info("Rebuilding RAG index...")
        with _index_state_lock():
            start_stale_token = _stale_token()

        entries = db.get_all_kb_entries(active_only=True)
        if not entries:
            logger.warning("No KB entries found. Creating empty index.")
            store = get_vector_store()
            store.build_index(np.array([]), [])
            store.save()
            _maybe_clear_stale(start_stale_token)
            return

        # Step 1: Create chunks for all entries
        all_chunks = []
        chunks_by_entry: dict[int, list[dict]] = {}
        for entry in entries:
            chunks = create_chunks_for_entry(
                entry_id=entry["id"],
                category=entry["category"],
                title=entry["title"],
                content=entry["content"],
            )
            all_chunks.extend(chunks)
            chunks_by_entry[entry["id"]] = chunks

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

        # Step 2: Load existing stored chunks to detect changes
        entry_ids = list(chunks_by_entry.keys())
        stored_chunks = db.get_chunks_for_entries(entry_ids)

        # Step 3: Determine which entries have changed by comparing chunk texts
        changed_entry_ids: set[int] = set()
        unchanged_entry_ids: set[int] = set()

        for eid, new_chunks in chunks_by_entry.items():
            old_chunks = stored_chunks.get(eid, [])
            new_texts = [c["text"] for c in new_chunks]
            old_texts = [c["chunk_text"] for c in old_chunks]

            if new_texts == old_texts and len(old_chunks) == len(new_chunks):
                unchanged_entry_ids.add(eid)
            else:
                changed_entry_ids.add(eid)

        logger.info(
            "Incremental rebuild: %d entries unchanged, %d entries need re-embedding",
            len(unchanged_entry_ids),
            len(changed_entry_ids),
        )

        # Step 4: Build embeddings — reuse stored ones for unchanged, generate for changed
        all_embeddings = []
        all_metadata = []
        entries_to_save: dict[int, list[dict]] = {}  # only changed entries

        for chunk in all_chunks:
            eid = chunk["entry_id"]
            all_metadata.append({
                "entry_id": eid,
                "chunk_index": chunk["index"],
                "category": chunk["category"],
                "title": chunk["title"],
                "text": chunk["text"],
            })

        # Collect chunks that need new embeddings
        new_embed_indices: list[int] = []  # positions in all_chunks
        new_embed_texts: list[str] = []

        for i, chunk in enumerate(all_chunks):
            eid = chunk["entry_id"]
            if eid in unchanged_entry_ids:
                # Reuse stored embedding
                old_chunks = stored_chunks[eid]
                matching = [c for c in old_chunks if c["chunk_index"] == chunk["index"]]
                if matching and matching[0]["embedding"]:
                    emb = np.frombuffer(matching[0]["embedding"], dtype=np.float32).copy()
                    all_embeddings.append(emb)
                    continue
                # Fallback: if stored embedding is missing, re-generate
                changed_entry_ids.add(eid)
                unchanged_entry_ids.discard(eid)

            all_embeddings.append(None)  # placeholder
            new_embed_indices.append(i)
            new_embed_texts.append(chunk["text"])

        # Generate embeddings only for new/changed chunks
        if new_embed_texts:
            new_embeddings = get_embeddings_batch(new_embed_texts)
            for j, idx in enumerate(new_embed_indices):
                all_embeddings[idx] = new_embeddings[j]
            logger.info(
                "Generated %d new embeddings (reused %d from cache)",
                len(new_embed_texts),
                len(all_chunks) - len(new_embed_texts),
            )
        else:
            logger.info("All %d embeddings reused from cache", len(all_chunks))

        embeddings_array = np.array(all_embeddings, dtype=np.float32)

        # Step 5: Build and save the FAISS index
        reset_vector_store()
        store = get_vector_store()
        store.build_index(embeddings_array, all_metadata)
        store.save()

        # Step 6: Save chunks to DB only for changed entries
        for i, chunk in enumerate(all_chunks):
            eid = chunk["entry_id"]
            if eid in changed_entry_ids:
                entries_to_save.setdefault(eid, []).append({
                    "index": chunk["index"],
                    "text": chunk["text"],
                    "embedding": embeddings_array[i].tobytes(),
                })

        for entry_id, entry_chunks in entries_to_save.items():
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
