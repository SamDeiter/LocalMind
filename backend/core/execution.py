"""
Durable Execution Engine — LocalMind enterprise task worker.

Python API layer on top of the durable-execution tables defined in
backend/core/schema.py: source_events, node_attempts, tool_invocations,
worker_leases, and dead_letters.

Provides six managers:
    1. SourceEvent        — dedup incoming events (Slack, web, API)
    2. NodeAttemptTracker — per-attempt tracking for job nodes
    3. ToolInvocationLog  — every tool call within an attempt
    4. WorkerLeaseManager — worker heartbeat / lifecycle
    5. DeadLetterQueue    — permanently failed items
    6. CancellationManager— job cancellation workflow

Design constraints:
    - stdlib only (sqlite3, uuid, datetime, dataclasses, logging, pathlib, shutil)
    - All IDs use uuid.uuid4().hex
    - All timestamps use datetime.utcnow().isoformat()
    - DB pattern: sqlite3.connect(str(DB_PATH)) with WAL + busy_timeout + foreign_keys
"""

import json
import logging
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from backend.config import DB_PATH, JOBS_DIR, RECYCLE_DIR

logger = logging.getLogger("localmind.core.execution")


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Return a configured SQLite connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.utcnow().isoformat()


def _new_id() -> str:
    """Generate a 32-char hex UUID."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Dataclasses — return types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Attempt:
    """Snapshot of a single node attempt row."""
    id: str
    node_id: str
    attempt_number: int
    status: str
    started_at: str
    completed_at: Optional[str]
    input_json: Optional[str]
    output_json: Optional[str]
    error: Optional[str]
    error_category: Optional[str]
    model_used: Optional[str]
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    cost_cents: Optional[float]
    duration_ms: Optional[int]


@dataclass(frozen=True)
class Invocation:
    """Snapshot of a single tool invocation row."""
    id: str
    attempt_id: str
    tool_name: str
    args_json: str
    result_json: Optional[str]
    status: str
    policy_result: Optional[str]
    started_at: str
    completed_at: Optional[str]
    duration_ms: Optional[int]
    idempotency_key: Optional[str]


@dataclass(frozen=True)
class WorkerLease:
    """Snapshot of a single worker lease row."""
    worker_id: str
    hostname: str
    pid: int
    started_at: str
    last_heartbeat: str
    current_job_id: Optional[str]
    current_node_id: Optional[str]
    status: str
    capabilities_json: Optional[str]


@dataclass(frozen=True)
class DeadLetter:
    """Snapshot of a single dead letter row."""
    id: str
    job_id: str
    node_id: Optional[str]
    attempt_id: Optional[str]
    error: str
    error_category: str
    payload_json: Optional[str]
    created_at: str
    resolved_at: Optional[str]
    resolved_by: Optional[str]
    resolution: Optional[str]


# ---------------------------------------------------------------------------
# Helper: row -> dataclass converters
# ---------------------------------------------------------------------------

def _row_to_attempt(row: sqlite3.Row) -> Attempt:
    return Attempt(
        id=row["id"],
        node_id=row["node_id"],
        attempt_number=row["attempt_number"],
        status=row["status"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        input_json=row["input_json"],
        output_json=row["output_json"],
        error=row["error"],
        error_category=row["error_category"],
        model_used=row["model_used"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cost_cents=row["cost_cents"],
        duration_ms=row["duration_ms"],
    )


def _row_to_invocation(row: sqlite3.Row) -> Invocation:
    return Invocation(
        id=row["id"],
        attempt_id=row["attempt_id"],
        tool_name=row["tool_name"],
        args_json=row["args_json"],
        result_json=row["result_json"],
        status=row["status"],
        policy_result=row["policy_result"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        duration_ms=row["duration_ms"],
        idempotency_key=row["idempotency_key"],
    )


def _row_to_dead_letter(row: sqlite3.Row) -> DeadLetter:
    return DeadLetter(
        id=row["id"],
        job_id=row["job_id"],
        node_id=row["node_id"],
        attempt_id=row["attempt_id"],
        error=row["error"],
        error_category=row["error_category"],
        payload_json=row["payload_json"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        resolved_by=row["resolved_by"],
        resolution=row["resolution"],
    )


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


# ===========================================================================
# 1. SourceEvent — dedup incoming events (Slack, web, API)
# ===========================================================================

class SourceEvent:
    """
    Deduplicates incoming events from external sources.

    Each event is uniquely identified by (source, source_event_id). Calling
    record_event twice with the same pair returns None on the second call,
    preventing duplicate job creation from webhook retries or Slack's
    3-second retry window.
    """

    def record_event(
        self, source: str, source_event_id: str, workspace_id: str
    ) -> Optional[str]:
        """
        Record a new event. Returns the event ID if new, None if duplicate.

        Parameters
        ----------
        source : str
            Origin of the event (e.g. "slack", "web", "api").
        source_event_id : str
            The upstream-provided unique identifier for this event.
        workspace_id : str
            The workspace this event belongs to.

        Returns
        -------
        str or None
            The newly generated event ID if the event was recorded,
            or None if a duplicate already exists.
        """
        event_id = _new_id()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO source_events (id, source, source_event_id, workspace_id)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, source, source_event_id, workspace_id),
            )
            conn.commit()
            logger.info(
                "SourceEvent.record_event: new event %s (source=%s, source_event_id=%s)",
                event_id, source, source_event_id,
            )
            return event_id
        except sqlite3.IntegrityError:
            # UNIQUE(source, source_event_id) violated — duplicate
            logger.debug(
                "SourceEvent.record_event: duplicate event (source=%s, source_event_id=%s)",
                source, source_event_id,
            )
            return None
        finally:
            conn.close()

    def mark_processed(self, event_id: str, result_json: str) -> None:
        """
        Mark an event as processed with its result payload.

        Parameters
        ----------
        event_id : str
            The event ID returned by record_event.
        result_json : str
            JSON string with the processing result / outcome.
        """
        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE source_events
                SET processed_at = ?, result_json = ?
                WHERE id = ?
                """,
                (_now(), result_json, event_id),
            )
            conn.commit()
            logger.debug("SourceEvent.mark_processed: event_id=%s", event_id)
        finally:
            conn.close()


