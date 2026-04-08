"""Async message bus for agent-to-agent communication within a job tree."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.config import SWARM_MESSAGE_TTL_SEC
from backend.db import get_db
from backend.swarm.models import AgentMessage

logger = logging.getLogger("localmind.swarm.messaging")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


class AgentMessageBus:
    """SQLite-backed message bus for inter-agent communication.

    Messages are scoped to a job tree (identified by tree_root_job_id).
    Supports direct messages (to_job_id set) and broadcasts (to_job_id=NULL).
    """

    def __init__(self) -> None:
        pass

    # ── Core send / receive ─────────────────────────────────────────

    def send(
        self,
        tree_root_job_id: str,
        from_job_id: str,
        body,
        to_job_id: Optional[str] = None,
        message_type: str = "data",
        subject: Optional[str] = None,
    ) -> AgentMessage:
        """Send a message within a job tree.

        Args:
            tree_root_job_id: Root job of the tree this message belongs to.
            from_job_id: Job that is sending the message.
            body: JSON-serializable payload.
            to_job_id: Recipient job, or None for broadcast to entire tree.
            message_type: One of data, request, response, signal.
            subject: Optional subject line / topic tag.

        Returns:
            The persisted AgentMessage.
        """
        msg_id = _uuid()
        now = _now()
        body_json = json.dumps(body)

        conn = get_db()
        try:
            conn.execute(
                """INSERT INTO agent_messages
                   (id, tree_root_job_id, from_job_id, to_job_id,
                    message_type, subject, body_json, status, created_at, read_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)""",
                (msg_id, tree_root_job_id, from_job_id, to_job_id,
                 message_type, subject, body_json, now),
            )
            conn.commit()
            logger.debug(
                "Message %s sent: %s -> %s (type=%s, tree=%s)",
                msg_id, from_job_id, to_job_id or "BROADCAST",
                message_type, tree_root_job_id,
            )

            return AgentMessage(
                id=msg_id,
                tree_root_job_id=tree_root_job_id,
                from_job_id=from_job_id,
                to_job_id=to_job_id,
                message_type=message_type,
                subject=subject,
                body_json=body_json,
                status="pending",
                created_at=now,
                read_at=None,
            )
        finally:
            conn.close()

    def receive(
        self,
        job_id: str,
        include_broadcasts: bool = True,
    ) -> list[AgentMessage]:
        """Fetch all pending messages addressed to *job_id*.

        If *include_broadcasts* is True, also returns broadcast messages
        (to_job_id IS NULL) from any tree the job belongs to.
        Returned messages are marked as 'delivered'.

        Returns:
            List of AgentMessage objects (may be empty).
        """
        conn = get_db()
        try:
            if include_broadcasts:
                # Find tree_root_job_ids this job participates in.
                # A job can be the root itself, or a child in job_delegation.
                tree_roots = self._get_tree_roots_for_job(conn, job_id)

                if tree_roots:
                    placeholders = ",".join("?" for _ in tree_roots)
                    rows = conn.execute(
                        f"""SELECT * FROM agent_messages
                            WHERE status = 'pending'
                              AND from_job_id != ?
                              AND (
                                  to_job_id = ?
                                  OR (to_job_id IS NULL
                                      AND tree_root_job_id IN ({placeholders}))
                              )
                            ORDER BY created_at""",
                        (job_id, job_id, *tree_roots),
                    ).fetchall()
                else:
                    # Fallback: only direct messages
                    rows = conn.execute(
                        """SELECT * FROM agent_messages
                           WHERE status = 'pending'
                             AND to_job_id = ?
                             AND from_job_id != ?
                           ORDER BY created_at""",
                        (job_id, job_id),
                    ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM agent_messages
                       WHERE status = 'pending'
                         AND to_job_id = ?
                         AND from_job_id != ?
                       ORDER BY created_at""",
                    (job_id, job_id),
                ).fetchall()

            if not rows:
                return []

            messages = [AgentMessage.from_row(r) for r in rows]
            msg_ids = [m.id for m in messages]
            placeholders = ",".join("?" for _ in msg_ids)
            conn.execute(
                f"UPDATE agent_messages SET status = 'delivered' WHERE id IN ({placeholders})",
                msg_ids,
            )
            conn.commit()

            logger.debug(
                "Job %s received %d message(s)", job_id, len(messages),
            )
            return messages
        finally:
            conn.close()

    # ── Read acknowledgement ────────────────────────────────────────

    def mark_read(self, message_id: str) -> None:
        """Mark a single message as read."""
        now = _now()
        conn = get_db()
        try:
            conn.execute(
                "UPDATE agent_messages SET status = 'read', read_at = ? WHERE id = ?",
                (now, message_id),
            )
            conn.commit()
            logger.debug("Message %s marked read", message_id)
        finally:
            conn.close()

    # ── Request / Response pattern ──────────────────────────────────

    def send_request(
        self,
        tree_root_job_id: str,
        from_job_id: str,
        to_job_id: str,
        subject: str,
        body,
    ) -> AgentMessage:
        """Send a request message to a specific agent and return it.

        The caller should poll with `get_response(msg.id)` to await the reply.
        """
        return self.send(
            tree_root_job_id=tree_root_job_id,
            from_job_id=from_job_id,
            body=body,
            to_job_id=to_job_id,
            message_type="request",
            subject=subject,
        )

    def get_response(
        self,
        request_message_id: str,
        timeout_sec: float = 30,
    ) -> Optional[AgentMessage]:
        """Synchronously poll for a response to a prior request.

        Looks for a message with message_type='response' and
        subject=request_message_id.  Polls the DB every 0.5 s up to
        *timeout_sec*.  Returns the response AgentMessage or None on timeout.
        """
        deadline = time.monotonic() + timeout_sec

        while time.monotonic() < deadline:
            conn = get_db()
            try:
                row = conn.execute(
                    """SELECT * FROM agent_messages
                       WHERE message_type = 'response'
                         AND subject = ?
                       LIMIT 1""",
                    (request_message_id,),
                ).fetchone()

                if row is not None:
                    msg = AgentMessage.from_row(row)
                    logger.debug(
                        "Response %s received for request %s",
                        msg.id, request_message_id,
                    )
                    return msg
            finally:
                conn.close()

            time.sleep(0.5)

        logger.warning(
            "Timed out waiting for response to request %s (%.1fs)",
            request_message_id, timeout_sec,
        )
        return None

    def send_response(
        self,
        request_message_id: str,
        from_job_id: str,
        body,
    ) -> AgentMessage:
        """Send a response to a previous request message.

        Looks up the original request to determine the tree_root_job_id and
        the recipient (the original sender becomes the new to_job_id).
        """
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM agent_messages WHERE id = ?",
                (request_message_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise ValueError(
                f"Request message {request_message_id} not found"
            )

        original = AgentMessage.from_row(row)

        return self.send(
            tree_root_job_id=original.tree_root_job_id,
            from_job_id=from_job_id,
            body=body,
            to_job_id=original.from_job_id,
            message_type="response",
            subject=request_message_id,
        )

    # ── Tree-level queries ──────────────────────────────────────────

    def get_messages_for_tree(
        self,
        tree_root_job_id: str,
    ) -> list[AgentMessage]:
        """Return all messages in a job tree (for UI / debugging)."""
        conn = get_db()
        try:
            rows = conn.execute(
                """SELECT * FROM agent_messages
                   WHERE tree_root_job_id = ?
                   ORDER BY created_at""",
                (tree_root_job_id,),
            ).fetchall()
            return [AgentMessage.from_row(r) for r in rows]
        finally:
            conn.close()

    # ── Lifecycle / cleanup ─────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """Mark messages older than SWARM_MESSAGE_TTL_SEC as expired.

        Returns:
            Number of messages marked expired.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=SWARM_MESSAGE_TTL_SEC)
        ).isoformat()

        conn = get_db()
        try:
            cursor = conn.execute(
                """UPDATE agent_messages
                   SET status = 'expired'
                   WHERE status IN ('pending', 'delivered')
                     AND created_at < ?""",
                (cutoff,),
            )
            conn.commit()
            count = cursor.rowcount
            if count:
                logger.info("Expired %d stale message(s) (TTL=%ds)", count, SWARM_MESSAGE_TTL_SEC)
            return count
        finally:
            conn.close()

    # ── Internal helpers ────────────────────────────────────────────

    @staticmethod
    def _get_tree_roots_for_job(conn, job_id: str) -> list[str]:
        """Determine which tree_root_job_ids a job belongs to.

        Strategy:
        1. The job itself may be a tree root (appears as tree_root_job_id
           in agent_messages or as parent_job_id with no parent of its own).
        2. Walk up job_delegation to find the root ancestor(s).
        3. Fallback: check agent_messages for any tree this job already
           participates in.

        Returns a deduplicated list of tree_root_job_ids (usually just one).
        """
        roots: set[str] = set()

        # Walk the delegation chain upward to find the root.
        current = job_id
        for _ in range(50):  # safety cap to avoid infinite loops
            row = conn.execute(
                "SELECT parent_job_id FROM job_delegation WHERE child_job_id = ?",
                (current,),
            ).fetchone()
            if row is None:
                # current has no parent -> it is the root (or standalone)
                roots.add(current)
                break
            current = row["parent_job_id"]
        else:
            # Exceeded depth limit; use whatever we reached
            roots.add(current)

        # Also check agent_messages for trees this job already participates in
        # (covers the case where the job IS the tree root itself).
        rows = conn.execute(
            """SELECT DISTINCT tree_root_job_id FROM agent_messages
               WHERE from_job_id = ? OR to_job_id = ?""",
            (job_id, job_id),
        ).fetchall()
        for r in rows:
            roots.add(r["tree_root_job_id"])

        return list(roots)
