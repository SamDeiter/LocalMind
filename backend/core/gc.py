"""
Garbage Collection & Data Lifecycle — backend/core/gc.py

Tiered retention policy:
  - Job input files:        retain for JOB_RETENTION_DAYS (default 90)
  - Intermediate files:     delete immediately after node success
  - Best-of-N candidates:   delete immediately after scoring
  - Final output artifacts: retain for JOB_RETENTION_DAYS, then archive
  - Audit logs:             NEVER delete (compliance)
  - Recycle bin:            purge after RECYCLE_RETENTION_DAYS (default 30)

All file deletions route through the recycle bin (RecycleBin.safe_delete)
unless we are purging the recycle bin itself.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from backend.config import (
    DB_PATH,
    WORKSPACE_ROOT,
    JOBS_DIR,
    RECYCLE_DIR,
    RECYCLE_RETENTION_DAYS,
)
from backend.security.recycle_bin import get_recycle_bin

logger = logging.getLogger("localmind.core.gc")

# ---------------------------------------------------------------------------
# GC config constants (env-overridable)
# ---------------------------------------------------------------------------

JOB_RETENTION_DAYS: int = int(os.getenv("JOB_RETENTION_DAYS", "90"))
MIN_FREE_SPACE_GB: int = int(os.getenv("MIN_FREE_SPACE_GB", "20"))
ARCHIVE_DIR: Path = WORKSPACE_ROOT / "archive"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DiskUsage:
    """Disk usage snapshot for a given mount point."""

    total_gb: float
    used_gb: float
    free_gb: float
    usage_percent: float

    def __repr__(self) -> str:
        return (
            f"DiskUsage(total={self.total_gb:.1f}GB, "
            f"used={self.used_gb:.1f}GB, "
            f"free={self.free_gb:.1f}GB, "
            f"usage={self.usage_percent:.1f}%)"
        )


@dataclass
class GCResult:
    """Result of a single GC operation."""

    gc_type: str
    files_deleted: int
    bytes_freed: int
    duration_ms: float
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Open a WAL-mode connection with standard pragmas."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_gc_stats_table() -> None:
    """Create the gc_stats table if it does not exist."""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gc_stats (
                id TEXT PRIMARY KEY,
                gc_type TEXT NOT NULL,
                files_deleted INTEGER NOT NULL,
                bytes_freed INTEGER NOT NULL,
                duration_ms REAL NOT NULL,
                ran_at TEXT NOT NULL,
                errors_json TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gc_stats_type
                ON gc_stats(gc_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gc_stats_ran
                ON gc_stats(ran_at)
        """)
        conn.commit()
    finally:
        conn.close()


# Ensure the table exists on module import.
try:
    _ensure_gc_stats_table()
except Exception:
    # Tolerate missing DB at import time (e.g. tests, CLI tools).
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# GCStats — track GC activity
# ---------------------------------------------------------------------------

