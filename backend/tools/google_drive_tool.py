"""
Google Drive Tool — List, search, upload, download, and manage files via Drive API.

Capabilities:
  - list_files     — list files in Drive (optional folder filter)
  - search         — search files by query string
  - get_metadata   — get file metadata by ID
  - download_file  — download file content to a local path
  - upload_file    — upload a local file to Drive
  - create_folder  — create a new folder
  - move_file      — move a file to a different folder
  - copy_file      — copy a file (optionally rename)

Prerequisites:
  pip install google-api-python-client google-auth

Google OAuth credentials are loaded automatically via the centralized
credential store (backend.routes.google_auth.get_credentials).

Security: All local file paths are resolved through safe_resolve() to
prevent path traversal attacks.
"""

from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
from pathlib import Path
from typing import Any, Callable

from .base import BaseTool

logger = logging.getLogger("localmind.tools.google_drive")

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
# Path security
# ---------------------------------------------------------------------------

# Base directory for safe file operations (user's home by default)
_SAFE_BASE = Path.home()


def _safe_path(user_path: str) -> Path:
    """Resolve a user-supplied path through the security jail.

    Falls back to simple Path.resolve() if the security module is unavailable.
    """
    try:
        from backend.security.paths import safe_resolve
        return safe_resolve(_SAFE_BASE, user_path)
    except ImportError:
        # Fallback: resolve without jail (dev/testing environments)
        resolved = Path(user_path).resolve()
        logger.warning(
            "security.paths.safe_resolve unavailable; using plain resolve for %s",
            resolved,
        )
        return resolved


# ---------------------------------------------------------------------------
# Service builder
# ---------------------------------------------------------------------------


def _build_drive_service(credentials):
    """Build an authorized Google Drive API v3 service client."""
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


# ---------------------------------------------------------------------------
# Synchronous worker functions (run in executor)
# ---------------------------------------------------------------------------


def _do_list_files(
    credentials,
    folder_id: str | None = None,
    page_size: int = 20,
) -> dict:
    """List files in Drive, optionally filtered by folder."""
    service = _build_drive_service(credentials)

    query_parts: list[str] = ["trashed = false"]
    if folder_id:
        query_parts.append(f"'{folder_id}' in parents")
    q = " and ".join(query_parts)

    try:
        response = service.files().list(
            q=q,
            pageSize=min(page_size, 100),
            fields="files(id, name, mimeType, modifiedTime, size, parents, webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()
    except Exception as exc:
        return _api_error("list_files", "", exc)

    files = response.get("files", [])
    return {
        "success": True,
        "result": {
            "file_count": len(files),
            "files": [
                {
                    "id": f.get("id"),
                    "name": f.get("name"),
                    "mimeType": f.get("mimeType"),
                    "modifiedTime": f.get("modifiedTime"),
                    "size": f.get("size"),
                    "parents": f.get("parents", []),
                }
                for f in files
            ],
        },
    }


def _do_search(query: str, credentials, page_size: int = 20) -> dict:
    """Search files by a Drive query string (e.g. name contains 'report')."""
    service = _build_drive_service(credentials)

    # Append trashed filter to user query
    full_query = f"({query}) and trashed = false"

    try:
        response = service.files().list(
            q=full_query,
            pageSize=min(page_size, 100),
            fields="files(id, name, mimeType, modifiedTime, size, parents, webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()
    except Exception as exc:
        return _api_error("search", query, exc)

    files = response.get("files", [])
    return {
        "success": True,
        "result": {
            "query": query,
            "file_count": len(files),
            "files": [
                {
                    "id": f.get("id"),
                    "name": f.get("name"),
                    "mimeType": f.get("mimeType"),
                    "modifiedTime": f.get("modifiedTime"),
                    "size": f.get("size"),
                    "parents": f.get("parents", []),
                }
                for f in files
            ],
        },
    }


def _do_get_metadata(file_id: str, credentials) -> dict:
    """Get detailed metadata for a single file."""
    service = _build_drive_service(credentials)

    try:
        meta = service.files().get(
            fileId=file_id,
            fields=(
                "id, name, mimeType, modifiedTime, createdTime, size, "
                "parents, webViewLink, webContentLink, owners, "
                "shared, trashed, description"
            ),
        ).execute()
    except Exception as exc:
        return _api_error("get_metadata", file_id, exc)

    return {
        "success": True,
        "result": meta,
    }


def _do_download_file(file_id: str, output_path: str, credentials) -> dict:
    """Download a file's content to a local path.

    For Google Workspace files (Docs, Sheets, Slides), exports as PDF.
    For binary files, downloads directly.
    """
    from googleapiclient.http import MediaIoBaseDownload

    service = _build_drive_service(credentials)

    # Resolve output path through security jail
    try:
        resolved = _safe_path(output_path)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Path security check failed: {exc}",
        }

    # Get file metadata to determine type
    try:
        meta = service.files().get(
            fileId=file_id, fields="mimeType, name"
        ).execute()
    except Exception as exc:
        return _api_error("download_file", file_id, exc)

    mime_type = meta.get("mimeType", "")

    # Google Workspace files need export
    google_export_map = {
        "application/vnd.google-apps.document": "application/pdf",
        "application/vnd.google-apps.spreadsheet": "application/pdf",
        "application/vnd.google-apps.presentation": "application/pdf",
        "application/vnd.google-apps.drawing": "image/png",
    }

    try:
        if mime_type in google_export_map:
            export_mime = google_export_map[mime_type]
            request = service.files().export(
                fileId=file_id, mimeType=export_mime
            )
        else:
            request = service.files().get_media(fileId=file_id)

        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    except Exception as exc:
        return _api_error("download_file", file_id, exc)

    # Write to disk
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(fh.getvalue())
    except Exception as exc:
        return {
            "success": False,
            "error": f"Failed to write file to {resolved}: {exc}",
        }

    return {
        "success": True,
        "result": {
            "file_id": file_id,
            "file_name": meta.get("name", ""),
            "output_path": str(resolved),
            "size_bytes": resolved.stat().st_size,
            "exported_as": google_export_map.get(mime_type, mime_type),
        },
    }


def _do_upload_file(
    file_path: str,
    credentials,
    folder_id: str | None = None,
) -> dict:
    """Upload a local file to Google Drive."""
    from googleapiclient.http import MediaFileUpload

    service = _build_drive_service(credentials)

    # Resolve file path through security jail
    try:
        resolved = _safe_path(file_path)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Path security check failed: {exc}",
        }

    if not resolved.exists():
        return {
            "success": False,
            "error": f"File not found: {resolved}",
        }

    # Detect MIME type
    mime_type, _ = mimetypes.guess_type(str(resolved))
    if not mime_type:
        mime_type = "application/octet-stream"

    file_metadata: dict[str, Any] = {"name": resolved.name}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(str(resolved), mimetype=mime_type, resumable=True)

    try:
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, mimeType, size, webViewLink",
        ).execute()
    except Exception as exc:
        return _api_error("upload_file", str(resolved), exc)

    return {
        "success": True,
        "result": {
            "file_id": uploaded.get("id", ""),
            "name": uploaded.get("name", ""),
            "mimeType": uploaded.get("mimeType", ""),
            "size": uploaded.get("size"),
            "webViewLink": uploaded.get("webViewLink", ""),
        },
    }


