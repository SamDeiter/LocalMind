"""
Artifact Management — versioning, previews, and diffs for LocalMind task outputs.

Provides three main components:
  - ArtifactManager: CRUD for artifacts and their versions with SHA-256 tracking
  - PreviewGenerator: text extraction from PPTX, XLSX, DOCX, PDF, JSON, TXT
  - DiffEngine: structured diffs between artifact versions (text and PPTX structural)

All data is persisted in SQLite tables: artifacts, artifact_versions,
artifact_previews, artifact_diffs (defined in backend.core.schema).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import mimetypes
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import DB_PATH, JOBS_DIR, WORKSPACE_ROOT

logger = logging.getLogger("localmind.core.artifacts")


# ── Database helpers ────────────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    """Open a WAL-mode connection with row_factory and foreign keys."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convert a sqlite3.Row to a plain dict, or return None."""
    if row is None:
        return None
    return dict(row)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generate a new hex UUID for use as a primary key."""
    return uuid.uuid4().hex


# ── SHA-256 helper ──────────────────────────────────────────────────────────────


def _compute_sha256(file_path: str | Path) -> str:
    """Compute the hex-encoded SHA-256 digest of a file.

    Args:
        file_path: Path to the file on disk.

    Returns:
        Lowercase hex string of the SHA-256 hash.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
#  ArtifactManager
# ═══════════════════════════════════════════════════════════════════════════════


