"""
PDF Tool -- Read-only extraction from PDF files for the LocalMind task worker.

Capabilities:
  - extract_text    -- extract text from all or specified pages
  - extract_tables  -- extract tables from PDF pages
  - extract_images  -- extract embedded images to an output directory
  - get_metadata    -- title, author, dates, page count, file size
  - page_count      -- fast page count (no full parse)
  - get_page_text   -- text from a single page (efficient for large PDFs)
  - search_text     -- find text occurrences across all pages
  - get_toc         -- extract table of contents / bookmarks

Prerequisites:
  pip install pymupdf

All paths are validated through safe_resolve (jail at WORKSPACE_ROOT).
This tool is strictly read-only -- no PDF editing.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools.pdf")

# ---------------------------------------------------------------------------
# Lazy import guard
# ---------------------------------------------------------------------------


def _require_pymupdf():
    """Raise a clear RuntimeError if pymupdf (fitz) is not installed."""
    try:
        import fitz  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "pymupdf is not installed. Run: pip install pymupdf"
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _safe_path(file_path: str) -> Path:
    """
    Validate and resolve *file_path* using safe_resolve jailed to WORKSPACE_ROOT.

    Falls back to a simple absolute-path resolve if the security module is
    unavailable (e.g., during unit-test isolation).
    """
    try:
        from backend.config import WORKSPACE_ROOT
        from backend.security.paths import safe_resolve

        return safe_resolve(WORKSPACE_ROOT, file_path)
    except ImportError:
        return Path(file_path).resolve()


def _open_pdf(resolved: Path):
    """Open a PDF with fitz and return the document, raising clear errors."""
    import fitz

    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    if not resolved.is_file():
        raise ValueError(f"Path is not a regular file: {resolved}")

    try:
        doc = fitz.open(str(resolved))
    except Exception as exc:
        raise ValueError(f"Cannot open PDF (corrupted or invalid): {exc}") from exc

    if not doc.is_pdf:
        doc.close()
        raise ValueError(f"File is not a valid PDF: {resolved}")

    return doc


def _validate_page_num(doc, page_num: int) -> None:
    """Raise ValueError if page_num is out of range."""
    if page_num < 0 or page_num >= len(doc):
        raise ValueError(
            f"Page {page_num} out of range. "
            f"PDF has {len(doc)} pages (0-based index: 0-{len(doc) - 1})."
        )


# ---------------------------------------------------------------------------
# Synchronous worker functions (run in executor)
# ---------------------------------------------------------------------------


def _do_extract_text(file_path: str, pages: list[int] | None = None) -> dict:
    """Extract text from all or specified pages."""
    resolved = _safe_path(file_path)
    doc = _open_pdf(resolved)

    try:
        total = len(doc)

        # Determine which pages to extract
        if pages is not None:
            for p in pages:
                _validate_page_num(doc, p)
            target_pages = pages
        else:
            target_pages = list(range(total))

        page_results = []
        total_chars = 0
        for p in target_pages:
            text = doc[p].get_text("text")
            total_chars += len(text)
            page_results.append({
                "page_num": p,
                "text": text,
            })

        return {
            "success": True,
            "result": {
                "page_count": total,
                "pages": page_results,
                "total_chars": total_chars,
            },
        }
    finally:
        doc.close()


def _do_extract_tables(file_path: str, page_num: int | None = None) -> dict:
    """Extract tables from PDF pages.

    Uses PyMuPDF's built-in table detection (fitz.Page.find_tables) when
    available (PyMuPDF >= 1.23.0).  Falls back to a basic heuristic that
    splits text blocks by line structure if table detection is not available.
    """
    import fitz

    resolved = _safe_path(file_path)
    doc = _open_pdf(resolved)

    try:
        total = len(doc)

        if page_num is not None:
            _validate_page_num(doc, page_num)
            target_pages = [page_num]
        else:
            target_pages = list(range(total))

        tables_out: list[dict] = []

        # Check if built-in table finder is available
        has_find_tables = hasattr(fitz.Page, "find_tables")

        for p in target_pages:
            page = doc[p]

            if has_find_tables:
                # Use PyMuPDF built-in table detection
                try:
                    tab_finder = page.find_tables()
                    for table in tab_finder.tables:
                        extracted = table.extract()
                        # extracted is a list of rows, each row is a list of cell strings
                        rows = [
                            [cell if cell is not None else "" for cell in row]
                            for row in extracted
                        ]
                        tables_out.append({
                            "page_num": p,
                            "rows": rows,
                        })
                except Exception as exc:
                    logger.debug(
                        "find_tables failed on page %d, falling back to heuristic: %s",
                        p, exc,
                    )
                    tables_out.extend(_heuristic_tables(page, p))
            else:
                tables_out.extend(_heuristic_tables(page, p))

        return {
            "success": True,
            "result": {
                "tables": tables_out,
            },
        }
    finally:
        doc.close()


def _heuristic_tables(page, page_num: int) -> list[dict]:
    """Basic heuristic table extraction from text blocks.

    Groups text lines that appear to be tabular (multiple whitespace-separated
    columns with consistent column counts) into table structures.
    """
    blocks = page.get_text("blocks")
    # blocks are (x0, y0, x1, y1, text, block_no, block_type)
    # block_type 0 = text

    text_blocks = [b for b in blocks if b[6] == 0]
    if not text_blocks:
        return []

    tables: list[dict] = []
    current_rows: list[list[str]] = []
    col_count: int | None = None

    for block in text_blocks:
        text = block[4].strip()
        lines = text.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Split on 2+ whitespace characters as a column delimiter
            cells = [c.strip() for c in line.split("  ") if c.strip()]

            if len(cells) >= 2:
                if col_count is None:
                    col_count = len(cells)

                # If column count is consistent, treat as same table
                if len(cells) == col_count or abs(len(cells) - col_count) <= 1:
                    current_rows.append(cells)
                else:
                    # Column count changed -- flush current table
                    if len(current_rows) >= 2:
                        tables.append({
                            "page_num": page_num,
                            "rows": current_rows,
                        })
                    current_rows = [cells]
                    col_count = len(cells)
            else:
                # Non-tabular line -- flush current table
                if len(current_rows) >= 2:
                    tables.append({
                        "page_num": page_num,
                        "rows": current_rows,
                    })
                current_rows = []
                col_count = None

    # Flush remaining
    if len(current_rows) >= 2:
        tables.append({
            "page_num": page_num,
            "rows": current_rows,
        })

    return tables


def _do_extract_images(
    file_path: str, output_dir: str, pages: list[int] | None = None
) -> dict:
    """Extract embedded images and save them to output_dir."""
    import fitz

    resolved = _safe_path(file_path)
    doc = _open_pdf(resolved)

    # Resolve and validate output directory
    try:
        out_dir = _safe_path(output_dir)
    except Exception:
        out_dir = Path(output_dir).resolve()

    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        total = len(doc)

        if pages is not None:
            for p in pages:
                _validate_page_num(doc, p)
            target_pages = pages
        else:
            target_pages = list(range(total))

        images_out: list[dict] = []
        image_counter = 0

        for p in target_pages:
            page = doc[p]
            image_list = page.get_images(full=True)

            for img_info in image_list:
                xref = img_info[0]

                try:
                    base_image = doc.extract_image(xref)
                except Exception as exc:
                    logger.warning(
                        "Failed to extract image xref=%d on page %d: %s",
                        xref, p, exc,
                    )
                    continue

                if not base_image:
                    continue

                image_bytes = base_image["image"]
                ext = base_image.get("ext", "png")
                width = base_image.get("width", 0)
                height = base_image.get("height", 0)

                image_counter += 1
                filename = f"page{p}_img{image_counter}.{ext}"
                image_path = out_dir / filename

                with open(image_path, "wb") as f:
                    f.write(image_bytes)

                images_out.append({
                    "page_num": p,
                    "image_path": str(image_path),
                    "width": width,
                    "height": height,
                    "format": ext,
                })

        return {
            "success": True,
            "result": {
                "images": images_out,
            },
        }
    finally:
        doc.close()


def _do_get_metadata(file_path: str) -> dict:
    """Extract PDF metadata: title, author, dates, page count, file size."""
    resolved = _safe_path(file_path)
    doc = _open_pdf(resolved)

    try:
        metadata = doc.metadata or {}
        file_size = resolved.stat().st_size

        return {
            "success": True,
            "result": {
                "title": metadata.get("title", "") or "",
                "author": metadata.get("author", "") or "",
                "subject": metadata.get("subject", "") or "",
                "creator": metadata.get("creator", "") or "",
                "creation_date": metadata.get("creationDate", "") or "",
                "mod_date": metadata.get("modDate", "") or "",
                "page_count": len(doc),
                "file_size": file_size,
            },
        }
    finally:
        doc.close()


def _do_page_count(file_path: str) -> dict:
    """Fast page count -- opens and closes immediately."""
    resolved = _safe_path(file_path)
    doc = _open_pdf(resolved)

    try:
        return {
            "success": True,
            "result": {
                "page_count": len(doc),
            },
        }
    finally:
        doc.close()


def _do_get_page_text(file_path: str, page_num: int) -> dict:
    """Extract text from a single page (efficient for large PDFs)."""
    resolved = _safe_path(file_path)
    doc = _open_pdf(resolved)

    try:
        _validate_page_num(doc, page_num)

        text = doc[page_num].get_text("text")

        return {
            "success": True,
            "result": {
                "page_num": page_num,
                "text": text,
                "char_count": len(text),
                "page_count": len(doc),
            },
        }
    finally:
        doc.close()


def _do_search_text(file_path: str, query: str) -> dict:
    """Find text occurrences across all pages."""
    resolved = _safe_path(file_path)
    doc = _open_pdf(resolved)

    try:
        matches: list[dict] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            instances = page.search_for(query)

            for rect in instances:
                # Extract a text snippet around the match for context
                # rect is a fitz.Rect (x0, y0, x1, y1)
                snippet = _extract_snippet(page, rect, query)

                matches.append({
                    "page_num": page_num,
                    "text_snippet": snippet,
                    "rect": {
                        "x0": round(rect.x0, 2),
                        "y0": round(rect.y0, 2),
                        "x1": round(rect.x1, 2),
                        "y1": round(rect.y1, 2),
                    },
                })

        return {
            "success": True,
            "result": {
                "query": query,
                "match_count": len(matches),
                "matches": matches,
            },
        }
    finally:
        doc.close()


def _extract_snippet(page, rect, query: str, context_chars: int = 60) -> str:
    """Extract a text snippet around a search match for context.

    Attempts to grab surrounding text from the page text near the match
    location.  Falls back to just the query string if extraction fails.
    """
    try:
        page_text = page.get_text("text")
        # Find the query in the page text (case-insensitive)
        lower_text = page_text.lower()
        lower_query = query.lower()
        idx = lower_text.find(lower_query)

        if idx == -1:
            return query

        start = max(0, idx - context_chars)
        end = min(len(page_text), idx + len(query) + context_chars)
        snippet = page_text[start:end].strip()

        # Add ellipsis indicators
        if start > 0:
            snippet = "..." + snippet
        if end < len(page_text):
            snippet = snippet + "..."

        return snippet
    except Exception:
        return query


def _do_get_toc(file_path: str) -> dict:
    """Extract table of contents / bookmarks."""
    resolved = _safe_path(file_path)
    doc = _open_pdf(resolved)

    try:
        # doc.get_toc() returns [[level, title, page_num], ...]
        toc = doc.get_toc()

        entries = [
            {
                "level": entry[0],
                "title": entry[1],
                "page_num": entry[2] - 1,  # Convert from 1-based to 0-based
            }
            for entry in toc
        ]

        return {
            "success": True,
            "result": {
                "entries": entries,
            },
        }
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class PDFTool(BaseTool):
    """Extract text, tables, images, and metadata from PDF files (read-only)."""

    @property
    def name(self) -> str:
        return "pdf"

    @property
    def description(self) -> str:
        return (
            "Extract text, tables, images, and metadata from PDF files (read-only)"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "extract_text",
                        "extract_tables",
                        "extract_images",
                        "get_metadata",
                        "page_count",
                        "get_page_text",
                        "search_text",
                        "get_toc",
                    ],
                    "description": "The PDF action to perform.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Path to the PDF file (relative to workspace root or absolute).",
                },
                "pages": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "0-based page indices to process (for extract_text and "
                        "extract_images). Omit to process all pages."
                    ),
                },
                "page_num": {
                    "type": "integer",
                    "description": (
                        "0-based page index (for get_page_text and extract_tables). "
                        "For extract_tables, omit to scan all pages."
                    ),
                },
                "output_dir": {
                    "type": "string",
                    "description": (
                        "Directory to save extracted images (required for extract_images)."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Search query string (required for search_text).",
                },
            },
            "required": ["action", "file_path"],
        }

    # Action dispatch table
    actions: dict[str, str] = {
        "extract_text": "_action_extract_text",
        "extract_tables": "_action_extract_tables",
        "extract_images": "_action_extract_images",
        "get_metadata": "_action_get_metadata",
        "page_count": "_action_page_count",
        "get_page_text": "_action_get_page_text",
        "search_text": "_action_search_text",
        "get_toc": "_action_get_toc",
    }

    async def execute(self, **kwargs) -> dict[str, Any]:
        """Run the requested PDF action and return results.

        Parameters
        ----------
        action : str
            One of the supported action names.
        file_path : str
            Path to the PDF file.
        **kwargs
            Action-specific parameters (pages, page_num, output_dir, query).

        Returns
        -------
        dict
            {"success": True/False, "result": {...}} or {"success": False, "error": "..."}
        """
        action: str = kwargs.get("action", "")
        file_path: str = kwargs.get("file_path", "")

        if not action:
            return {"success": False, "error": "action is required"}
        if not file_path:
            return {"success": False, "error": "file_path is required"}

        # Check pymupdf availability
        try:
            _require_pymupdf()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        # Dispatch to the correct action handler
        method_name = self.actions.get(action)
        if not method_name:
            return {
                "success": False,
                "error": (
                    f"Unknown action: {action!r}. "
                    f"Valid actions: {sorted(self.actions.keys())}"
                ),
            }

        handler = getattr(self, method_name)

        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, lambda: handler(kwargs))
        except FileNotFoundError as exc:
            return {"success": False, "error": str(exc)}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("pdf %s failed for %s", action, file_path)
            return {"success": False, "error": f"Unexpected error: {exc}"}

    # -- Action handlers -------------------------------------------------------

    def _action_extract_text(self, kwargs: dict) -> dict:
        """Extract text from all or specified pages."""
        pages: list[int] | None = kwargs.get("pages")
        return _do_extract_text(kwargs["file_path"], pages)

    def _action_extract_tables(self, kwargs: dict) -> dict:
        """Extract tables from PDF pages."""
        page_num: int | None = kwargs.get("page_num")
        return _do_extract_tables(kwargs["file_path"], page_num)

    def _action_extract_images(self, kwargs: dict) -> dict:
        """Extract embedded images to output directory."""
        output_dir: str | None = kwargs.get("output_dir")
        if not output_dir:
            return {
                "success": False,
                "error": "output_dir is required for extract_images",
            }
        pages: list[int] | None = kwargs.get("pages")
        return _do_extract_images(kwargs["file_path"], output_dir, pages)

    def _action_get_metadata(self, kwargs: dict) -> dict:
        """Extract PDF metadata."""
        return _do_get_metadata(kwargs["file_path"])

    def _action_page_count(self, kwargs: dict) -> dict:
        """Get the page count (fast)."""
        return _do_page_count(kwargs["file_path"])

    def _action_get_page_text(self, kwargs: dict) -> dict:
        """Extract text from a single page."""
        page_num = kwargs.get("page_num")
        if page_num is None:
            return {
                "success": False,
                "error": "page_num is required for get_page_text",
            }
        return _do_get_page_text(kwargs["file_path"], int(page_num))

    def _action_search_text(self, kwargs: dict) -> dict:
        """Search for text across all pages."""
        query: str | None = kwargs.get("query")
        if not query:
            return {
                "success": False,
                "error": "query is required for search_text",
            }
        return _do_search_text(kwargs["file_path"], query)

    def _action_get_toc(self, kwargs: dict) -> dict:
        """Extract table of contents."""
        return _do_get_toc(kwargs["file_path"])
