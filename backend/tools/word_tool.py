"""
Word Tool — Read, analyze, and edit DOCX files for the LocalMind task worker.

Capabilities:
  * read_document      — extract full document text with paragraph-level detail
  * get_paragraph      — get a single paragraph with full formatting details
  * edit_paragraph     — replace paragraph text while preserving style
  * add_paragraph      — insert a new paragraph at a specified position
  * delete_paragraph   — remove a paragraph by index
  * extract_styles     — list all styles used in the document with properties
  * get_tables         — extract all tables as structured row/cell data
  * edit_table_cell    — edit a specific table cell by table/row/col index
  * get_headers_footers — extract header/footer text from all sections
  * get_metadata       — author, title, created, modified, word count

Prerequisites:
  pip install python-docx

All file I/O goes through AtomicFileWriter (crash-safe) and all paths are
validated through safe_resolve (jail at WORKSPACE_ROOT).
"""

from __future__ import annotations

import asyncio
import copy
import logging
from pathlib import Path
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools.word")

# ---------------------------------------------------------------------------
# Lazy imports — kept at module level after first import
# ---------------------------------------------------------------------------


def _require_docx():
    """Raise a clear RuntimeError if python-docx is not installed."""
    try:
        import docx  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "python-docx is not installed. Run: pip install python-docx"
        )


# ---------------------------------------------------------------------------
# Helpers
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

        resolved = safe_resolve(WORKSPACE_ROOT, file_path)
        return resolved
    except ImportError:
        # Test / standalone environment — resolve without jail.
        return Path(file_path).resolve()


def _extract_run_data(run) -> dict[str, Any]:
    """Extract text and formatting from a single python-docx Run object."""
    font = run.font
    return {
        "text": run.text,
        "bold": font.bold,
        "italic": font.italic,
        "font_name": font.name,
        "font_size": str(font.size) if font.size else None,
    }


def _extract_paragraph_data(paragraph, index: int) -> dict[str, Any]:
    """Extract structured data from a single python-docx Paragraph."""
    return {
        "index": index,
        "text": paragraph.text,
        "style": paragraph.style.name if paragraph.style else None,
        "runs": [_extract_run_data(r) for r in paragraph.runs],
    }


def _open_document(file_path: str):
    """Open a DOCX file, returning (resolved_path, Document) or raising."""
    from docx import Document

    resolved = _safe_path(file_path)
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")
    if resolved.suffix.lower() != ".docx":
        raise ValueError(f"Not a .docx file: {resolved}")

    doc = Document(str(resolved))
    return resolved, doc


def _atomic_save(resolved: Path, doc) -> dict[str, Any]:
    """Save a Document atomically using AtomicFileWriter. Returns write metadata."""
    from backend.core.atomic_io import AtomicFileWriter

    writer = AtomicFileWriter()
    result = writer.write_atomic(resolved, lambda tmp: doc.save(str(tmp)))
    return {
        "output_path": str(result.path),
        "sha256": result.sha256,
        "size_bytes": result.size_bytes,
    }


# ---------------------------------------------------------------------------
# Synchronous worker functions (run in executor)
# ---------------------------------------------------------------------------


def _do_read_document(file_path: str) -> dict[str, Any]:
    """Extract full document text with paragraph-level detail."""
    resolved, doc = _open_document(file_path)

    paragraphs = [
        _extract_paragraph_data(p, i) for i, p in enumerate(doc.paragraphs)
    ]

    # Attempt to extract document title from core properties
    title = ""
    try:
        title = doc.core_properties.title or ""
    except Exception:
        pass

    return {
        "success": True,
        "result": {
            "file_path": str(resolved),
            "title": title,
            "paragraphs": paragraphs,
            "section_count": len(doc.sections),
            "paragraph_count": len(doc.paragraphs),
        },
    }


def _do_get_paragraph(file_path: str, index: int) -> dict[str, Any]:
    """Get a single paragraph with full formatting details."""
    resolved, doc = _open_document(file_path)
    paras = doc.paragraphs

    if not (0 <= index < len(paras)):
        return {
            "success": False,
            "error": (
                f"Paragraph index {index} out of range. "
                f"Document has {len(paras)} paragraphs (0-based)."
            ),
        }

    return {
        "success": True,
        "result": _extract_paragraph_data(paras[index], index),
    }


