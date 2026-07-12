"""
Recycle Bin — backend/security/recycle_bin.py

Every delete operation in LocalMind goes through safe_delete(). Files are
never permanently destroyed by LLM or tool actions. Only an explicit admin
purge or time-based expiry actually removes data from disk.

Storage layout:
    WORKSPACE_ROOT/.recycle/{entry_id}/{original_filename}
    WORKSPACE_ROOT/.recycle/{entry_id}/.meta.json

Database:
    recycle_bin table (created by backend/core/schema.py)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from backend.config import DB_PATH, WORKSPACE_ROOT

# Import safe_resolve from paths module; fall back to inline implementation
# if that module is not yet present (graceful bootstrap).
try:
    from backend.security.paths import safe_resolve  # type: ignore[import]
except ImportError:  # pragma: no cover — paths.py not written yet

    class SecurityError(Exception):
        """Raised when a path escapes its jail."""

    def safe_resolve(base_dir: Path | str, user_path: str | Path) -> Path:  # type: ignore[misc]
        """Resolve *user_path* relative to *base_dir* and verify it stays inside.

        Raises SecurityError if the resolved path escapes the base directory.
        This mirrors the contract that backend/security/paths.py will provide.
        """
        base = Path(base_dir).resolve()
        # Normalize backslashes for POSIX compatibility
        user_path_str = str(user_path).replace("\\", "/")
        # Strip leading separators to ensure it's treated as relative
        user_path_stripped = user_path_str.lstrip("/")
        candidate = (base / user_path_stripped).resolve()

        if not candidate.is_relative_to(base):
            raise SecurityError(
                f"Path {user_path!r} escapes jail {base!r}"
            )
        # Reject symlinks that point outside the jail
        if candidate.is_symlink():
            real = Path(os.path.realpath(candidate))
            try:
                real.relative_to(base)
            except ValueError:
                raise SecurityError(
                    f"Symlink {candidate!r} resolves outside jail {base!r}"
                )
        return candidate

else:
    # paths.py exists — grab SecurityError from there if it exports it.
    try:
        from backend.security.paths import SecurityError  # type: ignore[import,no-redef]
    except ImportError:

        class SecurityError(Exception):  # type: ignore[no-redef]
            """Raised when a path escapes its jail."""


logger = logging.getLogger("localmind.security.recycle_bin")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def compute_sha256(path: Path) -> str:
    """Return the hex-encoded SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RecycleEntry:
    """Represents one item in the recycle bin."""

    id: str
    original_path: str
    recycle_path: str
    deleted_by: str
    deleted_at: str
    expires_at: str
    job_id: Optional[str]
    node_id: Optional[str]
    size_bytes: int
    sha256: str
    reason: str = "deleted"
    restored_at: Optional[str] = None
    restored_by: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "RecycleEntry":
        return cls(
            id=row["id"],
            original_path=row["original_path"],
            recycle_path=row["recycle_path"],
            deleted_by=row["deleted_by"],
            deleted_at=row["deleted_at"],
            expires_at=row["expires_at"],
            job_id=row["job_id"],
            node_id=row["node_id"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            reason=row["reason"] or "deleted",
            restored_at=row["restored_at"],
            restored_by=row["restored_by"],
        )

    def to_meta_dict(self) -> dict:
        """Serialize to the .meta.json sidecar format."""
        return {
            "id": self.id,
            "original_path": self.original_path,
            "recycle_path": self.recycle_path,
            "deleted_by": self.deleted_by,
            "deleted_at": self.deleted_at,
            "expires_at": self.expires_at,
            "job_id": self.job_id,
            "node_id": self.node_id,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# RecycleBin
# ---------------------------------------------------------------------------

class RecycleBin:
    """Manages the LocalMind recycle bin.

    All file deletions go through safe_delete().  Restores, listing,
    and purges are managed here.  Nothing is ever permanently destroyed
    by LLM or tool actions — only admin purge or time-based expiry
    removes data from disk.
    """

    RETENTION_DAYS: int = int(os.getenv("RECYCLE_RETENTION_DAYS", "30"))
    MAX_SIZE_GB: float = float(os.getenv("RECYCLE_MAX_SIZE_GB", "10"))

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        self.workspace_root: Path = workspace_root or WORKSPACE_ROOT
        self.recycle_dir: Path = self.workspace_root / ".recycle"
        self.recycle_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _expires_iso(self) -> str:
        return (
            datetime.now(timezone.utc) + timedelta(days=self.RETENTION_DAYS)
        ).isoformat()

    def _purge_entry_files(self, entry_id: str) -> None:
        """Delete the recycle slot directory for *entry_id* from disk."""
        slot = self.recycle_dir / entry_id
        if slot.exists():
            shutil.rmtree(str(slot))
            logger.debug("Purged recycle slot %s from disk", entry_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def safe_delete(
        self,
        path: Path | str,
        deleted_by: str,
        job_id: Optional[str] = None,
        node_id: Optional[str] = None,
        reason: str = "deleted",
    ) -> RecycleEntry:
        """Move *path* to the recycle bin instead of permanently deleting it.

        Parameters
        ----------
        path:
            Absolute or workspace-relative path to the file to recycle.
        deleted_by:
            Identity string, e.g. ``"user:sam"``, ``"node:abc123:3"``,
            ``"system:auto_purge"``.
        job_id:
            Optional job context for audit.
        node_id:
            Optional node context for audit.
        reason:
            Short description of why this file was deleted.

        Returns
        -------
        RecycleEntry
            The created recycle bin record.

        Raises
        ------
        SecurityError
            If *path* resolves outside the workspace.
        FileNotFoundError
            If *path* does not exist.
        """
        path = Path(path)

        # Validate the path via path-jailing (relative paths resolved inside workspace)
        resolved = safe_resolve(self.workspace_root, path) if not path.is_absolute() else path.resolve()

        # For absolute paths, still verify they sit inside the workspace
        if path.is_absolute():
            try:
                resolved.relative_to(self.workspace_root.resolve())
            except ValueError:
                raise SecurityError(
                    f"Path {path!r} is outside workspace {self.workspace_root!r}"
                )

        if not resolved.exists():
            raise FileNotFoundError(f"Cannot recycle non-existent path: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(
                f"safe_delete targets files only; got directory: {resolved}"
            )

        entry_id = str(uuid.uuid4())
        original_filename = resolved.name
        slot_dir = self.recycle_dir / entry_id
        slot_dir.mkdir(parents=True, exist_ok=True)

        recycle_path = slot_dir / original_filename

        # Compute hash before moving (file must still be readable)
        digest = compute_sha256(resolved)
        size_bytes = resolved.stat().st_size

        # Move the file — use shutil.move for cross-device safety on Windows
        shutil.move(str(resolved), str(recycle_path))
        logger.info(
            "Recycled %s → %s (deleted_by=%s, job_id=%s)",
            resolved, recycle_path, deleted_by, job_id,
        )

        now_iso = self._now_iso()
        expires_iso = self._expires_iso()

        entry = RecycleEntry(
            id=entry_id,
            original_path=str(resolved),
            recycle_path=str(recycle_path),
            deleted_by=deleted_by,
            deleted_at=now_iso,
            expires_at=expires_iso,
            job_id=job_id,
            node_id=node_id,
            size_bytes=size_bytes,
            sha256=digest,
            reason=reason,
        )

        # Write metadata sidecar
        meta_path = slot_dir / ".meta.json"
        meta_path.write_text(
            json.dumps(entry.to_meta_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Persist to database
        conn = self._get_conn()
        try:
            conn.execute(
                """
                INSERT INTO recycle_bin (
                    id, original_path, recycle_path, deleted_by,
                    deleted_at, expires_at, job_id, node_id,
                    size_bytes, sha256, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.original_path,
                    entry.recycle_path,
                    entry.deleted_by,
                    entry.deleted_at,
                    entry.expires_at,
                    entry.job_id,
                    entry.node_id,
                    entry.size_bytes,
                    entry.sha256,
                    entry.reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return entry

    def restore(
        self,
        entry_id: str,
        restored_by: str,
        target_path: Optional[Path | str] = None,
        force: bool = False,
    ) -> Path:
        """Restore a recycled file to its original location (or *target_path*).

        Parameters
        ----------
        entry_id:
            UUID of the recycle bin entry to restore.
        restored_by:
            Identity string of who is performing the restore.
        target_path:
            Override destination.  If ``None``, restores to the original path.
        force:
            If ``True``, overwrite an existing file at the destination.

        Returns
        -------
        Path
            The path where the file was restored.

        Raises
        ------
        KeyError
            If *entry_id* is not found in the database.
        FileExistsError
            If the destination already exists and *force* is ``False``.
        FileNotFoundError
            If the recycled file is missing from disk (corrupted state).
        RuntimeError
            If the entry has already been restored.
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM recycle_bin WHERE id = ?", (entry_id,)
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise KeyError(f"Recycle bin entry not found: {entry_id!r}")

        entry = RecycleEntry.from_row(row)

        if entry.restored_at is not None:
            raise RuntimeError(
                f"Entry {entry_id!r} was already restored at {entry.restored_at} "
                f"by {entry.restored_by}"
            )

        recycle_path = Path(entry.recycle_path)
        if not recycle_path.exists():
            raise FileNotFoundError(
                f"Recycled file missing from disk: {recycle_path}. "
                "Recycle bin may be in an inconsistent state."
            )

        dest = Path(target_path) if target_path is not None else Path(entry.original_path)
        dest = dest.resolve()

        if dest.exists() and not force:
            raise FileExistsError(
                f"Destination already exists: {dest}. Use force=True to overwrite."
            )

        # Ensure parent directory exists
        dest.parent.mkdir(parents=True, exist_ok=True)

        if force and dest.exists():
            dest.unlink()

        # Prefer os.replace for same-device moves (atomic on POSIX, best-effort on Windows)
        try:
            os.replace(str(recycle_path), str(dest))
        except OSError:
            # Cross-device fallback
            shutil.move(str(recycle_path), str(dest))

        now_iso = self._now_iso()
        logger.info(
            "Restored recycle entry %s → %s (restored_by=%s)",
            entry_id, dest, restored_by,
        )

        # Update DB record
        conn = self._get_conn()
        try:
            conn.execute(
                """
                UPDATE recycle_bin
                   SET restored_at = ?, restored_by = ?
                 WHERE id = ?
                """,
                (now_iso, restored_by, entry_id),
            )
            conn.commit()
        finally:
            conn.close()

        # Clean up the now-empty slot directory (leave .meta.json for audit)
        slot_dir = recycle_path.parent
        if slot_dir.exists() and slot_dir != self.recycle_dir:
            remaining = list(slot_dir.iterdir())
            # If only .meta.json is left, leave it; full cleanup happens on purge
            if not remaining:
                try:
                    slot_dir.rmdir()
                except OSError:
                    pass

        return dest

    def list_entries(
        self,
        job_id: Optional[str] = None,
        deleted_by: Optional[str] = None,
        limit: int = 50,
    ) -> list[RecycleEntry]:
        """Return recycle bin entries, optionally filtered.

        Parameters
        ----------
        job_id:
            If given, only return entries associated with this job.
        deleted_by:
            If given, only return entries deleted by this identity.
        limit:
            Maximum number of entries to return (most-recently deleted first).

        Returns
        -------
        list[RecycleEntry]
        """
        clauses: list[str] = []
        params: list[object] = []

        if job_id is not None:
            clauses.append("job_id = ?")
            params.append(job_id)
        if deleted_by is not None:
            clauses.append("deleted_by = ?")
            params.append(deleted_by)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT * FROM recycle_bin
            {where}
            ORDER BY deleted_at DESC
            LIMIT ?
        """
        params.append(limit)

        conn = self._get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        return [RecycleEntry.from_row(r) for r in rows]

    def purge_expired(self) -> int:
        """Permanently delete all entries whose retention period has elapsed.

        Only entries that have **not** been restored are eligible.

        Returns
        -------
        int
            Number of entries purged.
        """
        now_iso = self._now_iso()
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id FROM recycle_bin
                 WHERE expires_at < ?
                   AND restored_at IS NULL
                """,
                (now_iso,),
            ).fetchall()

            entry_ids = [r["id"] for r in rows]
            if not entry_ids:
                return 0

            for eid in entry_ids:
                self._purge_entry_files(eid)

            placeholders = ",".join("?" * len(entry_ids))
            conn.execute(
                f"DELETE FROM recycle_bin WHERE id IN ({placeholders})",
                entry_ids,
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("purge_expired: removed %d expired recycle entries", len(entry_ids))
        return len(entry_ids)

    def purge_by_size(self) -> int:
        """Purge oldest non-restored entries if total recycle size exceeds MAX_SIZE_GB.

        Returns
        -------
        int
            Number of entries purged.
        """
        max_bytes = int(self.MAX_SIZE_GB * 1024 ** 3)

        conn = self._get_conn()
        try:
            # Fetch all non-restored entries ordered oldest-first
            rows = conn.execute(
                """
                SELECT id, size_bytes FROM recycle_bin
                 WHERE restored_at IS NULL
                 ORDER BY deleted_at ASC
                """
            ).fetchall()

            total_bytes: int = sum(r["size_bytes"] for r in rows)
            if total_bytes <= max_bytes:
                return 0

            to_purge: list[str] = []
            for row in rows:
                if total_bytes <= max_bytes:
                    break
                to_purge.append(row["id"])
                total_bytes -= row["size_bytes"]

            for eid in to_purge:
                self._purge_entry_files(eid)

            placeholders = ",".join("?" * len(to_purge))
            conn.execute(
                f"DELETE FROM recycle_bin WHERE id IN ({placeholders})",
                to_purge,
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "purge_by_size: removed %d entries to stay under %.1f GB",
            len(to_purge), self.MAX_SIZE_GB,
        )
        return len(to_purge)


# ---------------------------------------------------------------------------
# Module-level convenience instance
# ---------------------------------------------------------------------------

# Lazily created so import-time failures (missing DB) don't break the module.
_default_recycle_bin: Optional[RecycleBin] = None


def get_recycle_bin() -> RecycleBin:
    """Return the process-wide RecycleBin singleton."""
    global _default_recycle_bin
    if _default_recycle_bin is None:
        _default_recycle_bin = RecycleBin()
    return _default_recycle_bin
