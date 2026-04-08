"""
scheduler.py — Job scheduler with GPU admission control and fair queuing.
=========================================================================

Sits between the job queue and the worker pool.  Decides **which** node
to execute next based on:

  1. GPU availability (via :class:`GPUManager`)
  2. Priority pre-emption (review/QA nodes jump the queue)
  3. Same-base-model batching (LoRA swaps are ~<10ms, avoid full reloads)
  4. Per-workspace round-robin (prevent one workspace from starving others)
  5. Starvation prevention (MAX_GPU_WAIT_SEC hard ceiling)

DB tables used:
  ``gpu_slots``         — active GPU reservations
  ``scheduler_state``   — key/value state for round-robin cursor, etc.
  ``jobs``              — reads pending/executing jobs
  ``job_nodes``         — reads pending nodes to schedule
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.config import DB_PATH, GPU_VRAM_GB
from backend.core.gpu_manager import GPUManager

logger = logging.getLogger("localmind.core.scheduler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_GPU_WAIT_SEC: int = 600
"""Hard ceiling — any node waiting longer than this is force-scheduled."""

# Base VRAM estimates (MB) by model name pattern.
# The scheduler uses these when the model registry doesn't have a value.
_VRAM_ESTIMATES: dict[str, int] = {
    "gemma3:4b": 2800,
    "gemma4:e4b": 2800,
    "gemma4:26b": 6000,
    "gemma4:31b": 7000,
    "qwen2.5-coder:7b": 4500,
    "qwen2.5-coder:14b": 8500,
    "qwen2.5-coder:32b": 18000,
    "qwen2.5-coder:70b": 40000,
    "llama3.3:70b": 40000,
    "phi4-reasoning": 8000,
}

# LoRA adapter overhead — added on top of the base estimate.
_LORA_OVERHEAD_MB: int = 256

# Priority boost for review / QA node types.
_REVIEW_PRIORITY_BOOST: int = 50

# Node title patterns that get the review priority boost.
_REVIEW_KEYWORDS: frozenset[str] = frozenset({
    "review", "qa", "quality", "check", "validate", "verify", "test",
})


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_review_node(title: str) -> bool:
    """Check if a node title looks like a review/QA step."""
    lower = title.lower()
    return any(kw in lower for kw in _REVIEW_KEYWORDS)


# ---------------------------------------------------------------------------
# JobScheduler
# ---------------------------------------------------------------------------

class JobScheduler:
    """Schedules job nodes with GPU admission control and fair queuing.

    This is a single-instance coordinator intended to be created once
    at application startup and shared across all workers.

    Parameters
    ----------
    gpu_manager:
        The shared :class:`GPUManager` instance for VRAM tracking.
    """

    def __init__(
        self,
        gpu_manager: GPUManager,
        model_selector: Any | None = None,
    ) -> None:
        self._gpu = gpu_manager
        self._model_selector = model_selector
        # Round-robin workspace cursor — persisted to ``scheduler_state``.
        self._last_workspace_idx: int = 0
        self._restore_state()
        logger.info("JobScheduler initialised (MAX_GPU_WAIT=%ds)", MAX_GPU_WAIT_SEC)

    # ------------------------------------------------------------------
    # Core scheduling
    # ------------------------------------------------------------------

    async def schedule_next(self) -> dict[str, Any] | None:
        """Pick the next job node to execute.

        Selection strategy (in order of priority):
          1. **Starvation guard** — any node waiting > MAX_GPU_WAIT_SEC
             is selected regardless of GPU state.
          2. **Same-base-model batch** — if a model is already loaded,
             prefer a node that uses the same base model (LoRA swap only).
          3. **Priority pre-emption** — review/QA nodes get a boost.
          4. **Round-robin fairness** — rotate across workspaces.
          5. **FIFO within workspace** — oldest pending node wins.

        Returns a dict describing the node to execute, or ``None`` if
        the queue is empty or GPU is fully booked.
        """
        candidates = self._fetch_pending_nodes()
        if not candidates:
            return None

        # Annotate each candidate with scheduling metadata
        scored = self._score_candidates(candidates)

        if not scored:
            return None

        # Sort: highest score first, then oldest created_at
        scored.sort(key=lambda c: (-c["score"], c["created_at"]))
        winner = scored[0]

        model_id = winner.get("model_id") or self._pick_model_for_node(winner)
        lora_id = winner.get("lora_id")
        vram_needed = self.estimate_vram(model_id, lora_id)

        # Check GPU availability (non-blocking peek)
        if not self._gpu.can_fit(vram_needed):
            # Can't schedule right now unless starvation override
            if not winner.get("starving"):
                logger.debug(
                    "No VRAM for %s (%d MB needed, %d free) — skipping",
                    winner["node_id"], vram_needed,
                    self._gpu._vram_total_mb - self._gpu._vram_allocated_mb,
                )
                return None

        # Advance the round-robin cursor
        ws_id = winner.get("workspace_id")
        if ws_id:
            self._advance_workspace_cursor(ws_id, candidates)

        logger.info(
            "Scheduled node %s (job %s, model %s, score %d)",
            winner["node_id"], winner["job_id"], model_id, winner["score"],
        )

        return {
            "node_id": winner["node_id"],
            "job_id": winner["job_id"],
            "workspace_id": ws_id,
            "model_id": model_id,
            "lora_id": lora_id,
            "vram_mb": vram_needed,
            "priority": winner.get("priority", 0),
            "title": winner.get("title", ""),
            "score": winner["score"],
        }

    def estimate_vram(self, model_id: str, lora_id: str | None = None) -> int:
        """Estimate VRAM needed for a model+LoRA combo.

        Checks the ``model_registry`` table first, then falls back to the
        built-in ``_VRAM_ESTIMATES`` lookup table.

        Args:
            model_id: Ollama model tag.
            lora_id:  Optional LoRA adapter id.

        Returns:
            Estimated VRAM in megabytes.
        """
        # Try DB model registry first
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT vram_mb FROM model_registry WHERE model_id = ? AND status = 'active'",
                (model_id,),
            ).fetchone()
            conn.close()
            if row and row["vram_mb"]:
                base_mb = int(row["vram_mb"])
                return base_mb + (_LORA_OVERHEAD_MB if lora_id else 0)
        except Exception:
            pass  # Fall through to static estimates

        # Static estimates
        base_mb = _VRAM_ESTIMATES.get(model_id, 4000)  # 4GB default
        return base_mb + (_LORA_OVERHEAD_MB if lora_id else 0)

    # ------------------------------------------------------------------
    # GPU slot persistence (mirrors in-memory GPUManager state to DB)
    # ------------------------------------------------------------------

    async def record_gpu_slot(
        self,
        worker_id: str,
        model_id: str,
        lora_id: str | None,
        vram_mb: int,
        job_id: str | None,
        node_id: str | None,
        priority: int = 0,
    ) -> str:
        """Persist an active GPU reservation to the ``gpu_slots`` table.

        Returns the generated slot_id.
        """
        slot_id = str(uuid.uuid4())
        now = _now_iso()
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO gpu_slots
                   (slot_id, worker_id, model_id, lora_id, vram_mb,
                    acquired_at, job_id, node_id, priority)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (slot_id, worker_id, model_id, lora_id, vram_mb,
                 now, job_id, node_id, priority),
            )
            conn.commit()
            logger.debug(
                "Recorded GPU slot %s (worker=%s, model=%s, %d MB)",
                slot_id, worker_id, model_id, vram_mb,
            )
        finally:
            conn.close()
        return slot_id

    async def release_gpu_slot(self, slot_id: str) -> None:
        """Remove a GPU slot record from the database."""
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM gpu_slots WHERE slot_id = ?", (slot_id,))
            conn.commit()
            logger.debug("Released GPU slot %s from DB", slot_id)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Queue depth
    # ------------------------------------------------------------------

    def get_queue_depth(self, workspace_id: str | None = None) -> int:
        """Count pending job nodes, optionally filtered by workspace.

        Args:
            workspace_id: If provided, count only nodes belonging to
                jobs in this workspace.

        Returns:
            Number of pending nodes.
        """
        conn = _get_conn()
        try:
            if workspace_id:
                row = conn.execute(
                    """SELECT COUNT(*) AS cnt
                       FROM job_nodes jn
                       JOIN jobs j ON jn.job_id = j.id
                       WHERE jn.status = 'pending'
                         AND j.workspace_id = ?""",
                    (workspace_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM job_nodes WHERE status = 'pending'"
                ).fetchone()
            return int(row["cnt"]) if row else 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Full scheduler status
    # ------------------------------------------------------------------

    async def get_scheduler_state(self) -> dict[str, Any]:
        """Return a comprehensive snapshot of the scheduler.

        Includes:
          - GPU status (loaded models, VRAM usage)
          - Queue depth (total + per-workspace)
          - Active GPU slots from DB
          - Round-robin cursor position
        """
        gpu_status = self._gpu.get_status()

        # Active DB slots
        conn = _get_conn()
        try:
            slots = [
                dict(row)
                for row in conn.execute("SELECT * FROM gpu_slots").fetchall()
            ]

            # Per-workspace queue depths
            ws_rows = conn.execute(
                """SELECT j.workspace_id, COUNT(*) AS cnt
                   FROM job_nodes jn
                   JOIN jobs j ON jn.job_id = j.id
                   WHERE jn.status = 'pending'
                   GROUP BY j.workspace_id"""
            ).fetchall()
            ws_depths = {r["workspace_id"]: r["cnt"] for r in ws_rows}
        finally:
            conn.close()

        return {
            "gpu": gpu_status,
            "queue_depth_total": self.get_queue_depth(),
            "queue_depth_by_workspace": ws_depths,
            "active_gpu_slots": slots,
            "round_robin_cursor": self._last_workspace_idx,
            "max_gpu_wait_sec": MAX_GPU_WAIT_SEC,
        }

    # ------------------------------------------------------------------
    # Internal: fetch and score candidates
    # ------------------------------------------------------------------

    def _fetch_pending_nodes(self) -> list[dict[str, Any]]:
        """Load all schedulable nodes from the database.

        Returns a list of dicts with node + job metadata.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """SELECT
                        jn.id          AS node_id,
                        jn.job_id      AS job_id,
                        jn.sequence    AS sequence,
                        jn.title       AS title,
                        jn.created_at  AS created_at,
                        j.workspace_id AS workspace_id,
                        j.priority     AS priority,
                        j.status       AS job_status
                   FROM job_nodes jn
                   JOIN jobs j ON jn.job_id = j.id
                   WHERE jn.status = 'pending'
                     AND j.status IN ('pending', 'executing')
                   ORDER BY j.priority DESC, jn.sequence ASC
                   LIMIT 200"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _score_candidates(
        self, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Assign a numeric score to each candidate for ranking.

        Scoring components:
          - Base priority from the job                (+priority)
          - Review/QA node boost                      (+50)
          - Same base model already loaded             (+30)
          - Round-robin workspace match                (+20)
          - Starvation override                        (+1000)
        """
        loaded_models = set(self._gpu._loaded_models.keys())
        loaded_base_models = {k.split(":")[0] for k in loaded_models}

        workspace_ids = list({c["workspace_id"] for c in candidates})
        next_ws = (
            workspace_ids[self._last_workspace_idx % len(workspace_ids)]
            if workspace_ids
            else None
        )

        now_mono = time.monotonic()
        scored: list[dict[str, Any]] = []

        for c in candidates:
            score = c.get("priority", 0)

            # Review/QA boost
            if _is_review_node(c.get("title", "")):
                score += _REVIEW_PRIORITY_BOOST

            # Same-base-model batching
            model_id = self._pick_model_for_node(c)
            c["model_id"] = model_id
            c["lora_id"] = None  # Will be populated by the executor if needed
            base_model = model_id.split(":")[0] if model_id else ""
            if base_model in loaded_base_models:
                score += 30

            # Round-robin fairness
            if c.get("workspace_id") == next_ws:
                score += 20

            # Starvation prevention — check how long this node has been
            # waiting.  We use created_at as a proxy (nodes are created
            # when the planner runs).
            try:
                created_dt = datetime.fromisoformat(c["created_at"])
                age_sec = (datetime.now(timezone.utc) - created_dt).total_seconds()
            except (KeyError, ValueError, TypeError):
                age_sec = 0

            if age_sec > MAX_GPU_WAIT_SEC:
                score += 1000
                c["starving"] = True
                logger.warning(
                    "Node %s has been waiting %.0fs (> %ds) — starvation boost applied",
                    c["node_id"], age_sec, MAX_GPU_WAIT_SEC,
                )
            else:
                c["starving"] = False

            c["score"] = score
            scored.append(c)

        return scored

    def _pick_model_for_node(self, node: dict[str, Any]) -> str:
        """Choose a model for *node* based on job priority / node type.

        When a :class:`ModelSelector` is available, delegates to it for
        richer task-type-aware selection.  Otherwise falls back to the
        simple priority heuristic.
        """
        # Delegate to ModelSelector if available
        if self._model_selector is not None:
            try:
                job_dict = {"priority": node.get("priority", 0)}
                selection = self._model_selector.select_model(node, job_dict)
                return selection.model_id
            except Exception as exc:
                logger.warning(
                    "ModelSelector failed in scheduler, falling back: %s", exc
                )

        from backend.config import MODEL_TIERS

        priority = node.get("priority", 0)

        if priority >= 8:
            return MODEL_TIERS.get("heavy", "qwen2.5-coder:32b")
        if priority >= 5 or _is_review_node(node.get("title", "")):
            return MODEL_TIERS.get("medium", "qwen2.5-coder:14b")
        return MODEL_TIERS.get("light", "gemma4:e4b")

    # ------------------------------------------------------------------
    # Internal: round-robin and state persistence
    # ------------------------------------------------------------------

    def _advance_workspace_cursor(
        self,
        ws_id: str,
        candidates: list[dict[str, Any]],
    ) -> None:
        """Move the round-robin cursor past *ws_id*."""
        workspace_ids = list(dict.fromkeys(c["workspace_id"] for c in candidates))
        try:
            idx = workspace_ids.index(ws_id)
            self._last_workspace_idx = (idx + 1) % len(workspace_ids)
        except ValueError:
            self._last_workspace_idx = 0
        self._persist_state()

    def _persist_state(self) -> None:
        """Save scheduler cursor / metadata to the ``scheduler_state`` table."""
        now = _now_iso()
        state = json.dumps({
            "last_workspace_idx": self._last_workspace_idx,
        })
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO scheduler_state (key, value_json, updated_at)
                   VALUES ('cursor', ?, ?)
                   ON CONFLICT(key) DO UPDATE
                       SET value_json = excluded.value_json,
                           updated_at = excluded.updated_at""",
                (state, now),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("Failed to persist scheduler state: %s", exc)
        finally:
            conn.close()

    def _restore_state(self) -> None:
        """Load the round-robin cursor from the database (best-effort)."""
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT value_json FROM scheduler_state WHERE key = 'cursor'"
            ).fetchone()
            conn.close()
            if row:
                data = json.loads(row["value_json"])
                self._last_workspace_idx = int(data.get("last_workspace_idx", 0))
                logger.debug(
                    "Restored scheduler state: cursor=%d",
                    self._last_workspace_idx,
                )
        except Exception as exc:
            logger.debug("No prior scheduler state found: %s", exc)
            self._last_workspace_idx = 0
