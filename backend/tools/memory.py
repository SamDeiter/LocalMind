"""
Memory Tool — SQLite FTS5 persistent memory.

Single unified memory store shared between the backend chat system and the
src.agent ReAct agent.  Uses full-text search with recency weighting for
retrieval — zero external dependencies beyond stdlib sqlite3.

Design informed by arXiv:2512.13564 (Memory in the Age of AI Agents).
"""

import logging

from .base import BaseTool

logger = logging.getLogger("localmind.tools.memory")

# FTS5 store — lazy-loaded to avoid circular imports
_fts_store = None
_retriever = None


def _get_fts_store():
    """Get or create the FTS5 memory store (singleton)."""
    global _fts_store
    if _fts_store is None:
        try:
            from src.agent.memory.store import MemoryStore
            _fts_store = MemoryStore()
            logger.info("FTS5 memory store connected")
        except Exception as e:
            logger.warning(f"FTS5 memory store unavailable: {e}")
    return _fts_store


def _get_retriever():
    """Get or create the MemoryRetriever (singleton)."""
    global _retriever
    if _retriever is None:
        store = _get_fts_store()
        if store:
            try:
                from src.agent.memory.retrieval import MemoryRetriever
                _retriever = MemoryRetriever(store)
                logger.info("MemoryRetriever connected")
            except Exception as e:
                logger.warning(f"MemoryRetriever unavailable: {e}")
    return _retriever


# Global learning state (toggled via API)
_learning_enabled = True


def set_learning_enabled(enabled: bool):
    global _learning_enabled
    _learning_enabled = enabled


def get_learning_enabled() -> bool:
    return _learning_enabled


def get_recent_memories(n: int = 10) -> list[dict]:
    """Load the N most recent memories for context injection."""
    store = _get_fts_store()
    if not store:
        return []
    try:
        memories = store.get_recent(limit=n)
        return [
            {
                "content": m.content,
                "category": m.subcategory or m.category,
                "created_at": str(m.created_at),
            }
            for m in memories
        ]
    except Exception:
        return []


class SaveMemoryTool(BaseTool):
    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save an important fact, user preference, or instruction to long-term memory. "
            "Use this proactively when the user shares preferences, facts about themselves, "
            "or important context you should remember across conversations."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact, preference, or instruction to remember",
                },
                "category": {
                    "type": "string",
                    "description": "Category tag: 'preference', 'fact', 'instruction', or 'context'",
                    "enum": ["preference", "fact", "instruction", "context"],
                },
            },
            "required": ["content", "category"],
        }

    async def execute(self, content: str = "", category: str = "general", **kwargs) -> dict:
        if not _learning_enabled:
            return {
                "success": True,
                "result": "Learning is currently paused. Memory not saved.",
            }

        if not content.strip():
            return {"success": False, "error": "Content cannot be empty"}

        retriever = _get_retriever()
        if not retriever:
            return {"success": False, "error": "Memory store unavailable"}

        try:
            subcategory = {"preference": "preference", "fact": "fact",
                           "instruction": "instruction", "context": "context"
                           }.get(category, category)
            memory_id = retriever.save_from_conversation(
                content=content,
                category="semantic",
                subcategory=subcategory,
                source="backend_chat",
            )
        except Exception as exc:
            logger.warning(f"Memory save failed: {exc}")
            return {"success": False, "error": str(exc)}

        return {
            "success": True,
            "result": f"Memory saved [{category}]: {content[:80]}...",
            "id": memory_id,
        }


class RecallMemoriesTool(BaseTool):
    @property
    def name(self) -> str:
        return "recall_memories"

    @property
    def description(self) -> str:
        return (
            "Search long-term memories by meaning (semantic search). "
            "Use this to recall user preferences, past facts, or instructions. "
            "For example, searching 'color preferences' will find memories about dark mode."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for in memory (semantic search by meaning)",
                },
                "category": {
                    "type": "string",
                    "description": "Optional: filter by category",
                    "enum": ["preference", "fact", "instruction", "context"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of memories to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str = "", category: str = None, limit: int = 5, **kwargs) -> dict:
        if not query.strip():
            return {"success": False, "error": "Query cannot be empty"}

        retriever = _get_retriever()
        if not retriever:
            return {"success": True, "result": "No relevant memories found.", "memories": []}

        try:
            # Map frontend categories to store categories
            categories = None
            if category:
                categories = ["semantic"]  # All user-facing categories live under "semantic"
            results = retriever.retrieve_for_query(query, max_memories=limit, categories=categories)

            # Filter by subcategory if a specific frontend category was requested
            if category:
                results = [m for m in results if m.subcategory == category] or results

            memories = [
                {
                    "content": m.content,
                    "category": m.subcategory or m.category,
                    "relevance": round(m.relevance_score, 3),
                    "source": "fts5",
                }
                for m in results
            ]
        except Exception as exc:
            logger.warning(f"Memory recall failed: {exc}")
            memories = []

        if not memories:
            return {"success": True, "result": "No relevant memories found.", "memories": []}

        formatted = "\n".join(
            f"- [{m['category']}] ({m['relevance']:.0%} match): {m['content']}"
            for m in memories
        )
        return {"success": True, "result": formatted, "memories": memories}