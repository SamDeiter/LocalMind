"""
routes/documents.py — Document RAG Router
===========================================
Handles document upload, indexing, listing, and deletion for
Retrieval-Augmented Generation (RAG).

RAG allows users to upload documents (text files, code, notes) which
are then chunked, vector-embedded, and stored in ChromaDB. When the user
asks a question, relevant document chunks are retrieved and injected
into the system prompt, giving the AI context it wouldn't otherwise have.

This is LOCAL-ONLY — documents never leave the user's machine.
"""

import logging

from fastapi import APIRouter, UploadFile, File

logger = logging.getLogger("localmind.routes.documents")

# Create router — all endpoints are RAG document-related
router = APIRouter(prefix="/api/documents", tags=["documents"])

# RAG availability flag — set by server.py based on whether
# chromadb is installed and the RAG module loaded successfully
_RAG_AVAILABLE = False
_index_document = None
_list_indexed_documents = None
_delete_document = None


def configure(rag_available: bool, index_fn=None, list_fn=None, delete_fn=None):
    """Called by server.py to inject RAG functions.
    
    We use dependency injection to avoid importing RAG modules at
    module level — they may not be installed (chromadb is optional).
    """
    global _RAG_AVAILABLE, _index_document, _list_indexed_documents, _delete_document
    _RAG_AVAILABLE = rag_available
    _index_document = index_fn
    _list_indexed_documents = list_fn
    _delete_document = delete_fn


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """Upload and index a document for RAG queries.
    
    The document is:
    1. Read as UTF-8 text
    2. Split into chunks (by the RAG module)
    3. Each chunk is vector-embedded using nomic-embed-text
    4. Stored in ChromaDB for semantic search
    
    Supported formats: .txt, .py, .md, .js, .html, etc.
    (Anything that can be read as text.)
    """
    if not _RAG_AVAILABLE:
        return {"error": "RAG not available — install chromadb"}

    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    if not text.strip():
        return {"error": "Empty file"}

    result = _index_document(file.filename, text)
    logger.info(f"Document indexed: {file.filename}")
    return result


@router.get("/")
async def list_documents():
    """List all indexed documents.
    
    Returns document names and chunk counts so users can see
    what knowledge the AI has access to via RAG.
    """
    if not _RAG_AVAILABLE:
        return {"documents": []}
    return _list_indexed_documents()


@router.delete("/{filename:path}")
async def remove_document(filename: str):
    """Remove a document from the RAG index.
    
    Deletes all chunks associated with the given filename from ChromaDB.
    The AI will no longer have access to this document's content.
    Important for privacy — users can remove sensitive documents.
    """
    if not _RAG_AVAILABLE:
        return {"error": "RAG not available"}
    logger.info(f"Document removed from RAG: {filename}")
    return _delete_document(filename)
