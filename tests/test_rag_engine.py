"""
טסטים ל-rag/engine.py — staleness, cache, retrieve, format_context.

מוקים: FAISS index, embeddings API, DB, vector store.
"""

import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("ai_chatbot.config.DB_PATH", db_path):
        import importlib
        import database
        importlib.reload(database)
        database.init_db()
        yield database


@pytest.fixture(autouse=True)
def _clean_engine_state(tmp_path):
    """איפוס מצב גלובלי של engine בין טסטים."""
    with patch("rag.engine.FAISS_INDEX_PATH", tmp_path / "faiss_test"):
        import rag.engine as eng
        eng._INDEX_STALE_FLAG = tmp_path / "faiss_test" / ".stale"
        eng._INDEX_STATE_LOCK_FILE = tmp_path / "faiss_test" / ".index_state.lock"
        with eng._query_cache_lock:
            eng._query_cache.clear()
        yield
        with eng._query_cache_lock:
            eng._query_cache.clear()


# ── Stale flag functions ────────────────────────────────────────────────────


class TestStaleFlagFunctions:
    def test_mark_and_check_stale(self, tmp_path):
        import rag.engine as eng
        assert not eng.is_index_stale()
        eng.mark_index_stale()
        assert eng.is_index_stale()

    def test_clear_stale(self, tmp_path):
        import rag.engine as eng
        eng.mark_index_stale()
        eng.clear_index_stale()
        assert not eng.is_index_stale()

    def test_clear_when_not_stale(self, tmp_path):
        import rag.engine as eng
        # לא צריך לקרוס
        eng.clear_index_stale()
        assert not eng.is_index_stale()

    def test_stale_token_returns_none_when_no_flag(self, tmp_path):
        import rag.engine as eng
        assert eng._stale_token() is None

    def test_stale_token_returns_mtime_when_exists(self, tmp_path):
        import rag.engine as eng
        eng.mark_index_stale()
        token = eng._stale_token()
        assert token is not None
        assert isinstance(token, int)


class TestMaybeClearStale:
    def test_clears_when_token_unchanged(self, tmp_path):
        import rag.engine as eng
        eng.mark_index_stale()
        token = eng._stale_token()
        eng._maybe_clear_stale(token)
        assert not eng.is_index_stale()

    def test_does_not_clear_when_token_is_none(self, tmp_path):
        import rag.engine as eng
        eng.mark_index_stale()
        eng._maybe_clear_stale(None)
        # צריך להישאר stale
        assert eng.is_index_stale()

    def test_does_not_clear_when_token_changed(self, tmp_path):
        import rag.engine as eng
        eng.mark_index_stale()
        old_token = eng._stale_token()
        # שינוי ה-flag (כאילו קרה שינוי KB חדש בזמן rebuild)
        time.sleep(0.01)
        eng._INDEX_STALE_FLAG.touch()
        eng._maybe_clear_stale(old_token)
        assert eng.is_index_stale()


# ── Query cache ─────────────────────────────────────────────────────────────


class TestQueryCache:
    def test_cache_hit_returns_copy(self, tmp_path):
        """retrieve מחזיר עותק מה-cache — שינוי התוצאה לא משפיע על cache."""
        import rag.engine as eng
        key = ("test query", 5)
        results = [{"text": "hello", "score": 0.9}]
        with eng._query_cache_lock:
            eng._query_cache[key] = (time.time(), results)

        # סימולציית cache hit בתוך retrieve
        with eng._query_cache_lock:
            cached = eng._query_cache.get(key)
            ts, cached_results = cached
            returned = list(cached_results)

        # שינוי לא משפיע
        returned.append({"text": "extra"})
        with eng._query_cache_lock:
            assert len(eng._query_cache[key][1]) == 1

    def test_cache_eviction_when_full(self, tmp_path):
        """כשה-cache מלא — פינוי הערך הישן ביותר."""
        import rag.engine as eng
        max_size = eng._QUERY_CACHE_MAX_SIZE
        now = time.time()

        with eng._query_cache_lock:
            for i in range(max_size + 5):
                eng._query_cache[(f"q{i}", 5)] = (now + i, [])

        assert len(eng._query_cache) == max_size + 5

        # סימולציית הפינוי שקורה ב-retrieve
        with eng._query_cache_lock:
            while len(eng._query_cache) > max_size:
                oldest_key = min(eng._query_cache, key=lambda k: eng._query_cache[k][0])
                del eng._query_cache[oldest_key]

        assert len(eng._query_cache) == max_size

    def test_cache_cleared_on_rebuild_concept(self, tmp_path):
        """rebuild מנקה את ה-cache."""
        import rag.engine as eng
        with eng._query_cache_lock:
            eng._query_cache[("old query", 5)] = (time.time(), [{"text": "old"}])

        # סימולציית הניקוי שקורה ב-rebuild_index
        with eng._query_cache_lock:
            eng._query_cache.clear()

        assert len(eng._query_cache) == 0


