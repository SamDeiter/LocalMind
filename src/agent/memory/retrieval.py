"""
Memory retrieval — bridges the persistent store with the context window.

Design informed by arXiv:2512.13564 (Memory in the Age of AI Agents):
- Memory retrieval should be *selective* — not all stored memories are
  relevant to every query. Inject only what's needed.
- The paper distinguishes "passive retrieval" (system auto-injects based
  on relevance) from "active retrieval" (agent explicitly queries memory).
  We support both patterns.

arXiv:2512.16301 (Adaptation Survey) — tool-execution-signaled learning:
after a tool call succeeds/fails, reinforce or weaken the memories that
led to that decision.
"""

from __future__ import annotations

import logging
from typing import Optional

from .store import Memory, MemoryStore
from backend.memory.session_cache import get_session_cache

logger = logging.getLogger("agent.memory.retrieval")


class MemoryRetriever:
    """Retrieves relevant memories for injection into agent context.

    Two retrieval modes:
    1. Passive: called automatically before each agent turn to inject
       relevant facts, preferences, and instructions.
    2. Active: called explicitly by the agent via a tool when it needs
       to recall something specific.
    """

    def __init__(self, store: Optional[MemoryStore] = None):
        self.store = store or MemoryStore()

    def retrieve_for_query(
        self,
        query: str,
        max_memories: int = 5,
        categories: Optional[list[str]] = None,
    ) -> list[Memory]:
        """Retrieve memories relevant to a query.

        Args:
            query: The text to search for (user message or topic)
            max_memories: Maximum memories to return
            categories: Filter by category (None = all)

        Returns:
            Memories sorted by combined relevance + recency score
        """
        results = []
        if categories:
            for cat in categories:
                results.extend(
                    self.store.search(query, category=cat, limit=max_memories)
                )
            # De-duplicate and re-sort
            seen = set()
            unique = []
            for mem in results:
                if mem.id not in seen:
                    seen.add(mem.id)
                    unique.append(mem)
            unique.sort(key=lambda m: m.relevance_score, reverse=True)
            return unique[:max_memories]
        else:
            return self.store.search(query, limit=max_memories)

    def retrieve_passive(self, user_message: str, max_memories: int = 5) -> list[str]:
        """Two-tier retrieval: session cache first, then long-term FTS5.

        This is the "passive retrieval" pattern from arXiv:2512.13564,
        extended with an ephemeral session-cache tier for fast recall of
        recent context, tool results, and user intents.

        Tier 1 (session cache) -- fast, in-process, recent context.
        Tier 2 (FTS5 store)    -- persistent, long-term memory.

        Returns formatted strings ready for injection.
        """
        results: list[str] = []

        # ── Tier 1: Session cache (fast, recent context) ──────────
        try:
            cache = get_session_cache()
            session_hits = cache.search(user_message, limit=3)
            for entry in session_hits:
                results.append(f"[session/{entry.category}] {entry.value}")
        except Exception as exc:
            logger.debug("Session cache retrieval failed (non-fatal): %s", exc)

        # ── Tier 2: Long-term FTS5 store ──────────────────────────
        remaining = max_memories - len(results)
        if remaining > 0:
            memories = self.retrieve_for_query(
                user_message,
                max_memories=remaining,
                categories=["semantic", "procedural"],
            )

            # Also get any explicit instructions (always-relevant)
            instructions = self.store.search(
                user_message,
                category="semantic",
                limit=3,
            )
            # Merge, deduplicate
            seen_ids = {m.id for m in memories}
            for inst in instructions:
                if inst.id not in seen_ids and inst.subcategory == "instruction":
                    memories.append(inst)
                    seen_ids.add(inst.id)

            for m in memories[:remaining]:
                results.append(f"[{m.category}/{m.subcategory}] {m.content}")

        return results[:max_memories]

    def save_to_session(
        self,
        key: str,
        value: str,
        category: str = "context",
        ttl: Optional[float] = None,
    ) -> None:
        """Save a value to the session cache (Tier 1 -- ephemeral).

        Use this for transient context that should be quickly retrievable
        during the current session but does not need long-term persistence.
        """
        try:
            cache = get_session_cache()
            cache.put(key, value, category, ttl=ttl)
        except Exception as exc:
            logger.warning("Session cache save failed: %s", exc)

    def save_from_conversation(
        self,
        content: str,
        category: str = "semantic",
        subcategory: str = "",
        source: str = "conversation",
    ) -> int:
        """Save a new memory extracted from conversation.

        This is the write side of the retrieval system.
        Called when the agent detects saveable information.
        """
        mem = Memory(
            content=content,
            category=category,
            subcategory=subcategory,
            source=source,
        )
        return self.store.save(mem)

    def reinforce(self, memory_id: int, success: bool) -> None:
        """Reinforce or weaken a memory based on tool execution outcome.

        arXiv:2512.16301 — tool-execution-signaled learning: when a tool
        call succeeds, boost the relevance of memories that led to that
        decision. When it fails, reduce their relevance.
        """
        conn = self.store._get_conn()
        if success:
            conn.execute(
                "UPDATE memories SET relevance_score = MIN(relevance_score * 1.1, 5.0) WHERE id = ?",
                (memory_id,),
            )
        else:
            conn.execute(
                "UPDATE memories SET relevance_score = MAX(relevance_score * 0.9, 0.1) WHERE id = ?",
                (memory_id,),
            )
        conn.commit()

    def get_stats(self) -> dict:
        """Return memory store statistics."""
        return {
            "total": self.store.count(),
            "episodic": self.store.count("episodic"),
            "semantic": self.store.count("semantic"),
            "procedural": self.store.count("procedural"),
        }
