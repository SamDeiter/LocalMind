"""
Data protection utilities -- PII scrubbing, DB hardening, auto-purge.

Provides:
- ``PIIScrubber``: configurable regex-based PII removal for data leaving
  the local network (e.g. Gemini API calls).
- ``harden_db_permissions``: lock down the SQLite file to owner-only on
  POSIX systems (graceful no-op on Windows/NTFS).
- ``schedule_vacuum``: periodic ``VACUUM`` on the database to reclaim space.
- ``auto_purge_old_jobs``: move stale job directories to the recycle bin
  after a configurable retention period.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from backend.config import JOB_RETENTION_DAYS

logger = logging.getLogger("localmind.security.data_protection")

# ---------------------------------------------------------------------------
# PII Scrubber
# ---------------------------------------------------------------------------

# Default PII patterns — applied in order.  Each tuple is (label, regex).
_DEFAULT_PII_PATTERNS: list[tuple[str, str]] = [
    ("email",        r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ("ssn",          r"\b\d{3}-\d{2}-\d{4}\b"),
    ("credit_card",  r"\b(?:\d[ \-]*?){13,19}\b"),
    ("phone_us",     r"\b(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b"),
    ("ip_address",   r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
]

_PII_REPLACEMENT = "[PII_REDACTED]"


class PIIScrubber:
    """Regex-based PII scrubber with configurable patterns.

    Default patterns cover email addresses, SSNs, credit card numbers,
    US phone numbers, and IPv4 addresses.  Additional patterns can be
    supplied via the ``PII_SCRUB_PATTERNS`` environment variable (a JSON
    list of regex strings).

    Examples
    --------
    >>> scrubber = PIIScrubber()
    >>> scrubber.scrub("Call me at 555-867-5309")
    'Call me at [PII_REDACTED]'
    """

    def __init__(self, extra_patterns: Optional[list[str]] = None) -> None:
        """
        Parameters
        ----------
        extra_patterns : list[str] | None
            Additional regex strings to compile.  If ``None``, reads from
            the ``PII_SCRUB_PATTERNS`` env var (JSON list of strings).
        """
        self._compiled: list[tuple[str, re.Pattern]] = []

        # Load defaults
        for label, raw in _DEFAULT_PII_PATTERNS:
            self._compiled.append((label, re.compile(raw)))

        # Load env-provided extras
        if extra_patterns is None:
            env_raw = os.getenv("PII_SCRUB_PATTERNS", "")
            if env_raw.strip():
                try:
                    extra_patterns = json.loads(env_raw)
                except json.JSONDecodeError:
                    logger.warning(
                        "PII_SCRUB_PATTERNS env var is not valid JSON — ignoring"
                    )
                    extra_patterns = []
            else:
                extra_patterns = []

        for idx, pat_str in enumerate(extra_patterns):
            try:
                self._compiled.append((f"custom_{idx}", re.compile(pat_str)))
            except re.error as exc:
                logger.warning(
                    "Skipping invalid PII regex pattern %r: %s", pat_str, exc
                )

        logger.debug("PIIScrubber initialized with %d patterns", len(self._compiled))

    # ------------------------------------------------------------------

    def scrub(self, text: str) -> str:
        """Replace all PII matches in *text* with ``[PII_REDACTED]``.

        Parameters
        ----------
        text : str
            Input text potentially containing PII.

        Returns
        -------
        str
            Scrubbed text.
        """
        if not text:
            return text

        result = text
        for label, pat in self._compiled:
            result = pat.sub(_PII_REPLACEMENT, result)
        return result

    def scrub_dict(self, d: dict) -> dict:
        """Recursively scrub all string values in a dictionary.

        Parameters
        ----------
        d : dict
            Dictionary whose string leaf values should be PII-scrubbed.

        Returns
        -------
        dict
            A new dictionary (deep copy) with all string values scrubbed.
        """
        return self._walk(d)  # type: ignore[return-value]

    def _walk(self, obj: object) -> object:
        """Recursively walk and scrub strings in nested structures."""
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, dict):
            return {k: self._walk(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            walked = [self._walk(item) for item in obj]
            return type(obj)(walked)
        return obj


# ---------------------------------------------------------------------------
# DB Permission Hardening
# ---------------------------------------------------------------------------

def harden_db_permissions(db_path: Path) -> None:
    """Set the SQLite database file to mode 600 (owner read/write only).

    On Windows / NTFS, ``os.chmod`` does not enforce POSIX-style
    permissions, so this function logs a warning and returns without
    error.  On Linux / macOS it applies ``0o600``.

    Parameters
    ----------
    db_path : Path
        Absolute path to the SQLite database file.
    """
    if not db_path.exists():
        logger.warning("harden_db_permissions: %s does not exist yet — skipping", db_path)
        return

    if platform.system() == "Windows":
        logger.info(
            "harden_db_permissions: Windows/NTFS detected — chmod 600 is not "
            "effective. Use NTFS ACLs for production hardening."
        )
        return

    try:
        os.chmod(db_path, 0o600)
        logger.info("Set DB file permissions to 600: %s", db_path)
    except OSError as exc:
        logger.warning("Failed to set DB permissions on %s: %s", db_path, exc)


# ---------------------------------------------------------------------------
# Periodic VACUUM
# ---------------------------------------------------------------------------

async def schedule_vacuum(db_path: Path, interval_hours: int = 24) -> None:
    """Run SQLite ``VACUUM`` on *db_path* every *interval_hours* hours.

    This is designed to be launched as an ``asyncio.create_task`` background
    coroutine during server startup.  It runs indefinitely until the task
    is cancelled.

    Parameters
    ----------
    db_path : Path
        Path to the SQLite database.
    interval_hours : int
        Hours between successive ``VACUUM`` runs.  Defaults to 24.
    """
    interval_seconds = interval_hours * 3600
    logger.info(
        "VACUUM scheduler started — will run every %d hours on %s",
        interval_hours, db_path,
    )

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("VACUUM")
                logger.info("VACUUM completed on %s", db_path)
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("VACUUM failed on %s: %s", db_path, exc)


# ---------------------------------------------------------------------------
# Auto-Purge Old Jobs
# ---------------------------------------------------------------------------

async def auto_purge_old_jobs(
    workspace_root: Path,
    retention_days: int = JOB_RETENTION_DAYS,
) -> int:
    """Scan ``workspace_root/jobs/`` and recycle job directories older than
    *retention_days*.

    Uses the recycle bin (``backend.security.recycle_bin``) so that purged
    data can be restored if needed.  Only **files** inside each job directory
    are recycled (the recycle bin's ``safe_delete`` targets files, not
    directories).

    Parameters
    ----------
    workspace_root : Path
        Root of the LocalMind workspace (contains ``jobs/`` subdirectory).
    retention_days : int
        Number of days to keep job directories.  Defaults to the
        ``JOB_RETENTION_DAYS`` config value (90).

    Returns
    -------
    int
        Number of job directories purged (moved to recycle bin).
    """
    from backend.security.recycle_bin import get_recycle_bin

    jobs_dir = workspace_root / "jobs"
    if not jobs_dir.exists():
        logger.debug("auto_purge_old_jobs: %s does not exist — nothing to purge", jobs_dir)
        return 0

    cutoff_ts = time.time() - (retention_days * 86400)
    recycle_bin = get_recycle_bin()
    purged_count = 0

    for entry in jobs_dir.iterdir():
        if not entry.is_dir():
            continue

        # Use the directory's modification time as the "last activity" indicator
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue

        if mtime >= cutoff_ts:
            continue  # still within retention window

        # Recycle all files in the job directory
        job_id = entry.name
        files_recycled = 0

        try:
            for file_path in entry.rglob("*"):
                if not file_path.is_file():
                    continue
                try:
                    recycle_bin.safe_delete(
                        path=file_path,
                        deleted_by="system:auto_purge",
                        job_id=job_id,
                        reason=f"auto-purge: job older than {retention_days} days",
                    )
                    files_recycled += 1
                except Exception as exc:
                    logger.warning(
                        "auto_purge: failed to recycle %s: %s", file_path, exc
                    )

            # Remove the now-empty directory tree
            _remove_empty_dirs(entry)

            if files_recycled > 0:
                purged_count += 1
                logger.info(
                    "Auto-purged job %s (%d files recycled)", job_id, files_recycled
                )
            else:
                # Directory had no files — just clean it up
                _remove_empty_dirs(entry)

        except Exception as exc:
            logger.warning("auto_purge: error processing job %s: %s", job_id, exc)

    if purged_count:
        logger.info(
            "auto_purge_old_jobs complete: %d jobs purged (retention=%d days)",
            purged_count, retention_days,
        )
    return purged_count


def _remove_empty_dirs(root: Path) -> None:
    """Remove a directory tree bottom-up, ignoring non-empty dirs."""
    if not root.is_dir():
        return
    # Walk bottom-up
    for dirpath in sorted(root.rglob("*"), reverse=True):
        if dirpath.is_dir():
            try:
                dirpath.rmdir()  # only succeeds if empty
            except OSError:
                pass
    try:
        root.rmdir()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Daily purge loop (for use as asyncio background task)
# ---------------------------------------------------------------------------

async def daily_purge_loop(
    workspace_root: Path,
    retention_days: int = JOB_RETENTION_DAYS,
    interval_hours: int = 24,
) -> None:
    """Run :func:`auto_purge_old_jobs` on a recurring schedule.

    Designed to be launched via ``asyncio.create_task`` during server
    startup.  Runs indefinitely until the task is cancelled.

    Parameters
    ----------
    workspace_root : Path
        Root of the LocalMind workspace.
    retention_days : int
        How many days to retain job directories.
    interval_hours : int
        Hours between successive purge runs.
    """
    logger.info(
        "Daily purge loop started — retention=%d days, interval=%d hours",
        retention_days, interval_hours,
    )
    interval_seconds = interval_hours * 3600

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            count = await auto_purge_old_jobs(workspace_root, retention_days)
            if count:
                logger.info("Daily purge recycled %d old jobs", count)
        except Exception as exc:
            logger.warning("Daily purge loop error: %s", exc)