# ===========================================================================
# 2. NodeAttemptTracker — per-attempt tracking for job nodes
# ===========================================================================

class NodeAttemptTracker:
    """
    Tracks every execution attempt for each job node.

    A node may be attempted multiple times due to retries (transient errors,
    model failures, timeout extensions). Each attempt captures inputs, outputs,
    token usage, cost, and timing.
    """

    def start_attempt(
        self,
        node_id: str,
        attempt_number: int,
        input_json: Optional[str] = None,
        model_used: Optional[str] = None,
    ) -> str:
        """
        Begin a new execution attempt for a node.

        Parameters
        ----------
        node_id : str
            The job node being executed.
        attempt_number : int
            1-based attempt index (1 = first try, 2 = first retry, ...).
        input_json : str, optional
            JSON-encoded input data for this attempt.
        model_used : str, optional
            Ollama model name/tag used for this attempt.

        Returns
        -------
        str
            The newly created attempt ID.
        """
        attempt_id = _new_id()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO node_attempts
                    (id, node_id, attempt_number, status, started_at, input_json, model_used)
                VALUES (?, ?, ?, 'running', ?, ?, ?)
                """,
                (attempt_id, node_id, attempt_number, _now(), input_json, model_used),
            )
            conn.commit()
            logger.info(
                "NodeAttemptTracker.start_attempt: attempt_id=%s node_id=%s attempt=%d model=%s",
                attempt_id, node_id, attempt_number, model_used,
            )
            return attempt_id
        finally:
            conn.close()

    def complete_attempt(
        self,
        attempt_id: str,
        output_json: str,
        tokens_in: int,
        tokens_out: int,
        cost_cents: float,
        duration_ms: int,
    ) -> None:
        """
        Mark an attempt as successfully completed.

        Parameters
        ----------
        attempt_id : str
            The attempt to complete.
        output_json : str
            JSON-encoded output data produced by this attempt.
        tokens_in : int
            Number of input/prompt tokens consumed.
        tokens_out : int
            Number of output/completion tokens generated.
        cost_cents : float
            Estimated cost in cents for this attempt.
        duration_ms : int
            Wall-clock duration in milliseconds.
        """
        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE node_attempts
                SET status = 'completed',
                    completed_at = ?,
                    output_json = ?,
                    tokens_in = ?,
                    tokens_out = ?,
                    cost_cents = ?,
                    duration_ms = ?
                WHERE id = ?
                """,
                (_now(), output_json, tokens_in, tokens_out, cost_cents, duration_ms, attempt_id),
            )
            conn.commit()
            logger.info(
                "NodeAttemptTracker.complete_attempt: attempt_id=%s tokens=%d/%d cost=%.2fc duration=%dms",
                attempt_id, tokens_in, tokens_out, cost_cents, duration_ms,
            )
        finally:
            conn.close()

    def fail_attempt(
        self,
        attempt_id: str,
        error: str,
        error_category: str,
    ) -> None:
        """
        Mark an attempt as failed.

        Parameters
        ----------
        attempt_id : str
            The attempt to mark as failed.
        error : str
            Human-readable error message.
        error_category : str
            One of: transient, permanent, injection_detected, timeout,
            resource, contract, upstream, model.
        """
        valid_categories = {
            "transient", "permanent", "injection_detected", "timeout",
            "resource", "contract", "upstream", "model",
        }
        if error_category not in valid_categories:
            logger.warning(
                "NodeAttemptTracker.fail_attempt: unknown error_category %r, defaulting to 'transient'",
                error_category,
            )
            error_category = "transient"

        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE node_attempts
                SET status = 'failed',
                    completed_at = ?,
                    error = ?,
                    error_category = ?
                WHERE id = ?
                """,
                (_now(), error, error_category, attempt_id),
            )
            conn.commit()
            logger.info(
                "NodeAttemptTracker.fail_attempt: attempt_id=%s category=%s error=%s",
                attempt_id, error_category, error[:120],
            )
        finally:
            conn.close()

    def get_attempts(self, node_id: str) -> list[dict]:
        """
        Retrieve all attempts for a node, ordered by attempt number.

        Parameters
        ----------
        node_id : str
            The node to query.

        Returns
        -------
        list[dict]
            List of attempt records as plain dicts.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM node_attempts
                WHERE node_id = ?
                ORDER BY attempt_number ASC
                """,
                (node_id,),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get_latest_attempt(self, node_id: str) -> Optional[dict]:
        """
        Retrieve the most recent attempt for a node.

        Parameters
        ----------
        node_id : str
            The node to query.

        Returns
        -------
        dict or None
            The latest attempt record, or None if no attempts exist.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT * FROM node_attempts
                WHERE node_id = ?
                ORDER BY attempt_number DESC
                LIMIT 1
                """,
                (node_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()


# ===========================================================================
# 3. ToolInvocationLog — every tool call within an attempt
# ===========================================================================

class ToolInvocationLog:
    """
    Records every tool call made within a node attempt.

    Supports idempotency-key-based dedup so that retried attempts can skip
    tool calls whose results are already cached (e.g., file writes, API calls
    that should not be repeated).
    """

    def log_invocation(
        self,
        attempt_id: str,
        tool_name: str,
        args_json: str,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """
        Record the start of a tool invocation.

        Parameters
        ----------
        attempt_id : str
            The parent node attempt.
        tool_name : str
            Name of the tool being invoked (e.g. "write_file", "web_search").
        args_json : str
            JSON-encoded arguments passed to the tool.
        idempotency_key : str, optional
            A caller-provided key for dedup. If set, get_cached_result can
            retrieve the result without re-executing the tool.

        Returns
        -------
        str
            The newly created invocation ID.
        """
        invocation_id = _new_id()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO tool_invocations
                    (id, attempt_id, tool_name, args_json, status, started_at, idempotency_key)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (invocation_id, attempt_id, tool_name, args_json, _now(), idempotency_key),
            )
            conn.commit()
            logger.debug(
                "ToolInvocationLog.log_invocation: id=%s tool=%s attempt=%s idem_key=%s",
                invocation_id, tool_name, attempt_id, idempotency_key,
            )
            return invocation_id
        finally:
            conn.close()

    def update_invocation(
        self,
        invocation_id: str,
        result_json: str,
        status: str,
        policy_result: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """
        Update a tool invocation with its result.

        Parameters
        ----------
        invocation_id : str
            The invocation to update.
        result_json : str
            JSON-encoded result from the tool.
        status : str
            Final status (e.g. "success", "error", "denied", "timeout").
        policy_result : str, optional
            Result from the approval policy engine (e.g. "approved", "denied").
        duration_ms : int, optional
            Wall-clock duration of the tool invocation.
        """
        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE tool_invocations
                SET result_json = ?,
                    status = ?,
                    policy_result = ?,
                    completed_at = ?,
                    duration_ms = ?
                WHERE id = ?
                """,
                (result_json, status, policy_result, _now(), duration_ms, invocation_id),
            )
            conn.commit()
            logger.debug(
                "ToolInvocationLog.update_invocation: id=%s status=%s duration=%s",
                invocation_id, status, duration_ms,
            )
        finally:
            conn.close()

    def get_cached_result(self, idempotency_key: str) -> Optional[dict]:
        """
        Look up a completed tool invocation by its idempotency key.

        This enables retry dedup: if a previous attempt already executed a
        tool call with the same idempotency key and it succeeded, the caller
        can reuse the cached result instead of re-executing.

        Parameters
        ----------
        idempotency_key : str
            The idempotency key to search for.

        Returns
        -------
        dict or None
            The invocation record if found and completed successfully, else None.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT * FROM tool_invocations
                WHERE idempotency_key = ?
                  AND status = 'success'
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                (idempotency_key,),
            ).fetchone()
            if row:
                logger.debug(
                    "ToolInvocationLog.get_cached_result: cache hit for key=%s",
                    idempotency_key,
                )
                return _row_to_dict(row)
            return None
        finally:
            conn.close()

    def get_invocations(self, attempt_id: str) -> list[dict]:
        """
        Retrieve all tool invocations for a given attempt.

        Parameters
        ----------
        attempt_id : str
            The parent attempt to query.

        Returns
        -------
        list[dict]
            List of invocation records ordered by start time.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM tool_invocations
                WHERE attempt_id = ?
                ORDER BY started_at ASC
                """,
                (attempt_id,),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()


# ===========================================================================
# 4. WorkerLeaseManager — worker heartbeat / lifecycle
# ===========================================================================

class WorkerLeaseManager:
    """
    Manages worker registration, heartbeats, and lease lifecycle.

    Workers must acquire a lease before processing jobs. They send periodic
    heartbeats to prove liveness. Workers that stop heartbeating are detected
    by find_dead_workers and their in-progress jobs are reclaimed.
    """

    def acquire_lease(
        self,
        worker_id: str,
        hostname: str,
        pid: int,
        capabilities_json: Optional[str] = None,
    ) -> bool:
        """
        Register a worker and acquire an active lease.

        If the worker_id already exists (e.g., from a previous crash), the
        lease is re-acquired with updated metadata.

        Parameters
        ----------
        worker_id : str
            Unique identifier for this worker process.
        hostname : str
            Machine hostname where the worker is running.
        pid : int
            OS process ID of the worker.
        capabilities_json : str, optional
            JSON-encoded list of capabilities (e.g. GPU, tool access).

        Returns
        -------
        bool
            True if the lease was acquired successfully.
        """
        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO worker_leases
                    (worker_id, hostname, pid, started_at, last_heartbeat, status, capabilities_json)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    hostname = excluded.hostname,
                    pid = excluded.pid,
                    started_at = excluded.started_at,
                    last_heartbeat = excluded.last_heartbeat,
                    status = 'active',
                    capabilities_json = excluded.capabilities_json,
                    current_job_id = NULL,
                    current_node_id = NULL
                """,
                (worker_id, hostname, pid, now, now, capabilities_json),
            )
            conn.commit()
            logger.info(
                "WorkerLeaseManager.acquire_lease: worker_id=%s hostname=%s pid=%d",
                worker_id, hostname, pid,
            )
            return True
        except Exception:
            logger.exception("WorkerLeaseManager.acquire_lease: failed for worker_id=%s", worker_id)
            return False
        finally:
            conn.close()

    def heartbeat(
        self,
        worker_id: str,
        current_job_id: Optional[str] = None,
        current_node_id: Optional[str] = None,
    ) -> None:
        """
        Update the worker's heartbeat timestamp and current task info.

        Parameters
        ----------
        worker_id : str
            The worker sending the heartbeat.
        current_job_id : str, optional
            The job currently being processed, if any.
        current_node_id : str, optional
            The specific node currently being executed, if any.
        """
        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE worker_leases
                SET last_heartbeat = ?,
                    current_job_id = ?,
                    current_node_id = ?
                WHERE worker_id = ?
                  AND status = 'active'
                """,
                (_now(), current_job_id, current_node_id, worker_id),
            )
            conn.commit()
            logger.debug(
                "WorkerLeaseManager.heartbeat: worker_id=%s job=%s node=%s",
                worker_id, current_job_id, current_node_id,
            )
        finally:
            conn.close()

    def release_lease(self, worker_id: str) -> None:
        """
        Gracefully release a worker's lease (clean shutdown).

        Parameters
        ----------
        worker_id : str
            The worker releasing its lease.
        """
        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE worker_leases
                SET status = 'released',
                    current_job_id = NULL,
                    current_node_id = NULL,
                    last_heartbeat = ?
                WHERE worker_id = ?
                """,
                (_now(), worker_id),
            )
            conn.commit()
            logger.info("WorkerLeaseManager.release_lease: worker_id=%s", worker_id)
        finally:
            conn.close()

    def find_dead_workers(self, timeout_sec: int = 90) -> list[str]:
        """
        Find workers whose last heartbeat is older than the timeout threshold.

        Parameters
        ----------
        timeout_sec : int
            Number of seconds since last heartbeat before a worker is
            considered dead. Defaults to 90 seconds.

        Returns
        -------
        list[str]
            Worker IDs that have exceeded the heartbeat timeout.
        """
        cutoff = (datetime.utcnow() - timedelta(seconds=timeout_sec)).isoformat()
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT worker_id FROM worker_leases
                WHERE status = 'active'
                  AND last_heartbeat < ?
                """,
                (cutoff,),
            ).fetchall()
            dead = [r["worker_id"] for r in rows]
            if dead:
                logger.warning(
                    "WorkerLeaseManager.find_dead_workers: %d dead workers (timeout=%ds): %s",
                    len(dead), timeout_sec, dead,
                )
            return dead
        finally:
            conn.close()

    def claim_orphaned_jobs(self, dead_worker_ids: list[str]) -> list[str]:
        """
        Reassign jobs stuck on dead workers back to pending status.

        Marks the dead workers as 'dead' and sets any jobs they were
        processing back to 'pending' so they can be picked up by a
        healthy worker.

        Parameters
        ----------
        dead_worker_ids : list[str]
            Worker IDs identified as dead by find_dead_workers.

        Returns
        -------
        list[str]
            Job IDs that were reclaimed and set back to pending.
        """
        if not dead_worker_ids:
            return []

        conn = _get_conn()
        reclaimed_jobs: list[str] = []
        try:
            for wid in dead_worker_ids:
                # Get the job this worker was processing
                row = conn.execute(
                    """
                    SELECT current_job_id FROM worker_leases
                    WHERE worker_id = ?
                      AND current_job_id IS NOT NULL
                    """,
                    (wid,),
                ).fetchone()

                if row and row["current_job_id"]:
                    job_id = row["current_job_id"]
                    # Reset job to pending so another worker can pick it up
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'pending', updated_at = ?
                        WHERE id = ?
                          AND status = 'running'
                        """,
                        (_now(), job_id),
                    )
                    reclaimed_jobs.append(job_id)

                # Mark the worker as dead
                conn.execute(
                    """
                    UPDATE worker_leases
                    SET status = 'dead',
                        current_job_id = NULL,
                        current_node_id = NULL
                    WHERE worker_id = ?
                    """,
                    (wid,),
                )

            conn.commit()
            if reclaimed_jobs:
                logger.warning(
                    "WorkerLeaseManager.claim_orphaned_jobs: reclaimed %d jobs: %s",
                    len(reclaimed_jobs), reclaimed_jobs,
                )
            return reclaimed_jobs
        finally:
            conn.close()


