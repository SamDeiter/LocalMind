"""
Google Sheets Tool — Read, write, and manage Google Sheets spreadsheets
via the Sheets API v4 for the LocalMind task worker.

Capabilities:
  - read_spreadsheet  — get spreadsheet metadata: title, sheets list with names/IDs/row+col counts
  - read_range        — read cell values from a range (e.g. "Sheet1!A1:D10")
  - write_range       — write a 2D array of values to a range
  - append_data       — append rows after the last data row
  - create_sheet      — add a new sheet tab
  - delete_sheet      — remove a sheet tab by its numeric sheet ID
  - clear_range       — clear cell values in a range
  - get_cell_formats  — read cell formatting (bold, colors, number formats, etc.)
  - batch_update      — raw batchUpdate for complex / composite operations

Prerequisites:
  pip install google-api-python-client google-auth

Credentials are resolved automatically: if the caller passes a credentials
object it is used directly; otherwise, credentials are auto-loaded from the
centralized Google auth store (backend.routes.google_auth.get_credentials).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools.google_sheets")

# ---------------------------------------------------------------------------
# Lazy import guard
# ---------------------------------------------------------------------------

_gapi_available: bool | None = None


def _require_gapi():
    """Raise a clear RuntimeError if the Google API client library is missing."""
    global _gapi_available
    if _gapi_available is True:
        return
    try:
        import googleapiclient.discovery  # noqa: F401
        _gapi_available = True
    except ImportError:
        _gapi_available = False
        raise RuntimeError(
            "google-api-python-client is not installed. "
            "Run: pip install google-api-python-client google-auth"
        )


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helper — build a Sheets service object
# ---------------------------------------------------------------------------


def _build_service(credentials: Any):
    """
    Build and return a Google Sheets API v4 service resource.

    Args:
        credentials: A google.oauth2.credentials.Credentials (or compatible)
                     object already authorised for the Sheets scope.

    Returns:
        A googleapiclient.discovery.Resource for the Sheets v4 API.
    """
    from googleapiclient.discovery import build

    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


# ---------------------------------------------------------------------------
# Synchronous worker functions (run in executor)
# ---------------------------------------------------------------------------


def _do_read_spreadsheet(spreadsheet_id: str, credentials: Any) -> dict:
    """Fetch spreadsheet metadata: title, sheets list with names/IDs/row+col counts."""
    service = _build_service(credentials)
    try:
        meta = (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="properties.title,sheets.properties")
            .execute()
        )
    except Exception as exc:
        return {"success": False, "error": f"Sheets API error: {exc}"}

    title = meta.get("properties", {}).get("title", "")
    sheets_info: list[dict] = []
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        grid = props.get("gridProperties", {})
        sheets_info.append({
            "sheet_id": props.get("sheetId"),
            "title": props.get("title", ""),
            "index": props.get("index"),
            "row_count": grid.get("rowCount"),
            "column_count": grid.get("columnCount"),
        })

    return {
        "success": True,
        "result": {
            "spreadsheet_id": spreadsheet_id,
            "title": title,
            "sheets": sheets_info,
        },
    }


def _do_read_range(spreadsheet_id: str, range_notation: str, credentials: Any) -> dict:
    """Read cell values from a range."""
    service = _build_service(credentials)
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_notation)
            .execute()
        )
    except Exception as exc:
        return {"success": False, "error": f"Sheets API error: {exc}"}

    return {
        "success": True,
        "result": {
            "spreadsheet_id": spreadsheet_id,
            "range": result.get("range", range_notation),
            "major_dimension": result.get("majorDimension", "ROWS"),
            "values": result.get("values", []),
        },
    }


def _do_write_range(
    spreadsheet_id: str,
    range_notation: str,
    values: list[list[Any]],
    credentials: Any,
) -> dict:
    """Write a 2D array of values to a range."""
    service = _build_service(credentials)
    body = {
        "values": values,
    }
    try:
        result = (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_notation,
                valueInputOption="USER_ENTERED",
                body=body,
            )
            .execute()
        )
    except Exception as exc:
        return {"success": False, "error": f"Sheets API error: {exc}"}

    return {
        "success": True,
        "result": {
            "spreadsheet_id": spreadsheet_id,
            "updated_range": result.get("updatedRange", range_notation),
            "updated_rows": result.get("updatedRows", 0),
            "updated_columns": result.get("updatedColumns", 0),
            "updated_cells": result.get("updatedCells", 0),
        },
    }


def _do_append_data(
    spreadsheet_id: str,
    range_notation: str,
    values: list[list[Any]],
    credentials: Any,
) -> dict:
    """Append rows after the last data row in the specified range."""
    service = _build_service(credentials)
    body = {
        "values": values,
    }
    try:
        result = (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_notation,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body,
            )
            .execute()
        )
    except Exception as exc:
        return {"success": False, "error": f"Sheets API error: {exc}"}

    updates = result.get("updates", {})
    return {
        "success": True,
        "result": {
            "spreadsheet_id": spreadsheet_id,
            "table_range": result.get("tableRange", ""),
            "updated_range": updates.get("updatedRange", ""),
            "updated_rows": updates.get("updatedRows", 0),
            "updated_columns": updates.get("updatedColumns", 0),
            "updated_cells": updates.get("updatedCells", 0),
        },
    }


def _do_create_sheet(
    spreadsheet_id: str, sheet_name: str, credentials: Any
) -> dict:
    """Add a new sheet tab to the spreadsheet."""
    service = _build_service(credentials)
    body = {
        "requests": [
            {
                "addSheet": {
                    "properties": {
                        "title": sheet_name,
                    }
                }
            }
        ]
    }
    try:
        result = (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )
    except Exception as exc:
        return {"success": False, "error": f"Sheets API error: {exc}"}

    replies = result.get("replies", [])
    new_props = {}
    if replies:
        add_reply = replies[0].get("addSheet", {})
        new_props = add_reply.get("properties", {})

    return {
        "success": True,
        "result": {
            "spreadsheet_id": spreadsheet_id,
            "sheet_id": new_props.get("sheetId"),
            "title": new_props.get("title", sheet_name),
            "index": new_props.get("index"),
        },
    }


def _do_delete_sheet(
    spreadsheet_id: str, sheet_id: int, credentials: Any
) -> dict:
    """Remove a sheet tab by its numeric sheet ID."""
    service = _build_service(credentials)
    body = {
        "requests": [
            {
                "deleteSheet": {
                    "sheetId": sheet_id,
                }
            }
        ]
    }
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body=body
        ).execute()
    except Exception as exc:
        return {"success": False, "error": f"Sheets API error: {exc}"}

    return {
        "success": True,
        "result": {
            "spreadsheet_id": spreadsheet_id,
            "deleted_sheet_id": sheet_id,
        },
    }


def _do_clear_range(
    spreadsheet_id: str, range_notation: str, credentials: Any
) -> dict:
    """Clear cell values in a range (formatting is preserved)."""
    service = _build_service(credentials)
    try:
        result = (
            service.spreadsheets()
            .values()
            .clear(spreadsheetId=spreadsheet_id, range=range_notation, body={})
            .execute()
        )
    except Exception as exc:
        return {"success": False, "error": f"Sheets API error: {exc}"}

    return {
        "success": True,
        "result": {
            "spreadsheet_id": spreadsheet_id,
            "cleared_range": result.get("clearedRange", range_notation),
        },
    }


def _do_get_cell_formats(
    spreadsheet_id: str, range_notation: str, credentials: Any
) -> dict:
    """Read cell formatting (bold, colors, number formats, etc.) from a range."""
    service = _build_service(credentials)
    try:
        result = (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                ranges=[range_notation],
                fields="sheets.data.rowData.values.effectiveFormat,"
                       "sheets.data.rowData.values.userEnteredFormat,"
                       "sheets.properties.title",
                includeGridData=True,
            )
            .execute()
        )
    except Exception as exc:
        return {"success": False, "error": f"Sheets API error: {exc}"}

    sheets = result.get("sheets", [])
    formats: list[list[dict]] = []

    if sheets:
        sheet = sheets[0]
        sheet_title = sheet.get("properties", {}).get("title", "")
        for grid_data in sheet.get("data", []):
            for row_data in grid_data.get("rowData", []):
                row_formats: list[dict] = []
                for cell in row_data.get("values", []):
                    cell_fmt: dict[str, Any] = {}
                    eff = cell.get("effectiveFormat", {})
                    user = cell.get("userEnteredFormat", {})

                    # Text format
                    text_fmt = eff.get("textFormat", {})
                    cell_fmt["bold"] = text_fmt.get("bold", False)
                    cell_fmt["italic"] = text_fmt.get("italic", False)
                    cell_fmt["strikethrough"] = text_fmt.get("strikethrough", False)
                    cell_fmt["underline"] = text_fmt.get("underline", False)
                    cell_fmt["font_family"] = text_fmt.get("fontFamily")
                    cell_fmt["font_size"] = text_fmt.get("fontSize")

                    # Foreground color
                    fg = text_fmt.get("foregroundColorStyle", {}).get("rgbColor")
                    if not fg:
                        fg = text_fmt.get("foregroundColor")
                    cell_fmt["foreground_color"] = fg

                    # Background color
                    bg = eff.get("backgroundColorStyle", {}).get("rgbColor")
                    if not bg:
                        bg = eff.get("backgroundColor")
                    cell_fmt["background_color"] = bg

                    # Number format
                    num_fmt = eff.get("numberFormat", {})
                    cell_fmt["number_format_type"] = num_fmt.get("type")
                    cell_fmt["number_format_pattern"] = num_fmt.get("pattern")

                    # Horizontal / vertical alignment
                    cell_fmt["horizontal_alignment"] = eff.get("horizontalAlignment")
                    cell_fmt["vertical_alignment"] = eff.get("verticalAlignment")

                    # Wrap strategy
                    cell_fmt["wrap_strategy"] = eff.get("wrapStrategy")

                    # User-entered overrides (for completeness)
                    cell_fmt["user_entered_format"] = user if user else None

                    row_formats.append(cell_fmt)
                formats.append(row_formats)
    else:
        sheet_title = ""

    return {
        "success": True,
        "result": {
            "spreadsheet_id": spreadsheet_id,
            "range": range_notation,
            "sheet_title": sheet_title,
            "cell_formats": formats,
        },
    }


def _do_batch_update(
    spreadsheet_id: str, requests: list[dict], credentials: Any
) -> dict:
    """Execute a raw batchUpdate for complex / composite operations."""
    service = _build_service(credentials)
    body = {
        "requests": requests,
    }
    try:
        result = (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )
    except Exception as exc:
        return {"success": False, "error": f"Sheets API error: {exc}"}

    return {
        "success": True,
        "result": {
            "spreadsheet_id": result.get("spreadsheetId", spreadsheet_id),
            "replies": result.get("replies", []),
        },
    }


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class GoogleSheetsTool(BaseTool):
    """Read and edit Google Sheets spreadsheets via the Sheets API."""

    @property
    def name(self) -> str:
        return "google_sheets"

    @property
    def description(self) -> str:
        return "Read and edit Google Sheets spreadsheets via the Sheets API"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "read_spreadsheet",
                        "read_range",
                        "write_range",
                        "append_data",
                        "create_sheet",
                        "delete_sheet",
                        "clear_range",
                        "get_cell_formats",
                        "batch_update",
                    ],
                    "description": "The Google Sheets action to perform.",
                },
                "spreadsheet_id": {
                    "type": "string",
                    "description": (
                        "The ID of the Google Sheets spreadsheet "
                        "(the long string in the URL between /d/ and /edit)."
                    ),
                },
                "range_notation": {
                    "type": "string",
                    "description": (
                        "A1 range notation, e.g. 'Sheet1!A1:D10'. "
                        "Required for read_range, write_range, append_data, "
                        "clear_range, get_cell_formats."
                    ),
                },
                "values": {
                    "type": "array",
                    "items": {"type": "array"},
                    "description": (
                        "2D array of cell values for write_range / append_data. "
                        "Each inner array is one row."
                    ),
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Name for the new sheet tab (create_sheet).",
                },
                "sheet_id": {
                    "type": "integer",
                    "description": (
                        "Numeric sheet ID of the tab to delete (delete_sheet). "
                        "Obtain from read_spreadsheet results."
                    ),
                },
                "requests": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "List of Sheets API batchUpdate request objects "
                        "(batch_update). See Google Sheets API docs for schema."
                    ),
                },
                "credentials": {
                    "type": "object",
                    "description": (
                        "Google OAuth2 / service-account credentials object. "
                        "Optional — if omitted, credentials are auto-loaded "
                        "from the credential store."
                    ),
                },
            },
            "required": ["action", "spreadsheet_id"],
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        """Dispatch to the requested Google Sheets action.

        Args:
            action: One of the supported action names.
            spreadsheet_id: Target spreadsheet ID.
            credentials: Google credentials object.
            **kwargs: Action-specific parameters.

        Returns:
            Dict with ``success`` bool and ``result`` or ``error``.
        """
        action: str = kwargs.get("action", "")
        spreadsheet_id: str = kwargs.get("spreadsheet_id", "")

        if not spreadsheet_id:
            return {"success": False, "error": "spreadsheet_id is required"}

        # Resolve credentials: use explicit if provided, otherwise auto-load
        try:
            credentials = _resolve_credentials(kwargs.get("credentials"))
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}
        kwargs["credentials"] = credentials

        try:
            _require_gapi()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        dispatch = {
            "read_spreadsheet": self._read_spreadsheet,
            "read_range": self._read_range,
            "write_range": self._write_range,
            "append_data": self._append_data,
            "create_sheet": self._create_sheet,
            "delete_sheet": self._delete_sheet,
            "clear_range": self._clear_range,
            "get_cell_formats": self._get_cell_formats,
            "batch_update": self._batch_update,
        }

        handler = dispatch.get(action)
        if not handler:
            return {
                "success": False,
                "error": (
                    f"Unknown action: {action!r}. "
                    f"Valid actions: {list(dispatch.keys())}"
                ),
            }

        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, lambda: handler(kwargs))
        except Exception as exc:
            logger.exception(
                "google_sheets %s failed for spreadsheet %s",
                action,
                spreadsheet_id,
            )
            return {"success": False, "error": str(exc)}

    # ── Action handlers ──────────────────────────────────────────────────

    def _read_spreadsheet(self, kwargs: dict) -> dict:
        return _do_read_spreadsheet(
            kwargs["spreadsheet_id"], kwargs["credentials"]
        )

    def _read_range(self, kwargs: dict) -> dict:
        range_notation = kwargs.get("range_notation")
        if not range_notation:
            return {
                "success": False,
                "error": "range_notation is required for read_range",
            }
        return _do_read_range(
            kwargs["spreadsheet_id"], range_notation, kwargs["credentials"]
        )

    def _write_range(self, kwargs: dict) -> dict:
        range_notation = kwargs.get("range_notation")
        values = kwargs.get("values")
        if not range_notation:
            return {
                "success": False,
                "error": "range_notation is required for write_range",
            }
        if values is None:
            return {
                "success": False,
                "error": "values (2D array) is required for write_range",
            }
        return _do_write_range(
            kwargs["spreadsheet_id"], range_notation, values, kwargs["credentials"]
        )

    def _append_data(self, kwargs: dict) -> dict:
        range_notation = kwargs.get("range_notation")
        values = kwargs.get("values")
        if not range_notation:
            return {
                "success": False,
                "error": "range_notation is required for append_data",
            }
        if values is None:
            return {
                "success": False,
                "error": "values (2D array) is required for append_data",
            }
        return _do_append_data(
            kwargs["spreadsheet_id"], range_notation, values, kwargs["credentials"]
        )

    def _create_sheet(self, kwargs: dict) -> dict:
        sheet_name = kwargs.get("sheet_name")
        if not sheet_name:
            return {
                "success": False,
                "error": "sheet_name is required for create_sheet",
            }
        return _do_create_sheet(
            kwargs["spreadsheet_id"], sheet_name, kwargs["credentials"]
        )

    def _delete_sheet(self, kwargs: dict) -> dict:
        sheet_id = kwargs.get("sheet_id")
        if sheet_id is None:
            return {
                "success": False,
                "error": "sheet_id (integer) is required for delete_sheet",
            }
        return _do_delete_sheet(
            kwargs["spreadsheet_id"], int(sheet_id), kwargs["credentials"]
        )

    def _clear_range(self, kwargs: dict) -> dict:
        range_notation = kwargs.get("range_notation")
        if not range_notation:
            return {
                "success": False,
                "error": "range_notation is required for clear_range",
            }
        return _do_clear_range(
            kwargs["spreadsheet_id"], range_notation, kwargs["credentials"]
        )

    def _get_cell_formats(self, kwargs: dict) -> dict:
        range_notation = kwargs.get("range_notation")
        if not range_notation:
            return {
                "success": False,
                "error": "range_notation is required for get_cell_formats",
            }
        return _do_get_cell_formats(
            kwargs["spreadsheet_id"], range_notation, kwargs["credentials"]
        )

    def _batch_update(self, kwargs: dict) -> dict:
        requests = kwargs.get("requests")
        if not requests:
            return {
                "success": False,
                "error": "requests list is required and must be non-empty for batch_update",
            }
        return _do_batch_update(
            kwargs["spreadsheet_id"], requests, kwargs["credentials"]
        )
