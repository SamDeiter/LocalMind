"""
PowerPoint Tool — Read, analyze, and edit PPTX files for the LocalMind task worker.

Capabilities:
  • read_metadata   — slide count, dimensions, author/title, layout names, theme info
  • extract_styles  — style guide (fonts, colors, sizes, placeholder positions) for LLM formatting
  • extract_content — per-slide shape content with position and run-level style details
  • edit_slide      — targeted shape-text edits preserving run-level formatting
  • add_slide       — insert a new slide from an existing layout with content
  • apply_edits     — batch multi-slide edits in a single file write

Prerequisites:
  pip install python-pptx

All file I/O goes through AtomicFileWriter (crash-safe) and all paths are
validated through safe_resolve (jail at WORKSPACE_ROOT).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools.pptx")

# ---------------------------------------------------------------------------
# Lazy imports — kept at module level after first import
# ---------------------------------------------------------------------------

_pptx_available: bool | None = None


def _require_pptx():
    """Raise a clear RuntimeError if python-pptx is not installed."""
    try:
        import pptx  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "python-pptx is not installed. Run: pip install python-pptx"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emu_to_pt(emu: int | None) -> float | None:
    """Convert English Metric Units to points (914400 EMU = 1 inch = 72 pt)."""
    if emu is None:
        return None
    return round(emu / 12700, 2)


def _color_hex(color_obj) -> str | None:
    """Return a '#RRGGBB' string from a pptx RGBColor, or None."""
    try:
        from pptx.util import Pt  # noqa: F401 — guard import

        if color_obj is None:
            return None
        rgb = color_obj.rgb
        # RGBColor.__format__ does not support :06X — use str() instead.
        return f"#{rgb}"
    except Exception:
        return None


def _shape_type_name(shape) -> str:
    """Return a human-readable shape type string."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    type_map = {
        MSO_SHAPE_TYPE.AUTO_SHAPE: "auto_shape",
        MSO_SHAPE_TYPE.PICTURE: "picture",
        MSO_SHAPE_TYPE.GROUP: "group",
        MSO_SHAPE_TYPE.TEXT_BOX: "text_box",
        MSO_SHAPE_TYPE.TABLE: "table",
        MSO_SHAPE_TYPE.CHART: "chart",
        MSO_SHAPE_TYPE.PLACEHOLDER: "placeholder",
        MSO_SHAPE_TYPE.LINE: "line",
        MSO_SHAPE_TYPE.MEDIA: "media",
    }
    return type_map.get(shape.shape_type, f"shape_type_{shape.shape_type}")


def _extract_run_style(run) -> dict:
    """Extract font style from a single text run."""
    font = run.font
    style: dict[str, Any] = {}
    try:
        style["font"] = font.name
    except Exception:
        style["font"] = None
    try:
        size_pt = font.size
        style["size"] = round(size_pt / 12700, 1) if size_pt is not None else None
    except Exception:
        style["size"] = None
    try:
        style["bold"] = font.bold
    except Exception:
        style["bold"] = None
    try:
        style["italic"] = font.italic
    except Exception:
        style["italic"] = None
    try:
        style["underline"] = font.underline
    except Exception:
        style["underline"] = None
    try:
        color = font.color
        if color and color.type is not None:
            style["color"] = _color_hex(color)
        else:
            style["color"] = None
    except Exception:
        style["color"] = None
    return style


def _extract_shape_data(shape) -> dict:
    """Return a structured dict for a single shape."""
    data: dict[str, Any] = {
        "shape_id": shape.shape_id,
        "name": shape.name,
        "type": _shape_type_name(shape),
        "position": {
            "left": shape.left,
            "top": shape.top,
            "width": shape.width,
            "height": shape.height,
        },
    }

    # Text frame
    if shape.has_text_frame:
        paragraphs = []
        for para in shape.text_frame.paragraphs:
            runs = []
            for run in para.runs:
                runs.append(
                    {
                        "text": run.text,
                        "style": _extract_run_style(run),
                    }
                )
            paragraphs.append(
                {
                    "text": para.text,
                    "runs": runs,
                }
            )
        # Summarised style from first non-empty run
        first_run_style: dict = {}
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                if run.text.strip():
                    first_run_style = _extract_run_style(run)
                    break
            if first_run_style:
                break

        data["text"] = shape.text_frame.text
        data["paragraphs"] = paragraphs
        data["style"] = first_run_style
    else:
        data["text"] = None
        data["paragraphs"] = []
        data["style"] = {}

    # Table cells summary
    if shape.has_table:
        table = shape.table
        data["table"] = {
            "rows": table.rows.__len__(),
            "cols": table.columns.__len__(),
            "cells": [
                [cell.text for cell in row.cells] for row in table.rows
            ],
        }

    return data


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


