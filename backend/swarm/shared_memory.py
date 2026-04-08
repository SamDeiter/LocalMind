"""Key-value shared memory store scoped to job trees."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from backend.db import get_db
from backend.swarm.models import SharedMemoryEntry

logger = logging.getLogger("localmind.swarm.shared_memory")


class VersionConflictError(Exception):
    """Raised when an optimistic-concurrency version check fails on put()."""


class SharedMemoryStore:
    """Scoped key-value store backed by the swarm_shared_memory table."""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def put(
        self,
        tree_root_job_id: str,
        key: str,
        value,
        written_by_job_id: str,
        written_by_node_id: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> SharedMemoryEntry:
        """Upsert a key for *tree_root_job_id*.

        If the key already exists its version is incremented.  When
        *expected_version* is supplied and does not match the stored version a
        ``VersionConflictError`` is raised so callers can implement
        optimistic concurrency control.
        """
        now = datetime.now(timezone.utc).isoformat()
        value_json = json.dumps(value)
        conn = get_db()
        try:
            existing = conn.execute(
                "SELECT * FROM swarm_shared_memory "
                "WHERE tree_root_job_id = ? AND key = ?",
                (tree_root_job_id, key),
            ).fetchone()

            if existing is not None:
                current_version = existing["version"]
                if expected_version is not None and expected_version != current_version:
                    raise VersionConflictError(
                        f"Expected version {expected_version} for key "
                        f"'{key}' but found {current_version}"
                    )
                new_version = current_version + 1
                conn.execute(
                    "UPDATE swarm_shared_memory "
                    "SET value_json = ?, written_by_job_id = ?, "
                    "    written_by_node_id = ?, version = ?, updated_at = ? "
                    "WHERE tree_root_job_id = ? AND key = ?",
                    (
                        value_json,
                        written_by_job_id,
                        written_by_node_id,
                        new_version,
                        now,
                        tree_root_job_id,
                        key,
                    ),
                )
                conn.commit()
                entry = SharedMemoryEntry(
                    id=existing["id"],
                    tree_root_job_id=tree_root_job_id,
                    key=key,
                    value_json=value_json,
                    written_by_job_id=written_by_job_id,
                    written_by_node_id=written_by_node_id,
                    version=new_version,
                    created_at=existing["created_at"],
                    updated_at=now,
                )
            else:
                if expected_version is not None:
                    raise VersionConflictError(
                        f"Expected version {expected_version} for key "
                        f"'{key}' but key does not exist"
                    )
                entry_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO swarm_shared_memory "
                    "(id, tree_root_job_id, key, value_json, written_by_job_id, "
                    " written_by_node_id, version, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        entry_id,
                        tree_root_job_id,
                        key,
                        value_json,
                        written_by_job_id,
                        written_by_node_id,
                        now,
                        now,
                    ),
                )
                conn.commit()
                entry = SharedMemoryEntry(
                    id=entry_id,
                    tree_root_job_id=tree_root_job_id,
                    key=key,
                    value_json=value_json,
                    written_by_job_id=written_by_job_id,
                    written_by_node_id=written_by_node_id,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            logger.debug(
                "put key=%s tree=%s version=%d",
                key,
                tree_root_job_id,
                entry.version,
            )
            return entry
        finally:
            conn.close()

    def get(
        self,
        tree_root_job_id: str,
        key: str,
    ) -> Optional[SharedMemoryEntry]:
        """Return a single entry or ``None`` if the key does not exist."""
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM swarm_shared_memory "
                "WHERE tree_root_job_id = ? AND key = ?",
                (tree_root_job_id, key),
            ).fetchone()
            if row is None:
                return None
            return SharedMemoryEntry.from_row(row)
        finally:
            conn.close()

    def get_all(self, tree_root_job_id: str) -> list[SharedMemoryEntry]:
        """Return every entry belonging to *tree_root_job_id*."""
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM swarm_shared_memory "
                "WHERE tree_root_job_id = ? ORDER BY key",
                (tree_root_job_id,),
            ).fetchall()
            return [SharedMemoryEntry.from_row(r) for r in rows]
        finally:
            conn.close()

    def delete(self, tree_root_job_id: str, key: str) -> bool:
        """Delete a single key.  Returns ``True`` if the row existed."""
        conn = get_db()
        try:
            cursor = conn.execute(
                "DELETE FROM swarm_shared_memory "
                "WHERE tree_root_job_id = ? AND key = ?",
                (tree_root_job_id, key),
            )
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.debug("deleted key=%s tree=%s", key, tree_root_job_id)
            return deleted
        finally:
            conn.close()

    def get_version(
        self,
        tree_root_job_id: str,
        key: str,
    ) -> Optional[int]:
        """Return just the version number, or ``None`` if the key is absent."""
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT version FROM swarm_shared_memory "
                "WHERE tree_root_job_id = ? AND key = ?",
                (tree_root_job_id, key),
            ).fetchone()
            if row is None:
                return None
            return row["version"]
        finally:
            conn.close()

    def clear(self, tree_root_job_id: str) -> None:
        """Remove all entries for *tree_root_job_id*."""
        conn = get_db()
        try:
            conn.execute(
                "DELETE FROM swarm_shared_memory WHERE tree_root_job_id = ?",
                (tree_root_job_id,),
            )
            conn.commit()
            logger.debug("cleared all keys for tree=%s", tree_root_job_id)
        finally:
            conn.close()
