"""DelegationEngine — multi-agent job delegation for the LocalMind swarm."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from backend.config import MAX_CHILD_JOBS_PER_PARENT, MAX_DELEGATION_DEPTH
from backend.db import get_db
from backend.swarm.models import Delegation

logger = logging.getLogger("localmind.swarm.delegation")


class DelegationEngine:
    """Manages parent-child job delegation trees.

    Every spawned child job creates two records: a row in the ``jobs`` table
    (the actual work item) and a row in ``job_delegation`` (the relationship
    between parent and child).  The engine enforces fan-out and depth limits
    so runaway recursion cannot exhaust resources.
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def spawn_child_job(
        self,
        parent_job_id: str,
        title: str,
        description: str,
        delegation_type: str = "sub_task",
        context: Optional[dict] = None,
        priority: int = 5,
    ) -> Delegation:
        """Create a child job and its delegation record.

        Raises ``ValueError`` when:
        * The parent job does not exist.
        * The parent already has ``MAX_CHILD_JOBS_PER_PARENT`` children.
        * Spawning the child would exceed ``MAX_DELEGATION_DEPTH``.
        """
        conn = get_db()
        try:
            # --- guard: parent must exist ---
            parent = conn.execute(
                "SELECT id FROM jobs WHERE id = ?", (parent_job_id,)
            ).fetchone()
            if parent is None:
                raise ValueError(f"Parent job {parent_job_id!r} does not exist")

            # --- guard: fan-out limit ---
            child_count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM job_delegation WHERE parent_job_id = ?",
                (parent_job_id,),
            ).fetchone()["cnt"]
            if child_count >= MAX_CHILD_JOBS_PER_PARENT:
                raise ValueError(
                    f"Parent job {parent_job_id!r} already has {child_count} children "
                    f"(max {MAX_CHILD_JOBS_PER_PARENT})"
                )

            # --- guard: depth limit ---
            depth = self.get_depth(parent_job_id)
            if depth + 1 >= MAX_DELEGATION_DEPTH:
                raise ValueError(
                    f"Spawning a child under {parent_job_id!r} would reach depth "
                    f"{depth + 1} (max {MAX_DELEGATION_DEPTH})"
                )

            # --- create child job ---
            child_job_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()

            conn.execute(
                "INSERT INTO jobs (id, title, description, status, priority, created_at) "
                "VALUES (?, ?, ?, 'queued', ?, ?)",
                (child_job_id, title, description, priority, now),
            )

            # --- create delegation record ---
            delegation = Delegation(
                id=str(uuid.uuid4()),
                parent_job_id=parent_job_id,
                child_job_id=child_job_id,
                delegation_type=delegation_type,
                context_json=json.dumps(context) if context is not None else None,
                status="active",
                created_at=now,
            )

            conn.execute(
                "INSERT INTO job_delegation "
                "(id, parent_job_id, child_job_id, delegation_type, context_json, "
                "status, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    delegation.id,
                    delegation.parent_job_id,
                    delegation.child_job_id,
                    delegation.delegation_type,
                    delegation.context_json,
                    delegation.status,
                    delegation.created_at,
                    delegation.completed_at,
                ),
            )

            conn.commit()
            logger.info(
                "Spawned child job %s under parent %s (type=%s, depth=%d)",
                child_job_id,
                parent_job_id,
                delegation_type,
                depth + 1,
            )
            return delegation
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_children(self, job_id: str) -> list[Delegation]:
        """Return all direct child delegation records for *job_id*."""
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM job_delegation WHERE parent_job_id = ? "
                "ORDER BY created_at",
                (job_id,),
            ).fetchall()
            return [Delegation.from_row(r) for r in rows]
        finally:
            conn.close()

    def get_parent(self, job_id: str) -> Optional[Delegation]:
        """Return the delegation record where *job_id* is the child, or ``None``."""
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM job_delegation WHERE child_job_id = ?",
                (job_id,),
            ).fetchone()
            return Delegation.from_row(row) if row else None
        finally:
            conn.close()

    def get_tree_root(self, job_id: str) -> str:
        """Walk up the parent chain and return the root job ID.

        If *job_id* has no parent it is already the root.
        """
        current = job_id
        visited: set[str] = set()
        conn = get_db()
        try:
            while True:
                if current in visited:
                    logger.warning("Cycle detected in delegation tree at %s", current)
                    return current
                visited.add(current)

                row = conn.execute(
                    "SELECT parent_job_id FROM job_delegation WHERE child_job_id = ?",
                    (current,),
                ).fetchone()
                if row is None:
                    return current
                current = row["parent_job_id"]
        finally:
            conn.close()

    def get_depth(self, job_id: str) -> int:
        """Return the depth of *job_id* in its delegation tree (root = 0)."""
        depth = 0
        current = job_id
        visited: set[str] = set()
        conn = get_db()
        try:
            while True:
                if current in visited:
                    logger.warning("Cycle detected in delegation tree at %s", current)
                    return depth
                visited.add(current)

                row = conn.execute(
                    "SELECT parent_job_id FROM job_delegation WHERE child_job_id = ?",
                    (current,),
                ).fetchone()
                if row is None:
                    return depth
                current = row["parent_job_id"]
                depth += 1
        finally:
            conn.close()

    def get_full_tree(self, root_job_id: str) -> dict:
        """Build a recursive tree structure starting from *root_job_id*.

        Returns a dict of the form::

            {
                "job_id": "...",
                "status": "...",
                "title": "...",
                "delegation_type": None,   # root has no delegation type
                "children": [ ... ]        # same shape, recursively
            }
        """
        conn = get_db()
        try:
            return self._build_subtree(conn, root_job_id, delegation_type=None)
        finally:
            conn.close()

    def _build_subtree(
        self, conn, job_id: str, delegation_type: Optional[str]
    ) -> dict:
        job_row = conn.execute(
            "SELECT id, title, status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()

        node: dict = {
            "job_id": job_id,
            "status": job_row["status"] if job_row else "unknown",
            "title": job_row["title"] if job_row else "",
            "delegation_type": delegation_type,
            "children": [],
        }

        child_rows = conn.execute(
            "SELECT child_job_id, delegation_type FROM job_delegation "
            "WHERE parent_job_id = ? ORDER BY created_at",
            (job_id,),
        ).fetchall()

        for child_row in child_rows:
            node["children"].append(
                self._build_subtree(
                    conn,
                    child_row["child_job_id"],
                    child_row["delegation_type"],
                )
            )

        return node

    # ------------------------------------------------------------------
    # State mutations
    # ------------------------------------------------------------------

    def cancel_subtree(self, job_id: str) -> None:
        """Cascade-cancel *job_id* and all its descendants.

        Updates both the ``jobs`` table (status -> 'cancelled') and the
        ``job_delegation`` table (status -> 'cancelled') for every node in
        the subtree.
        """
        conn = get_db()
        try:
            self._cancel_recursive(conn, job_id)
            conn.commit()
            logger.info("Cancelled subtree rooted at %s", job_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _cancel_recursive(self, conn, job_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()

        # Cancel this job
        conn.execute(
            "UPDATE jobs SET status = 'cancelled' WHERE id = ? "
            "AND status NOT IN ('completed', 'failed', 'cancelled')",
            (job_id,),
        )

        # Cancel the delegation record pointing to this job (if any)
        conn.execute(
            "UPDATE job_delegation SET status = 'cancelled', completed_at = ? "
            "WHERE child_job_id = ? AND status = 'active'",
            (now, job_id),
        )

        # Recurse into children
        children = conn.execute(
            "SELECT child_job_id FROM job_delegation WHERE parent_job_id = ?",
            (job_id,),
        ).fetchall()
        for child in children:
            self._cancel_recursive(conn, child["child_job_id"])

    def mark_completed(self, child_job_id: str, status: str = "completed") -> None:
        """Mark the delegation record for *child_job_id* as finished.

        ``status`` should be one of ``completed``, ``failed``, or
        ``cancelled``.
        """
        if status not in ("completed", "failed", "cancelled"):
            raise ValueError(f"Invalid delegation status: {status!r}")

        now = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        try:
            result = conn.execute(
                "UPDATE job_delegation SET status = ?, completed_at = ? "
                "WHERE child_job_id = ? AND status = 'active'",
                (status, now, child_job_id),
            )
            if result.rowcount == 0:
                logger.warning(
                    "No active delegation found for child_job_id=%s", child_job_id
                )
            conn.commit()
            logger.info(
                "Marked delegation for child %s as %s", child_job_id, status
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def is_tree_complete(self, root_job_id: str) -> bool:
        """Return ``True`` if every job in the tree rooted at *root_job_id*
        has a terminal status (``completed``, ``failed``, or ``cancelled``).
        """
        conn = get_db()
        try:
            return self._check_complete(conn, root_job_id)
        finally:
            conn.close()

    def _check_complete(self, conn, job_id: str) -> bool:
        terminal = {"completed", "failed", "cancelled"}

        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return True  # missing job treated as terminal
        if row["status"] not in terminal:
            return False

        children = conn.execute(
            "SELECT child_job_id FROM job_delegation WHERE parent_job_id = ?",
            (job_id,),
        ).fetchall()
        return all(
            self._check_complete(conn, child["child_job_id"]) for child in children
        )