def _build_output_path(file_path: Path, output_path: str | None) -> Path:
    """
    Resolve the output path.  If output_path is None, generate a sibling
    filename like 'deck_edited.pptx' (or 'deck_edited_1.pptx' etc.).
    """
    if output_path:
        return _safe_path(output_path)

    stem = file_path.stem
    parent = file_path.parent
    candidate = parent / f"{stem}_edited.pptx"
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = parent / f"{stem}_edited_{counter}.pptx"
    return candidate


# ---------------------------------------------------------------------------
# Synchronous worker functions (run in executor)
# ---------------------------------------------------------------------------


def _do_read_metadata(file_path: str) -> dict:
    """Read high-level metadata from a PPTX file."""
    from pptx import Presentation
    from pptx.util import Emu

    resolved = _safe_path(file_path)
    if not resolved.exists():
        return {"success": False, "error": f"File not found: {resolved}"}

    prs = Presentation(str(resolved))

    # Slide dimensions (in EMU and inches)
    width_emu = prs.slide_width
    height_emu = prs.slide_height
    width_in = round(width_emu / 914400, 3) if width_emu else None
    height_in = round(height_emu / 914400, 3) if height_emu else None

    # Core properties
    props = prs.core_properties
    author = getattr(props, "author", None) or ""
    title = getattr(props, "title", None) or ""
    subject = getattr(props, "subject", None) or ""
    created = str(getattr(props, "created", "") or "")
    modified = str(getattr(props, "modified", "") or "")

    # Layout names
    layout_names = [layout.name for layout in prs.slide_layouts]

    # Theme colors (if accessible)
    theme_info: dict = {}
    try:
        theme_element = prs.slide_master.theme_color_map
        theme_info["theme_color_map_available"] = theme_element is not None
    except Exception:
        theme_info["theme_color_map_available"] = False

    # Slide relationship IDs for stable references
    slide_ids = []
    try:
        for rel in prs.slides._sldIdLst:
            slide_ids.append(rel.get("r:id", ""))
    except Exception:
        slide_ids = [f"slide{i+1}" for i in range(len(prs.slides))]

    return {
        "success": True,
        "result": {
            "file_path": str(resolved),
            "slide_count": len(prs.slides),
            "dimensions": {
                "width_emu": width_emu,
                "height_emu": height_emu,
                "width_inches": width_in,
                "height_inches": height_in,
            },
            "author": author,
            "title": title,
            "subject": subject,
            "created": created,
            "modified": modified,
            "layout_names": layout_names,
            "slide_ids": slide_ids,
            "theme_info": theme_info,
        },
    }


