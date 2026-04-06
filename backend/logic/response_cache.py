"""
Semantic Response Cache — Avoid redundant LLM calls for similar queries.

Uses ChromaDB to store query embeddings alongside cached responses.
On each new query, the cache embeds the query via Ollama and checks
ChromaDB for a nearest neighbor above the similarity threshold.
If found (and not expired), it returns the cached response immediately.

Graceful degradation: if no embedding model is available, the cache
disables itself silently instead of crashing.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

import chromadb
import httpx

from backend import config

logger = logging.getLogger("localmind.logic.response_cache")


@dataclass
class CacheStats:
    """Running statistics for cache performance monitoring."""
    hits: int = 0
    misses: int = 0
    stores: int = 0
    evictions: int = 0
    total_latency_saved_ms: float = 0.0
    _hit_latencies: List[float] = field(default_factory=list)

    @property
    def total_lookups(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total_lookups if self.total_lookups > 0 else 0.0

    @property
    def miss_rate(self) -> float:
        return self.misses / self.total_lookups if self.total_lookups > 0 else 0.0

    @property
    def avg_latency_saved_ms(self) -> float:
        return (
            self.total_latency_saved_ms / self.hits if self.hits > 0 else 0.0
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "evictions": self.evictions,
            "total_lookups": self.total_lookups,
            "hit_rate": round(self.hit_rate, 4),
            "miss_rate": round(self.miss_rate, 4),
            "total_latency_saved_ms": round(self.total_latency_saved_ms, 1),
            "avg_latency_saved_ms": round(self.avg_latency_saved_ms, 1),
        }


class SemanticCache:
    """ChromaDB-backed semantic cache for LLM responses.

    Parameters
    ----------
    ollama_base_url : str
        Base URL for the Ollama API (used to generate embeddings).
    embed_model : str
        Ollama embedding model name.
    similarity_threshold : float
        Minimum cosine similarity (0-1) to consider a cache hit.
    ttl_seconds : float
        Time-to-live for cache entries in seconds.
    max_cache_size : int
        Maximum number of entries; oldest are evicted when exceeded.
    enabled : bool
        Master on/off switch.
    """

    def __init__(
        self,
        ollama_base_url: str = config.OLLAMA_BASE_URL,
        embed_model: str = config.CACHE_EMBED_MODEL,
        similarity_threshold: float = config.CACHE_SIMILARITY_THRESHOLD,
        ttl_seconds: float = config.CACHE_TTL_SECONDS,
        max_cache_size: int = config.CACHE_MAX_SIZE,
        enabled: bool = config.CACHE_ENABLED,
    ):
        self.ollama_url = ollama_base_url.rstrip("/")
        self.embed_model = embed_model
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.max_cache_size = max_cache_size
        self.enabled = enabled

        self.stats = CacheStats()

        # Lazy-init ChromaDB resources
        self._client: Optional[chromadb.PersistentClient] = None
        self._collection = None
        self._embed_available: Optional[bool] = None  # None = not yet checked

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_collection(self):
        """Get or create the response_cache ChromaDB collection."""
        if self._collection is None:
            from pathlib import Path

            cache_dir = Path(__file__).parent.parent / "cache_data"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(cache_dir))
            self._collection = self._client.get_or_create_collection(
                name="response_cache",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    async def _embed(self, text: str) -> Optional[List[float]]:
        """Generate an embedding via Ollama. Returns None on failure."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/embed",
                    json={"model": self.embed_model, "input": text},
                )
                resp.raise_for_status()
                data = resp.json()
                # Ollama returns {"embeddings": [[...]]}
                return data["embeddings"][0]
        except Exception as exc:
            if self._embed_available is None:
                # First failure -- log and disable cache gracefully
                logger.warning(
                    "Embedding model '%s' unavailable (%s). "
                    "Semantic cache disabled.",
                    self.embed_model,
                    exc,
                )
                self._embed_available = False
            return None

    def _is_available(self) -> bool:
        """Check whether the cache should be used right now."""
        if not self.enabled:
            return False
        if self._embed_available is False:
            return False
        return True

    def _build_cache_key(self, messages: List[Dict[str, str]], n_recent: int = 3) -> str:
        """Concatenate the last N user messages into a single cache key string."""
        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        recent = user_msgs[-n_recent:]
        return " ".join(recent)

    async def _evict_expired(self):
        """Remove entries older than TTL."""
        collection = self._get_collection()
        if collection.count() == 0:
            return

        cutoff = str(time.time() - self.ttl_seconds)
        try:
            # ChromaDB where filter on metadata
            expired = collection.get(
                where={"stored_at": {"$lt": float(cutoff)}},
                include=[],
            )
            if expired and expired["ids"]:
                collection.delete(ids=expired["ids"])
                self.stats.evictions += len(expired["ids"])
                logger.debug("Evicted %d expired cache entries.", len(expired["ids"]))
        except Exception as exc:
            # ChromaDB metadata filtering can be finicky; log and move on
            logger.debug("Cache eviction sweep failed: %s", exc)

    async def _enforce_max_size(self):
        """If over max_cache_size, remove oldest entries."""
        collection = self._get_collection()
        count = collection.count()
        if count <= self.max_cache_size:
            return

        excess = count - self.max_cache_size
        try:
            all_entries = collection.get(include=["metadatas"])
            # Sort by stored_at ascending (oldest first)
            paired = list(zip(all_entries["ids"], all_entries["metadatas"]))
            paired.sort(key=lambda p: float(p[1].get("stored_at", 0)))
            ids_to_remove = [p[0] for p in paired[:excess]]
            if ids_to_remove:
                collection.delete(ids=ids_to_remove)
                self.stats.evictions += len(ids_to_remove)
                logger.debug("Evicted %d entries to enforce max size.", len(ids_to_remove))
        except Exception as exc:
            logger.debug("Max-size enforcement failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lookup(
        self,
        messages: List[Dict[str, str]],
        model: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Check the cache for a semantically similar query.

        Returns a dict with ``response``, ``similarity``, and ``cached: True``
        on hit, or None on miss.
        """
        if not self._is_available():
            self.stats.misses += 1
            return None

        cache_key = self._build_cache_key(messages)
        if not cache_key.strip():
            self.stats.misses += 1
            return None

        embedding = await self._embed(cache_key)
        if embedding is None:
            self.stats.misses += 1
            return None

        # Mark embedding as working on first success
        if self._embed_available is None:
            self._embed_available = True
            logger.info(
                "Semantic cache active (model=%s, threshold=%.2f, ttl=%ds).",
                self.embed_model,
                self.similarity_threshold,
                int(self.ttl_seconds),
            )

        start = time.time()
        try:
            collection = self._get_collection()
            if collection.count() == 0:
                self.stats.misses += 1
                return None

            results = collection.query(
                query_embeddings=[embedding],
                n_results=1,
                include=["documents", "metadatas", "distances"],
            )

            if not results["ids"] or not results["ids"][0]:
                self.stats.misses += 1
                return None

            distance = results["distances"][0][0]
            similarity = 1.0 - distance  # cosine distance -> similarity
            meta = results["metadatas"][0][0]

            # Check similarity threshold
            if similarity < self.similarity_threshold:
                self.stats.misses += 1
                return None

            # Check TTL
            stored_at = float(meta.get("stored_at", 0))
            if (time.time() - stored_at) > self.ttl_seconds:
                # Expired — treat as miss and delete
                collection.delete(ids=[results["ids"][0][0]])
                self.stats.evictions += 1
                self.stats.misses += 1
                return None

            # Check model match (optional — avoid returning cached response
            # from a different model if the user switched)
            if model and meta.get("model") and meta["model"] != model:
                self.stats.misses += 1
                return None

            # Cache hit!
            elapsed_ms = (time.time() - start) * 1000
            estimated_llm_ms = float(meta.get("generation_time_ms", 2000))
            latency_saved = max(0, estimated_llm_ms - elapsed_ms)
            self.stats.hits += 1
            self.stats.total_latency_saved_ms += latency_saved

            return {
                "response": results["documents"][0][0],
                "similarity": round(similarity, 4),
                "cached": True,
                "model": meta.get("model", ""),
                "original_tokens": int(meta.get("token_count", 0)),
                "cache_age_s": round(time.time() - stored_at, 1),
            }

        except Exception as exc:
            logger.warning("Cache lookup error: %s", exc)
            self.stats.misses += 1
            return None

    async def store(
        self,
        messages: List[Dict[str, str]],
        response_text: str,
        model: str = "",
        token_count: int = 0,
        generation_time_ms: float = 0.0,
    ) -> bool:
        """Store a query+response pair in the cache.

        Returns True on success, False on failure or if cache is disabled.
        """
        if not self._is_available():
            return False

        cache_key = self._build_cache_key(messages)
        if not cache_key.strip() or not response_text.strip():
            return False

        embedding = await self._embed(cache_key)
        if embedding is None:
            return False

        try:
            collection = self._get_collection()

            entry_id = str(uuid.uuid4())
            collection.add(
                ids=[entry_id],
                documents=[response_text],
                embeddings=[embedding],
                metadatas=[{
                    "model": model,
                    "stored_at": time.time(),
                    "token_count": token_count,
                    "generation_time_ms": generation_time_ms,
                    "query_preview": cache_key[:200],
                }],
            )
            self.stats.stores += 1

            # Periodic maintenance
            await self._enforce_max_size()

            return True

        except Exception as exc:
            logger.warning("Cache store error: %s", exc)
            return False

    async def invalidate(self, query_text: str) -> int:
        """Remove cache entries similar to the given query text.

        Returns the number of entries removed.
        """
        if not self._is_available():
            return 0

        embedding = await self._embed(query_text)
        if embedding is None:
            return 0

        try:
            collection = self._get_collection()
            if collection.count() == 0:
                return 0

            # Find close matches and remove them
            results = collection.query(
                query_embeddings=[embedding],
                n_results=min(10, collection.count()),
                include=["distances"],
            )

            ids_to_remove = []
            for i, dist in enumerate(results["distances"][0]):
                similarity = 1.0 - dist
                if similarity >= self.similarity_threshold:
                    ids_to_remove.append(results["ids"][0][i])

            if ids_to_remove:
                collection.delete(ids=ids_to_remove)
                self.stats.evictions += len(ids_to_remove)

            return len(ids_to_remove)

        except Exception as exc:
            logger.warning("Cache invalidation error: %s", exc)
            return 0

    async def clear(self) -> int:
        """Remove all cache entries. Returns the number of entries removed."""
        try:
            collection = self._get_collection()
            count = collection.count()
            if count > 0:
                # Delete all by getting all IDs
                all_ids = collection.get(include=[])["ids"]
                if all_ids:
                    collection.delete(ids=all_ids)
                    self.stats.evictions += len(all_ids)
            return count
        except Exception as exc:
            logger.warning("Cache clear error: %s", exc)
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """Return current cache statistics."""
        entry_count = 0
        try:
            if self._collection is not None:
                entry_count = self._collection.count()
        except Exception:
            pass

        stats = self.stats.to_dict()
        stats["enabled"] = self.enabled
        stats["available"] = self._is_available()
        stats["entry_count"] = entry_count
        stats["similarity_threshold"] = self.similarity_threshold
        stats["ttl_seconds"] = self.ttl_seconds
        stats["max_cache_size"] = self.max_cache_size
        stats["embed_model"] = self.embed_model
        return stats
