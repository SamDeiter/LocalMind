"""
SQLite connection helper + shared utilities for the scheduler package.

The scheduled_missions table DDL is owned by backend/core/schema.py — the
schema agent adds it in a parallel pass.  This module only reads/writes rows;
it never creates the table.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.config import DB_PATH


# ── timestamp helpers (match backend/core/schema.py style, Z-suffixed) ─────
def utc_now_iso() -> str:
    """ISO 8601 UTC timestamp with Z suffix.

    backend/core/schema.py uses datetime.now(timezone.utc).isoformat() which
    yields '+00:00'; the scheduler spec requires a 'Z' suffix for consistency
    with the scheduled_missions timestamp columns.  We normalize here.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ── connection factory ─────────────────────────────────────────────────────
def connect() -> sqlite3.Connection:
    """Open a configured sqlite3 connection to the LocalMind DB.

    Callers are responsible for commit() and close().  Row factory yields
    sqlite3.Row so columns are addressable by name.
    """
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── row adapter ────────────────────────────────────────────────────────────
SCHEDULED_MISSION_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "cron_expr",
    "goal_text",
    "enabled",
    "timezone",
    "last_run_at",
    "next_run_at",
    "last_status",
    "last_error",
    "created_at",
    "updated_at",
)


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """Convert a sqlite3.Row from scheduled_missions into a plain dict.

    Returns None if row is None.  Converts the `enabled` integer column
    into a Python bool for convenience at the CRUD boundary.
    """
    if row is None:
        return None
    out: Dict[str, Any] = {col: row[col] for col in SCHEDULED_MISSION_COLUMNS}
    out["enabled"] = bool(out["enabled"])
    return out


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [d for d in (row_to_dict(r) for r in rows) if d is not None]
