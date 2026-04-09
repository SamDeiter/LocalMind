"""
Train Orchestrator / Worker Loop — LocalMind enterprise task worker.

The JobWorker is the main background loop that picks up pending jobs and runs
them through the full plan → execute → review pipeline, emitting progress
events and handling failures.

"Train" metaphor:
  planner (engine)  →  node executor (cars)  →  reviewer (caboose)

Circuit breaker:
  3 consecutive job failures → pause 5 minutes before picking up more work.

Resumability:
  On startup the worker re-queues any jobs left in 'executing' status from a
  prior crash, resuming from the last completed node.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Awaitable

from backend.config import OLLAMA_BASE_URL, JOBS_DIR, MAX_JOB_TIMEOUT_SEC
from backend.jobs.models import Job, Node, JobStatus, NodeStatus
from backend.jobs.queue import JobQueue
from backend.jobs.planner import JobPlanner
from backend.jobs.executor import NodeExecutor
from backend.jobs.reviewer import JobReviewer
from backend.autonomy.services.reflection_service import ReflectionService
from backend.swarm.delegation import DelegationEngine
from backend.swarm.shared_memory import SharedMemoryStore

logger = logging.getLogger("localmind.jobs.worker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Seconds between main loop polls when idle.
_POLL_INTERVAL_SEC: float = 2.0

# Circuit breaker: consecutive failures before pausing.
_CIRCUIT_BREAKER_THRESHOLD: int = 3

# How long (seconds) to pause after the circuit opens.
_CIRCUIT_OPEN_PAUSE_SEC: float = 300.0  # 5 minutes

# Maximum review+re-plan cycles before giving up (fallback; job.max_reviews is
# the primary source of truth).
_ABSOLUTE_MAX_REVIEWS: int = 5


# ---------------------------------------------------------------------------
# JobWorker
# ---------------------------------------------------------------------------


class JobWorker:
    """Background worker that drives the full job lifecycle.

    Parameters
    ----------
    tool_registry:
        A ``ToolRegistry`` instance providing tool schemas and execution.
    ollama_url:
        Ollama API base URL.  Defaults to ``config.OLLAMA_BASE_URL``.
    activity_callback:
        Optional ``async fn(event_type: str, data: dict)`` called on every
        meaningful state transition.  Used to push SSE / Slack notifications.
    """

    def __init__(
        self,
        tool_registry: Any,
        ollama_url: str | None = None,
        activity_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        model_selector: Any | None = None,
        best_of_n_sampler: Any | None = None,
    ) -> None:
        self.queue = JobQueue()
        self.planner = JobPlanner(tool_registry)
        self.executor = NodeExecutor(
            tool_registry,
            ollama_url or OLLAMA_BASE_URL,
            model_selector=model_selector,
            best_of_n_sampler=best_of_n_sampler,
        )
        self.reflection = ReflectionService(ollama_url or OLLAMA_BASE_URL)
        self.reviewer = JobReviewer(tool_registry, reflection_service=self.reflection)
        self._activity_callback = activity_callback
        self._delegation_engine = DelegationEngine()
        self._shared_memory = SharedMemoryStore()

        self._running: bool = False
        self._current_job_id: str | None = None

        # Circuit breaker state.
        self._consecutive_failures: int = 0
        self._circuit_open_until: float = 0.0  # epoch seconds

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the worker loop.

        Before entering the main poll loop, resumes any jobs that were
        interrupted mid-execution (status='executing') from a prior crash.
        """
        self._running = True
        logger.info("JobWorker starting.")

        # Resume interrupted jobs first so we don't abandon prior work.
        try:
            interrupted = self.queue.get_interrupted_jobs()
        except Exception as exc:
            logger.error("Could not fetch interrupted jobs: %s", exc)
            interrupted = []

        for job in interrupted:
            if not self._running:
                break
            await self._resume_job(job)

        # Main loop.
        logger.info("JobWorker entering main poll loop.")
        while self._running:
            await self._poll_and_execute()
            await asyncio.sleep(_POLL_INTERVAL_SEC)

        logger.info("JobWorker stopped.")

    async def stop(self) -> None:
        """Request graceful shutdown.

        The worker will finish the current node, then exit the loop.
        """
        logger.info("JobWorker stop requested.")
        self._running = False

    # ------------------------------------------------------------------
    # Poll
    # ------------------------------------------------------------------

    async def _poll_and_execute(self) -> None:
        """Pick the next pending job (any workspace) and run it."""
        # Circuit breaker guard.
        if self._circuit_open_until > time.monotonic():
            remaining = self._circuit_open_until - time.monotonic()
            logger.debug(
                "Circuit breaker open; %.0f seconds remaining before retry.", remaining
            )
            return

        job = self._dequeue_next_pending()
        if job is None:
            return

        self._current_job_id = job.id
        try:
            await self._run_job(job)
        except asyncio.CancelledError:
            logger.warning("Worker task cancelled while processing job %s.", job.id)
            raise
        except Exception as exc:
            logger.exception("Unhandled exception processing job %s: %s", job.id, exc)
            await self._handle_failure(job, str(exc))
        finally:
            self._current_job_id = None

    def _dequeue_next_pending(self) -> Job | None:
        """Return and claim the next 'pending' job across all workspaces, or None."""
        try:
            # Scan across all workspaces — JobQueue.list_jobs requires workspace_id,
            # so we query the DB directly via a raw helper on the queue object.
            # JobQueue exposes no global pending query, so we use get_interrupted_jobs'
            # sister logic: fetch the oldest pending job via direct conn.
            from backend.config import DB_PATH
            import sqlite3

            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = ?
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                    """,
                    (JobStatus.PENDING.value,),
                ).fetchone()
                if row is None:
                    return None
                from backend.jobs.models import Job as _Job
                job = _Job.from_row(row)
            finally:
                conn.close()

            # Atomically claim the job by transitioning it to 'planning'.
            # If another worker grabbed it between our SELECT and UPDATE the
            # status update is a no-op and we move on.
            from backend.config import DB_PATH as _DB
            conn2 = sqlite3.connect(str(_DB))
            conn2.row_factory = sqlite3.Row
            conn2.execute("PRAGMA journal_mode=WAL")
            conn2.execute("PRAGMA busy_timeout=5000")
            try:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).isoformat()
                cur = conn2.execute(
                    """
                    UPDATE jobs
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (JobStatus.PLANNING.value, now, job.id, JobStatus.PENDING.value),
                )
                conn2.commit()
                if cur.rowcount == 0:
                    # Race: another worker claimed it.
                    return None
            finally:
                conn2.close()

            return job

        except Exception as exc:
            logger.error("Error dequeuing next pending job: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Full job pipeline
    # ------------------------------------------------------------------

    async def _run_job(self, job: Job) -> None:
        """Drive a job through planning → execution → review.

        Review failures trigger re-plan + retry up to job.max_reviews times.
        The overall job is bounded by MAX_JOB_TIMEOUT_SEC.
        """
        logger.info(
            "Starting job %s (mode=%s, workspace=%s, priority=%d).",
            job.id, job.mode, job.workspace_id, job.priority,
        )
        job_start = time.monotonic()

        # ── Phase 1: Planning ──────────────────────────────────────────
        self.queue.update_job_status(job.id, JobStatus.PLANNING.value)
        self.queue.add_audit(
            job.id,
            action="planning",
            detail=f"mode={job.mode}",
            actor="worker",
        )
        await self._emit("job_planning", {"job_id": job.id, "mode": job.mode})

        try:
            input_files = self.queue.get_files(job.id, file_type="input")
            node_defs = await self.planner.plan(job, input_files=input_files)
        except Exception as exc:
            logger.error("Planning failed for job %s: %s", job.id, exc)
            await self._handle_failure(job, f"Planning error: {exc}")
            return

        if not node_defs:
            await self._handle_failure(job, "Planner returned no nodes.")
            return

        # Persist nodes to DB.
        try:
            nodes = self.queue.create_nodes(job.id, node_defs)
        except Exception as exc:
            logger.error("Node creation failed for job %s: %s", job.id, exc)
            await self._handle_failure(job, f"Node creation error: {exc}")
            return

        logger.info("Job %s: %d nodes created.", job.id, len(nodes))
        await self._emit("job_nodes_created", {
            "job_id": job.id,
            "node_count": len(nodes),
            "nodes": [{"id": n.id, "title": n.title, "sequence": n.sequence}
                      for n in nodes],
        })

        # ── Phase 2: Execute → Review loop ────────────────────────────
        max_reviews = min(job.max_reviews, _ABSOLUTE_MAX_REVIEWS)

        for attempt in range(max_reviews + 1):
            # Check overall job timeout.
            elapsed_total = time.monotonic() - job_start
            if elapsed_total >= MAX_JOB_TIMEOUT_SEC:
                await self._handle_failure(
                    job,
                    f"Job exceeded maximum timeout of {MAX_JOB_TIMEOUT_SEC}s.",
                )
                return

            # Cancel check.
            if not self._running or self._is_cancelled(job):
                logger.info("Job %s cancelled or worker stopped — aborting.", job.id)
                self.queue.update_job_status(job.id, JobStatus.CANCELLED.value)
                self.queue.add_audit(job.id, action="cancelled", actor="worker")
                await self._emit("job_cancelled", {"job_id": job.id})
                return

            # ── Execution ─────────────────────────────────────────────
            self.queue.update_job_status(job.id, JobStatus.EXECUTING.value)
            self.queue.add_audit(
                job.id,
                action="executing",
                detail=f"attempt={attempt + 1}",
                actor="worker",
            )
            await self._emit("job_executing", {
                "job_id": job.id,
                "attempt": attempt + 1,
            })

            # Reload nodes (may differ if we re-planned on retry).
            nodes = self.queue.get_nodes(job.id)
            all_succeeded = await self._execute_nodes(job, nodes)

            if not all_succeeded:
                # Execution itself failed — don't bother reviewing.
                failed_node = self._first_failed_node(nodes)
                err = failed_node.error if failed_node else "One or more nodes failed."
                await self._handle_failure(job, err, node=failed_node)
                return

            # ── Wait for delegated child jobs (if any) ────────────
            try:
                children = self._delegation_engine.get_children(job.id)
            except Exception:
                children = []
            if children:
                logger.info(
                    "Job %s has %d child job(s) — waiting for completion.",
                    job.id, len(children),
                )
                await self._emit("job_waiting_children", {
                    "job_id": job.id,
                    "child_count": len(children),
                })
                children_done = await self._wait_for_children(job.id)
                if not children_done:
                    await self._handle_failure(
                        job,
                        "Timed out waiting for delegated child jobs to complete.",
                    )
                    return

            # ── Review ────────────────────────────────────────────────
            self.queue.update_job_status(job.id, JobStatus.REVIEWING.value)
            self.queue.add_audit(
                job.id,
                action="reviewing",
                detail=f"review_attempt={attempt + 1}",
                actor="worker",
            )
            await self._emit("job_reviewing", {
                "job_id": job.id,
                "review_attempt": attempt + 1,
            })

            try:
                output_files = self.queue.get_files(job.id, file_type="output")
                review = await self.reviewer.review(
                    job,
                    nodes=nodes,
                    output_files=output_files,
                )
            except Exception as exc:
                logger.error(
                    "Reviewer raised an exception for job %s: %s", job.id, exc
                )
                await self._handle_failure(job, f"Review error: {exc}")
                return

            self.queue.add_audit(
                job.id,
                action="review_result",
                detail=(
                    f"passed={review.passed} score={review.score:.3f} "
                    f"attempt={attempt + 1}"
                ),
                actor="worker",
            )
            await self._emit("job_review_result", {
                "job_id": job.id,
                "passed": review.passed,
                "score": review.score,
                "needs_human_review": review.needs_human_review,
                "feedback": review.feedback,
                "review_attempt": attempt + 1,
            })

            if review.passed:
                # Success path.
                self._consecutive_failures = 0
                self.queue.update_job_status(
                    job.id,
                    JobStatus.DONE.value,
                    result_summary=review.feedback,
                )
                self.queue.add_audit(
                    job.id,
                    action="done",
                    detail=f"score={review.score:.3f}",
                    actor="worker",
                )
                elapsed = time.monotonic() - job_start
                logger.info(
                    "Job %s DONE in %.1fs (score=%.3f).",
                    job.id, elapsed, review.score,
                )
                await self._emit("job_done", {
                    "job_id": job.id,
                    "score": review.score,
                    "elapsed_sec": round(elapsed, 1),
                    "result_summary": review.feedback,
                })
                return

            # Review failed.
            review_count = self.queue.increment_review_count(job.id)

            if review_count >= max_reviews:
                # Exhausted all retries.
                logger.warning(
                    "Job %s failed review %d/%d times — marking failed.",
                    job.id, review_count, max_reviews,
                )
                rca = await self.reflection.analyze_failure(job.id)
                if rca and "error" not in rca:
                    logger.warning(f"Job {job.id} failure RCA: {rca.get('root_cause')}")
                await self._handle_failure(
                    job,
                    f"Review failed after {review_count} attempt(s). "
                    f"Last feedback: {review.feedback}",
                )
                return

            # Re-plan with feedback injected.
            logger.info(
                "Job %s review failed (attempt %d/%d) — re-planning with feedback.",
                job.id, review_count, max_reviews,
            )
            await self._emit("job_replanning", {
                "job_id": job.id,
                "review_count": review_count,
                "feedback": review.feedback,
            })

            try:
                retry_node_defs = await self._build_retry_plan(
                    job, nodes, review.feedback
                )
                # Replace existing nodes with the retry plan.
                self._reset_nodes_for_retry(job, retry_node_defs)
            except Exception as exc:
                logger.error(
                    "Re-planning failed for job %s: %s", job.id, exc
                )
                await self._handle_failure(job, f"Re-planning error: {exc}")
                return

        # Should not reach here, but guard defensively.
        await self._handle_failure(job, "Exceeded max review cycles.")

    # ------------------------------------------------------------------
    # Node execution
    # ------------------------------------------------------------------

    async def _execute_nodes(self, job: Job, nodes: list[Node]) -> bool:
        """Execute all pending nodes in DAG order, piping outputs as inputs.

        Returns True if every node completed successfully.
        """
        node_outputs: dict[str, dict[str, Any]] = {}
        # Pre-load outputs of already-completed nodes (resume support).
        for node in nodes:
            if node.status == NodeStatus.COMPLETED.value and node.output_json:
                try:
                    node_outputs[node.id] = json.loads(node.output_json)
                except (json.JSONDecodeError, TypeError):
                    node_outputs[node.id] = {}

        node_start_times: dict[str, float] = {}
        node_durations: list[float] = []

        while True:
            # Cancel / stop check between nodes.
            if not self._running or self._is_cancelled(job):
                logger.info(
                    "Job %s: worker stopped or job cancelled between nodes.",
                    job.id,
                )
                return False

            node = self.queue.get_next_pending_node(job.id)
            if node is None:
                # No more pending nodes — check whether any failed.
                all_nodes = self.queue.get_nodes(job.id)
                failed = [
                    n for n in all_nodes
                    if n.status == NodeStatus.FAILED.value
                ]
                if failed:
                    return False
                break

            # Build input from the most recently completed predecessor.
            input_data = self._resolve_input(node, node_outputs)

            # Mark running.
            self.queue.update_node(
                node.id,
                status=NodeStatus.RUNNING.value,
                input_json=json.dumps(input_data) if input_data else None,
            )
            self.queue.add_audit(
                job.id,
                action="node_started",
                detail=f"seq={node.sequence} title={node.title!r}",
                actor="worker",
                node_id=node.id,
            )

            node_start_times[node.id] = time.monotonic()
            all_nodes = self.queue.get_nodes(job.id)
            await self._emit_progress(job, all_nodes, current_node=node)

            # Execute with the overall job timeout as a hard cap.
            elapsed_so_far = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self.executor.execute_node(
                        node=node,
                        job=job,
                        input_data=input_data,
                        progress_callback=self._activity_callback,
                    ),
                    timeout=float(node.timeout_sec),
                )
            except asyncio.TimeoutError:
                duration = time.monotonic() - elapsed_so_far
                node_durations.append(duration)
                error_msg = f"Node '{node.title}' timed out after {node.timeout_sec}s."
                logger.error(error_msg)
                self.queue.update_node(
                    node.id,
                    status=NodeStatus.FAILED.value,
                    error=error_msg,
                )
                self.queue.add_audit(
                    job.id,
                    action="node_failed",
                    detail=error_msg,
                    actor="worker",
                    node_id=node.id,
                )
                await self._emit("node_failed", {
                    "job_id": job.id,
                    "node_id": node.id,
                    "error": error_msg,
                })
                return False
            except Exception as exc:
                duration = time.monotonic() - elapsed_so_far
                node_durations.append(duration)
                error_msg = f"Node '{node.title}' raised exception: {exc}"
                self.reflection.log_step_failure(
                    task_id=job.id,
                    stage="node_execution",
                    validator=node.title,
                    error_msg=error_msg
                )
                logger.exception(error_msg)
                self.queue.update_node(
                    node.id,
                    status=NodeStatus.FAILED.value,
                    error=error_msg,
                )
                self.queue.add_audit(
                    job.id,
                    action="node_failed",
                    detail=error_msg,
                    actor="worker",
                    node_id=node.id,
                )
                await self._emit("node_failed", {
                    "job_id": job.id,
                    "node_id": node.id,
                    "error": error_msg,
                })
                return False

            duration = time.monotonic() - node_start_times.get(node.id, elapsed_so_far)
            node_durations.append(duration)

            if result.success:
                output_json = json.dumps(result.output, ensure_ascii=False)
                self.queue.update_node(
                    node.id,
                    status=NodeStatus.COMPLETED.value,
                    output_json=output_json,
                )
                self.queue.add_audit(
                    job.id,
                    action="node_completed",
                    detail=(
                        f"seq={node.sequence} duration={duration:.1f}s "
                        f"tokens_in={result.tokens_in} tokens_out={result.tokens_out}"
                    ),
                    actor="worker",
                    node_id=node.id,
                )
                node_outputs[node.id] = result.output

                all_nodes = self.queue.get_nodes(job.id)
                await self._emit_progress(job, all_nodes, current_node=None)
                await self._emit("node_completed", {
                    "job_id": job.id,
                    "node_id": node.id,
                    "sequence": node.sequence,
                    "title": node.title,
                    "duration_sec": round(duration, 2),
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "tool_calls_made": result.tool_calls_made,
                })

                # ── Delegation check ──────────────────────────────
                # If the node output contains a "delegate" key, spawn
                # a child job via the DelegationEngine.
                if isinstance(result.output, dict) and "delegate" in result.output:
                    try:
                        await self._handle_delegation(job, result.output["delegate"])
                    except Exception as e:
                        logger.warning("Delegation failed (non-fatal): %s", e)
            else:
                # Node reported failure (e.g. schema validation).
                error_msg = result.error or "Node execution failed."
                self.reflection.log_step_failure(
                    task_id=job.id,
                    stage="node_logic",
                    validator=node.title,
                    error_msg=error_msg
                )
                self.queue.update_node(
                    node.id,
                    status=NodeStatus.FAILED.value,
                    output_json=json.dumps(result.output, ensure_ascii=False)
                    if result.output
                    else None,
                    error=error_msg,
                )
                self.queue.add_audit(
                    job.id,
                    action="node_failed",
                    detail=error_msg,
                    actor="worker",
                    node_id=node.id,
                )
                await self._emit("node_failed", {
                    "job_id": job.id,
                    "node_id": node.id,
                    "error": error_msg,
                })
                return False

        return True

    # ------------------------------------------------------------------
    # Delegation helpers
    # ------------------------------------------------------------------

    async def _handle_delegation(self, job: Job, delegate_spec: dict[str, Any]) -> None:
        """Spawn a child job from a delegation signal in a node's output.

        ``delegate_spec`` is expected to contain ``title``, ``description``,
        and optionally ``priority`` (defaults to the parent job's priority).
        """
        title = delegate_spec.get("title", "Delegated sub-task")
        description = delegate_spec.get("description", "")
        priority = delegate_spec.get("priority", job.priority)

        try:
            delegation = self._delegation_engine.spawn_child_job(
                parent_job_id=job.id,
                title=title,
                description=description,
                priority=priority,
            )
            logger.info(
                "Job %s delegated child job %s: %s",
                job.id, delegation.child_job_id, title,
            )
            self.queue.add_audit(
                job.id,
                action="delegated_child",
                detail=f"child={delegation.child_job_id} title={title!r}",
                actor="worker",
            )
            await self._emit("job_delegated", {
                "job_id": job.id,
                "child_job_id": delegation.child_job_id,
                "title": title,
                "priority": priority,
            })
        except Exception as exc:
            logger.error(
                "Failed to spawn delegated child for job %s: %s", job.id, exc,
            )

    async def _wait_for_children(self, job_id: str) -> bool:
        """Poll until all child jobs in the delegation tree are complete.

        Returns ``True`` when every child has a terminal status, or ``False``
        if the maximum wait of 600 seconds (10 minutes) is exceeded.
        """
        max_wait = 600.0  # seconds
        poll_interval = 2.0  # seconds
        waited = 0.0

        while waited < max_wait:
            if self._delegation_engine.is_tree_complete(job_id):
                logger.info(
                    "All child jobs for %s are complete (waited %.1fs).",
                    job_id, waited,
                )
                return True
            await asyncio.sleep(poll_interval)
            waited += poll_interval

        logger.warning(
            "Timed out waiting for child jobs of %s after %.0fs.", job_id, max_wait,
        )
        return False

    # ------------------------------------------------------------------
    # Resume
    # ------------------------------------------------------------------

    async def _resume_job(self, job: Job) -> None:
        """Resume an interrupted job from its last completed node.

        'Interrupted' means the job was in 'executing' status when the
        process crashed.  We reset running nodes back to pending so the
        normal _execute_nodes path can pick them up.
        """
        logger.info("Resuming interrupted job %s.", job.id)

        # Re-set any nodes that were mid-run back to pending.
        nodes = self.queue.get_nodes(job.id)
        for node in nodes:
            if node.status == NodeStatus.RUNNING.value:
                logger.warning(
                    "Job %s: resetting node '%s' from RUNNING to PENDING for resume.",
                    job.id, node.title,
                )
                self.queue.update_node(node.id, status=NodeStatus.PENDING.value)
                self.queue.add_audit(
                    job.id,
                    action="node_reset_for_resume",
                    detail=f"seq={node.sequence}",
                    actor="worker",
                    node_id=node.id,
                )

        self.queue.add_audit(
            job.id, action="resuming", detail="worker restart", actor="worker"
        )
        await self._emit("job_resuming", {"job_id": job.id})

        # Refresh job state after reset.
        job = self.queue.get_job(job.id)
        if job is None:
            logger.error("Job %s disappeared during resume.", job.id if job else "?")
            return

        self._current_job_id = job.id
        try:
            await self._run_job(job)
        except Exception as exc:
            logger.exception("Resume of job %s failed: %s", job.id, exc)
            await self._handle_failure(job, f"Resume failed: {exc}")
        finally:
            self._current_job_id = None

    # ------------------------------------------------------------------
    # Progress emission
    # ------------------------------------------------------------------

    async def _emit_progress(
        self,
        job: Job,
        nodes: list[Node],
        current_node: Node | None = None,
    ) -> None:
        """Emit a structured progress event via the activity callback."""
        total = len(nodes)
        completed = sum(
            1 for n in nodes if n.status == NodeStatus.COMPLETED.value
        )

        # Collect elapsed times from audit log for completed nodes.
        # We approximate using created_at / updated_at delta on the node rows.
        node_durations: list[float] = []
        for n in nodes:
            if n.status == NodeStatus.COMPLETED.value:
                try:
                    from datetime import datetime, timezone
                    fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
                    created = datetime.fromisoformat(n.created_at)
                    updated = datetime.fromisoformat(n.updated_at)
                    delta = (updated - created).total_seconds()
                    if delta > 0:
                        node_durations.append(delta)
                except Exception:
                    pass

        avg_sec = (sum(node_durations) / len(node_durations)) if node_durations else 0.0
        remaining_nodes = total - completed - (1 if current_node else 0)
        estimated_remaining = round(avg_sec * max(remaining_nodes, 0), 1)

        # Rough elapsed based on job's updated_at vs now.
        try:
            from datetime import datetime, timezone
            job_refreshed = self.queue.get_job(job.id)
            created_at = datetime.fromisoformat(
                (job_refreshed or job).created_at
            )
            elapsed_sec = round(
                (datetime.now(timezone.utc) - created_at).total_seconds(), 1
            )
        except Exception:
            elapsed_sec = 0.0

        progress_data: dict[str, Any] = {
            "job_id": job.id,
            "progress": {
                "completed": completed,
                "total": total,
                "current_node": current_node.title if current_node else None,
                "elapsed_sec": elapsed_sec,
                "avg_sec_per_node": round(avg_sec, 1),
                "estimated_remaining_sec": estimated_remaining,
            },
        }
        await self._emit("job_progress", progress_data)

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    async def _handle_failure(
        self,
        job: Job,
        error: str,
        node: Node | None = None,
    ) -> None:
        """Mark the job failed, audit the error, update circuit breaker."""
        logger.error(
            "Job %s FAILED%s: %s",
            job.id,
            f" (node '{node.title}')" if node else "",
            error,
        )

        self.queue.update_job_status(
            job.id,
            JobStatus.FAILED.value,
            error=error,
        )
        self.queue.add_audit(
            job.id,
            action="failed",
            detail=error[:2000],  # Guard against enormous error strings.
            actor="worker",
            node_id=node.id if node else None,
        )

        # Circuit breaker: increment and potentially open.
        self._consecutive_failures += 1
        if self._consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open_until = time.monotonic() + _CIRCUIT_OPEN_PAUSE_SEC
            logger.warning(
                "Circuit breaker OPEN after %d consecutive failures. "
                "Pausing for %.0f seconds.",
                self._consecutive_failures,
                _CIRCUIT_OPEN_PAUSE_SEC,
            )
            await self._emit("worker_circuit_open", {
                "consecutive_failures": self._consecutive_failures,
                "pause_sec": _CIRCUIT_OPEN_PAUSE_SEC,
            })

        await self._emit("job_failed", {
            "job_id": job.id,
            "error": error,
            "node_id": node.id if node else None,
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Fire the activity_callback; silently swallow errors."""
        if self._activity_callback is None:
            return
        try:
            await self._activity_callback(event_type, data)
        except Exception as exc:
            logger.debug("activity_callback raised (ignored): %s", exc)

    def _is_cancelled(self, job: Job) -> bool:
        """Return True if the job's current DB status is cancelling/cancelled."""
        try:
            fresh = self.queue.get_job(job.id)
            if fresh is None:
                return False
            return fresh.status in (
                JobStatus.CANCELLING.value,
                JobStatus.CANCELLED.value,
            )
        except Exception:
            return False

    def _resolve_input(
        self,
        node: Node,
        node_outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Build input_data for a node from its dependencies' outputs.

        For linear pipelines (no depends_on) the immediately preceding
        completed node's output is used.  For DAG nodes all dependency
        outputs are merged (later keys win on collision).
        """
        if node.depends_on:
            merged: dict[str, Any] = {}
            for dep_id in node.depends_on:
                dep_output = node_outputs.get(dep_id)
                if dep_output:
                    merged.update(dep_output)
            return merged if merged else None

        # Linear chain: use output of node with sequence = this node's sequence - 1.
        if node.sequence > 1:
            # Find the completed predecessor by sequence.
            # node_outputs keys are node IDs; we need to scan.
            # Because we don't have a seq→id map here we use the last output added.
            if node_outputs:
                last_output = list(node_outputs.values())[-1]
                return last_output if last_output else None

        return None

    @staticmethod
    def _first_failed_node(nodes: list[Node]) -> Node | None:
        """Return the first node (by sequence) in FAILED status, or None."""
        for node in sorted(nodes, key=lambda n: n.sequence):
            if node.status == NodeStatus.FAILED.value:
                return node
        return None

    async def _build_retry_plan(
        self,
        job: Job,
        nodes: list[Node],
        feedback: str,
    ) -> list[dict[str, Any]]:
        """Call the planner again with review feedback injected into the job description."""
        # Create a synthetic job with the feedback appended so the planner
        # knows what went wrong and can adjust the plan.
        from dataclasses import replace
        augmented_description = (
            f"{job.description or job.title}\n\n"
            f"--- REVIEW FEEDBACK (attempt {job.review_count}) ---\n"
            f"{feedback}\n"
            f"--- END FEEDBACK ---\n"
            f"Please produce an improved plan that addresses the above issues."
        )
        augmented_job = replace(job, description=augmented_description)
        input_files = self.queue.get_files(job.id, file_type="input")
        return await self.planner.plan(augmented_job, input_files=input_files)

    def _reset_nodes_for_retry(
        self,
        job: Job,
        new_node_defs: list[dict[str, Any]],
    ) -> None:
        """Reset all existing nodes to CANCELLED and insert new ones for retry.

        Rather than deleting rows (which would break audit FK integrity if any
        FK constraints exist), we mark old nodes cancelled and insert fresh
        ones.
        """
        existing_nodes = self.queue.get_nodes(job.id)
        for node in existing_nodes:
            if node.status != NodeStatus.CANCELLED.value:
                self.queue.update_node(node.id, status=NodeStatus.CANCELLED.value)

        self.queue.add_audit(
            job.id,
            action="nodes_reset_for_retry",
            detail=f"cancelled {len(existing_nodes)} node(s), adding {len(new_node_defs)} new",
            actor="worker",
        )
        self.queue.create_nodes(job.id, new_node_defs)