def _do_extract_styles(file_path: str) -> dict:
    """
    Produce a style guide from a PPTX file.

    Walks every text run across all slides, tallying fonts, colors, and sizes.
    Also records placeholder positions per layout so the LLM knows where to
    place content when adding slides.
    """
    from pptx import Presentation

    resolved = _safe_path(file_path)
    if not resolved.exists():
        return {"success": False, "error": f"File not found: {resolved}"}

    prs = Presentation(str(resolved))

    font_counter: Counter = Counter()
    color_counter: Counter = Counter()
    size_counter: Counter = Counter()

    # Gather from all slides
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if not run.text.strip():
                        continue
                    font = run.font
                    try:
                        if font.name:
                            font_counter[font.name] += 1
                    except Exception:
                        pass
                    try:
                        if font.size is not None:
                            size_pt = round(font.size / 12700, 1)
                            size_counter[size_pt] += 1
                    except Exception:
                        pass
                    try:
                        color = font.color
                        if color and color.type is not None:
                            hex_color = _color_hex(color)
                            if hex_color:
                                color_counter[hex_color] += 1
                    except Exception:
                        pass

    # Placeholder positions per layout
    layouts: list[dict] = []
    for layout in prs.slide_layouts:
        placeholders = []
        for ph in layout.placeholders:
            placeholders.append(
                {
                    "idx": ph.placeholder_format.idx,
                    "name": ph.name,
                    "type": str(ph.placeholder_format.type),
                    "position": {
                        "left": ph.left,
                        "top": ph.top,
                        "width": ph.width,
                        "height": ph.height,
                    },
                }
            )
        layouts.append(
            {
                "layout_name": layout.name,
                "placeholders": placeholders,
            }
        )

    # Master slide style defaults
    master_defaults: dict = {}
    try:
        master = prs.slide_master
        if master.shapes:
            for shape in master.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        for run in para.runs:
                            if run.text.strip():
                                master_defaults = _extract_run_style(run)
                                break
                        if master_defaults:
                            break
                if master_defaults:
                    break
    except Exception:
        pass

    return {
        "success": True,
        "result": {
            "fonts": [
                {"name": name, "frequency": count}
                for name, count in font_counter.most_common()
            ],
            "colors": [
                {"hex": color, "frequency": count}
                for color, count in color_counter.most_common()
            ],
            "sizes_pt": [
                {"size": size, "frequency": count}
                for size, count in size_counter.most_common()
            ],
            "layouts": layouts,
            "master_defaults": master_defaults,
            "style_notes": (
                "Use the fonts, colors, and sizes above to match existing "
                "presentation formatting when adding or editing slides. "
                "The most frequent entries represent the dominant style."
            ),
        },
    }


def _do_extract_content(file_path: str, slide_indices: list[int] | None) -> dict:
    """
    Extract per-slide shape content from a PPTX file.

    If slide_indices is provided, only those 0-based indices are extracted.
    """
    from pptx import Presentation

    resolved = _safe_path(file_path)
    if not resolved.exists():
        return {"success": False, "error": f"File not found: {resolved}"}

    prs = Presentation(str(resolved))
    total_slides = len(prs.slides)

    # Validate requested indices
    if slide_indices is not None:
        bad = [i for i in slide_indices if not (0 <= i < total_slides)]
        if bad:
            return {
                "success": False,
                "error": (
                    f"slide_indices out of range: {bad}. "
                    f"Presentation has {total_slides} slides (0-based)."
                ),
            }
        target_indices = slide_indices
    else:
        target_indices = list(range(total_slides))

    # Resolve slide relationship IDs for stable references
    try:
        rids = [rel.get("r:id", f"slide{i+1}") for i, rel in enumerate(prs.slides._sldIdLst)]
    except Exception:
        rids = [f"slide{i+1}" for i in range(total_slides)]

    slides_out: list[dict] = []
    for idx in target_indices:
        slide = prs.slides[idx]
        layout_name = slide.slide_layout.name if slide.slide_layout else ""

        shapes_out = [_extract_shape_data(shape) for shape in slide.shapes]

        slides_out.append(
            {
                "slide_index": idx,
                "slide_id": rids[idx] if idx < len(rids) else f"slide{idx+1}",
                "layout_name": layout_name,
                "shapes": shapes_out,
            }
        )

    return {"success": True, "result": slides_out}


