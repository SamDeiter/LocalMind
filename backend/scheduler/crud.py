"""
Pure-SQLite CRUD for the scheduled_missions table.

Validation is enforced at this layer (not at fire time):
  - cron_expr    → croniter.croniter.is_valid
  - timezone     → zoneinfo.ZoneInfo(tz)  (reject invalid IANA names)

The scheduled_missions table DDL lives in backend/core/schema.py and is
added by the schema agent in a parallel pass; this module assumes the
table exists.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from croniter import croniter

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9 fallback (should not occur)
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore

from .store import connect, row_to_dict, rows_to_dicts, utc_now_iso


# ── validation helpers ─────────────────────────────────────────────────────
def _validate_cron(cron_expr: str) -> None:
    if not isinstance(cron_expr, str) or not cron_expr.strip():
        raise ValueError("cron_expr must be a non-empty string")
    if not croniter.is_valid(cron_expr):
        raise ValueError(f"Invalid cron expression: {cron_expr!r}")


def _validate_timezone(tz: str) -> None:
    if not isinstance(tz, str) or not tz.strip():
        raise ValueError("timezone must be a non-empty IANA string")
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid IANA timezone: {tz!r}") from exc


# ── read operations ────────────────────────────────────────────────────────
def list_missions() -> List[Dict[str, Any]]:
    """Return every scheduled_missions row as a dict (enabled & disabled)."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_missions ORDER BY created_at ASC"
        ).fetchall()
        return rows_to_dicts(rows)
    finally:
        conn.close()


def list_enabled_missions() -> List[Dict[str, Any]]:
    """Return only enabled rows — used by the scheduler startup reconciliation."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_missions WHERE enabled = 1 ORDER BY created_at ASC"
        ).fetchall()
        return rows_to_dicts(rows)
    finally:
        conn.close()


def get_mission(mission_id: str) -> Optional[Dict[str, Any]]:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM scheduled_missions WHERE id = ?",
            (mission_id,),
        ).fetchone()
        return row_to_dict(row)
    finally:
        conn.close()


# ── write operations ───────────────────────────────────────────────────────
def create_mission(
    name: str,
    cron: str,
    goal: str,
    tz: str = "UTC",
    enabled: bool = True,
) -> Dict[str, Any]:
    """Insert a new scheduled mission row.  Validates cron + timezone.

    Returns the full row as a dict.  Raises ValueError on validation failure.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("goal must be a non-empty string")

    _validate_cron(cron)
    _validate_timezone(tz)

    mission_id = str(uuid.uuid4())
    now = utc_now_iso()

    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO scheduled_missions (
                id, name, cron_expr, goal_text, enabled, timezone,
                last_run_at, next_run_at, last_status, last_error,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (
                mission_id,
                name,
                cron,
                goal,
                1 if enabled else 0,
                tz,
                now,
                now,
            ),
        )
    finally:
        conn.close()

    created = get_mission(mission_id)
    assert created is not None  # just inserted
    return created


# Whitelist of columns that can be updated via update_mission().
# Excludes id (immutable) and created_at (immutable); updated_at is
# always refreshed by this function.
_UPDATABLE_COLUMNS: frozenset[str] = frozenset({
    "name",
    "cron_expr",
    "goal_text",
    "enabled",
    "timezone",
    "last_run_at",
    "next_run_at",
    "last_status",
    "last_error",
})


def update_mission(mission_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Update any subset of allowed fields on a scheduled mission.

    Validates cron/timezone if those fields are being changed.  Returns the
    refreshed row, or None if the row does not exist.
    """
    if not fields:
        return get_mission(mission_id)

    unknown = set(fields.keys()) - _UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"Cannot update unknown/immutable fields: {sorted(unknown)}")

    if "cron_expr" in fields:
        _validate_cron(fields["cron_expr"])
    if "timezone" in fields:
        _validate_timezone(fields["timezone"])

    # Normalize enabled bool → int for storage
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0

    # Ensure the row exists before attempting update (so we can return None
    # cleanly for a miss instead of a silent no-op).
    existing = get_mission(mission_id)
    if existing is None:
        return None

    fields["updated_at"] = utc_now_iso()

    columns = list(fields.keys())
    set_clause = ", ".join(f"{col} = ?" for col in columns)
    values = [fields[col] for col in columns]
    values.append(mission_id)

    conn = connect()
    try:
        conn.execute(
            f"UPDATE scheduled_missions SET {set_clause} WHERE id = ?",
            values,
        )
    finally:
        conn.close()

    return get_mission(mission_id)


def delete_mission(mission_id: str) -> bool:
    """Remove a scheduled mission row.  Returns True if a row was deleted."""
    conn = connect()
    try:
        cur = conn.execute(
            "DELETE FROM scheduled_missions WHERE id = ?",
            (mission_id,),
        )
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_run(
    mission_id: str,
    status: str,
    error: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Record the result of a fire.  Updates last_run_at, last_status, last_error.

    Called from the APScheduler done-callback after the AutonomyEngine mission
    task completes (success or failure).  Does NOT touch `enabled` — a failed
    run does not auto-disable the trigger; that's an operator decision.
    """
    return update_mission(
        mission_id,
        last_run_at=utc_now_iso(),
        last_status=status,
        last_error=error,
    )
