"""
ActionRecorder — records every AI action into the action_versions table
so users can browse, inspect, and revert any change the AI ever made.

Usage:
    from backend.time_machine.recorder import get_recorder
    recorder = get_recorder()
    action_id = recorder.record_file_edit("/path/to/file", old, new)
"""

import difflib
import json
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger("localmind.time_machine.recorder")


class ActionRecorder:
    """Records AI actions into the ``action_versions`` SQLite table.

    Each instance is lightweight — it stores only the DB path and opens
    a short-lived connection per operation (no persistent connection).
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    # ── helpers ─────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return dict(row)

    # ── core recording ──────────────────────────────────────────────

    def record(
        self,
        action_type: str,
        entity_type: str,
        entity_id: str,
        before_state: str | None = None,
        after_state: str | None = None,
        diff: str | None = None,
        parent_id: int | None = None,
        conversation_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Insert an action version row and return its ``id``."""
        meta_json = json.dumps(metadata) if metadata is not None else None
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO action_versions
                    (action_type, timestamp, entity_type, entity_id,
                     before_state, after_state, diff,
                     parent_id, conversation_id, user_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_type,
                    time.time(),
                    entity_type,
                    entity_id,
                    before_state,
                    after_state,
                    diff,
                    parent_id,
                    conversation_id,
                    user_id,
                    meta_json,
                ),
            )
            conn.commit()
            row_id: int = cur.lastrowid  # type: ignore[assignment]
            logger.debug(
                "Recorded action %d: %s on %s/%s",
                row_id, action_type, entity_type, entity_id,
            )
            return row_id
        finally:
            conn.close()

    # ── convenience wrappers ────────────────────────────────────────

    def record_file_edit(
        self,
        file_path: str,
        before_content: str | None,
        after_content: str | None,
        conversation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record a file edit, automatically computing a unified diff."""
        before_lines = (before_content or "").splitlines(keepends=True)
        after_lines = (after_content or "").splitlines(keepends=True)
        unified = "".join(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
            )
        )
        return self.record(
            action_type="file_edit",
            entity_type="file",
            entity_id=file_path,
            before_state=before_content,
            after_state=after_content,
            diff=unified or None,
            conversation_id=conversation_id,
            metadata=metadata,
        )

    def record_proposal_execution(
        self,
        proposal_id: str,
        proposal_data: dict[str, Any],
        result: dict[str, Any],
        conversation_id: str | None = None,
    ) -> int:
        """Record the execution of an autonomy proposal."""
        return self.record(
            action_type="proposal_execute",
            entity_type="proposal",
            entity_id=proposal_id,
            before_state=json.dumps(proposal_data),
            after_state=json.dumps(result),
            conversation_id=conversation_id,
            metadata={
                "title": proposal_data.get("title", ""),
                "task_type": proposal_data.get("task_type", ""),
                "priority": proposal_data.get("priority", ""),
            },
        )

    # ── queries ─────────────────────────────────────────────────────

    def get_action(self, action_id: int) -> dict | None:
        """Retrieve a single action version by id."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM action_versions WHERE id = ?", (action_id,)
            ).fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def list_actions(
        self,
        limit: int = 50,
        offset: int = 0,
        entity_type: str | None = None,
        since: float | None = None,
    ) -> list[dict]:
        """List action versions with optional filtering."""
        clauses: list[str] = []
        params: list[Any] = []

        if entity_type is not None:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT * FROM action_versions
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── mutations ───────────────────────────────────────────────────

    def mark_reverted(self, action_id: int) -> bool:
        """Flag an action as reverted. Returns True if the row existed."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE action_versions SET reverted = 1 WHERE id = ?",
                (action_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ── module-level singleton ──────────────────────────────────────────

_recorder: ActionRecorder | None = None


def get_recorder() -> ActionRecorder:
    """Lazily create and return the module-level ActionRecorder singleton."""
    global _recorder
    if _recorder is None:
        from backend.config import DB_PATH

        _recorder = ActionRecorder(str(DB_PATH))
    return _recorder