# ===========================================================================
# 5. DeadLetterQueue — permanently failed items
# ===========================================================================

class DeadLetterQueue:
    """
    Receives items that have exhausted all retry attempts and fallback
    strategies. Dead letters are retained for manual review, retry, or skip.
    """

    def add_dead_letter(
        self,
        job_id: str,
        node_id: Optional[str],
        attempt_id: Optional[str],
        error: str,
        error_category: str,
        payload_json: Optional[str] = None,
    ) -> str:
        """
        Add a failed item to the dead letter queue.

        Parameters
        ----------
        job_id : str
            The job that failed.
        node_id : str, optional
            The specific node that failed.
        attempt_id : str, optional
            The final failed attempt ID.
        error : str
            Human-readable error description.
        error_category : str
            Error classification (transient, permanent, etc.).
        payload_json : str, optional
            JSON-encoded payload for debugging/replay.

        Returns
        -------
        str
            The newly created dead letter ID.
        """
        dl_id = _new_id()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO dead_letters
                    (id, job_id, node_id, attempt_id, error, error_category,
                     payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (dl_id, job_id, node_id, attempt_id, error, error_category,
                 payload_json, _now()),
            )
            conn.commit()
            logger.warning(
                "DeadLetterQueue.add_dead_letter: id=%s job=%s node=%s category=%s error=%s",
                dl_id, job_id, node_id, error_category, error[:120],
            )
            return dl_id
        finally:
            conn.close()

    def list_dead_letters(self, resolved: bool = False) -> list[dict]:
        """
        List dead letters, optionally filtering by resolution status.

        Parameters
        ----------
        resolved : bool
            If False (default), return only unresolved items.
            If True, return only resolved items.

        Returns
        -------
        list[dict]
            Dead letter records ordered by creation time (newest first).
        """
        conn = _get_conn()
        try:
            if resolved:
                rows = conn.execute(
                    """
                    SELECT * FROM dead_letters
                    WHERE resolved_at IS NOT NULL
                    ORDER BY created_at DESC
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM dead_letters
                    WHERE resolved_at IS NULL
                    ORDER BY created_at DESC
                    """,
                ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def resolve_dead_letter(
        self,
        dead_letter_id: str,
        resolved_by: str,
        resolution: str,
    ) -> None:
        """
        Mark a dead letter as resolved.

        Parameters
        ----------
        dead_letter_id : str
            The dead letter to resolve.
        resolved_by : str
            User ID or identifier of who resolved it.
        resolution : str
            How it was resolved: "retried", "skipped", or "manual_fix".

        Raises
        ------
        ValueError
            If resolution is not one of the allowed values.
        """
        valid_resolutions = {"retried", "skipped", "manual_fix"}
        if resolution not in valid_resolutions:
            raise ValueError(
                f"Invalid resolution {resolution!r}. Must be one of: {valid_resolutions}"
            )

        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE dead_letters
                SET resolved_at = ?,
                    resolved_by = ?,
                    resolution = ?
                WHERE id = ?
                """,
                (_now(), resolved_by, resolution, dead_letter_id),
            )
            conn.commit()
            logger.info(
                "DeadLetterQueue.resolve_dead_letter: id=%s by=%s resolution=%s",
                dead_letter_id, resolved_by, resolution,
            )
        finally:
            conn.close()


# ===========================================================================
# 6. CancellationManager — job cancellation workflow
# ===========================================================================

class CancellationManager:
    """
    Manages the job cancellation lifecycle.

    Cancellation is a two-phase process:
    1. request_cancellation sets status to 'cancelling' — the worker checks
       is_cancelling between steps and stops gracefully.
    2. complete_cancellation sets status to 'cancelled' and moves job files
       to the recycle bin.
    """

    def request_cancellation(self, job_id: str) -> None:
        """
        Request that a job be cancelled.

        Sets the job status to 'cancelling'. The worker is expected to
        check is_cancelling() between node executions and abort gracefully.

        Parameters
        ----------
        job_id : str
            The job to cancel.
        """
        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'cancelling', updated_at = ?
                WHERE id = ?
                  AND status IN ('pending', 'running')
                """,
                (_now(), job_id),
            )
            conn.commit()
            logger.info("CancellationManager.request_cancellation: job_id=%s", job_id)
        finally:
            conn.close()

    def is_cancelling(self, job_id: str) -> bool:
        """
        Check whether a cancellation has been requested for a job.

        Parameters
        ----------
        job_id : str
            The job to check.

        Returns
        -------
        bool
            True if the job status is 'cancelling'.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT status FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            return row is not None and row["status"] == "cancelling"
        finally:
            conn.close()

    def complete_cancellation(self, job_id: str) -> None:
        """
        Finalize job cancellation: mark as cancelled and move files to recycle.

        Moves the job's workspace directory (JOBS_DIR / job_id) to the
        recycle bin (RECYCLE_DIR / job_id) if it exists.

        Parameters
        ----------
        job_id : str
            The job to finalize cancellation for.
        """
        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', updated_at = ?
                WHERE id = ?
                """,
                (_now(), job_id),
            )
            conn.commit()
            logger.info("CancellationManager.complete_cancellation: job_id=%s", job_id)
        finally:
            conn.close()

        # Move job files to recycle bin
        job_dir = JOBS_DIR / job_id
        if job_dir.exists():
            recycle_dest = RECYCLE_DIR / job_id
            try:
                RECYCLE_DIR.mkdir(parents=True, exist_ok=True)
                if recycle_dest.exists():
                    # Target already exists in recycle — append a dedup suffix
                    recycle_dest = RECYCLE_DIR / f"{job_id}_{uuid.uuid4().hex[:8]}"
                shutil.move(str(job_dir), str(recycle_dest))
                logger.info(
                    "CancellationManager.complete_cancellation: moved %s -> %s",
                    job_dir, recycle_dest,
                )
            except Exception:
                logger.exception(
                    "CancellationManager.complete_cancellation: failed to move job dir %s to recycle",
                    job_dir,
                )
