"""
Migration 001 — Initial Schema Baseline
========================================
Documents the ChromaDB collection structure that already exists in LocalMind.
This migration is idempotent: it uses get_or_create_collection so it is safe
to run against both fresh installs and existing databases.

Collections:
  memory_db/memories      — user long-term memory (cosine, Ollama embeddings)
  rag_data/localmind_docs — document RAG chunks   (cosine, ChromaDB embeddings)
"""

version = "1"
description = "Baseline: ensure memories and localmind_docs collections exist"


def up(clients: dict) -> None:
    """Create (or confirm existence of) the two core collections."""
    memory_client = clients["memory"]
    rag_client = clients["rag"]

    # -- memories collection (used by backend/tools/memory.py) --
    memory_client.get_or_create_collection(
        name="memories",
        metadata={"hnsw:space": "cosine"},
    )

    # -- localmind_docs collection (used by backend/tools/rag.py) --
    rag_client.get_or_create_collection(
        name="localmind_docs",
        metadata={"hnsw:space": "cosine"},
    )


def down(clients: dict) -> None:
    """Baseline migration — down is a no-op.

    We never want to accidentally delete the core collections, so rolling
    back the baseline does nothing.  If you truly need to wipe them, do
    it manually.
    """
    pass
