"""
Memory Tool — Dual-store memory with ChromaDB (semantic) + SQLite FTS5 (keyword).

Writes to both stores so memories are shared between the backend chat system
and the src.agent ReAct agent. Reads merge results from both for best recall.

ChromaDB provides semantic similarity (finds "automobile" when searching "car").
FTS5 provides fast keyword search with zero extra dependencies.
"""

from backend.config import OLLAMA_BASE_URL
import logging
import time
import uuid

import chromadb
import httpx

from .base import BaseTool

logger = logging.getLogger("localmind.tools.memory")

# FTS5 store — lazy-loaded to avoid circular imports
_fts_store = None


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

EMBED_MODEL = "nomic-embed-text"

# Global learning state (toggled via API)
_learning_enabled = True


def set_learning_enabled(enabled: bool):
    global _learning_enabled
    _learning_enabled = enabled


def get_learning_enabled() -> bool:
    return _learning_enabled


# Module-level cache for ChromaDB client + collection
# Avoids re-creating PersistentClient on every memory operation
_chroma_client = None
_chroma_collection = None


def _get_collection():
    """Get or create the memories ChromaDB collection (cached singleton)."""
    global _chroma_client, _chroma_collection
    if _chroma_collection is None:
        _chroma_client = chromadb.PersistentClient(path=str(_db_path()))
        _chroma_collection = _chroma_client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"},
        )
    return _chroma_collection


def _db_path():
    from pathlib import Path
    return Path(__file__).parent.parent / "memory_db"


async def _embed(text: str) -> list[float]:
    """Get embedding vector from Ollama."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama returns {"embeddings": [[...]]}
        return data["embeddings"][0]


def get_recent_memories(n: int = 10) -> list[dict]:
    """Load the N most recent memories for context injection."""
    try:
        collection = _get_collection()
        if collection.count() == 0:
            return []
        results = collection.get(
            include=["documents", "metadatas"],
            limit=n,
        )
        memories = []
        for doc, meta in zip(results["documents"], results["metadatas"]):
            memories.append({
                "content": doc,
                "category": meta.get("category", "general"),
                "created_at": meta.get("created_at", ""),
            })
        # Sort by created_at descending
        memories.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return memories[:n]
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

        memory_id = str(uuid.uuid4())

        # Write to ChromaDB (semantic search)
        try:
            embedding = await _embed(content)
            collection = _get_collection()
            collection.add(
                ids=[memory_id],
                documents=[content],
                embeddings=[embedding],
                metadatas=[{
                    "category": category,
                    "created_at": str(time.time()),
                }],
            )
        except Exception as exc:
            logger.warning(f"ChromaDB save failed (FTS5 fallback): {exc}")

        # Write to FTS5 (keyword search, shared with src.agent)
        fts = _get_fts_store()
        if fts:
            try:
                # Map backend categories to agent taxonomy
                subcategory = {"preference": "preference", "fact": "fact",
                               "instruction": "instruction", "context": "context"
                               }.get(category, category)
                fts.save(content=content, category="semantic",
                         subcategory=subcategory, source="backend_chat")
            except Exception as exc:
                logger.warning(f"FTS5 save failed: {exc}")

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

        memories = []

        # Search ChromaDB (semantic similarity)
        try:
            embedding = await _embed(query)
            collection = _get_collection()

            if collection.count() > 0:
                where_filter = {"category": category} if category else None
                results = collection.query(
                    query_embeddings=[embedding],
                    n_results=min(limit, collection.count()),
                    where=where_filter,
                    include=["documents", "metadatas", "distances"],
                )
                for doc, meta, dist in zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                ):
                    memories.append({
                        "content": doc,
                        "category": meta.get("category", "general"),
                        "relevance": round(1 - dist, 3),
                        "source": "chromadb",
                    })
        except Exception as exc:
            logger.warning(f"ChromaDB recall failed (trying FTS5): {exc}")

        # Search FTS5 (keyword match, shared with src.agent)
        fts = _get_fts_store()
        if fts:
            try:
                fts_category = "semantic" if not category else None
                fts_results = fts.search(query, category=fts_category, limit=limit)
                seen_contents = {m["content"] for m in memories}
                for mem in fts_results:
                    if mem.content not in seen_contents:
                        memories.append({
                            "content": mem.content,
                            "category": mem.subcategory or mem.category,
                            "relevance": round(mem.relevance_score, 3),
                            "source": "fts5",
                        })
                        seen_contents.add(mem.content)
            except Exception as exc:
                logger.warning(f"FTS5 recall failed: {exc}")

        # Sort by relevance, deduplicated
        memories.sort(key=lambda m: m["relevance"], reverse=True)
        memories = memories[:limit]

        if not memories:
            return {"success": True, "result": "No relevant memories found.", "memories": []}

        formatted = "\n".join(
            f"- [{m['category']}] ({m['relevance']:.0%} match): {m['content']}"
            for m in memories
        )
        return {"success": True, "result": formatted, "memories": memories}