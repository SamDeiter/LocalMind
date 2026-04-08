"""
Google Slides Tool — Read and edit Google Slides presentations via the Slides API.

Capabilities:
  - read_presentation  — full presentation metadata: title, slides count, IDs, layouts
  - get_slide          — single slide details: shapes, text content, notes
  - get_slide_text     — extract just text from all shapes on a slide
  - update_text        — replace text in a specific shape via batchUpdate
  - add_slide          — insert a new slide with a chosen layout
  - delete_slide       — remove a slide by its object ID
  - duplicate_slide    — duplicate an existing slide
  - download_as_pptx   — export presentation as a .pptx file via Drive API

Prerequisites:
  pip install google-api-python-client google-auth

Credentials are resolved automatically: if the caller passes a credentials
object it is used directly; otherwise, credentials are auto-loaded from the
centralized Google auth store (backend.routes.google_auth.get_credentials).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

from .base import BaseTool

logger = logging.getLogger("localmind.tools.google_slides")

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


def _get_credentials():
    """Load Google credentials from the centralized credential store.

    Returns a credentials object or None if unavailable.
    Matches the pattern used by gmail_tool.py.
    """
    try:
        from backend.routes.google_auth import get_credentials
        return get_credentials()
    except Exception:
        return None


def _resolve_credentials(credentials):
    """Return *credentials* if provided, otherwise auto-load from the store.

    Raises RuntimeError when no credentials can be obtained.
    """
    if credentials is not None:
        return credentials
    creds = _get_credentials()
    if creds is None:
        raise RuntimeError(
            "No Google credentials available. Connect Google via Settings "
            "or pass credentials explicitly."
        )
    return creds


def _build_slides_service(credentials):
    """Build an authorized Google Slides API v1 service client."""
    from googleapiclient.discovery import build
    return build("slides", "v1", credentials=credentials, cache_discovery=False)


def _build_drive_service(credentials):
    """Build an authorized Google Drive API v3 service client."""
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


# ---------------------------------------------------------------------------
# Synchronous worker functions (run in executor)
# ---------------------------------------------------------------------------


def _do_read_presentation(presentation_id: str, credentials) -> dict:
    """Get full presentation metadata: title, slide count, slide IDs, layout info."""
    service = _build_slides_service(credentials)

    try:
        presentation = service.presentations().get(
            presentationId=presentation_id
        ).execute()
    except Exception as exc:
        return _api_error("read_presentation", presentation_id, exc)

    slides = presentation.get("slides", [])
    slide_info = []
    for i, slide in enumerate(slides):
        slide_info.append({
            "index": i,
            "objectId": slide.get("objectId"),
            "layoutObjectId": slide.get("slideProperties", {}).get(
                "layoutObjectId"
            ),
        })

    # Gather layout names from masters/layouts
    layouts = []
    for master in presentation.get("masters", []):
        for layout in presentation.get("layouts", []):
            layout_props = layout.get("layoutProperties", {})
            layouts.append({
                "objectId": layout.get("objectId"),
                "name": layout_props.get("name", ""),
                "displayName": layout_props.get("displayName", ""),
            })

    page_size = presentation.get("pageSize", {})
    width = page_size.get("width", {})
    height = page_size.get("height", {})

    return {
        "success": True,
        "result": {
            "presentationId": presentation_id,
            "title": presentation.get("title", ""),
            "locale": presentation.get("locale", ""),
            "slide_count": len(slides),
            "slides": slide_info,
            "page_size": {
                "width": width,
                "height": height,
            },
            "layouts": layouts,
        },
    }


def _do_get_slide(presentation_id: str, slide_index: int, credentials) -> dict:
    """Get single slide details: all shapes, text content, speaker notes."""
    service = _build_slides_service(credentials)

    try:
        presentation = service.presentations().get(
            presentationId=presentation_id
        ).execute()
    except Exception as exc:
        return _api_error("get_slide", presentation_id, exc)

    slides = presentation.get("slides", [])
    if not (0 <= slide_index < len(slides)):
        return {
            "success": False,
            "error": (
                f"slide_index {slide_index} out of range. "
                f"Presentation has {len(slides)} slides (0-based)."
            ),
        }

    slide = slides[slide_index]
    shapes = _extract_shapes(slide)

    # Speaker notes
    notes_text = ""
    notes_page = slide.get("slideProperties", {}).get("notesPage", {})
    if notes_page:
        for element in notes_page.get("pageElements", []):
            shape = element.get("shape", {})
            if shape.get("shapeType") == "TEXT_BOX" or shape.get(
                "placeholder", {}
            ).get("type") == "BODY":
                text_content = shape.get("text", {})
                notes_text = _text_content_to_string(text_content)
                if notes_text.strip():
                    break

    return {
        "success": True,
        "result": {
            "presentationId": presentation_id,
            "slide_index": slide_index,
            "objectId": slide.get("objectId"),
            "shapes": shapes,
            "notes": notes_text,
        },
    }


def _do_get_slide_text(presentation_id: str, slide_index: int, credentials) -> dict:
    """Extract just text from all shapes on a slide."""
    service = _build_slides_service(credentials)

    try:
        presentation = service.presentations().get(
            presentationId=presentation_id
        ).execute()
    except Exception as exc:
        return _api_error("get_slide_text", presentation_id, exc)

    slides = presentation.get("slides", [])
    if not (0 <= slide_index < len(slides)):
        return {
            "success": False,
            "error": (
                f"slide_index {slide_index} out of range. "
                f"Presentation has {len(slides)} slides (0-based)."
            ),
        }

    slide = slides[slide_index]
    text_entries: list[dict[str, str]] = []

    for element in slide.get("pageElements", []):
        shape = element.get("shape", {})
        text_content = shape.get("text")
        if text_content:
            text = _text_content_to_string(text_content)
            if text.strip():
                text_entries.append({
                    "objectId": element.get("objectId", ""),
                    "text": text,
                })

        # Also check tables
        table = element.get("table")
        if table:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    cell_text_content = cell.get("text")
                    if cell_text_content:
                        cell_text = _text_content_to_string(cell_text_content)
                        if cell_text.strip():
                            text_entries.append({
                                "objectId": element.get("objectId", ""),
                                "text": cell_text,
                                "type": "table_cell",
                            })

    return {
        "success": True,
        "result": {
            "presentationId": presentation_id,
            "slide_index": slide_index,
            "text_entries": text_entries,
        },
    }


def _do_update_text(
    presentation_id: str,
    shape_id: str,
    new_text: str,
    credentials,
) -> dict:
    """Replace all text in a specific shape using batchUpdate."""
    service = _build_slides_service(credentials)

    requests = [
        # First, delete all existing text in the shape
        {
            "deleteText": {
                "objectId": shape_id,
                "textRange": {
                    "type": "ALL",
                },
            },
        },
        # Then insert the new text
        {
            "insertText": {
                "objectId": shape_id,
                "insertionIndex": 0,
                "text": new_text,
            },
        },
    ]

    try:
        response = service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests},
        ).execute()
    except Exception as exc:
        return _api_error("update_text", presentation_id, exc)

    return {
        "success": True,
        "result": {
            "presentationId": presentation_id,
            "shape_id": shape_id,
            "new_text": new_text,
            "replies": response.get("replies", []),
        },
    }


def _do_add_slide(
    presentation_id: str,
    layout: str,
    insert_at: int | None,
    credentials,
) -> dict:
    """Add a new slide with the specified predefined layout."""
    service = _build_slides_service(credentials)

    # Build the insertionIndex field only if specified
    request: dict[str, Any] = {
        "createSlide": {
            "slideLayoutReference": {
                "predefinedLayout": layout,
            },
        },
    }

    if insert_at is not None:
        request["createSlide"]["insertionIndex"] = insert_at

    try:
        response = service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [request]},
        ).execute()
    except Exception as exc:
        return _api_error("add_slide", presentation_id, exc)

    # Extract the new slide's object ID from the reply
    replies = response.get("replies", [])
    new_slide_id = ""
    if replies:
        create_reply = replies[0].get("createSlide", {})
        new_slide_id = create_reply.get("objectId", "")

    return {
        "success": True,
        "result": {
            "presentationId": presentation_id,
            "new_slide_id": new_slide_id,
            "layout": layout,
            "insert_at": insert_at,
        },
    }


def _do_delete_slide(
    presentation_id: str,
    slide_id: str,
    credentials,
) -> dict:
    """Remove a slide by its object ID."""
    service = _build_slides_service(credentials)

    request = {
        "deleteObject": {
            "objectId": slide_id,
        },
    }

    try:
        response = service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [request]},
        ).execute()
    except Exception as exc:
        return _api_error("delete_slide", presentation_id, exc)

    return {
        "success": True,
        "result": {
            "presentationId": presentation_id,
            "deleted_slide_id": slide_id,
            "replies": response.get("replies", []),
        },
    }


def _do_duplicate_slide(
    presentation_id: str,
    slide_id: str,
    insert_at: int | None,
    credentials,
) -> dict:
    """Duplicate an existing slide."""
    service = _build_slides_service(credentials)

    request: dict[str, Any] = {
        "duplicateObject": {
            "objectId": slide_id,
        },
    }

    # The Slides API duplicateObject inserts right after the source by default.
    # To control position we need to duplicate then move via updateSlidesPosition.
    requests = [request]

    try:
        response = service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests},
        ).execute()
    except Exception as exc:
        return _api_error("duplicate_slide", presentation_id, exc)

    # Extract the duplicated slide's new object ID
    replies = response.get("replies", [])
    new_slide_id = ""
    if replies:
        dup_reply = replies[0].get("duplicateObject", {})
        new_slide_id = dup_reply.get("objectId", "")

    # If insert_at was specified and we got a new slide ID, move it
    if insert_at is not None and new_slide_id:
        move_request = {
            "updateSlidesPosition": {
                "slideObjectIds": [new_slide_id],
                "insertionIndex": insert_at,
            },
        }
        try:
            service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={"requests": [move_request]},
            ).execute()
        except Exception as exc:
            # Slide was duplicated but move failed — report partial success
            logger.warning(
                "Slide duplicated (%s) but move to index %d failed: %s",
                new_slide_id, insert_at, exc,
            )
            return {
                "success": True,
                "result": {
                    "presentationId": presentation_id,
                    "source_slide_id": slide_id,
                    "new_slide_id": new_slide_id,
                    "insert_at": insert_at,
                    "warning": (
                        f"Slide duplicated but move to index {insert_at} "
                        f"failed: {exc}"
                    ),
                },
            }

    return {
        "success": True,
        "result": {
            "presentationId": presentation_id,
            "source_slide_id": slide_id,
            "new_slide_id": new_slide_id,
            "insert_at": insert_at,
        },
    }


def _do_download_as_pptx(
    presentation_id: str,
    output_path: str,
    credentials,
) -> dict:
    """Export presentation as a PPTX file via the Drive API."""
    drive_service = _build_drive_service(credentials)

    try:
        # Export as application/vnd.openxmlformats-officedocument.presentationml.presentation
        request = drive_service.files().export(
            fileId=presentation_id,
            mimeType="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        content = request.execute()
    except Exception as exc:
        return _api_error("download_as_pptx", presentation_id, exc)

    # Write to disk
    resolved = Path(output_path).resolve()
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Failed to write file to {resolved}: {exc}",
        }

    return {
        "success": True,
        "result": {
            "presentationId": presentation_id,
            "output_path": str(resolved),
            "size_bytes": resolved.stat().st_size,
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_content_to_string(text_content: dict) -> str:
    """Convert a Slides API TextContent dict to a plain string."""
    if not text_content:
        return ""
    parts: list[str] = []
    for element in text_content.get("textElements", []):
        text_run = element.get("textRun")
        if text_run:
            parts.append(text_run.get("content", ""))
    return "".join(parts)


def _extract_shapes(slide: dict) -> list[dict]:
    """Extract shape info from a slide's pageElements."""
    shapes: list[dict] = []
    for element in slide.get("pageElements", []):
        shape_data: dict[str, Any] = {
            "objectId": element.get("objectId", ""),
            "size": element.get("size", {}),
            "transform": element.get("transform", {}),
        }

        shape = element.get("shape")
        if shape:
            shape_data["type"] = "shape"
            shape_data["shapeType"] = shape.get("shapeType", "")
            placeholder = shape.get("placeholder", {})
            if placeholder:
                shape_data["placeholder"] = {
                    "type": placeholder.get("type", ""),
                    "index": placeholder.get("index"),
                }
            text_content = shape.get("text")
            if text_content:
                shape_data["text"] = _text_content_to_string(text_content)
                shape_data["textElements"] = text_content.get(
                    "textElements", []
                )
            else:
                shape_data["text"] = ""

        table = element.get("table")
        if table:
            shape_data["type"] = "table"
            shape_data["rows"] = table.get("rows", 0)
            shape_data["columns"] = table.get("columns", 0)
            cells: list[list[str]] = []
            for row in table.get("tableRows", []):
                row_texts: list[str] = []
                for cell in row.get("tableCells", []):
                    cell_text_content = cell.get("text")
                    row_texts.append(
                        _text_content_to_string(cell_text_content)
                        if cell_text_content
                        else ""
                    )
                cells.append(row_texts)
            shape_data["cells"] = cells

        image = element.get("image")
        if image:
            shape_data["type"] = "image"
            shape_data["contentUrl"] = image.get("contentUrl", "")
            shape_data["sourceUrl"] = image.get("sourceUrl", "")

        # Fallback type
        if "type" not in shape_data:
            shape_data["type"] = "unknown"

        shapes.append(shape_data)

    return shapes


