"""
Document RAG Tool — "Talk to Your Files"
Upload documents, chunk them, store embeddings via ChromaDB,
and query them for relevant context during chat.
"""

import hashlib
import re
import time
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from .base import BaseTool

# Persistent storage for document embeddings
RAG_DATA_DIR = Path(__file__).parent.parent / "rag_data"
RAG_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ChromaDB client — persistent, local, no server needed
_client = chromadb.PersistentClient(path=str(RAG_DATA_DIR))
_collection = _client.get_or_create_collection(
    name="localmind_docs",
    metadata={"hnsw:space": "cosine"},
)


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks by sentence boundaries."""
    # Split into sentences (rough but effective)
    sentences = re.split(r'(?<=[.!?\n])\s+', text)
    chunks = []
    current_chunk = []
    current_len = 0

    for sentence in sentences:
        words = sentence.split()
        if current_len + len(words) > chunk_size and current_chunk:
            chunks.append(" ".join(current_chunk))
            # Keep overlap
            overlap_words = " ".join(current_chunk).split()[-overlap:]
            current_chunk = overlap_words + words
            current_len = len(current_chunk)
        else:
            current_chunk.extend(words)
            current_len += len(words)

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks if chunks else [text]


def _doc_id(filename: str, chunk_idx: int) -> str:
    """Generate a deterministic ID for a document chunk."""
    h = hashlib.md5(filename.encode()).hexdigest()[:8]
    return f"{h}_chunk_{chunk_idx}"


def index_document(filename: str, content: str) -> dict:
    """Index a document into ChromaDB for RAG queries."""
    chunks = _chunk_text(content)

    # Remove old chunks for this file (re-index)
    h = hashlib.md5(filename.encode()).hexdigest()[:8]
    existing = _collection.get(where={"source": filename})
    if existing and existing["ids"]:
        _collection.delete(ids=existing["ids"])

    # Add new chunks
    ids = [_doc_id(filename, i) for i in range(len(chunks))]
    metadatas = [
        {"source": filename, "chunk_index": i, "indexed_at": time.time()}
        for i in range(len(chunks))
    ]

    _collection.add(
        documents=chunks,
        ids=ids,
        metadatas=metadatas,
    )

    return {
        "success": True,
        "filename": filename,
        "chunks": len(chunks),
        "total_words": sum(len(c.split()) for c in chunks),
    }


def query_documents(query: str, n_results: int = 5) -> dict:
    """Search indexed documents for relevant chunks."""
    if _collection.count() == 0:
        return {"success": True, "results": [], "message": "No documents indexed yet."}

    results = _collection.query(
        query_texts=[query],
        n_results=min(n_results, _collection.count()),
    )

    formatted = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i]
        distance = results["distances"][0][i] if results.get("distances") else None
        formatted.append({
            "content": doc,
            "source": meta.get("source", "unknown"),
            "chunk_index": meta.get("chunk_index", 0),
            "relevance": round(1 - distance, 3) if distance is not None else None,
        })

    return {"success": True, "results": formatted}


def list_indexed_documents() -> dict:
    """List all unique documents in the index."""
    if _collection.count() == 0:
        return {"success": True, "documents": []}

    all_meta = _collection.get()
    sources = {}
    for meta in all_meta["metadatas"]:
        src = meta.get("source", "unknown")
        if src not in sources:
            sources[src] = {"filename": src, "chunks": 0, "indexed_at": meta.get("indexed_at", 0)}
        sources[src]["chunks"] += 1

    return {"success": True, "documents": list(sources.values())}


def delete_document(filename: str) -> dict:
    """Remove a document from the index."""
    existing = _collection.get(where={"source": filename})
    if existing and existing["ids"]:
        _collection.delete(ids=existing["ids"])
        return {"success": True, "deleted": filename, "chunks_removed": len(existing["ids"])}
    return {"success": False, "error": f"Document not found: {filename}"}


# ── Tool Classes for Agent ───────────────────────────────────────

class UploadDocumentTool(BaseTool):
    @property
    def name(self) -> str:
        return "upload_document"

    @property
    def description(self) -> str:
        return "Index a document for RAG queries. The document content will be chunked and stored for later retrieval."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the document being indexed",
                },
                "content": {
                    "type": "string",
                    "description": "Full text content of the document",
                },
            },
            "required": ["filename", "content"],
        }

    async def execute(self, filename: str = "", content: str = "", **kwargs) -> dict:
        try:
            return index_document(filename, content)
        except Exception as e:
            return {"success": False, "error": f"Index failed: {e}"}


class QueryDocumentsTool(BaseTool):
    @property
    def name(self) -> str:
        return "query_documents"

    @property
    def description(self) -> str:
        return "Search indexed documents for content relevant to a query. Use this when the user asks about uploaded files."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant document chunks",
                },
                "n_results": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str = "", n_results: int = 5, **kwargs) -> dict:
        try:
            return query_documents(query, n_results)
        except Exception as e:
            return {"success": False, "error": f"Query failed: {e}"}