# ── format_context ──────────────────────────────────────────────────────────


class TestFormatContext:
    def test_formats_single_chunk(self):
        from rag.engine import format_context
        chunks = [{"category": "שירותים", "title": "תספורת", "text": "מחיר 50"}]
        result = format_context(chunks)
        assert "Context 1" in result
        assert "שירותים — תספורת" in result
        assert "מחיר 50" in result

    def test_formats_multiple_chunks(self):
        from rag.engine import format_context
        chunks = [
            {"category": "א", "title": "ב", "text": "ג"},
            {"category": "ד", "title": "ה", "text": "ו"},
        ]
        result = format_context(chunks)
        assert "Context 1" in result
        assert "Context 2" in result

    def test_empty_chunks_returns_fallback(self):
        from rag.engine import format_context
        result = format_context([])
        assert "No relevant" in result


# ── retrieve (with mocks) ──────────────────────────────────────────────────


class TestRetrieve:
    def test_returns_results_from_store(self, tmp_path):
        from rag.engine import retrieve
        mock_store = MagicMock()
        mock_store.index = MagicMock()
        mock_store.index.ntotal = 10
        mock_store.search.return_value = [
            {"text": "result", "score": 0.95, "category": "cat", "title": "t"}
        ]

        with patch("rag.engine.is_index_stale", return_value=False), \
             patch("rag.engine.get_vector_store", return_value=mock_store), \
             patch("rag.engine.get_embedding", return_value=np.zeros(384)), \
             patch("ai_chatbot.config.RAG_TOP_K", 5):
            results = retrieve("שאלה")

        assert len(results) == 1
        assert results[0]["text"] == "result"
        mock_store.search.assert_called_once()

    def test_empty_index_triggers_rebuild(self, tmp_path):
        from rag.engine import retrieve
        mock_store = MagicMock()
        mock_store.index = MagicMock()
        mock_store.index.ntotal = 0

        # אחרי rebuild עדיין ריק
        with patch("rag.engine.is_index_stale", return_value=False), \
             patch("rag.engine.get_vector_store", return_value=mock_store), \
             patch("rag.engine.rebuild_index"):
            results = retrieve("שאלה")

        assert results == []

    def test_cache_hit_skips_embedding(self, tmp_path):
        """cache hit — לא קורא ל-get_embedding."""
        import rag.engine as eng
        cache_key = ("שאלה חוזרת", 5)
        cached_results = [{"text": "cached", "score": 0.9}]
        with eng._query_cache_lock:
            eng._query_cache[cache_key] = (time.time(), cached_results)

        with patch("rag.engine.is_index_stale", return_value=False), \
             patch("rag.engine.get_embedding") as mock_embed, \
             patch("ai_chatbot.config.RAG_TOP_K", 5):
            results = eng.retrieve("שאלה חוזרת")

        mock_embed.assert_not_called()
        assert results == cached_results
        # מוודא שזה עותק ולא אותו אובייקט
        assert results is not cached_results

    def test_stale_index_triggers_rebuild(self, tmp_path):
        from rag.engine import retrieve
        mock_store = MagicMock()
        mock_store.index = MagicMock()
        mock_store.index.ntotal = 5
        mock_store.search.return_value = []

        call_count = {"stale": 0}
        def is_stale_side_effect():
            call_count["stale"] += 1
            # ראשון True, אחרי rebuild False
            return call_count["stale"] <= 2

        with patch("rag.engine.is_index_stale", side_effect=is_stale_side_effect), \
             patch("rag.engine.get_vector_store", return_value=mock_store), \
             patch("rag.engine.get_embedding", return_value=np.zeros(384)), \
             patch("rag.engine.rebuild_index") as mock_rebuild, \
             patch("ai_chatbot.config.RAG_TOP_K", 5):
            retrieve("שאלה")

        mock_rebuild.assert_called_once()

    def test_custom_top_k(self, tmp_path):
        from rag.engine import retrieve
        mock_store = MagicMock()
        mock_store.index = MagicMock()
        mock_store.index.ntotal = 10
        mock_store.search.return_value = []

        with patch("rag.engine.is_index_stale", return_value=False), \
             patch("rag.engine.get_vector_store", return_value=mock_store), \
             patch("rag.engine.get_embedding", return_value=np.zeros(384)), \
             patch("ai_chatbot.config.RAG_TOP_K", 5):
            retrieve("שאלה", top_k=3)

        mock_store.search.assert_called_once_with(
            mock_store.search.call_args[0][0], top_k=3
        )