def _api_error(action: str, presentation_id: str, exc: Exception) -> dict:
    """Build a structured error dict from a Google API exception."""
    error_msg = str(exc)

    # Try to extract HTTP status code from googleapiclient errors
    status_code = None
    try:
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError):
            status_code = exc.resp.status
            if status_code == 404:
                error_msg = (
                    f"Presentation not found: '{presentation_id}'. "
                    "Check that the ID is correct and you have access."
                )
            elif status_code == 403:
                error_msg = (
                    f"Permission denied for presentation '{presentation_id}'. "
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
        "google_slides %s failed for %s: %s",
        action, presentation_id, error_msg,
    )

    result: dict[str, Any] = {
        "success": False,
        "error": error_msg,
        "action": action,
        "presentationId": presentation_id,
    }
    if status_code is not None:
        result["status_code"] = status_code

    return result


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class GoogleSlidesTool(BaseTool):
    """Read and edit Google Slides presentations via the Slides API."""

    def __init__(self):
        self.actions: dict[str, Callable] = {
            "read_presentation": self._read_presentation,
            "get_slide": self._get_slide,
            "get_slide_text": self._get_slide_text,
            "update_text": self._update_text,
            "add_slide": self._add_slide,
            "delete_slide": self._delete_slide,
            "duplicate_slide": self._duplicate_slide,
            "download_as_pptx": self._download_as_pptx,
        }

    @property
    def name(self) -> str:
        return "google_slides"

    @property
    def description(self) -> str:
        return "Read and edit Google Slides presentations via the Slides API"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(self.actions.keys()),
                    "description": "The Google Slides action to perform.",
                },
                "presentation_id": {
                    "type": "string",
                    "description": (
                        "The Google Slides presentation ID "
                        "(from the URL: docs.google.com/presentation/d/<ID>/edit)."
                    ),
                },
                "slide_index": {
                    "type": "integer",
                    "description": "0-based index of the slide (for get_slide, get_slide_text).",
                },
                "slide_id": {
                    "type": "string",
                    "description": (
                        "Object ID of a slide (for delete_slide, duplicate_slide). "
                        "Get this from read_presentation."
                    ),
                },
                "shape_id": {
                    "type": "string",
                    "description": (
                        "Object ID of a shape to update (for update_text). "
                        "Get this from get_slide."
                    ),
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text for update_text.",
                },
                "layout": {
                    "type": "string",
                    "description": (
                        "Predefined layout for add_slide. One of: "
                        "BLANK, CAPTION_ONLY, TITLE, TITLE_AND_BODY, "
                        "TITLE_AND_TWO_COLUMNS, TITLE_ONLY, SECTION_HEADER, "
                        "SECTION_TITLE_AND_DESCRIPTION, ONE_COLUMN_TEXT, "
                        "MAIN_POINT, BIG_NUMBER. Default: BLANK."
                    ),
                    "default": "BLANK",
                },
                "insert_at": {
                    "type": "integer",
                    "description": (
                        "0-based insertion index for add_slide / duplicate_slide. "
                        "Omit to append at end (add_slide) or insert after source "
                        "(duplicate_slide)."
                    ),
                },
                "output_path": {
                    "type": "string",
                    "description": "Local file path for download_as_pptx output.",
                },
                "credentials": {
                    "type": "object",
                    "description": (
                        "Google OAuth credentials object. Optional — if omitted, "
                        "credentials are auto-loaded from the credential store."
                    ),
                },
            },
            "required": ["action", "presentation_id"],
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        """Dispatch to the right action method."""
        action: str = kwargs.get("action", "")
        presentation_id: str = kwargs.get("presentation_id", "")

        if not presentation_id:
            return {"success": False, "error": "presentation_id is required"}

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
            logger.exception(
                "google_slides %s failed for %s", action, presentation_id,
            )
            return {"success": False, "error": str(exc)}

    # ── Action handlers ──────────────────────────────────────────────────

    def _read_presentation(self, kwargs: dict) -> dict:
        credentials = _resolve_credentials(kwargs.get("credentials"))
        return _do_read_presentation(kwargs["presentation_id"], credentials)

    def _get_slide(self, kwargs: dict) -> dict:
        credentials = _resolve_credentials(kwargs.get("credentials"))
        slide_index = kwargs.get("slide_index")
        if slide_index is None:
            return {
                "success": False,
                "error": "slide_index is required for get_slide",
            }
        return _do_get_slide(
            kwargs["presentation_id"], int(slide_index), credentials,
        )

    def _get_slide_text(self, kwargs: dict) -> dict:
        credentials = _resolve_credentials(kwargs.get("credentials"))
        slide_index = kwargs.get("slide_index")
        if slide_index is None:
            return {
                "success": False,
                "error": "slide_index is required for get_slide_text",
            }
        return _do_get_slide_text(
            kwargs["presentation_id"], int(slide_index), credentials,
        )

    def _update_text(self, kwargs: dict) -> dict:
        credentials = _resolve_credentials(kwargs.get("credentials"))
        shape_id = kwargs.get("shape_id")
        if not shape_id:
            return {
                "success": False,
                "error": "shape_id is required for update_text",
            }
        new_text = kwargs.get("new_text")
        if new_text is None:
            return {
                "success": False,
                "error": "new_text is required for update_text",
            }
        return _do_update_text(
            kwargs["presentation_id"], shape_id, new_text, credentials,
        )

    def _add_slide(self, kwargs: dict) -> dict:
        credentials = _resolve_credentials(kwargs.get("credentials"))
        layout = kwargs.get("layout", "BLANK")
        insert_at = kwargs.get("insert_at")
        return _do_add_slide(
            kwargs["presentation_id"], layout, insert_at, credentials,
        )

    def _delete_slide(self, kwargs: dict) -> dict:
        credentials = _resolve_credentials(kwargs.get("credentials"))
        slide_id = kwargs.get("slide_id")
        if not slide_id:
            return {
                "success": False,
                "error": "slide_id is required for delete_slide",
            }
        return _do_delete_slide(
            kwargs["presentation_id"], slide_id, credentials,
        )

    def _duplicate_slide(self, kwargs: dict) -> dict:
        credentials = _resolve_credentials(kwargs.get("credentials"))
        slide_id = kwargs.get("slide_id")
        if not slide_id:
            return {
                "success": False,
                "error": "slide_id is required for duplicate_slide",
            }
        insert_at = kwargs.get("insert_at")
        return _do_duplicate_slide(
            kwargs["presentation_id"], slide_id, insert_at, credentials,
        )

    def _download_as_pptx(self, kwargs: dict) -> dict:
        credentials = _resolve_credentials(kwargs.get("credentials"))
        output_path = kwargs.get("output_path")
        if not output_path:
            return {
                "success": False,
                "error": "output_path is required for download_as_pptx",
            }
        return _do_download_as_pptx(
            kwargs["presentation_id"], output_path, credentials,
        )
