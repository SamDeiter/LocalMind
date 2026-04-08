"""
Session Memory Cache -- fast in-process KV store for conversation context.

This is the "short-term memory" tier. Lives for one session (or until TTL
expires). Complements the FTS5 long-term store in retrieval.py.

Design: dict-backed with TTL, max-size eviction (LRU), and category tagging.
NOT persisted to disk -- intentionally ephemeral.
"""

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("localmind.memory.session_cache")


@dataclass
class SessionEntry:
    """A single session memory entry."""

    key: str
    value: str
    category: str  # "context", "tool_result", "working_file", "user_intent"
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    ttl: float = 3600.0  # 1 hour default

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl


class SessionMemoryCache:
    """LRU session cache with TTL and category filtering.

    This provides the fast, ephemeral "Tier 1" of the dual memory
    architecture. Entries are never written to disk -- they exist
    only for the lifetime of the server process (or until their TTL
    expires / they are evicted by LRU).

    Typical categories:
      - "context"      -- conversation context snippets
      - "tool_result"  -- results from recent tool executions
      - "working_file" -- files the user is actively editing
      - "user_intent"  -- inferred user goals from recent messages
    """

    def __init__(self, max_size: int = 200, default_ttl: float = 3600.0):
        self._store: OrderedDict[str, SessionEntry] = OrderedDict()
        self._max_size = max_size
        self._default_ttl = default_ttl

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def put(
        self,
        key: str,
        value: str,
        category: str = "context",
        ttl: Optional[float] = None,
    ) -> None:
        """Store a session memory entry.

        If the key already exists it is updated in-place and moved to the
        end of the LRU order.  When the cache exceeds ``max_size``, the
        least-recently-accessed entry is evicted.
        """
        now = time.time()

        if key in self._store:
            # Update existing entry and move to end (most-recent)
            entry = self._store[key]
            entry.value = value
            entry.category = category
            entry.accessed_at = now
            entry.access_count += 1
            entry.ttl = ttl if ttl is not None else self._default_ttl
            self._store.move_to_end(key)
        else:
            entry = SessionEntry(
                key=key,
                value=value,
                category=category,
                created_at=now,
                accessed_at=now,
                access_count=0,
                ttl=ttl if ttl is not None else self._default_ttl,
            )
            self._store[key] = entry

        # Evict LRU entries if we've exceeded capacity
        while len(self._store) > self._max_size:
            evicted_key, _ = self._store.popitem(last=False)
            logger.debug("LRU eviction: %s", evicted_key)

    def get(self, key: str) -> Optional[str]:
        """Retrieve by key, updating access time. Returns None if expired/missing."""
        entry = self._store.get(key)
        if entry is None:
            return None

        if entry.is_expired:
            del self._store[key]
            return None

        # Touch: update access time & count, move to end (most-recent)
        entry.accessed_at = time.time()
        entry.access_count += 1
        self._store.move_to_end(key)
        return entry.value

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 5,
    ) -> list[SessionEntry]:
        """Simple case-insensitive substring search across session entries.

        Matches against both key and value.  Expired entries are skipped
        (and lazily removed).
        """
        self.evict_expired()
        query_lower = query.lower()
        results: list[SessionEntry] = []

        for entry in reversed(list(self._store.values())):
            if category and entry.category != category:
                continue
            if query_lower in entry.key.lower() or query_lower in entry.value.lower():
                results.append(entry)
                if len(results) >= limit:
                    break

        return results

    def get_by_category(
        self,
        category: str,
        limit: int = 10,
    ) -> list[SessionEntry]:
        """Get all entries in a category, sorted by recency (newest first)."""
        self.evict_expired()
        results: list[SessionEntry] = []
        for entry in reversed(list(self._store.values())):
            if entry.category == category:
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def evict_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        expired_keys = [k for k, v in self._store.items() if v.is_expired]
        for k in expired_keys:
            del self._store[k]
        if expired_keys:
            logger.debug("Evicted %d expired entries", len(expired_keys))
        return len(expired_keys)

    def clear(self) -> None:
        """Clear all session memory."""
        self._store.clear()

    def stats(self) -> dict:
        """Return cache statistics."""
        now = time.time()
        categories: dict[str, int] = {}
        total_accesses = 0
        expired_count = 0

        for entry in self._store.values():
            categories[entry.category] = categories.get(entry.category, 0) + 1
            total_accesses += entry.access_count
            if entry.is_expired:
                expired_count += 1

        active_count = len(self._store) - expired_count

        return {
            "total_entries": len(self._store),
            "active_entries": active_count,
            "expired_entries": expired_count,
            "max_size": self._max_size,
            "default_ttl": self._default_ttl,
            "categories": categories,
            "total_accesses": total_accesses,
        }

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return False
        if entry.is_expired:
            del self._store[key]
            return False
        return True


# ── Module-level singleton ───────────────────────────────────────────
_session_cache: Optional[SessionMemoryCache] = None


def get_session_cache() -> SessionMemoryCache:
    """Return (or create) the module-level SessionMemoryCache singleton."""
    global _session_cache
    if _session_cache is None:
        _session_cache = SessionMemoryCache()
    return _session_cache


def reset_session_cache() -> None:
    """Reset the singleton (primarily for testing)."""
    global _session_cache
    _session_cache = None
