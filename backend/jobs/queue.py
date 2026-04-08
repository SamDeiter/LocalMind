"""
Job Queue CRUD — all database operations for jobs, nodes, files, audit, and templates.

Tables targeted (created by backend/core/schema.py):
    jobs, job_nodes, job_files, job_audit_log, pipeline_templates

Each public method opens its own connection, commits, and closes — matching
the pattern established in backend/db.py.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.config import DB_PATH, JOBS_DIR  # noqa: F401 (JOBS_DIR kept for callers)
from backend.jobs.models import (
    AuditEntry,
    Job,
    JobFile,
    JobStatus,
    Node,
    NodeStatus,
    PipelineTemplate,
    RetryPolicy,
    parse_json_field,
)

logger = logging.getLogger("localmind.jobs.queue")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# JobQueue
# ---------------------------------------------------------------------------


class JobQueue:
    """All CRUD operations for the job pipeline tables."""

    # ------------------------------------------------------------------
    # Job CRUD
    # ------------------------------------------------------------------

    def create_job(
        self,
        workspace_id: str,
        title: str,
        description: str | None,
        source: str,
        requester: str | None,
        mode: str = "quick",
        template_id: str | None = None,
        priority: int = 0,
        source_ref: str | None = None,
    ) -> Job:
        """Insert a new job row and return the populated Job model."""
        job_id = _new_id()
        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, workspace_id, title, description, source, source_ref,
                    status, priority, requester, mode, template_id,
                    result_summary, review_count, max_reviews, error,
                    cost_cents, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 3, NULL, 0, ?, ?)
                """,
                (
                    job_id, workspace_id, title, description, source, source_ref,
                    JobStatus.PENDING.value, priority, requester, mode, template_id,
                    now, now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            job = Job.from_row(row)
        finally:
            conn.close()
        logger.info("Created job %s (%s) in workspace %s", job_id, mode, workspace_id)
        return job

    def get_job(self, job_id: str) -> Job | None:
        """Return the Job for *job_id*, or None if not found."""
        conn = _get_conn()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            conn.close()
        return Job.from_row(row) if row else None

    def list_jobs(
        self,
        workspace_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        """Return jobs for a workspace, optionally filtered by status."""
        conn = _get_conn()
        try:
            if status is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE workspace_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (workspace_id, status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE workspace_id = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (workspace_id, limit, offset),
                ).fetchall()
        finally:
            conn.close()
        return [Job.from_row(r) for r in rows]

    def update_job_status(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        """Update a job's status (and optionally error / result_summary)."""
        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, result_summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error, result_summary, now, job_id),
            )
            conn.commit()
        finally:
            conn.close()
        logger.debug("Job %s → status=%s", job_id, status)

    def cancel_job(self, job_id: str) -> None:
        """Transition a job to 'cancelling' status."""
        self.update_job_status(job_id, JobStatus.CANCELLING.value)

    def track_cost(self, job_id: str, cents: float) -> None:
        """Atomically add *cents* to the job's cost_cents column."""
        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE jobs SET cost_cents = cost_cents + ?, updated_at = ? WHERE id = ?",
                (cents, now, job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def increment_review_count(self, job_id: str) -> int:
        """Atomically increment review_count and return the new value."""
        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE jobs SET review_count = review_count + 1, updated_at = ? WHERE id = ?",
                (now, job_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT review_count FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            count = int(row["review_count"]) if row else 0
        finally:
            conn.close()
        return count

    # ------------------------------------------------------------------
    # Node CRUD
    # ------------------------------------------------------------------

    def create_nodes(self, job_id: str, nodes: list[dict[str, Any]]) -> list[Node]:
        """Batch-insert nodes from planner output; auto-assigns sequence 1, 2, 3…

        Each dict may contain:
            title, instructions, tools_allowed, expected_output,
            input_schema_json, output_schema_json, depends_on,
            timeout_sec, retry_policy_json
        """
        now = _now()
        created: list[Node] = []
        conn = _get_conn()
        try:
            for seq, node_def in enumerate(nodes, start=1):
                node_id = _new_id()
                tools_allowed = node_def.get("tools_allowed", [])
                tools_allowed_json = (
                    json.dumps(tools_allowed)
                    if not isinstance(tools_allowed, str)
                    else tools_allowed
                )
                depends_on = node_def.get("depends_on", [])
                depends_on_json = (
                    json.dumps(depends_on)
                    if not isinstance(depends_on, str)
                    else depends_on
                )
                retry_policy_json = node_def.get("retry_policy_json") or RetryPolicy().to_json()

                conn.execute(
                    """
                    INSERT INTO job_nodes (
                        id, job_id, sequence, title, instructions,
                        tools_allowed, expected_output, status,
                        input_json, output_json, error,
                        auto_generated, input_schema_json, output_schema_json,
                        side_effects_json, retry_policy_json, timeout_sec,
                        rollback_strategy, acceptance_tests_json,
                        depends_on, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        NULL, NULL, NULL,
                        1, ?, ?,
                        NULL, ?, ?,
                        NULL, NULL,
                        ?, ?, ?
                    )
                    """,
                    (
                        node_id, job_id, seq,
                        node_def.get("title", f"Node {seq}"),
                        node_def.get("instructions"),
                        tools_allowed_json,
                        node_def.get("expected_output"),
                        NodeStatus.PENDING.value,
                        node_def.get("input_schema_json"),
                        node_def.get("output_schema_json"),
                        retry_policy_json,
                        int(node_def.get("timeout_sec", 300)),
                        depends_on_json,
                        now, now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM job_nodes WHERE id = ?", (node_id,)
                ).fetchone()
                created.append(Node.from_row(row))

            conn.commit()
        finally:
            conn.close()

        logger.info("Created %d nodes for job %s", len(created), job_id)
        return created

    def get_nodes(self, job_id: str) -> list[Node]:
        """Return all nodes for a job ordered by sequence."""
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM job_nodes WHERE job_id = ? ORDER BY sequence",
                (job_id,),
            ).fetchall()
        finally:
            conn.close()
        return [Node.from_row(r) for r in rows]

    def get_node(self, node_id: str) -> Node | None:
        """Return a single node by its ID, or None."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM job_nodes WHERE id = ?", (node_id,)
            ).fetchone()
        finally:
            conn.close()
        return Node.from_row(row) if row else None

    def update_node(
        self,
        node_id: str,
        status: str | None = None,
        input_json: str | None = None,
        output_json: str | None = None,
        error: str | None = None,
    ) -> None:
        """Partially update a node row; only non-None kwargs are applied."""
        now = _now()
        conn = _get_conn()
        try:
            # Build SET clause dynamically for only provided fields
            parts: list[str] = ["updated_at = ?"]
            params: list[Any] = [now]

            if status is not None:
                parts.append("status = ?")
                params.append(status)
            if input_json is not None:
                parts.append("input_json = ?")
                params.append(input_json)
            if output_json is not None:
                parts.append("output_json = ?")
                params.append(output_json)
            if error is not None:
                parts.append("error = ?")
                params.append(error)

            params.append(node_id)
            conn.execute(
                f"UPDATE job_nodes SET {', '.join(parts)} WHERE id = ?",
                params,
            )
            conn.commit()
        finally:
            conn.close()
        logger.debug("Node %s updated: status=%s", node_id, status)

    def get_next_pending_node(self, job_id: str) -> Node | None:
        """Return the next executable pending node for *job_id*.

        A node is eligible when:
          - Its own status is 'pending'
          - Every node ID listed in its depends_on has status='completed'

        For linear pipelines (no depends_on), this is simply the pending node
        with the lowest sequence number.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM job_nodes WHERE job_id = ? ORDER BY sequence",
                (job_id,),
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return None

        # Build lookup: node_id -> status for dependency checking
        status_by_id: dict[str, str] = {r["id"]: r["status"] for r in rows}

        for row in rows:
            if row["status"] != NodeStatus.PENDING.value:
                continue
            depends_on: list[str] = parse_json_field(row["depends_on"], default=[])
            if not depends_on:
                # Linear node — ready if no blocking predecessor is still running/pending
                return Node.from_row(row)
            # DAG node — all dependencies must be completed
            if all(
                status_by_id.get(dep_id) == NodeStatus.COMPLETED.value
                for dep_id in depends_on
            ):
                return Node.from_row(row)

        return None

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def add_file(
        self,
        job_id: str,
        filename: str,
        file_path: str,
        file_type: str,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        node_id: str | None = None,
    ) -> JobFile:
        """Insert a file record and return the populated JobFile model."""
        file_id = _new_id()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO job_files (
                    id, job_id, node_id, filename, file_path,
                    file_type, mime_type, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (file_id, job_id, node_id, filename, file_path,
                 file_type, mime_type, size_bytes),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM job_files WHERE id = ?", (file_id,)
            ).fetchone()
            jf = JobFile.from_row(row)
        finally:
            conn.close()
        return jf

    def get_files(self, job_id: str, file_type: str | None = None) -> list[JobFile]:
        """Return all file records for a job, optionally filtered by type."""
        conn = _get_conn()
        try:
            if file_type is not None:
                rows = conn.execute(
                    "SELECT * FROM job_files WHERE job_id = ? AND file_type = ?",
                    (job_id, file_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM job_files WHERE job_id = ?",
                    (job_id,),
                ).fetchall()
        finally:
            conn.close()
        return [JobFile.from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def add_audit(
        self,
        job_id: str,
        action: str,
        detail: str | None = None,
        actor: str | None = None,
        node_id: str | None = None,
    ) -> None:
        """Append an entry to job_audit_log."""
        entry_id = _new_id()
        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO job_audit_log (id, job_id, node_id, action, detail, actor, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (entry_id, job_id, node_id, action, detail, actor, now),
            )
            conn.commit()
        finally:
            conn.close()

    def get_audit_log(self, job_id: str, limit: int = 100) -> list[AuditEntry]:
        """Return audit entries for a job, newest first, up to *limit*."""
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM job_audit_log
                WHERE job_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [AuditEntry.from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def save_as_template(
        self,
        job_id: str,
        name: str,
        workspace_id: str,
        created_by: str | None = None,
    ) -> PipelineTemplate:
        """Extract completed job nodes and save them as a reusable template."""
        nodes = self.get_nodes(job_id)
        nodes_snapshot = [
            {
                "title": n.title,
                "instructions": n.instructions,
                "tools_allowed": n.tools_allowed,
                "expected_output": n.expected_output,
                "input_schema_json": n.input_schema_json,
                "output_schema_json": n.output_schema_json,
                "depends_on": n.depends_on,
                "timeout_sec": n.timeout_sec,
                "retry_policy_json": n.retry_policy.to_json(),
            }
            for n in nodes
        ]

        template_id = _new_id()
        now = _now()
        nodes_json = json.dumps(nodes_snapshot)

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO pipeline_templates (
                    id, workspace_id, name, description, nodes_json,
                    created_by, use_count, created_at, updated_at
                ) VALUES (?, ?, ?, NULL, ?, ?, 0, ?, ?)
                """,
                (template_id, workspace_id, name, nodes_json, created_by, now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM pipeline_templates WHERE id = ?", (template_id,)
            ).fetchone()
            tmpl = PipelineTemplate.from_row(row)
        finally:
            conn.close()

        logger.info(
            "Saved template '%s' (%s) from job %s (%d nodes)",
            name, template_id, job_id, len(nodes_snapshot),
        )
        return tmpl

    def create_job_from_template(
        self,
        template_id: str,
        workspace_id: str,
        requester: str | None,
        files: list[dict[str, Any]] | None = None,
    ) -> Job:
        """Instantiate a new job (and its nodes) from a saved template."""
        tmpl = self.get_template(template_id)
        if tmpl is None:
            raise ValueError(f"Template not found: {template_id}")

        job = self.create_job(
            workspace_id=workspace_id,
            title=tmpl.name,
            description=tmpl.description,
            source="template",
            requester=requester,
            mode="pipeline",
            template_id=template_id,
        )

        if tmpl.nodes:
            self.create_nodes(job.id, tmpl.nodes)

        if files:
            for f in files:
                self.add_file(
                    job_id=job.id,
                    filename=f["filename"],
                    file_path=f["file_path"],
                    file_type=f.get("file_type", "input"),
                    mime_type=f.get("mime_type"),
                    size_bytes=f.get("size_bytes"),
                    node_id=f.get("node_id"),
                )

        self.increment_template_use(template_id)
        self.add_audit(
            job.id,
            action="job_created_from_template",
            detail=f"template_id={template_id}",
            actor=requester,
        )
        logger.info(
            "Created job %s from template %s for workspace %s",
            job.id, template_id, workspace_id,
        )
        return job

    def list_templates(self, workspace_id: str) -> list[PipelineTemplate]:
        """Return all templates for a workspace ordered by name."""
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM pipeline_templates WHERE workspace_id = ? ORDER BY name",
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()
        return [PipelineTemplate.from_row(r) for r in rows]

    def get_template(self, template_id: str) -> PipelineTemplate | None:
        """Return a single template by ID, or None."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM pipeline_templates WHERE id = ?", (template_id,)
            ).fetchone()
        finally:
            conn.close()
        return PipelineTemplate.from_row(row) if row else None

    def increment_template_use(self, template_id: str) -> None:
        """Atomically increment the use_count for a template."""
        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE pipeline_templates SET use_count = use_count + 1, updated_at = ? WHERE id = ?",
                (now, template_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Resumability
    # ------------------------------------------------------------------

    def get_interrupted_jobs(self) -> list[Job]:
        """Return jobs stuck in 'executing' status — candidates for resume on restart."""
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at",
                (JobStatus.EXECUTING.value,),
            ).fetchall()
        finally:
            conn.close()
        jobs = [Job.from_row(r) for r in rows]
        if jobs:
            logger.warning(
                "Found %d interrupted jobs to resume: %s",
                len(jobs),
                [j.id for j in jobs],
            )
        return jobs
