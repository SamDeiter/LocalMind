"""
Migration NNN — Short Description
==================================
Explain what this migration does and *why*.

The `clients` dict contains:
    clients["memory"] — chromadb.PersistentClient for backend/memory_db/
    clients["rag"]    — chromadb.PersistentClient for backend/rag_data/

Use clients[...].get_or_create_collection(), .delete_collection(), etc.
"""

# Bump the version number. Must be unique across all migration files.
version = "NNN"
description = "Short human-readable description of the change"


def up(clients: dict) -> None:
    """Apply the migration.

    Example operations:
        # Create a new collection
        clients["memory"].get_or_create_collection(
            name="my_new_collection",
            metadata={"hnsw:space": "cosine"},
        )

        # Read and re-insert data (schema change)
        col = clients["rag"].get_collection("localmind_docs")
        data = col.get(include=["documents", "metadatas", "embeddings"])
        for doc_id, doc, meta, emb in zip(
            data["ids"], data["documents"], data["metadatas"], data["embeddings"]
        ):
            meta["new_field"] = "default_value"
        col.update(ids=data["ids"], metadatas=data["metadatas"])
    """
    raise NotImplementedError("Replace this with your migration logic")


def down(clients: dict) -> None:
    """Revert the migration (best-effort).

    Not all migrations are reversible.  If yours isn't, set this to:
        pass
    and document why in a comment.
    """
    raise NotImplementedError("Replace this with your rollback logic")