def _apply_edits_to_slide(slide, edits: list[dict]) -> list[dict]:
    """
    Apply a list of edit dicts to a single python-pptx Slide object in place.

    Each edit is one of:
      {"shape_name": "Title 1", "new_text": "Updated Title"}
      {"shape_id": 1,           "new_text": "Updated"}

    Run-level formatting is preserved: the new text is distributed across the
    existing runs, keeping each run's font/color/size untouched. If there is
    only one run (or no runs at all), the text is set on that run or directly
    on the text frame.

    Returns a list of change records: [{"shape": name, "old_text": ..., "new_text": ...}].
    """
    changes: list[dict] = []

    for edit in edits:
        new_text: str = edit.get("new_text", "")
        shape_name: str | None = edit.get("shape_name")
        shape_id: int | None = edit.get("shape_id")

        # Find target shape
        target = None
        for shape in slide.shapes:
            if shape_name and shape.name == shape_name:
                target = shape
                break
            if shape_id is not None and shape.shape_id == shape_id:
                target = shape
                break

        if target is None:
            identifier = shape_name or f"id={shape_id}"
            logger.warning("Shape not found on slide: %s", identifier)
            changes.append(
                {
                    "shape": identifier,
                    "error": "shape not found",
                }
            )
            continue

        if not target.has_text_frame:
            identifier = shape_name or f"id={shape_id}"
            changes.append(
                {
                    "shape": identifier,
                    "error": "shape has no text frame",
                }
            )
            continue

        tf = target.text_frame
        old_text = tf.text

        # Collect all runs across all paragraphs
        all_runs = [
            run
            for para in tf.paragraphs
            for run in para.runs
        ]

        if not all_runs:
            # No runs — write directly to first paragraph's first run-less text
            # via the paragraph's add_run() approach. We clear existing and add one.
            if tf.paragraphs:
                para = tf.paragraphs[0]
                # Clear existing paragraph XML children that are <a:r> runs
                from pptx.oxml.ns import qn

                for r_elem in para._p.findall(qn("a:r")):
                    para._p.remove(r_elem)
                run = para.add_run()
                run.text = new_text
        elif len(all_runs) == 1:
            # Single run — set text directly; formatting untouched
            all_runs[0].text = new_text
        else:
            # Multiple runs: put entire text into the first run, clear the rest.
            # This is the safest strategy — it keeps the dominant formatting
            # (from run 0) while avoiding empty runs that can cause rendering issues.
            all_runs[0].text = new_text
            for run in all_runs[1:]:
                run.text = ""

        changes.append(
            {
                "shape": target.name,
                "old_text": old_text,
                "new_text": new_text,
            }
        )

    return changes


def _do_edit_slide(
    file_path: str,
    slide_index: int,
    edits: list[dict],
    output_path: str | None,
) -> dict:
    """Edit shapes on a single slide and write atomically."""
    from pptx import Presentation

    from backend.core.atomic_io import AtomicFileWriter

    resolved = _safe_path(file_path)
    if not resolved.exists():
        return {"success": False, "error": f"File not found: {resolved}"}

    prs = Presentation(str(resolved))
    total = len(prs.slides)

    if not (0 <= slide_index < total):
        return {
            "success": False,
            "error": f"slide_index {slide_index} out of range (0–{total - 1})",
        }

    slide = prs.slides[slide_index]
    changes = _apply_edits_to_slide(slide, edits)

    out_path = _build_output_path(resolved, output_path)

    writer = AtomicFileWriter()
    result = writer.write_atomic(out_path, lambda tmp: prs.save(str(tmp)))

    return {
        "success": True,
        "result": {
            "output_path": str(result.path),
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "slide_index": slide_index,
            "changes": changes,
        },
    }


