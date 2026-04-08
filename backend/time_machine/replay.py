"""
time_machine/replay.py -- Replay Engine for AI Time Machine
============================================================
Provides entity restoration, entity history, and lightweight timeline
queries against the action_versions table.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.time_machine.replay")


class ReplayEngine:
    """Reads action_versions and can restore entities to a prior state."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    # ── helpers ──────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return dict(row)

    # ── restore ──────────────────────────────────────────────────────

    def restore_action(self, action_id: int) -> dict:
        """Restore an entity to its *before_state* for a given action.

        For entity_type == "file":
            Writes the before_state content back to disk at the path
            stored in entity_id.
        For all other entity types:
            Returns the before_state so the caller can decide how to
            apply it (e.g. update a DB row).

        A new "revert" action is recorded and the original action is
        marked as reverted.
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM action_versions WHERE id = ?", (action_id,)
            ).fetchone()

            if not row:
                return {"ok": False, "error": f"Action {action_id} not found"}

            action = self._row_to_dict(row)

            if action.get("reverted"):
                return {"ok": False, "error": f"Action {action_id} is already reverted"}

            before_state = action.get("before_state")
            if before_state is None:
                return {
                    "ok": False,
                    "error": f"Action {action_id} has no before_state to restore",
                }

            entity_type = action["entity_type"]
            entity_id = action["entity_id"]

            # --- For files, write the content back to disk ---
            restored_path = None
            if entity_type == "file":
                target = Path(entity_id)
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(before_state, encoding="utf-8")
                    restored_path = str(target)
                    logger.info("Restored file %s from action %s", target, action_id)
                except Exception as e:
                    return {
                        "ok": False,
                        "error": f"Failed to write file {entity_id}: {e}",
                    }

            # --- Record a new revert action ---
            now = time.time()
            conn.execute(
                """INSERT INTO action_versions
                   (action_type, timestamp, entity_type, entity_id,
                    before_state, after_state, diff, parent_id,
                    conversation_id, user_id, metadata, reverted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    "revert",
                    now,
                    entity_type,
                    entity_id,
                    action.get("after_state"),   # current state becomes before
                    before_state,                # restored state becomes after
                    None,
                    action_id,
                    action.get("conversation_id"),
                    action.get("user_id"),
                    json.dumps({"reverted_action_id": action_id}),
                ),
            )

            # --- Mark the original action as reverted ---
            conn.execute(
                "UPDATE action_versions SET reverted = 1 WHERE id = ?",
                (action_id,),
            )
            conn.commit()

            result: dict = {
                "ok": True,
                "action_id": action_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "before_state": before_state,
            }
            if restored_path:
                result["restored_path"] = restored_path

            return result

        except Exception as e:
            logger.exception("restore_action(%s) failed", action_id)
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    # ── entity history ───────────────────────────────────────────────

    def get_entity_history(
        self,
        entity_type: str,
        entity_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """Return all actions affecting a specific entity, newest first."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM action_versions
                   WHERE entity_type = ? AND entity_id = ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (entity_type, entity_id, limit),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.exception("get_entity_history failed")
            return []
        finally:
            conn.close()

    # ── timeline (lightweight) ───────────────────────────────────────

    def get_timeline(
        self,
        since: float = 0,
        limit: int = 100,
    ) -> list[dict]:
        """Lightweight timeline data for the UI slider.

        Returns only the columns needed for rendering -- no full
        before/after state blobs.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, action_type, entity_type, entity_id,
                          timestamp, reverted
                   FROM action_versions
                   WHERE timestamp >= ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (since, limit),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.exception("get_timeline failed")
            return []
        finally:
            conn.close()


# ── module-level singleton ──────────────────────────────────────────

_engine: Optional[ReplayEngine] = None


def get_replay_engine() -> ReplayEngine:
    """Return (or lazily create) the singleton ReplayEngine."""
    global _engine
    if _engine is None:
        from backend.config import DB_PATH
        _engine = ReplayEngine(str(DB_PATH))
    return _engine
