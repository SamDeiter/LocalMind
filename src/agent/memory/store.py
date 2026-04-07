"""
Persistent memory store — SQLite-backed key-value + semantic storage.

Design informed by arXiv:2512.13564 (Memory in the Age of AI Agents):

Memory taxonomy from the paper:
  1. Episodic Memory  — what happened (conversation history, events)
  2. Semantic Memory   — what is known (facts, preferences, instructions)
  3. Procedural Memory — how to do things (tool usage patterns, workflows)

This store implements all three types in a single SQLite database with:
- Full-text search for retrieval (no embedding model dependency)
- Automatic timestamping for recency-weighted retrieval
- Category-based organization matching the taxonomy
- Access counting for importance-weighted recall

arXiv:2512.16301 (Adaptation Survey) — A2 paradigm: agent-output-signaled
learning. The store supports recording which memories led to successful
tool executions, enabling reinforcement of useful memories.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import MEMORY_DB_PATH

logger = logging.getLogger("agent.memory.store")


@dataclass
class Memory:
    """A single memory entry."""
    id: Optional[int] = None
    content: str = ""
    category: str = "semantic"        # episodic | semantic | procedural
    subcategory: str = ""             # fact, preference, instruction, tool_pattern, etc.
    source: str = ""                  # Where this memory came from
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    accessed_at: float = 0.0
    access_count: int = 0
    relevance_score: float = 1.0     # For retrieval ranking

    def age_hours(self) -> float:
        return (time.time() - self.created_at) / 3600


class MemoryStore:
    """SQLite-backed persistent memory with full-text search.

    Uses FTS5 for fast text search without requiring embedding models,
    keeping the system fully local with zero external dependencies.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or MEMORY_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'semantic',
                subcategory TEXT DEFAULT '',
                source TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}',
                created_at REAL NOT NULL,
                accessed_at REAL NOT NULL,
                access_count INTEGER DEFAULT 0,
                relevance_score REAL DEFAULT 1.0
            );

            CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
            CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);

            -- FTS5 virtual table for full-text search
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content,
                category,
                subcategory,
                content='memories',
                content_rowid='id'
            );

            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, category, subcategory)
                VALUES (new.id, new.content, new.category, new.subcategory);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, category, subcategory)
                VALUES ('delete', old.id, old.content, old.category, old.subcategory);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, category, subcategory)
                VALUES ('delete', old.id, old.content, old.category, old.subcategory);
                INSERT INTO memories_fts(rowid, content, category, subcategory)
                VALUES (new.id, new.content, new.category, new.subcategory);
            END;
        """)
        conn.commit()
        logger.info(f"Memory store initialized at {self.db_path}")

    def save(self, memory: Memory) -> int:
        """Save a memory and return its ID."""
        now = time.time()
        if not memory.created_at:
            memory.created_at = now
        memory.accessed_at = now

        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO memories (content, category, subcategory, source, metadata,
               created_at, accessed_at, access_count, relevance_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory.content,
                memory.category,
                memory.subcategory,
                memory.source,
                json.dumps(memory.metadata),
                memory.created_at,
                memory.accessed_at,
                memory.access_count,
                memory.relevance_score,
            ),
        )
        conn.commit()
        memory.id = cursor.lastrowid
        logger.debug(f"Saved memory #{memory.id}: [{memory.category}] {memory.content[:60]}")
        return memory.id

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 10,
        recency_weight: float = 0.3,
    ) -> list[Memory]:
        """Search memories using FTS5 with recency weighting.

        arXiv:2512.13564 recommends combining text relevance with recency
        for memory retrieval, as recent memories are more likely to be
        contextually relevant.

        Score = text_relevance * (1 - recency_weight) + recency_score * recency_weight
        """
        conn = self._get_conn()
        now = time.time()

        # FTS5 search
        sql = """
            SELECT m.*, rank as fts_rank
            FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH ?
        """
        params: list[Any] = [query]
        if category:
            sql += " AND m.category = ?"
            params.append(category)

        sql += " ORDER BY fts_rank LIMIT ?"
        params.append(limit * 3)  # Over-fetch for re-ranking

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # FTS query syntax error — fall back to LIKE search
            return self._search_like(query, category, limit)

        # Re-rank with recency
        results = []
        for row in rows:
            mem = self._row_to_memory(row)
            age_hours = max(1, (now - mem.created_at) / 3600)
            recency_score = 1.0 / (1.0 + age_hours / 24)  # Decays over days
            text_score = -row["fts_rank"]  # FTS5 rank is negative (lower = better)
            combined = text_score * (1 - recency_weight) + recency_score * recency_weight
            mem.relevance_score = combined
            results.append(mem)

        results.sort(key=lambda m: m.relevance_score, reverse=True)

        # Update access timestamps
        for mem in results[:limit]:
            conn.execute(
                "UPDATE memories SET accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                (now, mem.id),
            )
        conn.commit()

        return results[:limit]

    def _search_like(self, query: str, category: Optional[str], limit: int) -> list[Memory]:
        """Fallback search using LIKE when FTS query syntax fails."""
        conn = self._get_conn()
        sql = "SELECT * FROM memories WHERE content LIKE ?"
        params: list[Any] = [f"%{query}%"]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY accessed_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def get_recent(self, category: Optional[str] = None, limit: int = 10) -> list[Memory]:
        """Get the most recently accessed memories."""
        conn = self._get_conn()
        sql = "SELECT * FROM memories"
        params: list[Any] = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY accessed_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def delete(self, memory_id: int) -> bool:
        """Delete a memory by ID."""
        conn = self._get_conn()
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        return True

    def count(self, category: Optional[str] = None) -> int:
        """Count memories, optionally filtered by category."""
        conn = self._get_conn()
        if category:
            row = conn.execute("SELECT COUNT(*) FROM memories WHERE category = ?", (category,)).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0] if row else 0

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        metadata = {}
        try:
            metadata = json.loads(row["metadata"])
        except (json.JSONDecodeError, KeyError):
            pass
        return Memory(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            subcategory=row["subcategory"],
            source=row["source"],
            metadata=metadata,
            created_at=row["created_at"],
            accessed_at=row["accessed_at"],
            access_count=row["access_count"],
            relevance_score=row["relevance_score"],
        )