def _do_add_slide(
    file_path: str,
    layout_name: str,
    content: dict,
    position: int | None,
    output_path: str | None,
) -> dict:
    """
    Add a new slide using an existing layout and populate its placeholders.

    *content* maps placeholder name (or 'idx:N' for index-based lookup) to text.
    *position* is a 0-based insertion index; None means append at end.
    """
    from pptx import Presentation

    from backend.core.atomic_io import AtomicFileWriter

    resolved = _safe_path(file_path)
    if not resolved.exists():
        return {"success": False, "error": f"File not found: {resolved}"}

    prs = Presentation(str(resolved))

    # Find the requested layout
    matched_layout = None
    for layout in prs.slide_layouts:
        if layout.name == layout_name:
            matched_layout = layout
            break

    if matched_layout is None:
        available = [l.name for l in prs.slide_layouts]
        return {
            "success": False,
            "error": (
                f"Layout '{layout_name}' not found. "
                f"Available layouts: {available}"
            ),
        }

    # Add the slide (python-pptx appends by default)
    new_slide = prs.slides.add_slide(matched_layout)

    # Populate placeholders from content dict
    applied: dict[str, str] = {}
    skipped: list[str] = []
    for ph in new_slide.placeholders:
        ph_name = ph.name
        ph_idx = ph.placeholder_format.idx

        # Try name match first, then 'idx:N' fallback key
        text = content.get(ph_name) or content.get(f"idx:{ph_idx}")
        if text is not None:
            if ph.has_text_frame:
                if ph.text_frame.paragraphs:
                    runs = ph.text_frame.paragraphs[0].runs
                    if runs:
                        runs[0].text = str(text)
                        for run in runs[1:]:
                            run.text = ""
                    else:
                        ph.text_frame.paragraphs[0].add_run().text = str(text)
                applied[ph_name] = str(text)
        else:
            skipped.append(ph_name)

    # Reposition if needed (python-pptx appends; move by XML manipulation)
    new_slide_index = len(prs.slides) - 1
    if position is not None:
        clamped_pos = max(0, min(position, len(prs.slides) - 1))
        if clamped_pos != new_slide_index:
            # Move slide XML element to the target position in the slide list
            slides_xml = prs.slides._sldIdLst
            slide_elems = list(slides_xml)
            moved = slide_elems.pop(new_slide_index)
            slides_xml.remove(moved)
            if clamped_pos >= len(list(slides_xml)):
                slides_xml.append(moved)
            else:
                ref_elem = list(slides_xml)[clamped_pos]
                slides_xml.insert(ref_elem, moved)
            new_slide_index = clamped_pos

    out_path = _build_output_path(resolved, output_path)
    writer = AtomicFileWriter()
    result = writer.write_atomic(out_path, lambda tmp: prs.save(str(tmp)))

    return {
        "success": True,
        "result": {
            "output_path": str(result.path),
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "slide_index": new_slide_index,
            "layout_name": layout_name,
            "placeholders_applied": applied,
            "placeholders_skipped": skipped,
        },
    }


def _do_apply_edits(
    file_path: str,
    edit_plan: list[dict],
    output_path: str | None,
) -> dict:
    """
    Apply edits to multiple slides in a single pass (one file write).

    *edit_plan* is a list of:
        {"slide_index": N, "edits": [{"shape_name": ..., "new_text": ...}, ...]}
    """
    from pptx import Presentation

    from backend.core.atomic_io import AtomicFileWriter

    resolved = _safe_path(file_path)
    if not resolved.exists():
        return {"success": False, "error": f"File not found: {resolved}"}

    prs = Presentation(str(resolved))
    total = len(prs.slides)

    all_changes: dict[int, list[dict]] = {}
    errors: list[str] = []

    for entry in edit_plan:
        slide_index = entry.get("slide_index")
        edits = entry.get("edits", [])

        if slide_index is None:
            errors.append("Entry missing 'slide_index'")
            continue

        if not isinstance(slide_index, int) or not (0 <= slide_index < total):
            errors.append(
                f"slide_index {slide_index} out of range (0–{total - 1})"
            )
            continue

        slide = prs.slides[slide_index]
        changes = _apply_edits_to_slide(slide, edits)
        all_changes[slide_index] = changes

    out_path = _build_output_path(resolved, output_path)
    writer = AtomicFileWriter()
    result = writer.write_atomic(out_path, lambda tmp: prs.save(str(tmp)))

    return {
        "success": True,
        "result": {
            "output_path": str(result.path),
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "slides_edited": sorted(all_changes.keys()),
            "changes_by_slide": {
                str(k): v for k, v in all_changes.items()
            },
            "errors": errors,
        },
    }


