"""
Excel Tool — Read, analyze, and edit XLSX files for the LocalMind task worker.

Capabilities:
  - read_sheet       — read all cells from a sheet (or active sheet)
  - read_range       — read a specific cell range like "A1:D10"
  - write_cells      — targeted cell edits preserving formatting (atomic write)
  - create_sheet     — add a new sheet to a workbook
  - delete_sheet     — remove a sheet from a workbook
  - list_sheets      — list all sheet names in a workbook
  - get_chart_data   — extract chart data series from a sheet
  - extract_formulas — return cells containing formulas
  - get_metadata     — author, created, modified, sheet count

Prerequisites:
  pip install openpyxl

All file I/O goes through AtomicFileWriter (crash-safe) and all paths are
validated through safe_resolve (jail at WORKSPACE_ROOT).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools.excel")

# ---------------------------------------------------------------------------
# Lazy import guard
# ---------------------------------------------------------------------------

_openpyxl_available: bool | None = None


def _require_openpyxl():
    """Raise a clear RuntimeError if openpyxl is not installed."""
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "openpyxl is not installed. Run: pip install openpyxl"
        )


# ---------------------------------------------------------------------------
# Path helpers (mirrors pptx_tool pattern)
# ---------------------------------------------------------------------------


def _safe_path(file_path: str) -> Path:
    """
    Validate and resolve *file_path* using safe_resolve jailed to WORKSPACE_ROOT.

    Falls back to a simple absolute-path resolve if the security module is
    unavailable (e.g., during unit-test isolation).
    """
    try:
        from backend.config import WORKSPACE_ROOT
        from backend.security.paths import SecurityError, safe_resolve

        resolved = safe_resolve(WORKSPACE_ROOT, file_path)
        return resolved
    except ImportError:
        # Test / standalone environment — resolve without jail.
        return Path(file_path).resolve()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cell_value(cell) -> Any:
    """Return the cell value, converting datetime objects to ISO strings."""
    import datetime

    val = cell.value
    if isinstance(val, datetime.datetime):
        return val.isoformat()
    if isinstance(val, datetime.date):
        return val.isoformat()
    if isinstance(val, datetime.time):
        return val.isoformat()
    return val


def _get_workbook_and_sheet(file_path: str, sheet_name: str | None, data_only: bool = True):
    """
    Load a workbook and resolve the target sheet.

    Returns (wb, ws) tuple.

    Raises ValueError if the file is not found or the sheet does not exist.
    """
    import openpyxl

    resolved = _safe_path(file_path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    try:
        wb = openpyxl.load_workbook(str(resolved), data_only=data_only)
    except Exception as exc:
        raise ValueError(f"Cannot open workbook: {exc}") from exc

    if sheet_name is not None:
        if sheet_name not in wb.sheetnames:
            raise ValueError(
                f"Sheet '{sheet_name}' not found. "
                f"Available sheets: {wb.sheetnames}"
            )
        ws = wb[sheet_name]
    else:
        ws = wb.active
        if ws is None:
            raise ValueError("Workbook has no active sheet")

    return wb, ws


def _parse_range(range_notation: str) -> tuple[str, str]:
    """
    Parse a range like 'A1:D10' into (start_cell, end_cell).

    Raises ValueError on invalid format.
    """
    pattern = re.compile(r"^([A-Za-z]+\d+):([A-Za-z]+\d+)$")
    m = pattern.match(range_notation.strip())
    if not m:
        raise ValueError(
            f"Invalid range notation: {range_notation!r}. "
            f"Expected format like 'A1:D10'."
        )
    return m.group(1).upper(), m.group(2).upper()


# ---------------------------------------------------------------------------
# Synchronous worker functions (run in executor)
# ---------------------------------------------------------------------------


def _do_read_sheet(file_path: str, sheet_name: str | None) -> dict:
    """Read all cells from a sheet (or active sheet)."""
    wb, ws = _get_workbook_and_sheet(file_path, sheet_name)

    headers: list[Any] = []
    rows: list[list[Any]] = []

    for row_idx, row in enumerate(ws.iter_rows(min_row=1), start=1):
        cell_values = [_cell_value(cell) for cell in row]
        if row_idx == 1:
            headers = cell_values
        else:
            rows.append(cell_values)

    wb.close()

    return {
        "success": True,
        "result": {
            "sheet_name": ws.title,
            "headers": headers,
            "rows": rows,
            "row_count": ws.max_row or 0,
            "col_count": ws.max_column or 0,
        },
    }


def _do_read_range(
    file_path: str, range_notation: str, sheet_name: str | None
) -> dict:
    """Read a specific cell range like 'A1:D10'."""
    start_cell, end_cell = _parse_range(range_notation)
    wb, ws = _get_workbook_and_sheet(file_path, sheet_name)

    try:
        cell_range = ws[f"{start_cell}:{end_cell}"]
    except Exception as exc:
        wb.close()
        raise ValueError(
            f"Invalid cell range '{start_cell}:{end_cell}': {exc}"
        ) from exc

    values: list[list[Any]] = []
    for row in cell_range:
        values.append([_cell_value(cell) for cell in row])

    wb.close()

    return {
        "success": True,
        "result": {
            "range": f"{start_cell}:{end_cell}",
            "sheet_name": ws.title,
            "values": values,
        },
    }


def _do_write_cells(
    file_path: str,
    updates: list[dict],
    sheet_name: str | None,
) -> dict:
    """
    Write cell values with atomic file save.

    Each update: {"cell": "A1", "value": ...}
    """
    import openpyxl

    from backend.core.atomic_io import AtomicFileWriter

    resolved = _safe_path(file_path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    # Load WITH formatting preserved (data_only=False so formulas stay)
    wb = openpyxl.load_workbook(str(resolved), data_only=False)

    if sheet_name is not None:
        if sheet_name not in wb.sheetnames:
            wb.close()
            raise ValueError(
                f"Sheet '{sheet_name}' not found. "
                f"Available sheets: {wb.sheetnames}"
            )
        ws = wb[sheet_name]
    else:
        ws = wb.active
        if ws is None:
            wb.close()
            raise ValueError("Workbook has no active sheet")

    # Validate cell references before writing
    cell_pattern = re.compile(r"^[A-Za-z]{1,3}\d+$")
    changes: list[dict] = []
    for update in updates:
        cell_ref = update.get("cell", "")
        if not cell_pattern.match(cell_ref):
            wb.close()
            raise ValueError(
                f"Invalid cell reference: {cell_ref!r}. "
                f"Expected format like 'A1', 'B12', 'AA100'."
            )
        new_value = update.get("value")
        old_value = _cell_value(ws[cell_ref])
        ws[cell_ref] = new_value
        changes.append({
            "cell": cell_ref.upper(),
            "old_value": old_value,
            "new_value": new_value,
        })

    # Atomic write
    writer = AtomicFileWriter()
    result = writer.write_atomic(resolved, lambda tmp: wb.save(str(tmp)))
    wb.close()

    return {
        "success": True,
        "result": {
            "file_path": str(result.path),
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "sheet_name": ws.title,
            "changes": changes,
        },
    }


def _do_create_sheet(file_path: str, sheet_name: str) -> dict:
    """Add a new sheet to an existing workbook (atomic write)."""
    import openpyxl

    from backend.core.atomic_io import AtomicFileWriter

    resolved = _safe_path(file_path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    wb = openpyxl.load_workbook(str(resolved))

    if sheet_name in wb.sheetnames:
        wb.close()
        raise ValueError(
            f"Sheet '{sheet_name}' already exists. "
            f"Existing sheets: {wb.sheetnames}"
        )

    wb.create_sheet(title=sheet_name)

    writer = AtomicFileWriter()
    result = writer.write_atomic(resolved, lambda tmp: wb.save(str(tmp)))
    wb.close()

    return {
        "success": True,
        "result": {
            "file_path": str(result.path),
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "sheet_name": sheet_name,
            "all_sheets": wb.sheetnames,
        },
    }


def _do_delete_sheet(file_path: str, sheet_name: str) -> dict:
    """Remove a sheet from an existing workbook (atomic write)."""
    import openpyxl

    from backend.core.atomic_io import AtomicFileWriter

    resolved = _safe_path(file_path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    wb = openpyxl.load_workbook(str(resolved))

    if sheet_name not in wb.sheetnames:
        wb.close()
        raise ValueError(
            f"Sheet '{sheet_name}' not found. "
            f"Available sheets: {wb.sheetnames}"
        )

    if len(wb.sheetnames) == 1:
        wb.close()
        raise ValueError(
            "Cannot delete the only sheet in the workbook. "
            "A workbook must contain at least one sheet."
        )

    del wb[sheet_name]

    writer = AtomicFileWriter()
    result = writer.write_atomic(resolved, lambda tmp: wb.save(str(tmp)))
    remaining = wb.sheetnames
    wb.close()

    return {
        "success": True,
        "result": {
            "file_path": str(result.path),
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "deleted_sheet": sheet_name,
            "remaining_sheets": remaining,
        },
    }


def _do_list_sheets(file_path: str) -> dict:
    """Return list of sheet names in the workbook."""
    import openpyxl

    resolved = _safe_path(file_path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    wb = openpyxl.load_workbook(str(resolved), read_only=True)
    sheet_names = wb.sheetnames
    active_name = wb.active.title if wb.active else None
    wb.close()

    return {
        "success": True,
        "result": {
            "file_path": str(resolved),
            "sheets": sheet_names,
            "sheet_count": len(sheet_names),
            "active_sheet": active_name,
        },
    }


def _do_get_chart_data(file_path: str, sheet_name: str | None) -> dict:
    """Extract chart data series if any charts exist on the sheet."""
    import openpyxl

    resolved = _safe_path(file_path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    wb = openpyxl.load_workbook(str(resolved))

    if sheet_name is not None:
        if sheet_name not in wb.sheetnames:
            wb.close()
            raise ValueError(
                f"Sheet '{sheet_name}' not found. "
                f"Available sheets: {wb.sheetnames}"
            )
        ws = wb[sheet_name]
    else:
        ws = wb.active
        if ws is None:
            wb.close()
            raise ValueError("Workbook has no active sheet")

    charts_data: list[dict] = []
    for chart in ws._charts:
        chart_info: dict[str, Any] = {
            "title": str(chart.title) if chart.title else None,
            "type": type(chart).__name__,
            "style": getattr(chart, "style", None),
            "series": [],
        }

        for series in chart.series:
            series_info: dict[str, Any] = {
                "title": None,
                "values_ref": None,
                "categories_ref": None,
            }

            # Series title
            if hasattr(series, "title") and series.title:
                series_info["title"] = str(series.title)

            # Value reference (the data range)
            if hasattr(series, "val") and series.val is not None:
                ref = series.val
                if hasattr(ref, "numRef") and ref.numRef is not None:
                    series_info["values_ref"] = str(ref.numRef.f)
                elif hasattr(ref, "numLit") and ref.numLit is not None:
                    series_info["values_ref"] = "[literal data]"

            # Category reference (axis labels)
            if hasattr(series, "cat") and series.cat is not None:
                ref = series.cat
                if hasattr(ref, "numRef") and ref.numRef is not None:
                    series_info["categories_ref"] = str(ref.numRef.f)
                elif hasattr(ref, "strRef") and ref.strRef is not None:
                    series_info["categories_ref"] = str(ref.strRef.f)

            chart_info["series"].append(series_info)

        charts_data.append(chart_info)

    wb.close()

    return {
        "success": True,
        "result": {
            "sheet_name": ws.title,
            "chart_count": len(charts_data),
            "charts": charts_data,
        },
    }


def _do_extract_formulas(file_path: str, sheet_name: str | None) -> dict:
    """Return cells that contain formulas."""
    import openpyxl

    resolved = _safe_path(file_path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    # data_only=False to preserve formula strings
    wb = openpyxl.load_workbook(str(resolved), data_only=False)

    if sheet_name is not None:
        if sheet_name not in wb.sheetnames:
            wb.close()
            raise ValueError(
                f"Sheet '{sheet_name}' not found. "
                f"Available sheets: {wb.sheetnames}"
            )
        ws = wb[sheet_name]
    else:
        ws = wb.active
        if ws is None:
            wb.close()
            raise ValueError("Workbook has no active sheet")

    formulas: list[dict] = []
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                formulas.append({
                    "cell": cell.coordinate,
                    "formula": cell.value,
                })

    wb.close()

    return {
        "success": True,
        "result": {
            "sheet_name": ws.title,
            "formula_count": len(formulas),
            "formulas": formulas,
        },
    }


def _do_get_metadata(file_path: str) -> dict:
    """Return workbook metadata: author, created, modified, sheet count."""
    import openpyxl

    resolved = _safe_path(file_path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    wb = openpyxl.load_workbook(str(resolved), read_only=True)
    props = wb.properties

    author = getattr(props, "creator", None) or ""
    title = getattr(props, "title", None) or ""
    subject = getattr(props, "subject", None) or ""
    description = getattr(props, "description", None) or ""
    created = str(getattr(props, "created", "") or "")
    modified = str(getattr(props, "modified", "") or "")
    last_modified_by = getattr(props, "lastModifiedBy", None) or ""

    sheet_names = wb.sheetnames
    wb.close()

    return {
        "success": True,
        "result": {
            "file_path": str(resolved),
            "author": author,
            "last_modified_by": last_modified_by,
            "title": title,
            "subject": subject,
            "description": description,
            "created": created,
            "modified": modified,
            "sheet_count": len(sheet_names),
            "sheets": sheet_names,
        },
    }


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class ExcelTool(BaseTool):
    """Read and edit Excel (.xlsx) spreadsheets while preserving formatting."""

    @property
    def name(self) -> str:
        return "excel"

    @property
    def description(self) -> str:
        return (
            "Read and edit Excel (.xlsx) spreadsheets while preserving formatting"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "read_sheet",
                        "read_range",
                        "write_cells",
                        "create_sheet",
                        "delete_sheet",
                        "list_sheets",
                        "get_chart_data",
                        "extract_formulas",
                        "get_metadata",
                    ],
                    "description": "The Excel action to perform.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Path to the .xlsx file (relative to workspace root or absolute).",
                },
                "sheet_name": {
                    "type": "string",
                    "description": (
                        "Target sheet name. If omitted, uses the active sheet. "
                        "Required for create_sheet and delete_sheet."
                    ),
                },
                "range_notation": {
                    "type": "string",
                    "description": (
                        "Cell range for read_range, e.g. 'A1:D10'. "
                        "Must include both start and end cells separated by a colon."
                    ),
                },
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cell": {
                                "type": "string",
                                "description": "Cell reference, e.g. 'A1', 'B12'.",
                            },
                            "value": {
                                "description": "The value to write (string, number, or null).",
                            },
                        },
                        "required": ["cell", "value"],
                    },
                    "description": (
                        "List of cell updates for write_cells. "
                        "Each item has 'cell' (e.g. 'A1') and 'value'."
                    ),
                },
            },
            "required": ["action", "file_path"],
        }

    # ── Action dispatch map ──────────────────────────────────────────────────

    @property
    def actions(self) -> dict[str, Any]:
        """Map action names to handler methods."""
        return {
            "read_sheet": self._read_sheet,
            "read_range": self._read_range,
            "write_cells": self._write_cells,
            "create_sheet": self._create_sheet,
            "delete_sheet": self._delete_sheet,
            "list_sheets": self._list_sheets,
            "get_chart_data": self._get_chart_data,
            "extract_formulas": self._extract_formulas,
            "get_metadata": self._get_metadata,
        }

    # ── Main entry point ─────────────────────────────────────────────────────

    async def execute(self, **kwargs) -> dict[str, Any]:
        """Route to the appropriate action handler."""
        action: str = kwargs.get("action", "")
        file_path: str = kwargs.get("file_path", "")

        if not action:
            return {"success": False, "error": "action is required"}
        if not file_path:
            return {"success": False, "error": "file_path is required"}

        try:
            _require_openpyxl()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        handler = self.actions.get(action)
        if not handler:
            return {
                "success": False,
                "error": (
                    f"Unknown action: {action!r}. "
                    f"Available actions: {list(self.actions.keys())}"
                ),
            }

        loop = asyncio.get_event_loop()

        try:
            return await loop.run_in_executor(None, lambda: handler(kwargs))
        except FileNotFoundError as exc:
            return {"success": False, "error": str(exc)}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("excel %s failed for %s", action, file_path)
            return {"success": False, "error": f"Unexpected error: {exc}"}

    # ── Action handlers ──────────────────────────────────────────────────────

    def _read_sheet(self, kwargs: dict) -> dict:
        return _do_read_sheet(kwargs["file_path"], kwargs.get("sheet_name"))

    def _read_range(self, kwargs: dict) -> dict:
        range_notation = kwargs.get("range_notation")
        if not range_notation:
            raise ValueError("range_notation is required for read_range (e.g. 'A1:D10')")
        return _do_read_range(kwargs["file_path"], range_notation, kwargs.get("sheet_name"))

    def _write_cells(self, kwargs: dict) -> dict:
        updates = kwargs.get("updates")
        if not updates:
            raise ValueError("updates list is required and must be non-empty for write_cells")
        return _do_write_cells(kwargs["file_path"], updates, kwargs.get("sheet_name"))

    def _create_sheet(self, kwargs: dict) -> dict:
        sheet_name = kwargs.get("sheet_name")
        if not sheet_name:
            raise ValueError("sheet_name is required for create_sheet")
        return _do_create_sheet(kwargs["file_path"], sheet_name)

    def _delete_sheet(self, kwargs: dict) -> dict:
        sheet_name = kwargs.get("sheet_name")
        if not sheet_name:
            raise ValueError("sheet_name is required for delete_sheet")
        return _do_delete_sheet(kwargs["file_path"], sheet_name)

    def _list_sheets(self, kwargs: dict) -> dict:
        return _do_list_sheets(kwargs["file_path"])

    def _get_chart_data(self, kwargs: dict) -> dict:
        return _do_get_chart_data(kwargs["file_path"], kwargs.get("sheet_name"))

    def _extract_formulas(self, kwargs: dict) -> dict:
        return _do_extract_formulas(kwargs["file_path"], kwargs.get("sheet_name"))

    def _get_metadata(self, kwargs: dict) -> dict:
        return _do_get_metadata(kwargs["file_path"])