def _do_edit_paragraph(file_path: str, index: int, new_text: str) -> dict[str, Any]:
    """Replace paragraph text while preserving its style. Atomic write."""
    resolved, doc = _open_document(file_path)
    paras = doc.paragraphs

    if not (0 <= index < len(paras)):
        return {
            "success": False,
            "error": (
                f"Paragraph index {index} out of range. "
                f"Document has {len(paras)} paragraphs (0-based)."
            ),
        }

    para = paras[index]
    old_text = para.text
    style_name = para.style.name if para.style else "Normal"

    # Preserve formatting: put all text in the first run, clear the rest.
    runs = para.runs
    if not runs:
        # No existing runs — add one with the paragraph style.
        run = para.add_run(new_text)
    elif len(runs) == 1:
        runs[0].text = new_text
    else:
        # Multiple runs: set text on first, clear the rest to preserve
        # the dominant run-level formatting.
        runs[0].text = new_text
        for run in runs[1:]:
            run.text = ""

    write_meta = _atomic_save(resolved, doc)

    return {
        "success": True,
        "result": {
            **write_meta,
            "index": index,
            "old_text": old_text,
            "new_text": new_text,
            "style": style_name,
        },
    }


def _do_add_paragraph(
    file_path: str, text: str, style: str = "Normal", after_index: int | None = None
) -> dict[str, Any]:
    """Add a new paragraph. Optionally insert after a specific index. Atomic write."""
    from docx.oxml.ns import qn

    resolved, doc = _open_document(file_path)
    paras = doc.paragraphs

    # Validate style exists in the document
    style_names = [s.name for s in doc.styles if s.type is not None]
    # python-docx allows style lookup by name even if not in the explicit list,
    # but we still validate for a clean error message.
    try:
        _ = doc.styles[style]
    except KeyError:
        return {
            "success": False,
            "error": (
                f"Style '{style}' not found in document. "
                f"Available paragraph styles: "
                f"{[s.name for s in doc.styles if str(getattr(s, 'type', '')) == 'WD_STYLE_TYPE.PARAGRAPH (1)' or (hasattr(s, 'type') and s.type is not None and s.type.name == 'PARAGRAPH')]}"
            ),
        }

    if after_index is not None:
        if not (0 <= after_index < len(paras)):
            return {
                "success": False,
                "error": (
                    f"after_index {after_index} out of range. "
                    f"Document has {len(paras)} paragraphs (0-based)."
                ),
            }
        # Insert after the specified paragraph by manipulating the XML.
        ref_para = paras[after_index]
        new_para_element = copy.deepcopy(ref_para._element)
        # Clear the copied element's text content
        for child in list(new_para_element):
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "r":  # run elements
                new_para_element.remove(child)
        # Remove paragraph properties except the style
        ref_para._element.addnext(new_para_element)

        # Now create a proper paragraph wrapper and set text + style
        from docx.text.paragraph import Paragraph

        new_para = Paragraph(new_para_element, doc)
        new_para.style = doc.styles[style]
        new_para.add_run(text)
        inserted_index = after_index + 1
    else:
        # Append at the end
        new_para = doc.add_paragraph(text, style=style)
        inserted_index = len(doc.paragraphs) - 1

    write_meta = _atomic_save(resolved, doc)

    return {
        "success": True,
        "result": {
            **write_meta,
            "index": inserted_index,
            "text": text,
            "style": style,
            "paragraph_count": len(doc.paragraphs),
        },
    }


def _do_delete_paragraph(file_path: str, index: int) -> dict[str, Any]:
    """Remove a paragraph by index. Atomic write."""
    resolved, doc = _open_document(file_path)
    paras = doc.paragraphs

    if not (0 <= index < len(paras)):
        return {
            "success": False,
            "error": (
                f"Paragraph index {index} out of range. "
                f"Document has {len(paras)} paragraphs (0-based)."
            ),
        }

    para = paras[index]
    deleted_text = para.text
    deleted_style = para.style.name if para.style else None

    # Remove the paragraph's XML element from its parent
    parent = para._element.getparent()
    parent.remove(para._element)

    write_meta = _atomic_save(resolved, doc)

    return {
        "success": True,
        "result": {
            **write_meta,
            "deleted_index": index,
            "deleted_text": deleted_text,
            "deleted_style": deleted_style,
            "paragraph_count": len(doc.paragraphs),
        },
    }