class ArtifactManager:
    """Manage artifacts and their versioned files.

    Each artifact belongs to a job and workspace.  Versions track the file
    path, size, SHA-256 hash, MIME type, and optional parent lineage.
    The artifact's ``current_version_id`` always points to the latest version.
    """

    # ── Create ──────────────────────────────────────────────────────────────

    def create_artifact(
        self,
        job_id: str,
        workspace_id: str,
        name: str,
        artifact_type: str,
    ) -> str:
        """Create a new artifact record.

        Args:
            job_id: The owning job's ID.
            workspace_id: The workspace this artifact belongs to.
            name: Human-readable artifact name (e.g. "Q4 Report").
            artifact_type: File category such as ``pptx``, ``xlsx``, ``docx``,
                ``pdf``, ``json``, ``txt``, etc.

        Returns:
            The newly created artifact ID.
        """
        artifact_id = _new_id()
        now = _now_iso()
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO artifacts (id, job_id, workspace_id, name, artifact_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, job_id, workspace_id, name, artifact_type, now),
            )
            conn.commit()
            logger.info(
                "Created artifact %s (%s) for job %s",
                artifact_id[:12],
                artifact_type,
                job_id[:12],
            )
            return artifact_id
        finally:
            conn.close()

    # ── Add version ─────────────────────────────────────────────────────────

    def add_version(
        self,
        artifact_id: str,
        file_path: str | Path,
        created_by: str,
        node_attempt_id: str | None = None,
        parent_version_id: str | None = None,
        metadata_json: str | None = None,
    ) -> str:
        """Record a new version of an artifact from a file on disk.

        Computes the SHA-256 hash, file size, and MIME type automatically.
        The artifact's ``current_version_id`` is updated to point to this
        new version.

        Args:
            artifact_id: The artifact to add a version to.
            file_path: Absolute or workspace-relative path to the file.
            created_by: User or system identifier that produced this version.
            node_attempt_id: Optional link to the node_attempt that created it.
            parent_version_id: Optional previous version this was derived from.
            metadata_json: Optional JSON string with extra metadata.

        Returns:
            The newly created version ID.

        Raises:
            FileNotFoundError: If file_path does not exist.
            ValueError: If the artifact does not exist.
        """
        path = Path(file_path)
        if not path.is_absolute():
            path = WORKSPACE_ROOT / path
        if not path.exists():
            raise FileNotFoundError(f"Version file not found: {path}")

        sha256 = _compute_sha256(path)
        file_size = path.stat().st_size
        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        version_id = _new_id()
        now = _now_iso()

        conn = _connect()
        try:
            # Determine next version number
            row = conn.execute(
                "SELECT MAX(version_number) AS max_ver FROM artifact_versions WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            next_ver = (row["max_ver"] or 0) + 1

            conn.execute(
                """
                INSERT INTO artifact_versions
                    (id, artifact_id, version_number, file_path, file_size_bytes,
                     sha256, mime_type, created_by, node_attempt_id,
                     parent_version_id, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    artifact_id,
                    next_ver,
                    str(path),
                    file_size,
                    sha256,
                    mime_type,
                    created_by,
                    node_attempt_id,
                    parent_version_id,
                    metadata_json or "{}",
                    now,
                ),
            )

            # Update current_version_id on the artifact
            conn.execute(
                "UPDATE artifacts SET current_version_id = ? WHERE id = ?",
                (version_id, artifact_id),
            )
            conn.commit()

            logger.info(
                "Added version %d (id=%s) to artifact %s — %d bytes, sha256=%s...",
                next_ver,
                version_id[:12],
                artifact_id[:12],
                file_size,
                sha256[:12],
            )
            return version_id
        finally:
            conn.close()

    # ── Read operations ─────────────────────────────────────────────────────

    def get_artifact(self, artifact_id: str) -> dict | None:
        """Fetch a single artifact by ID.

        Args:
            artifact_id: The artifact's primary key.

        Returns:
            A dict of the artifact row, or None if not found.
        """
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    def get_version(self, version_id: str) -> dict | None:
        """Fetch a single artifact version by ID.

        Args:
            version_id: The version's primary key.

        Returns:
            A dict of the version row, or None if not found.
        """
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM artifact_versions WHERE id = ?", (version_id,)
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    def get_versions(self, artifact_id: str) -> list[dict]:
        """Fetch all versions for an artifact, ordered by version_number ascending.

        Args:
            artifact_id: The artifact to list versions for.

        Returns:
            A list of version dicts.
        """
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id = ? ORDER BY version_number",
                (artifact_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_current_version(self, artifact_id: str) -> dict | None:
        """Fetch the current (latest) version of an artifact.

        Args:
            artifact_id: The artifact to look up.

        Returns:
            A dict of the current version row, or None if the artifact has
            no versions or does not exist.
        """
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT v.* FROM artifact_versions v
                JOIN artifacts a ON a.current_version_id = v.id
                WHERE a.id = ?
                """,
                (artifact_id,),
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    def get_artifacts_by_job(self, job_id: str) -> list[dict]:
        """Fetch all artifacts belonging to a job.

        Args:
            job_id: The job to list artifacts for.

        Returns:
            A list of artifact dicts.
        """
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE job_id = ?", (job_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  PPTX Stable Anchors
# ═══════════════════════════════════════════════════════════════════════════════


def compute_shape_fingerprint(
    slide_id: str | int,
    shape_name: str,
    position: dict[str, int | float],
) -> str:
    """Compute a stable fingerprint for a PPTX shape.

    Shapes are referenced by this fingerprint rather than by array index,
    which shifts when slides or shapes are added/removed.

    The fingerprint is a SHA-256 of the concatenation of the slide identifier,
    shape name, and rounded position values (left, top, width, height).

    Args:
        slide_id: The slide's relationship ID or index.
        shape_name: The shape's ``name`` attribute from python-pptx.
        position: Dict with ``left``, ``top``, ``width``, ``height`` keys
            (EMU values).

    Returns:
        A hex SHA-256 fingerprint string.
    """
    left = int(position.get("left", 0))
    top = int(position.get("top", 0))
    width = int(position.get("width", 0))
    height = int(position.get("height", 0))

    payload = f"{slide_id}|{shape_name}|{left}|{top}|{width}|{height}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
#  PreviewGenerator
# ═══════════════════════════════════════════════════════════════════════════════


class PreviewGenerator:
    """Generate text-based previews from artifact versions.

    Supports PPTX (slide text), XLSX (cell values), DOCX (paragraphs),
    PDF (page text), and plain text/JSON (first 500 chars).

    Previews are stored in the ``artifact_previews`` table for later
    retrieval by the diff engine or the UI.
    """

    # Maximum characters for plain-text file previews.
    MAX_TEXT_CHARS: int = 500

    # ── Public API ──────────────────────────────────────────────────────────

    def generate_preview(
        self,
        version_id: str,
        preview_type: str = "text_extract",
    ) -> str:
        """Generate and store a text preview for an artifact version.

        Args:
            version_id: The artifact version to preview.
            preview_type: Label for the kind of preview (default ``text_extract``).

        Returns:
            The preview ID.

        Raises:
            ValueError: If the version does not exist or the file is missing.
        """
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM artifact_versions WHERE id = ?", (version_id,)
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise ValueError(f"Artifact version not found: {version_id}")

        version = dict(row)
        file_path = Path(version["file_path"])
        if not file_path.exists():
            raise ValueError(f"Version file missing on disk: {file_path}")

        mime = version["mime_type"]
        content_text = self._extract_text(file_path, mime)
        metadata: dict[str, Any] = {
            "mime_type": mime,
            "file_size_bytes": version["file_size_bytes"],
            "extraction_method": self._method_for_mime(mime),
        }

        preview_id = _new_id()
        now = _now_iso()

        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO artifact_previews
                    (id, version_id, preview_type, content_text, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    preview_id,
                    version_id,
                    preview_type,
                    content_text,
                    json.dumps(metadata),
                    now,
                ),
            )
            conn.commit()
            logger.info(
                "Generated %s preview %s for version %s (%d chars)",
                preview_type,
                preview_id[:12],
                version_id[:12],
                len(content_text),
            )
            return preview_id
        finally:
            conn.close()

    def get_preview(self, version_id: str) -> dict | None:
        """Retrieve the stored preview for a version.

        Args:
            version_id: The artifact version whose preview to fetch.

        Returns:
            A dict of the preview row, or None if no preview exists.
        """
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM artifact_previews WHERE version_id = ? ORDER BY created_at DESC LIMIT 1",
                (version_id,),
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    # ── Extraction dispatch ─────────────────────────────────────────────────

    def _extract_text(self, file_path: Path, mime_type: str) -> str:
        """Route to the appropriate text extractor based on MIME type.

        Args:
            file_path: Path to the file on disk.
            mime_type: The file's MIME type string.

        Returns:
            Extracted text content.
        """
        suffix = file_path.suffix.lower()

        if suffix == ".pptx" or "presentation" in mime_type:
            return self._extract_pptx(file_path)
        if suffix == ".xlsx" or "spreadsheet" in mime_type:
            return self._extract_xlsx(file_path)
        if suffix == ".docx" or "wordprocessingml" in mime_type:
            return self._extract_docx(file_path)
        if suffix == ".pdf" or mime_type == "application/pdf":
            return self._extract_pdf(file_path)
        if suffix in (".json",) or mime_type == "application/json":
            return self._extract_plain(file_path)
        if suffix in (".txt", ".md", ".csv", ".log", ".yaml", ".yml", ".xml", ".html"):
            return self._extract_plain(file_path)
        if mime_type.startswith("text/"):
            return self._extract_plain(file_path)

        # Fallback: try reading as text, otherwise return a placeholder
        try:
            return self._extract_plain(file_path)
        except Exception:
            return f"[Binary file — no text preview available for {mime_type}]"

    def _method_for_mime(self, mime_type: str) -> str:
        """Return a human-readable extraction method label."""
        if "presentation" in mime_type or mime_type.endswith(".pptx"):
            return "pptx_slide_text"
        if "spreadsheet" in mime_type or mime_type.endswith(".xlsx"):
            return "xlsx_cell_values"
        if "wordprocessingml" in mime_type or mime_type.endswith(".docx"):
            return "docx_paragraphs"
        if mime_type == "application/pdf":
            return "pdf_page_text"
        if mime_type.startswith("text/") or mime_type == "application/json":
            return "plain_text_head"
        return "fallback_text"

    # ── Format-specific extractors ──────────────────────────────────────────

    def _extract_pptx(self, file_path: Path) -> str:
        """Extract slide-by-slide text from a PPTX file.

        Returns:
            A string with each slide's text separated by double newlines,
            prefixed with ``[Slide N]``.
        """
        try:
            from pptx import Presentation
        except ImportError:
            return "[python-pptx not installed — cannot extract PPTX text]"

        prs = Presentation(str(file_path))
        parts: list[str] = []

        for i, slide in enumerate(prs.slides, start=1):
            slide_texts: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    text = shape.text_frame.text.strip()
                    if text:
                        slide_texts.append(text)
                if shape.has_table:
                    for row in shape.table.rows:
                        row_text = " | ".join(cell.text.strip() for cell in row.cells)
                        if row_text.strip(" |"):
                            slide_texts.append(row_text)
            parts.append(f"[Slide {i}]\n" + "\n".join(slide_texts))

        return "\n\n".join(parts)

    def _extract_xlsx(self, file_path: Path) -> str:
        """Extract cell values from all sheets of an XLSX file.

        Returns:
            A string with each sheet's content prefixed by ``[Sheet: name]``,
            rows separated by newlines, cells by `` | ``.
        """
        try:
            from openpyxl import load_workbook
        except ImportError:
            return "[openpyxl not installed — cannot extract XLSX text]"

        wb = load_workbook(str(file_path), read_only=True, data_only=True)
        parts: list[str] = []

        try:
            for ws in wb.worksheets:
                rows_text: list[str] = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) if c is not None else "" for c in row]
                    line = " | ".join(cells)
                    if line.strip(" |"):
                        rows_text.append(line)
                parts.append(f"[Sheet: {ws.title}]\n" + "\n".join(rows_text))
        finally:
            wb.close()

        return "\n\n".join(parts)

    def _extract_docx(self, file_path: Path) -> str:
        """Extract paragraph text from a DOCX file.

        Returns:
            All non-empty paragraphs joined by newlines.
        """
        try:
            from docx import Document
        except ImportError:
            return "[python-docx not installed — cannot extract DOCX text]"

        doc = Document(str(file_path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)

    def _extract_pdf(self, file_path: Path) -> str:
        """Extract page text from a PDF file.

        Returns:
            A string with each page's text prefixed by ``[Page N]``.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            try:
                from PyPDF2 import PdfReader

                reader = PdfReader(str(file_path))
                parts: list[str] = []
                for i, page in enumerate(reader.pages, start=1):
                    text = page.extract_text() or ""
                    parts.append(f"[Page {i}]\n{text.strip()}")
                return "\n\n".join(parts)
            except ImportError:
                return "[No PDF library installed (PyMuPDF or PyPDF2) — cannot extract PDF text]"

        doc = fitz.open(str(file_path))
        parts_mu: list[str] = []
        try:
            for i, page in enumerate(doc, start=1):
                text = page.get_text().strip()
                parts_mu.append(f"[Page {i}]\n{text}")
        finally:
            doc.close()
        return "\n\n".join(parts_mu)

    def _extract_plain(self, file_path: Path) -> str:
        """Read the first N characters of a text file.

        Returns:
            Up to ``MAX_TEXT_CHARS`` characters from the file.
        """
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(self.MAX_TEXT_CHARS)


# ═══════════════════════════════════════════════════════════════════════════════
#  DiffEngine
# ═══════════════════════════════════════════════════════════════════════════════


class DiffEngine:
    """Compute and store diffs between artifact versions.

    Supports:
      - **Text diffs**: line-by-line unified diff of extracted text previews,
        stored as structured JSON.
      - **Structural PPTX diffs**: slide-by-slide comparison showing which
        slides were added, removed, or changed (and which shapes within
        changed slides were modified).
      - **Summaries**: human-readable summary like "Changed 3 slides, updated
        12 text boxes".
    """

    def __init__(self) -> None:
        self._preview_gen = PreviewGenerator()

    # ── Public API ──────────────────────────────────────────────────────────

    def compute_diff(
        self,
        from_version_id: str,
        to_version_id: str,
        diff_type: str = "text",
    ) -> str:
        """Compute and store a diff between two artifact versions.

        Args:
            from_version_id: The "before" version ID.
            to_version_id: The "after" version ID.
            diff_type: Either ``text`` (default) or ``structural`` (PPTX only).

        Returns:
            The diff ID.

        Raises:
            ValueError: If either version does not exist.
        """
        mgr = ArtifactManager()
        from_ver = mgr.get_version(from_version_id)
        to_ver = mgr.get_version(to_version_id)

        if from_ver is None:
            raise ValueError(f"From-version not found: {from_version_id}")
        if to_ver is None:
            raise ValueError(f"To-version not found: {to_version_id}")

        if diff_type == "structural":
            diff_json, summary = self._structural_diff(from_ver, to_ver)
        else:
            diff_json, summary = self._text_diff(from_ver, to_ver)

        diff_id = _new_id()
        now = _now_iso()

        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO artifact_diffs
                    (id, from_version_id, to_version_id, diff_type,
                     diff_json, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    diff_id,
                    from_version_id,
                    to_version_id,
                    diff_type,
                    json.dumps(diff_json),
                    summary,
                    now,
                ),
            )
            conn.commit()
            logger.info(
                "Computed %s diff %s: %s -> %s — %s",
                diff_type,
                diff_id[:12],
                from_version_id[:12],
                to_version_id[:12],
                summary,
            )
            return diff_id
        finally:
            conn.close()

    def get_diff(self, diff_id: str) -> dict | None:
        """Retrieve a stored diff by ID.

        Args:
            diff_id: The diff's primary key.

        Returns:
            A dict of the diff row, or None if not found.
        """
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM artifact_diffs WHERE id = ?", (diff_id,)
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    # ── Text diff ───────────────────────────────────────────────────────────

    def _text_diff(
        self,
        from_ver: dict,
        to_ver: dict,
    ) -> tuple[dict, str]:
        """Compute a line-by-line text diff between two versions.

        Uses extracted text previews. If no preview exists yet, one is
        generated on-the-fly.

        Returns:
            A tuple of (diff_json, summary_string).
        """
        from_text = self._get_or_generate_text(from_ver)
        to_text = self._get_or_generate_text(to_ver)

        from_lines = from_text.splitlines(keepends=True)
        to_lines = to_text.splitlines(keepends=True)

        differ = difflib.unified_diff(
            from_lines,
            to_lines,
            fromfile=f"v{from_ver['version_number']}",
            tofile=f"v{to_ver['version_number']}",
            lineterm="",
        )
        diff_lines = list(differ)

        # Compute stats
        added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
        changed_sections = sum(1 for l in diff_lines if l.startswith("@@"))

        diff_json: dict[str, Any] = {
            "type": "text",
            "from_version": from_ver["version_number"],
            "to_version": to_ver["version_number"],
            "unified_diff": "".join(diff_lines),
            "stats": {
                "lines_added": added,
                "lines_removed": removed,
                "hunks": changed_sections,
            },
        }

        summary = f"Text diff: +{added} -{removed} lines across {changed_sections} sections"
        return diff_json, summary

    def _get_or_generate_text(self, version: dict) -> str:
        """Get existing preview text or generate one on-the-fly.

        Args:
            version: A version dict (from artifact_versions).

        Returns:
            The extracted text content.
        """
        preview = self._preview_gen.get_preview(version["id"])
        if preview is not None and preview.get("content_text"):
            return preview["content_text"]

        # Generate preview on-the-fly
        try:
            self._preview_gen.generate_preview(version["id"])
            preview = self._preview_gen.get_preview(version["id"])
            if preview is not None and preview.get("content_text"):
                return preview["content_text"]
        except (ValueError, FileNotFoundError) as exc:
            logger.warning("Could not generate preview for version %s: %s", version["id"][:12], exc)

        return ""

    # ── Structural PPTX diff ────────────────────────────────────────────────

    def _structural_diff(
        self,
        from_ver: dict,
        to_ver: dict,
    ) -> tuple[dict, str]:
        """Compute a slide-by-slide structural diff for PPTX files.

        Compares slide text and shape fingerprints to determine which slides
        were added, removed, or changed, and within changed slides which
        shapes were modified.

        Falls back to text diff if python-pptx is not available.

        Returns:
            A tuple of (diff_json, summary_string).
        """
        try:
            from pptx import Presentation
        except ImportError:
            logger.warning("python-pptx not installed, falling back to text diff")
            return self._text_diff(from_ver, to_ver)

        from_path = Path(from_ver["file_path"])
        to_path = Path(to_ver["file_path"])

        if not from_path.exists() or not to_path.exists():
            logger.warning("One or both PPTX files missing, falling back to text diff")
            return self._text_diff(from_ver, to_ver)

        from_slides = self._extract_slide_structures(Presentation(str(from_path)))
        to_slides = self._extract_slide_structures(Presentation(str(to_path)))

        # Build comparison
        max_slides = max(len(from_slides), len(to_slides))
        slides_added: list[int] = []
        slides_removed: list[int] = []
        slides_changed: list[dict] = []
        slides_unchanged: list[int] = []

        total_shapes_changed = 0

        for i in range(max_slides):
            if i >= len(from_slides):
                # Slide was added
                slides_added.append(i)
                continue
            if i >= len(to_slides):
                # Slide was removed
                slides_removed.append(i)
                continue

            # Both exist — compare
            from_slide = from_slides[i]
            to_slide = to_slides[i]

            shape_changes = self._compare_slide_shapes(from_slide, to_slide, i)
            if shape_changes:
                slides_changed.append({
                    "slide_index": i,
                    "changes": shape_changes,
                })
                total_shapes_changed += len(shape_changes)
            else:
                slides_unchanged.append(i)

        diff_json: dict[str, Any] = {
            "type": "structural",
            "from_version": from_ver["version_number"],
            "to_version": to_ver["version_number"],
            "from_slide_count": len(from_slides),
            "to_slide_count": len(to_slides),
            "slides_added": slides_added,
            "slides_removed": slides_removed,
            "slides_changed": slides_changed,
            "slides_unchanged": slides_unchanged,
        }

        # Build summary
        parts: list[str] = []
        if slides_changed:
            parts.append(f"Changed {len(slides_changed)} slide{'s' if len(slides_changed) != 1 else ''}")
        if slides_added:
            parts.append(f"added {len(slides_added)} slide{'s' if len(slides_added) != 1 else ''}")
        if slides_removed:
            parts.append(f"removed {len(slides_removed)} slide{'s' if len(slides_removed) != 1 else ''}")
        if total_shapes_changed:
            parts.append(f"updated {total_shapes_changed} text box{'es' if total_shapes_changed != 1 else ''}")
        if not parts:
            parts.append("No structural changes detected")

        summary = ", ".join(parts)
        return diff_json, summary

    def _extract_slide_structures(
        self,
        prs: Any,
    ) -> list[dict]:
        """Extract a simplified structural representation of each slide.

        Args:
            prs: A python-pptx Presentation object.

        Returns:
            A list of dicts, one per slide, each containing a list of shapes
            with their names, fingerprints, and text content.
        """
        slides: list[dict] = []

        for i, slide in enumerate(prs.slides):
            shapes: list[dict] = []
            for shape in slide.shapes:
                shape_info: dict[str, Any] = {
                    "name": shape.name,
                    "shape_id": shape.shape_id,
                    "position": {
                        "left": shape.left,
                        "top": shape.top,
                        "width": shape.width,
                        "height": shape.height,
                    },
                    "text": "",
                }
                shape_info["fingerprint"] = compute_shape_fingerprint(
                    slide_id=i,
                    shape_name=shape.name,
                    position=shape_info["position"],
                )
                if shape.has_text_frame:
                    shape_info["text"] = shape.text_frame.text
                if shape.has_table:
                    cell_texts: list[str] = []
                    for row in shape.table.rows:
                        for cell in row.cells:
                            cell_texts.append(cell.text)
                    shape_info["text"] += " ".join(cell_texts)
                shapes.append(shape_info)
            slides.append({"slide_index": i, "shapes": shapes})

        return slides

    def _compare_slide_shapes(
        self,
        from_slide: dict,
        to_slide: dict,
        slide_index: int,
    ) -> list[dict]:
        """Compare shapes between two versions of the same slide.

        Matches shapes by fingerprint first, then falls back to name matching.

        Args:
            from_slide: The "before" slide structure dict.
            to_slide: The "after" slide structure dict.
            slide_index: The 0-based slide index (for reporting).

        Returns:
            A list of change dicts describing each modification.
        """
        changes: list[dict] = []

        from_by_fp: dict[str, dict] = {
            s["fingerprint"]: s for s in from_slide["shapes"]
        }
        to_by_fp: dict[str, dict] = {
            s["fingerprint"]: s for s in to_slide["shapes"]
        }

        # Shapes present in both (by fingerprint)
        common_fps = set(from_by_fp.keys()) & set(to_by_fp.keys())
        for fp in common_fps:
            from_shape = from_by_fp[fp]
            to_shape = to_by_fp[fp]
            if from_shape["text"] != to_shape["text"]:
                changes.append({
                    "type": "text_changed",
                    "shape_name": to_shape["name"],
                    "fingerprint": fp,
                    "old_text": from_shape["text"],
                    "new_text": to_shape["text"],
                })

        # Shapes removed (in from but not in to)
        removed_fps = set(from_by_fp.keys()) - set(to_by_fp.keys())
        # Try name-based matching for moved shapes before reporting removal
        to_by_name: dict[str, dict] = {s["name"]: s for s in to_slide["shapes"]}
        for fp in removed_fps:
            from_shape = from_by_fp[fp]
            # Check if a shape with the same name exists in the to-slide
            if from_shape["name"] in to_by_name:
                to_shape = to_by_name[from_shape["name"]]
                if to_shape["fingerprint"] not in common_fps:
                    # Shape moved position
                    if from_shape["text"] != to_shape["text"]:
                        changes.append({
                            "type": "text_changed_and_moved",
                            "shape_name": from_shape["name"],
                            "old_fingerprint": fp,
                            "new_fingerprint": to_shape["fingerprint"],
                            "old_text": from_shape["text"],
                            "new_text": to_shape["text"],
                        })
                    else:
                        changes.append({
                            "type": "moved",
                            "shape_name": from_shape["name"],
                            "old_fingerprint": fp,
                            "new_fingerprint": to_shape["fingerprint"],
                        })
            else:
                changes.append({
                    "type": "removed",
                    "shape_name": from_shape["name"],
                    "fingerprint": fp,
                })

        # Shapes added (in to but not in from, and not matched by name above)
        added_fps = set(to_by_fp.keys()) - set(from_by_fp.keys())
        from_by_name: dict[str, dict] = {s["name"]: s for s in from_slide["shapes"]}
        for fp in added_fps:
            to_shape = to_by_fp[fp]
            if to_shape["name"] not in from_by_name:
                changes.append({
                    "type": "added",
                    "shape_name": to_shape["name"],
                    "fingerprint": fp,
                    "text": to_shape["text"],
                })

        return changes