def _do_create_folder(
    name: str,
    credentials,
    parent_id: str | None = None,
) -> dict:
    """Create a new folder in Google Drive."""
    service = _build_drive_service(credentials)

    file_metadata: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        file_metadata["parents"] = [parent_id]

    try:
        folder = service.files().create(
            body=file_metadata,
            fields="id, name, mimeType, webViewLink",
        ).execute()
    except Exception as exc:
        return _api_error("create_folder", name, exc)

    return {
        "success": True,
        "result": {
            "folder_id": folder.get("id", ""),
            "name": folder.get("name", ""),
            "webViewLink": folder.get("webViewLink", ""),
        },
    }


def _do_move_file(file_id: str, folder_id: str, credentials) -> dict:
    """Move a file to a different folder."""
    service = _build_drive_service(credentials)

    # Get current parents to remove them
    try:
        file_meta = service.files().get(
            fileId=file_id, fields="parents"
        ).execute()
    except Exception as exc:
        return _api_error("move_file", file_id, exc)

    previous_parents = ",".join(file_meta.get("parents", []))

    try:
        updated = service.files().update(
            fileId=file_id,
            addParents=folder_id,
            removeParents=previous_parents,
            fields="id, name, parents",
        ).execute()
    except Exception as exc:
        return _api_error("move_file", file_id, exc)

    return {
        "success": True,
        "result": {
            "file_id": updated.get("id", ""),
            "name": updated.get("name", ""),
            "new_parents": updated.get("parents", []),
        },
    }


