"""
Google Docs Tool — Read and edit Google Docs documents via the Docs API.

Capabilities:
  - read_document  — get full document content (plain text extraction)
  - create         — create a new Google Doc
  - insert_text    — insert text at a specific index
  - replace_text   — find and replace text throughout the document
  - append_text    — append text to the end of the document
  - get_metadata   — get document metadata (title, locale, revision ID)

Prerequisites:
  pip install google-api-python-client google-auth

Google OAuth credentials are loaded automatically via the centralized
credential store (backend.routes.google_auth.get_credentials).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .base import BaseTool

logger = logging.getLogger("localmind.tools.google_docs")

# ---------------------------------------------------------------------------
# Lazy import guard
# ---------------------------------------------------------------------------

_google_api_available: bool | None = None


def _require_google_api():
    """Raise a clear RuntimeError if google-api-python-client is not installed."""
    global _google_api_available
    if _google_api_available is True:
        return
    try:
        import googleapiclient.discovery  # noqa: F401
        _google_api_available = True
    except ImportError:
        _google_api_available = False
        raise RuntimeError(
            "google-api-python-client is not installed. "
            "Run: pip install google-api-python-client google-auth"
        )


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------


def _get_credentials():
    """Load Google OAuth credentials from the centralized credential store.

    Tries the DB-backed store first, then falls back to file-based tokens.
    Returns a google.oauth2.credentials.Credentials object or raises.
    """
    try:
        from backend.routes.google_auth import get_credentials
        creds = get_credentials()
    except Exception:
        creds = None

    if creds is None:
        raise RuntimeError(
            "No valid Google credentials found. Connect Google via Settings "
            "or run the OAuth flow at /api/google/auth"
        )
    return creds


# ---------------------------------------------------------------------------
# Service builder
# ---------------------------------------------------------------------------


def _build_docs_service(credentials):
    """Build an authorized Google Docs API v1 service client."""
    from googleapiclient.discovery import build
    return build("docs", "v1", credentials=credentials, cache_discovery=False)


# ---------------------------------------------------------------------------
# Synchronous worker functions (run in executor)
# ---------------------------------------------------------------------------


def _do_read_document(document_id: str, credentials) -> dict:
    """Get full document content as structured text."""
    service = _build_docs_service(credentials)

    try:
        doc = service.documents().get(documentId=document_id).execute()
    except Exception as exc:
        return _api_error("read_document", document_id, exc)

    # Extract plain text from the document body
    body = doc.get("body", {})
    text = _extract_text(body)

    return {
        "success": True,
        "result": {
            "documentId": document_id,
            "title": doc.get("title", ""),
            "body_text": text,
            "revisionId": doc.get("revisionId", ""),
        },
    }


def _do_create(title: str, credentials) -> dict:
    """Create a new empty Google Doc with the given title."""
    service = _build_docs_service(credentials)

    try:
        doc = service.documents().create(body={"title": title}).execute()
    except Exception as exc:
        return _api_error("create", "new_document", exc)

    return {
        "success": True,
        "result": {
            "documentId": doc.get("documentId", ""),
            "title": doc.get("title", ""),
            "revisionId": doc.get("revisionId", ""),
        },
    }


def _do_insert_text(document_id: str, text: str, index: int, credentials) -> dict:
    """Insert text at a specific index in the document."""
    service = _build_docs_service(credentials)

    requests = [
        {
            "insertText": {
                "location": {"index": index},
                "text": text,
            },
        },
    ]

    try:
        response = service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": requests},
        ).execute()
    except Exception as exc:
        return _api_error("insert_text", document_id, exc)

    return {
        "success": True,
        "result": {
            "documentId": document_id,
            "inserted_text_length": len(text),
            "index": index,
            "replies": response.get("replies", []),
        },
    }


def _do_replace_text(
    document_id: str,
    find_text: str,
    replace_text: str,
    credentials,
) -> dict:
    """Find and replace text throughout the document."""
    service = _build_docs_service(credentials)

    requests = [
        {
            "replaceAllText": {
                "containsText": {
                    "text": find_text,
                    "matchCase": True,
                },
                "replaceText": replace_text,
            },
        },
    ]

    try:
        response = service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": requests},
        ).execute()
    except Exception as exc:
        return _api_error("replace_text", document_id, exc)

    # Extract occurrences changed count from the reply
    replies = response.get("replies", [])
    occurrences = 0
    if replies:
        replace_reply = replies[0].get("replaceAllText", {})
        occurrences = replace_reply.get("occurrencesChanged", 0)

    return {
        "success": True,
        "result": {
            "documentId": document_id,
            "find_text": find_text,
            "replace_text": replace_text,
            "occurrences_changed": occurrences,
        },
    }


def _do_append_text(document_id: str, text: str, credentials) -> dict:
    """Append text to the end of the document.

    Reads the document to find the end index, then inserts at that position.
    """
    service = _build_docs_service(credentials)

    # Get document to find end index
    try:
        doc = service.documents().get(documentId=document_id).execute()
    except Exception as exc:
        return _api_error("append_text", document_id, exc)

    body = doc.get("body", {})
    content = body.get("content", [])

    # The end index is the endIndex of the last structural element minus 1
    # (to insert before the final newline)
    end_index = 1
    if content:
        last_element = content[-1]
        end_index = last_element.get("endIndex", 1) - 1

    # Ensure we don't go below 1
    end_index = max(end_index, 1)

    requests = [
        {
            "insertText": {
                "location": {"index": end_index},
                "text": text,
            },
        },
    ]

    try:
        response = service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": requests},
        ).execute()
    except Exception as exc:
        return _api_error("append_text", document_id, exc)

    return {
        "success": True,
        "result": {
            "documentId": document_id,
            "appended_text_length": len(text),
            "insert_index": end_index,
            "replies": response.get("replies", []),
        },
    }


def _do_get_metadata(document_id: str, credentials) -> dict:
    """Get document metadata: title, locale, revision ID, etc."""
    service = _build_docs_service(credentials)

    try:
        doc = service.documents().get(documentId=document_id).execute()
    except Exception as exc:
        return _api_error("get_metadata", document_id, exc)

    # Count structural elements
    body = doc.get("body", {})
    content = body.get("content", [])

    return {
        "success": True,
        "result": {
            "documentId": document_id,
            "title": doc.get("title", ""),
            "revisionId": doc.get("revisionId", ""),
            "suggestionsViewMode": doc.get("suggestionsViewMode", ""),
            "body_element_count": len(content),
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(body: dict) -> str:
    """Extract plain text from a Google Docs body structure."""
    parts: list[str] = []
    for element in body.get("content", []):
        paragraph = element.get("paragraph")
        if paragraph:
            for pe in paragraph.get("elements", []):
                text_run = pe.get("textRun")
                if text_run:
                    parts.append(text_run.get("content", ""))
        table = element.get("table")
        if table:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    cell_text = _extract_text(cell)
                    if cell_text.strip():
                        parts.append(cell_text)
    return "".join(parts)


def _api_error(action: str, document_id: str, exc: Exception) -> dict:
    """Build a structured error dict from a Google API exception."""
    error_msg = str(exc)

    status_code = None
    try:
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError):
            status_code = exc.resp.status
            if status_code == 404:
                error_msg = (
                    f"Document not found: '{document_id}'. "
                    "Check that the ID is correct and you have access."
                )
            elif status_code == 403:
                error_msg = (
                    f"Permission denied for document '{document_id}'. "
                    "Ensure the credentials have sufficient access."
                )
            elif status_code == 401:
                error_msg = (
                    "Authentication failed. The credentials may be expired "
                    "or invalid."
                )
    except ImportError:
        pass

    logger.error(
        "google_docs %s failed for %s: %s",
        action, document_id, error_msg,
    )

    result: dict[str, Any] = {
        "success": False,
        "error": error_msg,
        "action": action,
        "documentId": document_id,
    }
    if status_code is not None:
        result["status_code"] = status_code

    return result


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class GoogleDocsTool(BaseTool):
    """Read and edit Google Docs documents via the Docs API."""

    def __init__(self):
        self.actions: dict[str, Callable] = {
            "read_document": self._read_document,
            "create": self._create,
            "insert_text": self._insert_text,
            "replace_text": self._replace_text,
            "append_text": self._append_text,
            "get_metadata": self._get_metadata,
        }

    @property
    def name(self) -> str:
        return "google_docs"

    @property
    def description(self) -> str:
        return "Read and edit Google Docs documents via the Docs API"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(self.actions.keys()),
                    "description": "The Google Docs action to perform.",
                },
                "document_id": {
                    "type": "string",
                    "description": (
                        "The Google Docs document ID "
                        "(from the URL: docs.google.com/document/d/<ID>/edit)."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Title for a new document (for create action).",
                },
                "text": {
                    "type": "string",
                    "description": (
                        "Text to insert or append "
                        "(for insert_text, append_text actions)."
                    ),
                },
                "index": {
                    "type": "integer",
                    "description": (
                        "0-based character index for text insertion "
                        "(for insert_text action). Index 1 is the start of "
                        "the document body."
                    ),
                },
                "find_text": {
                    "type": "string",
                    "description": "Text to search for (for replace_text action).",
                },
                "replace_text": {
                    "type": "string",
                    "description": "Replacement text (for replace_text action).",
                },
                "credentials": {
                    "type": "object",
                    "description": (
                        "Google OAuth credentials object. Typically provided "
                        "automatically by the credential manager."
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        """Dispatch to the right action method."""
        action: str = kwargs.get("action", "")

        try:
            _require_google_api()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        handler = self.actions.get(action)
        if not handler:
            return {
                "success": False,
                "error": (
                    f"Unknown action: '{action}'. "
                    f"Available actions: {list(self.actions.keys())}"
                ),
            }

        loop = asyncio.get_event_loop()

        try:
            return await loop.run_in_executor(None, lambda: handler(kwargs))
        except Exception as exc:
            logger.exception("google_docs %s failed", action)
            return {"success": False, "error": str(exc)}

    # -- Action handlers -----------------------------------------------------

    def _resolve_credentials(self, kwargs: dict):
        """Get credentials from kwargs or auto-load from credential store."""
        creds = kwargs.get("credentials")
        if not creds:
            creds = _get_credentials()
        return creds

    def _read_document(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        document_id = kwargs.get("document_id")
        if not document_id:
            return {"success": False, "error": "document_id is required"}
        return _do_read_document(document_id, creds)

    def _create(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        title = kwargs.get("title")
        if not title:
            return {"success": False, "error": "title is required for create"}
        return _do_create(title, creds)

    def _insert_text(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        document_id = kwargs.get("document_id")
        if not document_id:
            return {"success": False, "error": "document_id is required"}
        text = kwargs.get("text")
        if text is None:
            return {"success": False, "error": "text is required for insert_text"}
        index = kwargs.get("index")
        if index is None:
            return {"success": False, "error": "index is required for insert_text"}
        return _do_insert_text(document_id, text, int(index), creds)

    def _replace_text(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        document_id = kwargs.get("document_id")
        if not document_id:
            return {"success": False, "error": "document_id is required"}
        find_text = kwargs.get("find_text")
        if not find_text:
            return {"success": False, "error": "find_text is required for replace_text"}
        replace_text = kwargs.get("replace_text")
        if replace_text is None:
            return {
                "success": False,
                "error": "replace_text is required for replace_text",
            }
        return _do_replace_text(document_id, find_text, replace_text, creds)

    def _append_text(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        document_id = kwargs.get("document_id")
        if not document_id:
            return {"success": False, "error": "document_id is required"}
        text = kwargs.get("text")
        if text is None:
            return {"success": False, "error": "text is required for append_text"}
        return _do_append_text(document_id, text, creds)

    def _get_metadata(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        document_id = kwargs.get("document_id")
        if not document_id:
            return {"success": False, "error": "document_id is required"}
        return _do_get_metadata(document_id, creds)
