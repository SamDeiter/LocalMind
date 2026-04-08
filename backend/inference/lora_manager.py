"""
lora_manager.py — LoRA adapter registry and lifecycle management.
=================================================================

Manages a library of LoRA adapters that can be hot-swapped on top of
base models.  Adapter metadata is persisted in SQLite; adapter weight
files live on disk under ``adapters_dir``.

Integrates with :class:`GPUManager`'s ``ModelSlot.lora_id`` field for
VRAM-aware adapter swapping (<10ms overhead via S-LoRA serving).

Design references:
  - MoLoRA (Microsoft 2026): 1.7B base + 4 LoRAs beats monolithic 8B
  - S-LoRA: 2,000 concurrent adapters on one GPU
  - Unsloth: 2x faster fine-tuning, 60% less VRAM

Usage::

    mgr = LoRAManager(adapters_dir="/data/lora_adapters")
    mgr.register_adapter(LoRAAdapter(
        adapter_id="research-lora-v2",
        base_model="qwen3:8b",
        task_types=["research", "writing"],
        path="/data/lora_adapters/research-lora-v2",
        vram_overhead_mb=128,
        enabled=True,
    ))

    adapter = mgr.get_adapter(task_type="research", base_model="qwen3:8b")
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config import DB_PATH

logger = logging.getLogger("localmind.inference.lora_manager")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LoRAAdapter:
    """Describes a single LoRA adapter in the registry."""

    adapter_id: str
    base_model: str         # e.g. "qwen3:8b"
    task_types: list[str]   # e.g. ["research", "code"]
    path: str               # path to adapter weights on disk
    vram_overhead_mb: int   # additional VRAM (~50-200MB typical)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "base_model": self.base_model,
            "task_types": self.task_types,
            "path": self.path,
            "vram_overhead_mb": self.vram_overhead_mb,
            "enabled": self.enabled,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> LoRAAdapter:
        """Construct from a sqlite3.Row."""
        task_types_raw = row["task_types_json"]
        try:
            task_types = json.loads(task_types_raw) if task_types_raw else []
        except (json.JSONDecodeError, TypeError):
            task_types = []

        return cls(
            adapter_id=row["adapter_id"],
            base_model=row["base_model"],
            task_types=task_types,
            path=row["path"],
            vram_overhead_mb=int(row["vram_overhead_mb"]),
            enabled=bool(row["enabled"]),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Open a connection with standard pragmas."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create the lora_adapters table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lora_adapters (
            adapter_id      TEXT PRIMARY KEY,
            base_model      TEXT NOT NULL,
            task_types_json TEXT NOT NULL DEFAULT '[]',
            path            TEXT NOT NULL,
            vram_overhead_mb INTEGER NOT NULL DEFAULT 128,
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# LoRAManager
# ---------------------------------------------------------------------------

class LoRAManager:
    """Registry and lifecycle manager for LoRA adapters.

    Adapter metadata is stored in SQLite; adapter weight files live on
    disk.  The manager provides lookup-by-task-type so the
    :class:`ModelSelector` can pair an adapter with its base model.

    Parameters
    ----------
    adapters_dir:
        Root directory where adapter weight files are stored.  If
        ``None``, adapter path validation is skipped (useful for
        testing or when adapters are served remotely via S-LoRA).
    """

    def __init__(self, adapters_dir: str | None = None) -> None:
        self._adapters_dir = Path(adapters_dir) if adapters_dir else None
        if self._adapters_dir:
            self._adapters_dir.mkdir(parents=True, exist_ok=True)

        # Ensure DB table exists
        conn = _get_conn()
        try:
            _ensure_table(conn)
        finally:
            conn.close()

        logger.info(
            "LoRAManager initialised (adapters_dir=%s)",
            self._adapters_dir or "<none>",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_adapters(self, enabled_only: bool = False) -> list[LoRAAdapter]:
        """Return all registered adapters, optionally filtered to enabled ones."""
        conn = _get_conn()
        try:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM lora_adapters WHERE enabled = 1 ORDER BY adapter_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM lora_adapters ORDER BY adapter_id"
                ).fetchall()
            return [LoRAAdapter.from_row(r) for r in rows]
        finally:
            conn.close()

    def get_adapter(
        self,
        task_type: str,
        base_model: str,
    ) -> LoRAAdapter | None:
        """Find the best enabled adapter for a task type and base model.

        Looks for an adapter whose ``task_types`` list contains
        ``task_type`` and whose ``base_model`` matches.  Returns ``None``
        if no match is found (caller should fall back to the base model).

        Args:
            task_type:  e.g. "research", "code", "document".
            base_model: e.g. "qwen3:8b".

        Returns:
            The matching :class:`LoRAAdapter` or ``None``.
        """
        conn = _get_conn()
        try:
            # SQLite JSON search — task_types_json contains the task_type
            # We use a LIKE fallback since json_each may not be available
            # in all SQLite builds.
            rows = conn.execute(
                """SELECT * FROM lora_adapters
                   WHERE enabled = 1
                     AND base_model = ?
                     AND task_types_json LIKE ?
                   ORDER BY adapter_id
                   LIMIT 1""",
                (base_model, f'%"{task_type}"%'),
            ).fetchall()

            if rows:
                return LoRAAdapter.from_row(rows[0])
            return None
        finally:
            conn.close()

    def get_adapter_by_id(self, adapter_id: str) -> LoRAAdapter | None:
        """Look up an adapter by its unique ID."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM lora_adapters WHERE adapter_id = ?",
                (adapter_id,),
            ).fetchone()
            return LoRAAdapter.from_row(row) if row else None
        finally:
            conn.close()

    def register_adapter(self, adapter: LoRAAdapter) -> None:
        """Register (or update) an adapter in the registry.

        If an adapter with the same ``adapter_id`` already exists, it is
        updated in place (upsert).

        Args:
            adapter: The adapter to register.

        Raises:
            FileNotFoundError: If ``adapters_dir`` is set and the adapter
                path does not exist on disk.
        """
        # Validate adapter path if we have a root dir
        if self._adapters_dir and not Path(adapter.path).exists():
            raise FileNotFoundError(
                f"Adapter weights not found at: {adapter.path}"
            )

        task_types_json = json.dumps(adapter.task_types, separators=(",", ":"))

        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO lora_adapters
                       (adapter_id, base_model, task_types_json, path,
                        vram_overhead_mb, enabled)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(adapter_id) DO UPDATE SET
                       base_model       = excluded.base_model,
                       task_types_json  = excluded.task_types_json,
                       path             = excluded.path,
                       vram_overhead_mb = excluded.vram_overhead_mb,
                       enabled          = excluded.enabled,
                       updated_at       = datetime('now')""",
                (
                    adapter.adapter_id,
                    adapter.base_model,
                    task_types_json,
                    adapter.path,
                    adapter.vram_overhead_mb,
                    int(adapter.enabled),
                ),
            )
            conn.commit()
            logger.info(
                "Registered adapter '%s' (base=%s, tasks=%s, vram_overhead=%dMB)",
                adapter.adapter_id, adapter.base_model,
                adapter.task_types, adapter.vram_overhead_mb,
            )
        finally:
            conn.close()

    def remove_adapter(self, adapter_id: str) -> bool:
        """Remove an adapter from the registry.

        Does NOT delete adapter weight files from disk (use
        ``purge_adapter`` for that).

        Returns True if the adapter was found and removed.
        """
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM lora_adapters WHERE adapter_id = ?",
                (adapter_id,),
            )
            conn.commit()
            removed = cursor.rowcount > 0
            if removed:
                logger.info("Removed adapter '%s' from registry", adapter_id)
            else:
                logger.warning(
                    "Adapter '%s' not found in registry (nothing removed)",
                    adapter_id,
                )
            return removed
        finally:
            conn.close()

    def enable_adapter(self, adapter_id: str) -> bool:
        """Enable a previously disabled adapter. Returns True if updated."""
        return self._set_enabled(adapter_id, True)

    def disable_adapter(self, adapter_id: str) -> bool:
        """Disable an adapter without removing it. Returns True if updated."""
        return self._set_enabled(adapter_id, False)

    def get_adapters_for_task(self, task_type: str) -> list[LoRAAdapter]:
        """Return all enabled adapters that support the given task type.

        Unlike :meth:`get_adapter`, this returns all matches across all
        base models — useful for the model selector when it needs to
        decide both model and adapter together.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM lora_adapters
                   WHERE enabled = 1
                     AND task_types_json LIKE ?
                   ORDER BY adapter_id""",
                (f'%"{task_type}"%',),
            ).fetchall()
            return [LoRAAdapter.from_row(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_enabled(self, adapter_id: str, enabled: bool) -> bool:
        """Toggle the enabled flag for an adapter."""
        conn = _get_conn()
        try:
            cursor = conn.execute(
                """UPDATE lora_adapters
                   SET enabled = ?, updated_at = datetime('now')
                   WHERE adapter_id = ?""",
                (int(enabled), adapter_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
