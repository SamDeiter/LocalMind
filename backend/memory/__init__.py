"""backend.memory — Dual Memory Architecture (Sprint 7, Feature 3).

Session-scoped KV cache (fast, ephemeral) + long-term FTS5 store (persistent).
"""

from .session_cache import SessionMemoryCache, get_session_cache

__all__ = ["SessionMemoryCache", "get_session_cache"]