def _do_extract_styles(file_path: str) -> dict[str, Any]:
    """List all styles used in the document with their properties."""
    from docx.enum.style import WD_STYLE_TYPE

    resolved, doc = _open_document(file_path)

    styles_out: list[dict[str, Any]] = []
    for style in doc.styles:
        if style.type is None:
            continue

        style_data: dict[str, Any] = {
            "name": style.name,
            "style_id": style.style_id,
            "type": style.type.name if hasattr(style.type, "name") else str(style.type),
            "builtin": style.builtin,
            "hidden": getattr(style, "hidden", None),
        }

        # Extract font properties if available (paragraph and character styles)
        if style.type in (WD_STYLE_TYPE.PARAGRAPH, WD_STYLE_TYPE.CHARACTER):
            try:
                font = style.font
                style_data["font"] = {
                    "name": font.name,
                    "size": str(font.size) if font.size else None,
                    "bold": font.bold,
                    "italic": font.italic,
                    "underline": font.underline,
                }
            except Exception:
                style_data["font"] = None

        # Extract paragraph formatting if available
        if style.type == WD_STYLE_TYPE.PARAGRAPH:
            try:
                pf = style.paragraph_format
                style_data["paragraph_format"] = {
                    "alignment": str(pf.alignment) if pf.alignment else None,
                    "line_spacing": str(pf.line_spacing) if pf.line_spacing else None,
                    "space_before": str(pf.space_before) if pf.space_before else None,
                    "space_after": str(pf.space_after) if pf.space_after else None,
                }
            except Exception:
                style_data["paragraph_format"] = None

        # Base style
        try:
            style_data["base_style"] = style.base_style.name if style.base_style else None
        except Exception:
            style_data["base_style"] = None

        styles_out.append(style_data)

    # Also collect styles actually used in the document body
    used_styles: set[str] = set()
    for para in doc.paragraphs:
        if para.style:
            used_styles.add(para.style.name)

    return {
        "success": True,
        "result": {
            "file_path": str(resolved),
            "styles": styles_out,
            "styles_in_use": sorted(used_styles),
            "total_styles": len(styles_out),
        },
    }


def _do_get_tables(file_path: str) -> dict[str, Any]:
    """Extract all tables as structured row/cell data."""
    resolved, doc = _open_document(file_path)

    tables_out: list[dict[str, Any]] = []
    for idx, table in enumerate(doc.tables):
        rows_out: list[list[str]] = []
        for row in table.rows:
            rows_out.append([cell.text for cell in row.cells])
        tables_out.append({
            "table_index": idx,
            "rows": rows_out,
            "row_count": len(table.rows),
            "col_count": len(table.columns),
        })

    return {
        "success": True,
        "result": {
            "file_path": str(resolved),
            "tables": tables_out,
            "table_count": len(tables_out),
        },
    }


def _do_edit_table_cell(
    file_path: str, table_index: int, row: int, col: int, new_text: str
) -> dict[str, Any]:
    """Edit a specific table cell. Atomic write."""
    resolved, doc = _open_document(file_path)
    tables = doc.tables

    if not (0 <= table_index < len(tables)):
        return {
            "success": False,
            "error": (
                f"table_index {table_index} out of range. "
                f"Document has {len(tables)} tables (0-based)."
            ),
        }

    table = tables[table_index]

    if not (0 <= row < len(table.rows)):
        return {
            "success": False,
            "error": (
                f"Row {row} out of range. "
                f"Table {table_index} has {len(table.rows)} rows (0-based)."
            ),
        }

    if not (0 <= col < len(table.columns)):
        return {
            "success": False,
            "error": (
                f"Column {col} out of range. "
                f"Table {table_index} has {len(table.columns)} columns (0-based)."
            ),
        }

    cell = table.cell(row, col)
    old_text = cell.text

    # Replace cell text while preserving formatting of first paragraph/run
    paras = cell.paragraphs
    if paras:
        first_para = paras[0]
        runs = first_para.runs
        if runs:
            runs[0].text = new_text
            for run in runs[1:]:
                run.text = ""
        else:
            first_para.add_run(new_text)
        # Remove extra paragraphs (cells can have multiple)
        for extra_para in paras[1:]:
            parent = extra_para._element.getparent()
            parent.remove(extra_para._element)
    else:
        cell.text = new_text

    write_meta = _atomic_save(resolved, doc)

    return {
        "success": True,
        "result": {
            **write_meta,
            "table_index": table_index,
            "row": row,
            "col": col,
            "old_text": old_text,
            "new_text": new_text,
        },
    }


