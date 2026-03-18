"""
Memory Tool — ChromaDB vector memory with Ollama embeddings.
Supports save_memory and recall_memories with semantic search.
Learning can be paused via the /api/memory/toggle endpoint.
"""

import time
import uuid

import chromadb
import httpx

from .base import BaseTool

OLLAMA_BASE = "http://localhost:11434"
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
            f"{OLLAMA_BASE}/api/embed",
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

        try:
            embedding = await _embed(content)
            collection = _get_collection()

            memory_id = str(uuid.uuid4())
            collection.add(
                ids=[memory_id],
                documents=[content],
                embeddings=[embedding],
                metadatas=[{
                    "category": category,
                    "created_at": str(time.time()),
                }],
            )

            return {
                "success": True,
                "result": f"Memory saved [{category}]: {content[:80]}...",
                "id": memory_id,
            }
        except Exception as exc:
            return {"success": False, "error": f"Failed to save memory: {exc}"}


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

        try:
            embedding = await _embed(query)
            collection = _get_collection()

            if collection.count() == 0:
                return {"success": True, "result": "No memories stored yet.", "memories": []}

            where_filter = {"category": category} if category else None

            results = collection.query(
                query_embeddings=[embedding],
                n_results=min(limit, collection.count()),
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )

            memories = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                memories.append({
                    "content": doc,
                    "category": meta.get("category", "general"),
                    "relevance": round(1 - dist, 3),  # cosine similarity
                })

            if not memories:
                return {"success": True, "result": "No relevant memories found.", "memories": []}

            formatted = "\n".join(
                f"- [{m['category']}] ({m['relevance']:.0%} match): {m['content']}"
                for m in memories
            )
            return {"success": True, "result": formatted, "memories": memories}

        except Exception as exc:
            return {"success": False, "error": f"Recall failed: {exc}"}