def _do_copy_file(
    file_id: str,
    credentials,
    new_name: str | None = None,
) -> dict:
    """Copy a file, optionally with a new name."""
    service = _build_drive_service(credentials)

    body: dict[str, Any] = {}
    if new_name:
        body["name"] = new_name

    try:
        copied = service.files().copy(
            fileId=file_id,
            body=body,
            fields="id, name, mimeType, webViewLink",
        ).execute()
    except Exception as exc:
        return _api_error("copy_file", file_id, exc)

    return {
        "success": True,
        "result": {
            "original_file_id": file_id,
            "copied_file_id": copied.get("id", ""),
            "name": copied.get("name", ""),
            "mimeType": copied.get("mimeType", ""),
            "webViewLink": copied.get("webViewLink", ""),
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api_error(action: str, resource_id: str, exc: Exception) -> dict:
    """Build a structured error dict from a Google API exception."""
    error_msg = str(exc)

    status_code = None
    try:
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError):
            status_code = exc.resp.status
            if status_code == 404:
                error_msg = (
                    f"File not found: '{resource_id}'. "
                    "Check that the ID is correct and you have access."
                )
            elif status_code == 403:
                error_msg = (
                    f"Permission denied for '{resource_id}'. "
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
        "google_drive %s failed for %s: %s",
        action, resource_id, error_msg,
    )

    result: dict[str, Any] = {
        "success": False,
        "error": error_msg,
        "action": action,
    }
    if resource_id:
        result["resource_id"] = resource_id
    if status_code is not None:
        result["status_code"] = status_code

    return result


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class GoogleDriveTool(BaseTool):
    """List, search, upload, download, and manage Google Drive files."""

    def __init__(self):
        self.actions: dict[str, Callable] = {
            "list_files": self._list_files,
            "search": self._search,
            "get_metadata": self._get_metadata,
            "download_file": self._download_file,
            "upload_file": self._upload_file,
            "create_folder": self._create_folder,
            "move_file": self._move_file,
            "copy_file": self._copy_file,
        }

    @property
    def name(self) -> str:
        return "google_drive"

    @property
    def description(self) -> str:
        return (
            "List, search, upload, download, and manage files in Google Drive"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(self.actions.keys()),
                    "description": "The Google Drive action to perform.",
                },
                "file_id": {
                    "type": "string",
                    "description": (
                        "Google Drive file ID "
                        "(for get_metadata, download_file, move_file, copy_file)."
                    ),
                },
                "folder_id": {
                    "type": "string",
                    "description": (
                        "Google Drive folder ID "
                        "(for list_files, upload_file, move_file, create_folder parent)."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Drive search query string for the search action. "
                        "Example: \"name contains 'report'\" or "
                        "\"mimeType = 'application/pdf'\"."
                    ),
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Local file path for upload_file action."
                    ),
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "Local file path to save downloaded file "
                        "(for download_file action)."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Name for create_folder action."
                    ),
                },
                "new_name": {
                    "type": "string",
                    "description": (
                        "Optional new name for copy_file action."
                    ),
                },
                "parent_id": {
                    "type": "string",
                    "description": (
                        "Parent folder ID for create_folder action."
                    ),
                },
                "page_size": {
                    "type": "integer",
                    "description": (
                        "Number of results to return (default 20, max 100). "
                        "For list_files and search actions."
                    ),
                    "default": 20,
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
            logger.exception("google_drive %s failed", action)
            return {"success": False, "error": str(exc)}

    # -- Action handlers -----------------------------------------------------

    def _resolve_credentials(self, kwargs: dict):
        """Get credentials from kwargs or auto-load from credential store."""
        creds = kwargs.get("credentials")
        if not creds:
            creds = _get_credentials()
        return creds

    def _list_files(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        folder_id = kwargs.get("folder_id")
        page_size = kwargs.get("page_size", 20)
        return _do_list_files(creds, folder_id=folder_id, page_size=page_size)

    def _search(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        query = kwargs.get("query")
        if not query:
            return {"success": False, "error": "query is required for search"}
        page_size = kwargs.get("page_size", 20)
        return _do_search(query, creds, page_size=page_size)

    def _get_metadata(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        file_id = kwargs.get("file_id")
        if not file_id:
            return {"success": False, "error": "file_id is required"}
        return _do_get_metadata(file_id, creds)

    def _download_file(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        file_id = kwargs.get("file_id")
        if not file_id:
            return {"success": False, "error": "file_id is required for download_file"}
        output_path = kwargs.get("output_path")
        if not output_path:
            return {
                "success": False,
                "error": "output_path is required for download_file",
            }
        return _do_download_file(file_id, output_path, creds)

    def _upload_file(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        file_path = kwargs.get("file_path")
        if not file_path:
            return {"success": False, "error": "file_path is required for upload_file"}
        folder_id = kwargs.get("folder_id")
        return _do_upload_file(file_path, creds, folder_id=folder_id)

    def _create_folder(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        name = kwargs.get("name")
        if not name:
            return {"success": False, "error": "name is required for create_folder"}
        parent_id = kwargs.get("parent_id")
        return _do_create_folder(name, creds, parent_id=parent_id)

    def _move_file(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        file_id = kwargs.get("file_id")
        if not file_id:
            return {"success": False, "error": "file_id is required for move_file"}
        folder_id = kwargs.get("folder_id")
        if not folder_id:
            return {"success": False, "error": "folder_id is required for move_file"}
        return _do_move_file(file_id, folder_id, creds)

    def _copy_file(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        file_id = kwargs.get("file_id")
        if not file_id:
            return {"success": False, "error": "file_id is required for copy_file"}
        new_name = kwargs.get("new_name")
        return _do_copy_file(file_id, creds, new_name=new_name)