def _do_create(
    file_path: str,
    title: str,
    slides: list[dict],
) -> dict:
    """
    Create a new .pptx presentation from scratch.

    *slides* is a list of dicts, each with:
      layout  — layout name (e.g. "Title Slide", "Title and Content", "Blank")
      title   — slide title text (optional)
      body    — body / content text (optional, supports newline-separated bullets)
      notes   — speaker notes text (optional)

    The file is written atomically to *file_path*.
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt

    from backend.core.atomic_io import AtomicFileWriter

    resolved = _safe_path(file_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()  # blank default template

    # Map of available layout names for user feedback
    layout_map: dict[str, Any] = {}
    for layout in prs.slide_layouts:
        layout_map[layout.name] = layout

    available_names = list(layout_map.keys())
    slides_added: list[dict] = []

    for i, slide_def in enumerate(slides):
        layout_name = slide_def.get("layout", "Blank")
        slide_title = slide_def.get("title", "")
        slide_body = slide_def.get("body", "")
        slide_notes = slide_def.get("notes", "")

        # Find layout — fall back to first available if not found
        layout = layout_map.get(layout_name)
        if layout is None:
            # Try case-insensitive match
            for name, lay in layout_map.items():
                if name.lower() == layout_name.lower():
                    layout = lay
                    layout_name = name
                    break
            if layout is None:
                layout = prs.slide_layouts[0]
                layout_name = layout.name

        slide = prs.slides.add_slide(layout)

        # Populate placeholders by index convention:
        #   idx 0 = title, idx 1 = body/content
        applied = {}
        for ph in slide.placeholders:
            idx = ph.placeholder_format.idx
            if idx == 0 and slide_title and ph.has_text_frame:
                ph.text_frame.paragraphs[0].text = slide_title
                applied["title"] = slide_title
            elif idx == 1 and slide_body and ph.has_text_frame:
                # Support bullet points via newlines
                lines = slide_body.split("\n")
                ph.text_frame.paragraphs[0].text = lines[0]
                for line in lines[1:]:
                    p = ph.text_frame.add_paragraph()
                    p.text = line
                applied["body"] = slide_body

        # Speaker notes
        if slide_notes:
            notes_slide = slide.notes_slide
            notes_slide.notes_text_frame.text = slide_notes
            applied["notes"] = slide_notes

        slides_added.append({
            "index": i,
            "layout": layout_name,
            "applied": applied,
        })

    # Set presentation title metadata
    if title:
        prs.core_properties.title = title

    writer = AtomicFileWriter()
    result = writer.write_atomic(resolved, lambda tmp: prs.save(str(tmp)))

    return {
        "success": True,
        "result": {
            "output_path": str(result.path),
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "slide_count": len(slides_added),
            "slides": slides_added,
            "available_layouts": available_names,
        },
    }


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class PptxTool(BaseTool):
    """Read, analyze, and edit Microsoft PowerPoint (.pptx) files."""

    @property
    def name(self) -> str:
        return "pptx"

    @property
    def description(self) -> str:
        return (
            "Work with PowerPoint (.pptx) files: create new presentations from "
            "scratch, read metadata and structure, extract style guides "
            "(fonts/colors/sizes) for format matching, extract per-slide content, "
            "edit slide text while preserving run-level formatting, add new slides "
            "from existing layouts, and apply bulk multi-slide edits in a single "
            "atomic write."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create",
                        "read_metadata",
                        "extract_styles",
                        "extract_content",
                        "edit_slide",
                        "add_slide",
                        "apply_edits",
                    ],
                    "description": "The PowerPoint action to perform.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Path to the .pptx file (relative to workspace root or absolute).",
                },
                "slide_index": {
                    "type": "integer",
                    "description": "0-based index of the slide to edit (for edit_slide).",
                },
                "slide_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "0-based list of slide indices to extract (for extract_content). "
                        "Omit to extract all slides."
                    ),
                },
                "edits": {
                    "type": "array",
                    "description": (
                        "List of edit dicts for edit_slide. Each is "
                        '{"shape_name": "Title 1", "new_text": "..."} or '
                        '{"shape_id": 1, "new_text": "..."}.'
                    ),
                },
                "edit_plan": {
                    "type": "array",
                    "description": (
                        "List of per-slide edit groups for apply_edits. Each is "
                        '{"slide_index": N, "edits": [...]}.'
                    ),
                },
                "layout_name": {
                    "type": "string",
                    "description": "Exact layout name for add_slide (must match an existing layout).",
                },
                "content": {
                    "type": "object",
                    "description": (
                        "Placeholder name → text mapping for add_slide. "
                        "Use 'idx:N' keys for index-based placeholder lookup."
                    ),
                },
                "position": {
                    "type": "integer",
                    "description": "Insertion index for add_slide (0-based, default: end).",
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "Destination path for the modified file. "
                        "If omitted, a sibling file is auto-named (e.g. 'deck_edited.pptx')."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Presentation title metadata (for create action).",
                },
                "slides": {
                    "type": "array",
                    "description": (
                        "List of slide defs for create action. Each is "
                        '{"layout": "Title Slide", "title": "...", "body": "...", "notes": "..."}. '
                        "Common layouts: 'Title Slide', 'Title and Content', 'Section Header', 'Blank'. "
                        "Body text supports newline-separated bullet points."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "layout": {"type": "string"},
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["action", "file_path"],
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        action: str = kwargs.get("action", "")
        file_path: str = kwargs.get("file_path", "")

        if not file_path:
            return {"success": False, "error": "file_path is required"}

        try:
            _require_pptx()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        loop = asyncio.get_event_loop()

        dispatch = {
            "create": self._create,
            "read_metadata": self._read_metadata,
            "extract_styles": self._extract_styles,
            "extract_content": self._extract_content,
            "edit_slide": self._edit_slide,
            "add_slide": self._add_slide,
            "apply_edits": self._apply_edits,
        }

        handler = dispatch.get(action)
        if not handler:
            return {"success": False, "error": f"Unknown action: {action}"}

        try:
            return await loop.run_in_executor(None, lambda: handler(kwargs))
        except Exception as exc:
            logger.exception("pptx %s failed for %s", action, file_path)
            return {"success": False, "error": str(exc)}

    # ── Action handlers ───────────────────────────────────────────────────────

    def _create(self, kwargs: dict) -> dict:
        title = kwargs.get("title", "")
        slides = kwargs.get("slides")
        if not slides:
            return {
                "success": False,
                "error": (
                    "slides list is required for create. Provide a list of "
                    '{"layout": "Title Slide", "title": "...", "body": "...", "notes": "..."}.'
                ),
            }
        return _do_create(kwargs["file_path"], title, slides)

    def _read_metadata(self, kwargs: dict) -> dict:
        return _do_read_metadata(kwargs["file_path"])

    def _extract_styles(self, kwargs: dict) -> dict:
        return _do_extract_styles(kwargs["file_path"])

    def _extract_content(self, kwargs: dict) -> dict:
        slide_indices: list[int] | None = kwargs.get("slide_indices")
        return _do_extract_content(kwargs["file_path"], slide_indices)

    def _edit_slide(self, kwargs: dict) -> dict:
        slide_index = kwargs.get("slide_index")
        if slide_index is None:
            return {"success": False, "error": "slide_index is required for edit_slide"}
        edits = kwargs.get("edits")
        if not edits:
            return {"success": False, "error": "edits list is required and must be non-empty"}
        output_path: str | None = kwargs.get("output_path")
        return _do_edit_slide(kwargs["file_path"], int(slide_index), edits, output_path)

    def _add_slide(self, kwargs: dict) -> dict:
        layout_name = kwargs.get("layout_name")
        if not layout_name:
            return {"success": False, "error": "layout_name is required for add_slide"}
        content = kwargs.get("content") or {}
        position: int | None = kwargs.get("position")
        output_path: str | None = kwargs.get("output_path")
        return _do_add_slide(
            kwargs["file_path"], layout_name, content, position, output_path
        )

    def _apply_edits(self, kwargs: dict) -> dict:
        edit_plan = kwargs.get("edit_plan")
        if not edit_plan:
            return {
                "success": False,
                "error": "edit_plan list is required and must be non-empty",
            }
        output_path: str | None = kwargs.get("output_path")
        return _do_apply_edits(kwargs["file_path"], edit_plan, output_path)