def _do_get_headers_footers(file_path: str) -> dict[str, Any]:
    """Extract header/footer text from all sections."""
    resolved, doc = _open_document(file_path)

    sections_out: list[dict[str, Any]] = []
    for idx, section in enumerate(doc.sections):
        section_data: dict[str, Any] = {"section_index": idx}

        # Headers
        headers: dict[str, str | None] = {}
        try:
            header = section.header
            if header and not header.is_linked_to_previous:
                headers["default"] = "\n".join(p.text for p in header.paragraphs)
            else:
                headers["default"] = None
        except Exception:
            headers["default"] = None

        try:
            first_page_header = section.first_page_header
            if first_page_header and not first_page_header.is_linked_to_previous:
                headers["first_page"] = "\n".join(
                    p.text for p in first_page_header.paragraphs
                )
            else:
                headers["first_page"] = None
        except Exception:
            headers["first_page"] = None

        try:
            even_page_header = section.even_page_header
            if even_page_header and not even_page_header.is_linked_to_previous:
                headers["even_page"] = "\n".join(
                    p.text for p in even_page_header.paragraphs
                )
            else:
                headers["even_page"] = None
        except Exception:
            headers["even_page"] = None

        section_data["headers"] = headers

        # Footers
        footers: dict[str, str | None] = {}
        try:
            footer = section.footer
            if footer and not footer.is_linked_to_previous:
                footers["default"] = "\n".join(p.text for p in footer.paragraphs)
            else:
                footers["default"] = None
        except Exception:
            footers["default"] = None

        try:
            first_page_footer = section.first_page_footer
            if first_page_footer and not first_page_footer.is_linked_to_previous:
                footers["first_page"] = "\n".join(
                    p.text for p in first_page_footer.paragraphs
                )
            else:
                footers["first_page"] = None
        except Exception:
            footers["first_page"] = None

        try:
            even_page_footer = section.even_page_footer
            if even_page_footer and not even_page_footer.is_linked_to_previous:
                footers["even_page"] = "\n".join(
                    p.text for p in even_page_footer.paragraphs
                )
            else:
                footers["even_page"] = None
        except Exception:
            footers["even_page"] = None

        section_data["footers"] = footers
        sections_out.append(section_data)

    return {
        "success": True,
        "result": {
            "file_path": str(resolved),
            "sections": sections_out,
            "section_count": len(sections_out),
        },
    }


