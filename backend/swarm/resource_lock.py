"""Resource locking system for multi-agent conflict prevention.

Provides exclusive and shared locks on resource paths so that concurrent
jobs in the swarm cannot clobber each other's work.  All state lives in
the ``resource_locks`` SQLite table.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.config import RESOURCE_LOCK_TTL_SEC
from backend.db import get_db
from backend.swarm.models import ResourceLock

logger = logging.getLogger("localmind.swarm.resource_lock")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LockConflictError(Exception):
    """Raised when a lock cannot be acquired because a conflicting lock
    is already held by another job."""

    def __init__(self, resource_path: str, held_by: str) -> None:
        self.resource_path = resource_path
        self.held_by = held_by
        super().__init__(
            f"Lock conflict on {resource_path!r}: already held by job {held_by}"
        )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ResourceLockManager:
    """Manages resource locks backed by the ``resource_locks`` table."""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _utcnow() -> str:
        """Return the current UTC time as an ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _active_clause() -> str:
        """SQL fragment that selects only active (non-released, non-expired)
        locks."""
        return "released_at IS NULL AND expires_at > ?"

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def __init__(self) -> None:  # noqa: D107 – intentionally empty
        pass

    def acquire(
        self,
        resource_path: str,
        job_id: str,
        node_id: Optional[str] = None,
        lock_type: str = "exclusive",
        ttl_sec: Optional[int] = None,
    ) -> ResourceLock:
        """Acquire a lock on *resource_path* for *job_id*.

        Parameters
        ----------
        resource_path:
            Logical path of the resource to lock (e.g. ``"file:/workspace/foo.py"``).
        job_id:
            The job requesting the lock.
        node_id:
            Optional node within the job.
        lock_type:
            ``"exclusive"`` (default) or ``"shared"``.
        ttl_sec:
            Time-to-live in seconds.  Falls back to ``RESOURCE_LOCK_TTL_SEC``.

        Returns
        -------
        ResourceLock
            The newly-created (or already-existing) lock row.

        Raises
        ------
        LockConflictError
            If an incompatible active lock is held by another job.
        """
        if ttl_sec is None:
            ttl_sec = RESOURCE_LOCK_TTL_SEC

        now = self._utcnow()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)
        ).isoformat()

        db = get_db()
        try:
            # Check if this job already holds an active lock on the resource.
            existing = db.execute(
                f"SELECT * FROM resource_locks "
                f"WHERE resource_path = ? AND held_by_job_id = ? "
                f"AND {self._active_clause()}",
                (resource_path, job_id, now),
            ).fetchone()

            if existing:
                logger.debug(
                    "Job %s already holds lock on %s — returning existing",
                    job_id,
                    resource_path,
                )
                return ResourceLock.from_row(existing)

            # --- conflict detection ---

            # (a) Active exclusive lock held by another job?
            exclusive_lock = db.execute(
                f"SELECT * FROM resource_locks "
                f"WHERE resource_path = ? AND lock_type = 'exclusive' "
                f"AND held_by_job_id != ? AND {self._active_clause()}",
                (resource_path, job_id, now),
            ).fetchone()

            if exclusive_lock:
                raise LockConflictError(
                    resource_path, exclusive_lock["held_by_job_id"]
                )

            # (b) Requesting exclusive but other jobs hold active shared locks?
            if lock_type == "exclusive":
                shared_lock = db.execute(
                    f"SELECT * FROM resource_locks "
                    f"WHERE resource_path = ? AND lock_type = 'shared' "
                    f"AND held_by_job_id != ? AND {self._active_clause()}",
                    (resource_path, job_id, now),
                ).fetchone()

                if shared_lock:
                    raise LockConflictError(
                        resource_path, shared_lock["held_by_job_id"]
                    )

            # --- insert new lock ---
            lock_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO resource_locks "
                "(id, resource_path, lock_type, held_by_job_id, held_by_node_id, "
                "acquired_at, expires_at, released_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (lock_id, resource_path, lock_type, job_id, node_id, now, expires_at),
            )
            db.commit()

            logger.info(
                "Acquired %s lock on %s for job %s (expires %s)",
                lock_type,
                resource_path,
                job_id,
                expires_at,
            )

            return ResourceLock(
                id=lock_id,
                resource_path=resource_path,
                lock_type=lock_type,
                held_by_job_id=job_id,
                held_by_node_id=node_id,
                acquired_at=now,
                expires_at=expires_at,
                released_at=None,
            )
        finally:
            db.close()

    def release(self, lock_id: str) -> bool:
        """Release a lock by its ID.  Returns ``True`` if the lock existed
        and was released, ``False`` otherwise."""
        now = self._utcnow()
        db = get_db()
        try:
            cur = db.execute(
                "UPDATE resource_locks SET released_at = ? "
                "WHERE id = ? AND released_at IS NULL",
                (now, lock_id),
            )
            db.commit()
            released = cur.rowcount > 0
            if released:
                logger.info("Released lock %s", lock_id)
            return released
        finally:
            db.close()

    def release_all(self, job_id: str) -> None:
        """Release every active lock held by *job_id*."""
        now = self._utcnow()
        db = get_db()
        try:
            cur = db.execute(
                "UPDATE resource_locks SET released_at = ? "
                "WHERE held_by_job_id = ? AND released_at IS NULL",
                (now, job_id),
            )
            db.commit()
            logger.info(
                "Released %d lock(s) for job %s", cur.rowcount, job_id
            )
        finally:
            db.close()

    def is_locked(self, resource_path: str) -> bool:
        """Return ``True`` if *resource_path* has any active lock."""
        now = self._utcnow()
        db = get_db()
        try:
            row = db.execute(
                f"SELECT 1 FROM resource_locks "
                f"WHERE resource_path = ? AND {self._active_clause()} "
                f"LIMIT 1",
                (resource_path, now),
            ).fetchone()
            return row is not None
        finally:
            db.close()

    def get_lock_holder(self, resource_path: str) -> Optional[ResourceLock]:
        """Return the active exclusive lock on *resource_path*, or ``None``."""
        now = self._utcnow()
        db = get_db()
        try:
            row = db.execute(
                f"SELECT * FROM resource_locks "
                f"WHERE resource_path = ? AND lock_type = 'exclusive' "
                f"AND {self._active_clause()}",
                (resource_path, now),
            ).fetchone()
            return ResourceLock.from_row(row) if row else None
        finally:
            db.close()

    def get_locks_for_job(self, job_id: str) -> list[ResourceLock]:
        """Return all active locks held by *job_id*."""
        now = self._utcnow()
        db = get_db()
        try:
            rows = db.execute(
                f"SELECT * FROM resource_locks "
                f"WHERE held_by_job_id = ? AND {self._active_clause()}",
                (job_id, now),
            ).fetchall()
            return [ResourceLock.from_row(r) for r in rows]
        finally:
            db.close()

    def force_release(self, lock_id: str) -> bool:
        """Admin force-release a lock regardless of ownership.

        Behaves identically to :meth:`release` but is semantically separate
        to aid auditing.
        """
        now = self._utcnow()
        db = get_db()
        try:
            cur = db.execute(
                "UPDATE resource_locks SET released_at = ? "
                "WHERE id = ? AND released_at IS NULL",
                (now, lock_id),
            )
            db.commit()
            released = cur.rowcount > 0
            if released:
                logger.warning("Force-released lock %s", lock_id)
            return released
        finally:
            db.close()

    def cleanup_expired(self) -> int:
        """Release all expired locks and return the count released."""
        now = self._utcnow()
        db = get_db()
        try:
            cur = db.execute(
                "UPDATE resource_locks SET released_at = ? "
                "WHERE released_at IS NULL AND expires_at <= ?",
                (now, now),
            )
            db.commit()
            count = cur.rowcount
            if count:
                logger.info("Cleaned up %d expired lock(s)", count)
            return count
        finally:
            db.close()

    def list_active(self) -> list[ResourceLock]:
        """Return all currently active (non-released, non-expired) locks."""
        now = self._utcnow()
        db = get_db()
        try:
            rows = db.execute(
                f"SELECT * FROM resource_locks WHERE {self._active_clause()}",
                (now,),
            ).fetchall()
            return [ResourceLock.from_row(r) for r in rows]
        finally:
            db.close()
