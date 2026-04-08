"""Tests for the Dual Memory Architecture — Session Cache (Tier 1).

Covers:
- Basic put/get operations
- TTL expiration
- LRU eviction when max_size is exceeded
- Category-based filtering
- Substring search (case-insensitive)
- Stats reporting
- Two-tier retrieval integration (session cache + FTS5)
"""

import time
from unittest.mock import patch, MagicMock

import pytest

from backend.memory.session_cache import (
    SessionMemoryCache,
    SessionEntry,
    get_session_cache,
    reset_session_cache,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def cache():
    """Fresh cache for each test."""
    return SessionMemoryCache(max_size=10, default_ttl=3600.0)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure the module-level singleton is fresh for each test."""
    reset_session_cache()
    yield
    reset_session_cache()


# ── Basic put / get ──────────────────────────────────────────────────

class TestPutAndGet:
    def test_put_and_get_basic(self, cache):
        cache.put("k1", "hello world", category="context")
        assert cache.get("k1") == "hello world"

    def test_get_missing_key(self, cache):
        assert cache.get("nonexistent") is None

    def test_put_overwrites_existing_key(self, cache):
        cache.put("k1", "first")
        cache.put("k1", "second")
        assert cache.get("k1") == "second"
        assert len(cache) == 1

    def test_put_updates_category(self, cache):
        cache.put("k1", "val", category="context")
        cache.put("k1", "val", category="tool_result")
        entries = cache.get_by_category("tool_result")
        assert len(entries) == 1
        assert entries[0].key == "k1"

    def test_get_updates_access_count(self, cache):
        cache.put("k1", "val")
        cache.get("k1")
        cache.get("k1")
        # Access count: 0 (initial) + 1 (first get) + 1 (second get) = 2
        entry = cache._store["k1"]
        assert entry.access_count >= 2

    def test_contains(self, cache):
        cache.put("present", "yes")
        assert "present" in cache
        assert "absent" not in cache


# ── TTL expiration ───────────────────────────────────────────────────

class TestTTLExpiration:
    def test_expired_entry_returns_none(self, cache):
        cache.put("k1", "val", ttl=0.01)
        time.sleep(0.02)
        assert cache.get("k1") is None

    def test_non_expired_entry_returns_value(self, cache):
        cache.put("k1", "val", ttl=9999)
        assert cache.get("k1") == "val"

    def test_evict_expired_removes_entries(self, cache):
        cache.put("a", "1", ttl=0.01)
        cache.put("b", "2", ttl=0.01)
        cache.put("c", "3", ttl=9999)
        time.sleep(0.02)
        removed = cache.evict_expired()
        assert removed == 2
        assert len(cache) == 1
        assert cache.get("c") == "3"

    def test_expired_entry_excluded_from_contains(self, cache):
        cache.put("k1", "val", ttl=0.01)
        time.sleep(0.02)
        assert "k1" not in cache

    def test_custom_ttl_per_entry(self, cache):
        cache.put("short", "val", ttl=0.01)
        cache.put("long", "val", ttl=9999)
        time.sleep(0.02)
        assert cache.get("short") is None
        assert cache.get("long") == "val"


# ── LRU eviction ────────────────────────────────────────────────────

class TestLRUEviction:
    def test_evicts_lru_when_over_capacity(self):
        cache = SessionMemoryCache(max_size=3, default_ttl=3600.0)
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")
        # "a" is least recently used
        cache.put("d", "4")
        assert cache.get("a") is None  # evicted
        assert cache.get("b") == "2"
        assert cache.get("d") == "4"

    def test_accessing_entry_prevents_eviction(self):
        cache = SessionMemoryCache(max_size=3, default_ttl=3600.0)
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")
        # Access "a" to make it recently used
        cache.get("a")
        # Now "b" is LRU
        cache.put("d", "4")
        assert cache.get("a") == "1"  # still present
        assert cache.get("b") is None  # evicted

    def test_updating_entry_prevents_eviction(self):
        cache = SessionMemoryCache(max_size=3, default_ttl=3600.0)
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")
        # Update "a" -> moves it to end
        cache.put("a", "updated")
        cache.put("d", "4")
        assert cache.get("a") == "updated"
        assert cache.get("b") is None  # evicted

    def test_max_size_enforced(self):
        cache = SessionMemoryCache(max_size=5, default_ttl=3600.0)
        for i in range(20):
            cache.put(f"k{i}", f"v{i}")
        assert len(cache) == 5


# ── Category-based retrieval ─────────────────────────────────────────

class TestSearchByCategory:
    def test_get_by_category(self, cache):
        cache.put("t1", "result1", category="tool_result")
        cache.put("c1", "ctx1", category="context")
        cache.put("t2", "result2", category="tool_result")
        cache.put("c2", "ctx2", category="context")

        tools = cache.get_by_category("tool_result")
        assert len(tools) == 2
        assert all(e.category == "tool_result" for e in tools)

    def test_get_by_category_respects_limit(self, cache):
        for i in range(10):
            cache.put(f"k{i}", f"v{i}", category="context")
        results = cache.get_by_category("context", limit=3)
        assert len(results) == 3

    def test_get_by_category_returns_newest_first(self, cache):
        cache.put("old", "old_val", category="context")
        cache.put("new", "new_val", category="context")
        results = cache.get_by_category("context")
        assert results[0].key == "new"
        assert results[1].key == "old"

    def test_get_by_category_empty(self, cache):
        cache.put("k1", "v1", category="context")
        assert cache.get_by_category("tool_result") == []

    def test_search_with_category_filter(self, cache):
        cache.put("python_tool", "python result", category="tool_result")
        cache.put("python_ctx", "python context", category="context")
        results = cache.search("python", category="tool_result")
        assert len(results) == 1
        assert results[0].category == "tool_result"


# ── Substring search ────────────────────────────────────────────────

class TestSearchSubstring:
    def test_search_matches_value(self, cache):
        cache.put("k1", "The quick brown fox jumps")
        cache.put("k2", "A lazy dog sleeps")
        results = cache.search("fox")
        assert len(results) == 1
        assert results[0].key == "k1"

    def test_search_matches_key(self, cache):
        cache.put("weather_report", "sunny day")
        results = cache.search("weather")
        assert len(results) == 1

    def test_search_case_insensitive(self, cache):
        cache.put("k1", "Hello World")
        results = cache.search("hello world")
        assert len(results) == 1

    def test_search_respects_limit(self, cache):
        for i in range(10):
            cache.put(f"match_{i}", f"findme {i}")
        results = cache.search("findme", limit=3)
        assert len(results) == 3

    def test_search_no_results(self, cache):
        cache.put("k1", "hello")
        results = cache.search("zzzzz")
        assert results == []

    def test_search_excludes_expired(self, cache):
        cache.put("k1", "findme", ttl=0.01)
        cache.put("k2", "findme too", ttl=9999)
        time.sleep(0.02)
        results = cache.search("findme")
        assert len(results) == 1
        assert results[0].key == "k2"


# ── Stats ────────────────────────────────────────────────────────────

class TestStats:
    def test_stats_empty(self, cache):
        s = cache.stats()
        assert s["total_entries"] == 0
        assert s["active_entries"] == 0
        assert s["categories"] == {}
        assert s["max_size"] == 10
        assert s["default_ttl"] == 3600.0

    def test_stats_with_entries(self, cache):
        cache.put("a", "1", category="context")
        cache.put("b", "2", category="tool_result")
        cache.put("c", "3", category="context")
        s = cache.stats()
        assert s["total_entries"] == 3
        assert s["categories"]["context"] == 2
        assert s["categories"]["tool_result"] == 1

    def test_stats_counts_expired(self, cache):
        cache.put("a", "1", ttl=0.01)
        cache.put("b", "2", ttl=9999)
        time.sleep(0.02)
        s = cache.stats()
        assert s["expired_entries"] == 1
        assert s["active_entries"] == 1


# ── Clear ────────────────────────────────────────────────────────────

class TestClear:
    def test_clear_removes_all(self, cache):
        cache.put("a", "1")
        cache.put("b", "2")
        cache.clear()
        assert len(cache) == 0
        assert cache.get("a") is None


# ── Singleton ────────────────────────────────────────────────────────

class TestSingleton:
    def test_get_session_cache_returns_same_instance(self):
        c1 = get_session_cache()
        c2 = get_session_cache()
        assert c1 is c2

    def test_reset_creates_new_instance(self):
        c1 = get_session_cache()
        reset_session_cache()
        c2 = get_session_cache()
        assert c1 is not c2


# ── Two-tier retrieval integration ───────────────────────────────────

class TestTwoTierRetrieval:
    """Test that MemoryRetriever.retrieve_passive returns session cache
    results before long-term FTS5 results."""

    def test_session_results_come_first(self):
        """Session cache hits should appear before long-term FTS5 hits."""
        from backend.memory.session_cache import get_session_cache

        # Seed session cache
        cache = get_session_cache()
        cache.put("intent:1", "user wants to edit config.py", category="user_intent")

        # Mock the MemoryStore to avoid needing a real SQLite DB
        mock_store = MagicMock()
        mock_store.search.return_value = [
            MagicMock(
                id=1,
                content="User prefers dark mode",
                category="semantic",
                subcategory="preference",
                relevance_score=1.0,
            )
        ]
        mock_store.count.return_value = 1

        from src.agent.memory.retrieval import MemoryRetriever
        retriever = MemoryRetriever(store=mock_store)

        results = retriever.retrieve_passive("edit config", max_memories=5)

        # The first result should be from the session cache
        assert len(results) >= 1
        assert results[0].startswith("[session/")

    def test_session_cache_empty_falls_through(self):
        """When session cache has no hits, all results come from FTS5."""
        mock_store = MagicMock()
        mock_memory = MagicMock(
            id=1,
            content="User prefers vim",
            category="semantic",
            subcategory="preference",
            relevance_score=1.0,
        )
        mock_store.search.return_value = [mock_memory]
        mock_store.count.return_value = 1

        from src.agent.memory.retrieval import MemoryRetriever
        retriever = MemoryRetriever(store=mock_store)

        results = retriever.retrieve_passive("zzzzz_no_match", max_memories=5)

        # Should still get long-term results
        session_results = [r for r in results if r.startswith("[session/")]
        assert len(session_results) == 0

    def test_save_to_session(self):
        """MemoryRetriever.save_to_session should populate the session cache."""
        mock_store = MagicMock()
        from src.agent.memory.retrieval import MemoryRetriever
        retriever = MemoryRetriever(store=mock_store)

        retriever.save_to_session("my_key", "my_value", category="tool_result")

        cache = get_session_cache()
        assert cache.get("my_key") == "my_value"

    def test_max_memories_respected(self):
        """Total results (session + FTS5) should not exceed max_memories."""
        cache = get_session_cache()
        for i in range(10):
            cache.put(f"session_{i}", f"match value {i}", category="context")

        mock_store = MagicMock()
        mock_store.search.return_value = []

        from src.agent.memory.retrieval import MemoryRetriever
        retriever = MemoryRetriever(store=mock_store)

        results = retriever.retrieve_passive("match", max_memories=3)
        assert len(results) <= 3


# ── SessionEntry dataclass ──────────────────────────────────────────

class TestSessionEntry:
    def test_is_expired_false(self):
        e = SessionEntry(key="k", value="v", category="c", ttl=9999)
        assert not e.is_expired

    def test_is_expired_true(self):
        e = SessionEntry(
            key="k",
            value="v",
            category="c",
            created_at=time.time() - 10,
            ttl=1,
        )
        assert e.is_expired