def _do_get_metadata(file_path: str) -> dict[str, Any]:
    """Extract document metadata: author, title, created, modified, word count."""
    resolved, doc = _open_document(file_path)

    props = doc.core_properties

    # Word count: count words across all paragraphs
    word_count = sum(
        len(p.text.split()) for p in doc.paragraphs if p.text.strip()
    )

    # Character count
    char_count = sum(len(p.text) for p in doc.paragraphs)

    return {
        "success": True,
        "result": {
            "file_path": str(resolved),
            "author": getattr(props, "author", None) or "",
            "title": getattr(props, "title", None) or "",
            "subject": getattr(props, "subject", None) or "",
            "keywords": getattr(props, "keywords", None) or "",
            "category": getattr(props, "category", None) or "",
            "comments": getattr(props, "comments", None) or "",
            "last_modified_by": getattr(props, "last_modified_by", None) or "",
            "revision": getattr(props, "revision", None),
            "created": str(getattr(props, "created", "") or ""),
            "modified": str(getattr(props, "modified", "") or ""),
            "word_count": word_count,
            "char_count": char_count,
            "paragraph_count": len(doc.paragraphs),
            "section_count": len(doc.sections),
            "table_count": len(doc.tables),
        },
    }


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class WordTool(BaseTool):
    """Read and edit Word (.docx) documents while preserving formatting."""

    @property
    def name(self) -> str:
        return "word"

    @property
    def description(self) -> str:
        return (
            "Read and edit Word (.docx) documents while preserving formatting. "
            "Supports reading full document text with run-level detail, "
            "editing/adding/deleting paragraphs, extracting styles, tables, "
            "headers/footers, and metadata. All writes are atomic and crash-safe."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "read_document",
                        "get_paragraph",
                        "edit_paragraph",
                        "add_paragraph",
                        "delete_paragraph",
                        "extract_styles",
                        "get_tables",
                        "edit_table_cell",
                        "get_headers_footers",
                        "get_metadata",
                    ],
                    "description": "The Word document action to perform.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Path to the .docx file (relative to workspace root or absolute).",
                },
                "index": {
                    "type": "integer",
                    "description": "0-based paragraph index (for get_paragraph, edit_paragraph, delete_paragraph).",
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text (for edit_paragraph, edit_table_cell).",
                },
                "text": {
                    "type": "string",
                    "description": "Text content for the new paragraph (for add_paragraph).",
                },
                "style": {
                    "type": "string",
                    "description": "Paragraph style name (for add_paragraph, default 'Normal').",
                },
                "after_index": {
                    "type": "integer",
                    "description": (
                        "Insert the new paragraph after this 0-based index "
                        "(for add_paragraph). Omit to append at end."
                    ),
                },
                "table_index": {
                    "type": "integer",
                    "description": "0-based table index (for edit_table_cell).",
                },
                "row": {
                    "type": "integer",
                    "description": "0-based row index (for edit_table_cell).",
                },
                "col": {
                    "type": "integer",
                    "description": "0-based column index (for edit_table_cell).",
                },
            },
            "required": ["action", "file_path"],
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        """Route to the appropriate action handler."""
        action: str = kwargs.get("action", "")
        file_path: str = kwargs.get("file_path", "")

        if not file_path:
            return {"success": False, "error": "file_path is required"}

        try:
            _require_docx()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        loop = asyncio.get_event_loop()

        dispatch = {
            "read_document": self._read_document,
            "get_paragraph": self._get_paragraph,
            "edit_paragraph": self._edit_paragraph,
            "add_paragraph": self._add_paragraph,
            "delete_paragraph": self._delete_paragraph,
            "extract_styles": self._extract_styles,
            "get_tables": self._get_tables,
            "edit_table_cell": self._edit_table_cell,
            "get_headers_footers": self._get_headers_footers,
            "get_metadata": self._get_metadata,
        }

        handler = dispatch.get(action)
        if not handler:
            return {
                "success": False,
                "error": (
                    f"Unknown action: '{action}'. "
                    f"Valid actions: {sorted(dispatch.keys())}"
                ),
            }

        try:
            return await loop.run_in_executor(None, lambda: handler(kwargs))
        except FileNotFoundError as exc:
            return {"success": False, "error": str(exc)}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("word %s failed for %s", action, file_path)
            return {"success": False, "error": str(exc)}

    # ── Action handlers ───────────────────────────────────────────────────────

    def _read_document(self, kwargs: dict) -> dict:
        return _do_read_document(kwargs["file_path"])

    def _get_paragraph(self, kwargs: dict) -> dict:
        index = kwargs.get("index")
        if index is None:
            return {"success": False, "error": "index is required for get_paragraph"}
        return _do_get_paragraph(kwargs["file_path"], int(index))

    def _edit_paragraph(self, kwargs: dict) -> dict:
        index = kwargs.get("index")
        if index is None:
            return {"success": False, "error": "index is required for edit_paragraph"}
        new_text = kwargs.get("new_text")
        if new_text is None:
            return {"success": False, "error": "new_text is required for edit_paragraph"}
        return _do_edit_paragraph(kwargs["file_path"], int(index), str(new_text))

    def _add_paragraph(self, kwargs: dict) -> dict:
        text = kwargs.get("text")
        if text is None:
            return {"success": False, "error": "text is required for add_paragraph"}
        style = kwargs.get("style", "Normal")
        after_index = kwargs.get("after_index")
        if after_index is not None:
            after_index = int(after_index)
        return _do_add_paragraph(
            kwargs["file_path"], str(text), style=str(style), after_index=after_index
        )

    def _delete_paragraph(self, kwargs: dict) -> dict:
        index = kwargs.get("index")
        if index is None:
            return {"success": False, "error": "index is required for delete_paragraph"}
        return _do_delete_paragraph(kwargs["file_path"], int(index))

    def _extract_styles(self, kwargs: dict) -> dict:
        return _do_extract_styles(kwargs["file_path"])

    def _get_tables(self, kwargs: dict) -> dict:
        return _do_get_tables(kwargs["file_path"])

    def _edit_table_cell(self, kwargs: dict) -> dict:
        table_index = kwargs.get("table_index")
        if table_index is None:
            return {"success": False, "error": "table_index is required for edit_table_cell"}
        row = kwargs.get("row")
        if row is None:
            return {"success": False, "error": "row is required for edit_table_cell"}
        col = kwargs.get("col")
        if col is None:
            return {"success": False, "error": "col is required for edit_table_cell"}
        new_text = kwargs.get("new_text")
        if new_text is None:
            return {"success": False, "error": "new_text is required for edit_table_cell"}
        return _do_edit_table_cell(
            kwargs["file_path"], int(table_index), int(row), int(col), str(new_text)
        )

    def _get_headers_footers(self, kwargs: dict) -> dict:
        return _do_get_headers_footers(kwargs["file_path"])

    def _get_metadata(self, kwargs: dict) -> dict:
        return _do_get_metadata(kwargs["file_path"])
