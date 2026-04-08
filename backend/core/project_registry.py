"""
Cross-Project Hub — Project Registry
=====================================
CRUD + directory-scanning logic for the project_registry and
cross_project_patterns tables (Sprint 5).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path

from backend.config import DB_PATH

logger = logging.getLogger("localmind.project_registry")

# ---------------------------------------------------------------------------
# Extension -> language mapping
# ---------------------------------------------------------------------------
_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".md": "markdown",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "shell",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
}

# Directories to skip while scanning
_SKIP_DIRS: set[str] = {
    ".git",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    "env",
    ".env",
    ".idea",
    ".vscode",
}


# ---------------------------------------------------------------------------
# ProjectRegistry
# ---------------------------------------------------------------------------
class ProjectRegistry:
    """Manages project registration, listing, and filesystem scanning."""

    def __init__(self) -> None:
        self.db_path: str = str(DB_PATH)

    # -- connection helper ---------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        """Create a short-lived SQLite connection (WAL, FK, Row factory)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    # -- CRUD ----------------------------------------------------------------
    def register(self, name: str, path: str, description: str = "") -> dict:
        """Register a new project directory.

        Raises ``ValueError`` if *path* is already registered.
        """
        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT id FROM project_registry WHERE path = ?", (path,)
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Path already registered: {path}")

            project_id = str(uuid.uuid4())
            now = time.time()
            conn.execute(
                """INSERT INTO project_registry
                   (id, name, path, description, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (project_id, name, path, description, now),
            )
            conn.commit()

            row = conn.execute(
                "SELECT * FROM project_registry WHERE id = ?", (project_id,)
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def unregister(self, project_id: str) -> bool:
        """Remove a project from the registry. Returns True if a row was deleted."""
        conn = self._conn()
        try:
            cursor = conn.execute(
                "DELETE FROM project_registry WHERE id = ?", (project_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def list_projects(self, active_only: bool = True) -> list[dict]:
        """Return all registered projects, optionally filtered to active ones."""
        conn = self._conn()
        try:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM project_registry WHERE active = 1 ORDER BY name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM project_registry ORDER BY name"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_project(self, project_id: str) -> dict | None:
        """Fetch a single project by id, or None if not found."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM project_registry WHERE id = ?", (project_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # -- scanning ------------------------------------------------------------
    def scan_project(self, project_id: str) -> dict:
        """Walk the project directory, count files/lines, build language breakdown.

        Updates the DB row and returns the refreshed project dict.
        Raises ``ValueError`` if project not found or path does not exist.
        """
        project = self.get_project(project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        root = Path(project["path"])
        if not root.is_dir():
            raise ValueError(f"Project path is not a directory: {root}")

        file_count = 0
        total_lines = 0
        lang_lines: dict[str, int] = {}

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune skipped directories in-place
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

            for fname in filenames:
                filepath = os.path.join(dirpath, fname)
                ext = os.path.splitext(fname)[1].lower()
                lang = _EXT_LANG.get(ext)

                try:
                    with open(filepath, encoding="utf-8", errors="ignore") as f:
                        lines = sum(1 for _ in f)
                except (OSError, PermissionError):
                    continue

                file_count += 1
                total_lines += lines
                if lang:
                    lang_lines[lang] = lang_lines.get(lang, 0) + lines

        # Convert to percentages
        language_breakdown: dict[str, float] = {}
        if total_lines > 0:
            for lang, count in sorted(
                lang_lines.items(), key=lambda x: x[1], reverse=True
            ):
                language_breakdown[lang] = round(count / total_lines * 100, 1)

        now = time.time()
        conn = self._conn()
        try:
            conn.execute(
                """UPDATE project_registry
                   SET file_count = ?,
                       total_lines = ?,
                       language_breakdown = ?,
                       last_scanned_at = ?
                   WHERE id = ?""",
                (
                    file_count,
                    total_lines,
                    json.dumps(language_breakdown),
                    now,
                    project_id,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM project_registry WHERE id = ?", (project_id,)
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    # -- metadata helpers ----------------------------------------------------
    def update_metadata(self, project_id: str, metadata: dict) -> bool:
        """Merge *metadata* into the project's existing metadata JSON.

        Returns True if the project was found and updated.
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT metadata FROM project_registry WHERE id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                return False

            existing = json.loads(row["metadata"] or "{}")
            existing.update(metadata)

            conn.execute(
                "UPDATE project_registry SET metadata = ? WHERE id = ?",
                (json.dumps(existing), project_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_registry: ProjectRegistry | None = None


def get_registry() -> ProjectRegistry:
    """Return the module-level ProjectRegistry singleton (created on first call)."""
    global _registry
    if _registry is None:
        _registry = ProjectRegistry()
    return _registry