class GCStats:
    """Record and query garbage collection activity."""

    @staticmethod
    def record_gc_run(
        gc_type: str,
        files_deleted: int,
        bytes_freed: int,
        duration_ms: float,
        errors: Optional[list[str]] = None,
    ) -> None:
        """Persist a GC run record to the gc_stats table.

        Parameters
        ----------
        gc_type:
            Category of GC run, e.g. ``"orphaned_temps"``, ``"intermediate"``,
            ``"expired_jobs"``, ``"recycle_purge"``, ``"emergency"``.
        files_deleted:
            Number of files removed in this run.
        bytes_freed:
            Total bytes freed.
        duration_ms:
            Wall-clock duration of the run in milliseconds.
        errors:
            Optional list of error messages encountered during the run.
        """
        import json

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO gc_stats (id, gc_type, files_deleted, bytes_freed,
                                      duration_ms, ran_at, errors_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    gc_type,
                    files_deleted,
                    bytes_freed,
                    duration_ms,
                    _now_iso(),
                    json.dumps(errors) if errors else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_gc_stats(since: Optional[str] = None) -> dict:
        """Return a summary of GC activity.

        Parameters
        ----------
        since:
            ISO-8601 timestamp.  If provided, only runs after this time are
            included.  If ``None``, all history is returned.

        Returns
        -------
        dict
            ``{"total_runs", "total_files_deleted", "total_bytes_freed",
            "by_type": {gc_type: {"runs", "files_deleted", "bytes_freed"}}, ...}``
        """
        conn = _get_conn()
        try:
            if since:
                rows = conn.execute(
                    "SELECT * FROM gc_stats WHERE ran_at >= ? ORDER BY ran_at DESC",
                    (since,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM gc_stats ORDER BY ran_at DESC"
                ).fetchall()
        finally:
            conn.close()

        by_type: dict[str, dict] = {}
        total_files = 0
        total_bytes = 0

        for row in rows:
            gc_type = row["gc_type"]
            if gc_type not in by_type:
                by_type[gc_type] = {"runs": 0, "files_deleted": 0, "bytes_freed": 0}
            by_type[gc_type]["runs"] += 1
            by_type[gc_type]["files_deleted"] += row["files_deleted"]
            by_type[gc_type]["bytes_freed"] += row["bytes_freed"]
            total_files += row["files_deleted"]
            total_bytes += row["bytes_freed"]

        return {
            "total_runs": len(rows),
            "total_files_deleted": total_files,
            "total_bytes_freed": total_bytes,
            "by_type": by_type,
        }


# ---------------------------------------------------------------------------
# GarbageCollector — synchronous GC operations
# ---------------------------------------------------------------------------

class GarbageCollector:
    """Garbage collection engine for LocalMind workspace files.

    Implements tiered retention with all deletions routed through the
    recycle bin for safety and audit-trail compliance.

    Parameters
    ----------
    job_retention_days:
        Number of days to retain completed/failed jobs before recycling.
    min_free_space_gb:
        Minimum free disk space in GB before triggering emergency GC.
    archive_dir:
        Directory where old artifacts are archived.  Defaults to
        ``WORKSPACE_ROOT / "archive"``.
    """

    def __init__(
        self,
        job_retention_days: int = JOB_RETENTION_DAYS,
        min_free_space_gb: int = MIN_FREE_SPACE_GB,
        archive_dir: Optional[Path] = None,
    ) -> None:
        self.job_retention_days = job_retention_days
        self.min_free_space_gb = min_free_space_gb
        self.archive_dir = archive_dir or ARCHIVE_DIR
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._recycle = get_recycle_bin()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_delete_file(
        self,
        path: Path,
        reason: str,
        job_id: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> int:
        """Delete a single file via the recycle bin.

        Returns the file size in bytes (0 if the file doesn't exist or an
        error occurs).
        """
        try:
            if not path.exists() or not path.is_file():
                return 0
            size = path.stat().st_size
            self._recycle.safe_delete(
                path=path,
                deleted_by="system:gc",
                job_id=job_id,
                node_id=node_id,
                reason=reason,
            )
            return size
        except Exception as e:
            logger.warning("Failed to recycle %s: %s", path, e)
            return 0

    def _safe_delete_dir(
        self,
        dir_path: Path,
        reason: str,
        job_id: Optional[str] = None,
    ) -> tuple[int, int]:
        """Recycle all files in a directory, then remove the empty directory.

        Returns (files_deleted, bytes_freed).
        """
        files_deleted = 0
        bytes_freed = 0

        if not dir_path.exists() or not dir_path.is_dir():
            return 0, 0

        # Recycle each file individually (recycle bin is file-oriented).
        for file_path in sorted(dir_path.rglob("*")):
            if file_path.is_file():
                size = self._safe_delete_file(
                    file_path,
                    reason=reason,
                    job_id=job_id,
                )
                if size > 0:
                    files_deleted += 1
                    bytes_freed += size

        # Remove empty directory tree from bottom up.
        try:
            for d in sorted(dir_path.rglob("*"), reverse=True):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except OSError:
                        pass  # Not empty yet — fine.
            if dir_path.exists():
                dir_path.rmdir()
        except OSError:
            pass

        return files_deleted, bytes_freed

    # ------------------------------------------------------------------
    # cleanup_orphaned_temps
    # ------------------------------------------------------------------

    def cleanup_orphaned_temps(self) -> GCResult:
        """Scan JOBS_DIR for ``.tmp.*`` files and delete them.

        Intended to run on startup to clean up files left behind by
        interrupted operations.

        Returns
        -------
        GCResult
        """
        t0 = time.monotonic()
        files_deleted = 0
        bytes_freed = 0
        errors: list[str] = []

        try:
            for tmp_file in JOBS_DIR.rglob(".tmp.*"):
                if tmp_file.is_file():
                    size = self._safe_delete_file(
                        tmp_file,
                        reason="orphaned_temp_cleanup",
                    )
                    if size > 0:
                        files_deleted += 1
                        bytes_freed += size

            # Also catch files matching *.tmp pattern
            for tmp_file in JOBS_DIR.rglob("*.tmp"):
                if tmp_file.is_file():
                    size = self._safe_delete_file(
                        tmp_file,
                        reason="orphaned_temp_cleanup",
                    )
                    if size > 0:
                        files_deleted += 1
                        bytes_freed += size
        except Exception as e:
            errors.append(f"Error scanning for temp files: {e}")
            logger.error("cleanup_orphaned_temps error: %s", e)

        duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "cleanup_orphaned_temps: deleted %d files, freed %d bytes (%.0fms)",
            files_deleted, bytes_freed, duration_ms,
        )

        result = GCResult(
            gc_type="orphaned_temps",
            files_deleted=files_deleted,
            bytes_freed=bytes_freed,
            duration_ms=duration_ms,
            errors=errors,
        )
        GCStats.record_gc_run(
            result.gc_type, result.files_deleted,
            result.bytes_freed, result.duration_ms,
            errors=result.errors or None,
        )
        return result

    # ------------------------------------------------------------------
    # cleanup_intermediate_files
    # ------------------------------------------------------------------

    def cleanup_intermediate_files(self) -> GCResult:
        """Find completed nodes and delete their working copies.

        Working copies live in ``jobs/{job_id}/working/`` and are only
        needed during node execution.  Once a node reaches ``completed``
        status, the working directory can be reclaimed.

        Returns
        -------
        GCResult
        """
        t0 = time.monotonic()
        files_deleted = 0
        bytes_freed = 0
        errors: list[str] = []

        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT jn.id AS node_id, jn.job_id
                  FROM job_nodes jn
                 WHERE jn.status = 'completed'
                """
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            job_id = row["job_id"]
            working_dir = JOBS_DIR / job_id / "working"

            if not working_dir.exists():
                continue

            fd, bf = self._safe_delete_dir(
                working_dir,
                reason="intermediate_cleanup",
                job_id=job_id,
            )
            files_deleted += fd
            bytes_freed += bf

        duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "cleanup_intermediate_files: deleted %d files, freed %d bytes (%.0fms)",
            files_deleted, bytes_freed, duration_ms,
        )

        result = GCResult(
            gc_type="intermediate",
            files_deleted=files_deleted,
            bytes_freed=bytes_freed,
            duration_ms=duration_ms,
            errors=errors,
        )
        GCStats.record_gc_run(
            result.gc_type, result.files_deleted,
            result.bytes_freed, result.duration_ms,
            errors=result.errors or None,
        )
        return result

    # ------------------------------------------------------------------
    # cleanup_expired_jobs
    # ------------------------------------------------------------------

    def cleanup_expired_jobs(
        self, retention_days: Optional[int] = None,
    ) -> GCResult:
        """Find jobs older than *retention_days* that are done or failed,
        and move their directories to the recycle bin.

        Parameters
        ----------
        retention_days:
            Override the default ``job_retention_days``.  If ``None``,
            the instance default is used.

        Returns
        -------
        GCResult
        """
        t0 = time.monotonic()
        files_deleted = 0
        bytes_freed = 0
        errors: list[str] = []
        days = retention_days if retention_days is not None else self.job_retention_days

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id FROM jobs
                 WHERE status IN ('done', 'failed')
                   AND updated_at < ?
                """,
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            job_id = row["id"]
            job_dir = JOBS_DIR / job_id

            if not job_dir.exists():
                continue

            fd, bf = self._safe_delete_dir(
                job_dir,
                reason=f"expired_job (>{days}d)",
                job_id=job_id,
            )
            files_deleted += fd
            bytes_freed += bf

        duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "cleanup_expired_jobs: deleted %d files from %d jobs, freed %d bytes (%.0fms)",
            files_deleted, len(rows), bytes_freed, duration_ms,
        )

        result = GCResult(
            gc_type="expired_jobs",
            files_deleted=files_deleted,
            bytes_freed=bytes_freed,
            duration_ms=duration_ms,
            errors=errors,
        )
        GCStats.record_gc_run(
            result.gc_type, result.files_deleted,
            result.bytes_freed, result.duration_ms,
            errors=result.errors or None,
        )
        return result

    # ------------------------------------------------------------------
    # purge_recycle_bin
    # ------------------------------------------------------------------

    def purge_recycle_bin(self) -> GCResult:
        """Purge recycle bin entries older than RECYCLE_RETENTION_DAYS.

        This permanently deletes files and their DB rows.  It does NOT
        go through the recycle bin again (that would be circular).

        Returns
        -------
        GCResult
        """
        t0 = time.monotonic()
        errors: list[str] = []

        # Gather size info before purge for reporting.
        conn = _get_conn()
        try:
            now_iso = _now_iso()
            rows = conn.execute(
                """
                SELECT id, size_bytes FROM recycle_bin
                 WHERE expires_at < ?
                   AND restored_at IS NULL
                """,
                (now_iso,),
            ).fetchall()
        finally:
            conn.close()

        bytes_freed = sum(r["size_bytes"] for r in rows)
        files_count = len(rows)

        # Use RecycleBin's purge_expired (handles disk + DB deletion).
        try:
            purged = self._recycle.purge_expired()
        except Exception as e:
            errors.append(f"purge_expired error: {e}")
            logger.error("purge_recycle_bin error: %s", e)
            purged = 0

        # Also enforce size limits.
        try:
            size_purged = self._recycle.purge_by_size()
            if size_purged:
                logger.info("purge_recycle_bin: purged %d entries by size limit", size_purged)
        except Exception as e:
            errors.append(f"purge_by_size error: {e}")
            logger.error("purge_by_size error: %s", e)

        duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "purge_recycle_bin: purged %d entries, freed %d bytes (%.0fms)",
            purged, bytes_freed, duration_ms,
        )

        result = GCResult(
            gc_type="recycle_purge",
            files_deleted=files_count,
            bytes_freed=bytes_freed,
            duration_ms=duration_ms,
            errors=errors,
        )
        GCStats.record_gc_run(
            result.gc_type, result.files_deleted,
            result.bytes_freed, result.duration_ms,
            errors=result.errors or None,
        )
        return result

    # ------------------------------------------------------------------
    # check_disk_usage
    # ------------------------------------------------------------------

    def check_disk_usage(self) -> DiskUsage:
        """Return disk usage statistics for the WORKSPACE_ROOT volume.

        Returns
        -------
        DiskUsage
            Snapshot of total, used, and free space.
        """
        usage = shutil.disk_usage(str(WORKSPACE_ROOT))
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        usage_percent = (usage.used / usage.total) * 100 if usage.total > 0 else 0.0

        du = DiskUsage(
            total_gb=round(total_gb, 2),
            used_gb=round(used_gb, 2),
            free_gb=round(free_gb, 2),
            usage_percent=round(usage_percent, 1),
        )
        logger.debug("Disk usage: %s", du)
        return du

    # ------------------------------------------------------------------
    # emergency_gc
    # ------------------------------------------------------------------

    def emergency_gc(self) -> GCResult:
        """Aggressive GC triggered when free space < min_free_space_gb.

        Steps (executed in order of increasing impact):
          1. Delete all intermediate/working files.
          2. Purge recycle bin entries older than 7 days.
          3. Delete non-current artifact versions older than 30 days.

        Returns
        -------
        GCResult
        """
        t0 = time.monotonic()
        total_files = 0
        total_bytes = 0
        errors: list[str] = []

        logger.warning(
            "EMERGENCY GC triggered — free space below %d GB",
            self.min_free_space_gb,
        )

        # Step 1: All intermediate files (regardless of node status).
        try:
            for job_dir in JOBS_DIR.iterdir():
                if not job_dir.is_dir():
                    continue
                working_dir = job_dir / "working"
                if working_dir.exists():
                    fd, bf = self._safe_delete_dir(
                        working_dir,
                        reason="emergency_gc:intermediate",
                        job_id=job_dir.name,
                    )
                    total_files += fd
                    total_bytes += bf
        except Exception as e:
            errors.append(f"emergency intermediate cleanup: {e}")
            logger.error("Emergency intermediate cleanup error: %s", e)

        # Step 2: Purge recycle entries older than 7 days.
        try:
            seven_days_ago = (
                datetime.now(timezone.utc) - timedelta(days=7)
            ).isoformat()
            conn = _get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT id, size_bytes FROM recycle_bin
                     WHERE deleted_at < ?
                       AND restored_at IS NULL
                    """,
                    (seven_days_ago,),
                ).fetchall()

                for row in rows:
                    entry_id = row["id"]
                    total_bytes += row["size_bytes"]
                    total_files += 1
                    self._recycle._purge_entry_files(entry_id)

                if rows:
                    entry_ids = [r["id"] for r in rows]
                    placeholders = ",".join("?" * len(entry_ids))
                    conn.execute(
                        f"DELETE FROM recycle_bin WHERE id IN ({placeholders})",
                        entry_ids,
                    )
                    conn.commit()
                    logger.info(
                        "Emergency GC: purged %d recycle entries (>7 days)",
                        len(entry_ids),
                    )
            finally:
                conn.close()
        except Exception as e:
            errors.append(f"emergency recycle purge: {e}")
            logger.error("Emergency recycle purge error: %s", e)

        # Step 3: Delete non-current artifact versions older than 30 days.
        try:
            thirty_days_ago = (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).isoformat()
            conn = _get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT av.id, av.file_path, av.file_size_bytes
                      FROM artifact_versions av
                      JOIN artifacts a ON a.id = av.artifact_id
                     WHERE av.id != a.current_version_id
                       AND av.created_at < ?
                    """,
                    (thirty_days_ago,),
                ).fetchall()

                for row in rows:
                    fpath = Path(row["file_path"])
                    size = self._safe_delete_file(
                        fpath,
                        reason="emergency_gc:old_artifact_version",
                    )
                    if size > 0:
                        total_files += 1
                        total_bytes += size

                logger.info(
                    "Emergency GC: processed %d old artifact versions",
                    len(rows),
                )
            finally:
                conn.close()
        except Exception as e:
            errors.append(f"emergency artifact cleanup: {e}")
            logger.error("Emergency artifact cleanup error: %s", e)

        duration_ms = (time.monotonic() - t0) * 1000
        logger.warning(
            "EMERGENCY GC complete: deleted %d files, freed %d bytes (%.0fms)",
            total_files, total_bytes, duration_ms,
        )

        result = GCResult(
            gc_type="emergency",
            files_deleted=total_files,
            bytes_freed=total_bytes,
            duration_ms=duration_ms,
            errors=errors,
        )
        GCStats.record_gc_run(
            result.gc_type, result.files_deleted,
            result.bytes_freed, result.duration_ms,
            errors=result.errors or None,
        )
        return result

    # ------------------------------------------------------------------
    # archive_old_artifacts
    # ------------------------------------------------------------------

    def archive_old_artifacts(
        self, archive_dir: Optional[Path] = None,
    ) -> GCResult:
        """Move old artifact files to an archive directory.

        Artifacts whose jobs are older than ``job_retention_days`` and
        whose status is ``done`` are moved.  The DB ``file_path`` column
        in ``artifact_versions`` is updated to point to the new location.

        Parameters
        ----------
        archive_dir:
            Destination directory.  Defaults to ``self.archive_dir``.

        Returns
        -------
        GCResult
        """
        t0 = time.monotonic()
        files_moved = 0
        bytes_moved = 0
        errors: list[str] = []
        dest_root = archive_dir or self.archive_dir
        dest_root.mkdir(parents=True, exist_ok=True)

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.job_retention_days)
        ).isoformat()

        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT av.id AS version_id, av.file_path, av.file_size_bytes,
                       a.job_id
                  FROM artifact_versions av
                  JOIN artifacts a ON a.id = av.artifact_id
                  JOIN jobs j ON j.id = a.job_id
                 WHERE j.status = 'done'
                   AND j.updated_at < ?
                """,
                (cutoff,),
            ).fetchall()

            for row in rows:
                src = Path(row["file_path"])
                if not src.exists():
                    continue

                # Preserve relative structure under archive.
                job_id = row["job_id"]
                archive_job_dir = dest_root / job_id
                archive_job_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_job_dir / src.name

                # Handle name collisions.
                if dest.exists():
                    stem = dest.stem
                    suffix = dest.suffix
                    dest = archive_job_dir / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

                try:
                    shutil.move(str(src), str(dest))
                    files_moved += 1
                    bytes_moved += row["file_size_bytes"] or 0

                    # Update DB to point to the new path.
                    conn.execute(
                        "UPDATE artifact_versions SET file_path = ? WHERE id = ?",
                        (str(dest), row["version_id"]),
                    )
                except Exception as e:
                    errors.append(
                        f"Failed to archive {src} -> {dest}: {e}"
                    )
                    logger.warning("archive_old_artifacts: %s", e)

            conn.commit()
        finally:
            conn.close()

        duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "archive_old_artifacts: moved %d files (%d bytes) to %s (%.0fms)",
            files_moved, bytes_moved, dest_root, duration_ms,
        )

        result = GCResult(
            gc_type="archive",
            files_deleted=files_moved,  # "deleted" = moved out of hot storage
            bytes_freed=bytes_moved,
            duration_ms=duration_ms,
            errors=errors,
        )
        GCStats.record_gc_run(
            result.gc_type, result.files_deleted,
            result.bytes_freed, result.duration_ms,
            errors=result.errors or None,
        )
        return result


# ---------------------------------------------------------------------------
# GCWorker — async background worker
# ---------------------------------------------------------------------------

class GCWorker:
    """Async background worker that runs GC tasks on schedule.

    Usage::

        worker = GCWorker()
        # In your FastAPI lifespan:
        asyncio.create_task(worker.start())

    The worker runs:
      - On startup: ``cleanup_orphaned_temps``
      - Every hour:  ``cleanup_intermediate_files``
      - Every day:   ``cleanup_expired_jobs``, ``purge_recycle_bin``,
                     ``archive_old_artifacts``, disk usage check
                     (with emergency GC if needed)
    """

    # Intervals in seconds
    HOURLY: int = 3600
    DAILY: int = 86400

    def __init__(
        self,
        gc: Optional[GarbageCollector] = None,
    ) -> None:
        self._gc = gc or GarbageCollector()
        self._last_hourly: float = 0.0
        self._last_daily: float = 0.0
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the GC background loop.

        Runs startup cleanup immediately, then loops hourly/daily tasks.
        Designed to be launched as an ``asyncio.Task`` and cancelled on
        shutdown.
        """
        if self._running:
            logger.warning("GCWorker already running; ignoring duplicate start()")
            return

        self._running = True
        logger.info("GCWorker starting")

        # Startup cleanup.
        await self.run_startup_cleanup()

        try:
            while self._running:
                now = time.monotonic()

                # Hourly tasks.
                if now - self._last_hourly >= self.HOURLY:
                    await self.run_hourly()
                    self._last_hourly = now

                # Daily tasks.
                if now - self._last_daily >= self.DAILY:
                    await self.run_daily()
                    self._last_daily = now

                # Sleep for 60 seconds between checks.
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("GCWorker shutting down (cancelled)")
        except Exception as e:
            logger.error("GCWorker crashed: %s", e, exc_info=True)
        finally:
            self._running = False
            logger.info("GCWorker stopped")

    async def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def run_startup_cleanup(self) -> None:
        """Run startup-time GC: clean orphaned temp files."""
        logger.info("GCWorker: running startup cleanup")
        result = await asyncio.to_thread(self._gc.cleanup_orphaned_temps)
        logger.info(
            "GCWorker startup cleanup: %d files, %d bytes freed",
            result.files_deleted, result.bytes_freed,
        )

    async def run_hourly(self) -> None:
        """Run hourly GC tasks: intermediate file cleanup."""
        logger.info("GCWorker: running hourly tasks")
        result = await asyncio.to_thread(self._gc.cleanup_intermediate_files)
        logger.info(
            "GCWorker hourly: %d intermediate files cleaned, %d bytes freed",
            result.files_deleted, result.bytes_freed,
        )

    async def run_daily(self) -> None:
        """Run daily GC tasks: expired jobs, recycle purge, archive, disk check."""
        logger.info("GCWorker: running daily tasks")

        # 1. Expired jobs
        result = await asyncio.to_thread(self._gc.cleanup_expired_jobs)
        logger.info(
            "GCWorker daily [expired_jobs]: %d files, %d bytes freed",
            result.files_deleted, result.bytes_freed,
        )

        # 2. Recycle bin purge
        result = await asyncio.to_thread(self._gc.purge_recycle_bin)
        logger.info(
            "GCWorker daily [recycle_purge]: %d files, %d bytes freed",
            result.files_deleted, result.bytes_freed,
        )

        # 3. Archive old artifacts
        result = await asyncio.to_thread(self._gc.archive_old_artifacts)
        logger.info(
            "GCWorker daily [archive]: %d files archived, %d bytes moved",
            result.files_deleted, result.bytes_freed,
        )

        # 4. Disk usage check + emergency GC if needed
        du = await asyncio.to_thread(self._gc.check_disk_usage)
        logger.info("GCWorker daily [disk]: %s", du)

        if du.free_gb < self._gc.min_free_space_gb:
            logger.warning(
                "Free space %.1f GB < threshold %d GB — running emergency GC",
                du.free_gb, self._gc.min_free_space_gb,
            )
            result = await asyncio.to_thread(self._gc.emergency_gc)
            logger.warning(
                "GCWorker daily [emergency]: %d files, %d bytes freed",
                result.files_deleted, result.bytes_freed,
            )
