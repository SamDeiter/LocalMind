"""
Tests for node-level retry logic in the JobWorker.

Verifies that transient failures (Ollama timeouts, network errors) are
retried according to the node's retry_policy before marking the job as
failed.  This was the root cause of job c03bcd34 — a single Ollama
timeout immediately killed the job instead of retrying.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.jobs.models import (
    Job, Node, JobStatus, NodeStatus, RetryPolicy,
)
from backend.jobs.executor import NodeResult
from backend.jobs.worker import JobWorker
from backend.jobs.queue import JobQueue

# ---------------------------------------------------------------------------
# Minimal DB schema (same as test_jobs.py)
# ---------------------------------------------------------------------------

_JOB_PIPELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    source TEXT NOT NULL DEFAULT 'web',
    source_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    requester TEXT,
    mode TEXT NOT NULL DEFAULT 'quick',
    template_id TEXT,
    result_summary TEXT,
    review_count INTEGER NOT NULL DEFAULT 0,
    max_reviews INTEGER NOT NULL DEFAULT 3,
    error TEXT,
    cost_cents REAL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    parent_job_id TEXT,
    tree_root_id TEXT
);

CREATE TABLE IF NOT EXISTS job_nodes (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    title TEXT NOT NULL,
    instructions TEXT,
    tools_allowed TEXT,
    expected_output TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    input_json TEXT,
    output_json TEXT,
    error TEXT,
    auto_generated INTEGER NOT NULL DEFAULT 1,
    input_schema_json TEXT,
    output_schema_json TEXT,
    side_effects_json TEXT,
    retry_policy_json TEXT,
    timeout_sec INTEGER DEFAULT 300,
    rollback_strategy TEXT,
    acceptance_tests_json TEXT,
    depends_on TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_files (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    node_id TEXT,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL DEFAULT 'input',
    mime_type TEXT,
    size_bytes INTEGER
);

CREATE TABLE IF NOT EXISTS job_audit_log (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    node_id TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    actor TEXT,
    timestamp TEXT NOT NULL,
    source_ip TEXT
);
"""


def _init_job_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(_JOB_PIPELINE_SCHEMA)
    conn.commit()
    conn.close()


@pytest.fixture
def job_db(tmp_path):
    """Create a temporary SQLite DB with pipeline schema and patch DB_PATH."""
    db_file = tmp_path / "test_retry.db"
    _init_job_db(db_file)
    with patch("backend.jobs.queue.DB_PATH", db_file), \
         patch("backend.config.DB_PATH", db_file):
        yield db_file


def _make_worker() -> JobWorker:
    """Create a JobWorker with mocked external dependencies."""
    mock_registry = MagicMock()
    worker = JobWorker(tool_registry=mock_registry)
    worker._running = True
    # Mock reflection service so it doesn't need a real DB
    worker.reflection = MagicMock()
    worker.reflection.log_step_failure = MagicMock()
    return worker


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNodeRetryOnTransientFailure:
    """Verify that transient errors (like Ollama timeout) trigger retries."""

    @pytest.mark.asyncio
    async def test_retry_on_ollama_timeout_then_succeed(self, job_db):
        """A node that fails once with a timeout then succeeds should NOT fail the job."""
        worker = _make_worker()
        worker.queue = JobQueue()

        job = worker.queue.create_job(
            workspace_id="ws-test",
            title="send me an email",
            description="send me an email",
            source="api",
            requester=None,
        )
        nodes = worker.queue.create_nodes(job.id, [
            {
                "title": "Execute task",
                "instructions": "Send email",
                "retry_policy_json": RetryPolicy(max_attempts=3, backoff_ms=[0, 0, 0]).to_json(),
            },
        ])

        # First call: simulate Ollama timeout (returns failed NodeResult)
        timeout_result = NodeResult(
            success=False,
            output={},
            error="Ollama call failed: Ollama call timed out after 120s. Ensure Ollama is running.",
        )
        # Second call: simulate success
        success_result = NodeResult(
            success=True,
            output={"result": "Email sent"},
            tokens_in=100,
            tokens_out=50,
            tool_calls_made=1,
            model_used="qwen3:8b",
        )

        call_count = 0
        original_execute = worker.executor.execute_node

        async def mock_execute_node(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return timeout_result
            return success_result

        worker.executor.execute_node = mock_execute_node

        result = await worker._execute_nodes(job, nodes)

        assert result is True, "Job should succeed after retry"
        assert call_count == 2, "Should have called execute_node twice (1 fail + 1 success)"

        # Verify audit log has a retry entry
        from backend.config import DB_PATH
        conn = sqlite3.connect(str(job_db))
        conn.row_factory = sqlite3.Row
        audits = conn.execute(
            "SELECT * FROM job_audit_log WHERE job_id = ? AND action = 'node_retry'",
            (job.id,),
        ).fetchall()
        conn.close()

        assert len(audits) >= 1, "Should have at least one node_retry audit entry"

    @pytest.mark.asyncio
    async def test_fail_after_all_retries_exhausted(self, job_db):
        """A node that fails all retry attempts should mark the job as failed."""
        worker = _make_worker()
        worker.queue = JobQueue()

        job = worker.queue.create_job(
            workspace_id="ws-test",
            title="failing job",
            description="this will fail",
            source="api",
            requester=None,
        )
        nodes = worker.queue.create_nodes(job.id, [
            {
                "title": "Always fails",
                "instructions": "Fail every time",
                "retry_policy_json": RetryPolicy(max_attempts=3, backoff_ms=[0, 0, 0]).to_json(),
            },
        ])

        call_count = 0

        async def mock_execute_node(**kwargs):
            nonlocal call_count
            call_count += 1
            return NodeResult(
                success=False,
                output={},
                error="Ollama call failed: Ollama call timed out after 120s.",
            )

        worker.executor.execute_node = mock_execute_node

        result = await worker._execute_nodes(job, nodes)

        assert result is False, "Job should fail after exhausting all retries"
        assert call_count == 3, "Should have tried 3 times (max_attempts=3)"

    @pytest.mark.asyncio
    async def test_no_retry_on_permanent_error(self, job_db):
        """Permanent errors (like missing file) should NOT be retried."""
        worker = _make_worker()
        worker.queue = JobQueue()

        job = worker.queue.create_job(
            workspace_id="ws-test",
            title="permanent fail",
            description="missing file",
            source="api",
            requester=None,
        )
        nodes = worker.queue.create_nodes(job.id, [
            {
                "title": "Perm fail",
                "instructions": "Fail permanently",
                "retry_policy_json": RetryPolicy(max_attempts=3, backoff_ms=[0, 0, 0]).to_json(),
            },
        ])

        call_count = 0

        async def mock_execute_node(**kwargs):
            nonlocal call_count
            call_count += 1
            raise FileNotFoundError("credentials.json not found")

        worker.executor.execute_node = mock_execute_node

        result = await worker._execute_nodes(job, nodes)

        assert result is False
        # FileNotFoundError is classified as PERMANENT -> dead_letter on attempt 0
        # so it should NOT retry
        assert call_count == 1, "Permanent errors should not be retried"

    @pytest.mark.asyncio
    async def test_retry_on_asyncio_timeout(self, job_db):
        """asyncio.TimeoutError (node-level timeout) should be retried."""
        worker = _make_worker()
        worker.queue = JobQueue()

        job = worker.queue.create_job(
            workspace_id="ws-test",
            title="timeout job",
            description="times out",
            source="api",
            requester=None,
        )
        nodes = worker.queue.create_nodes(job.id, [
            {
                "title": "Timeout node",
                "instructions": "Will timeout then succeed",
                "retry_policy_json": RetryPolicy(max_attempts=3, backoff_ms=[0, 0, 0]).to_json(),
                "timeout_sec": 5,
            },
        ])

        call_count = 0

        async def mock_execute_node(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            return NodeResult(
                success=True,
                output={"result": "done"},
                tokens_in=50,
                tokens_out=25,
            )

        worker.executor.execute_node = mock_execute_node

        result = await worker._execute_nodes(job, nodes)

        assert result is True, "Should succeed after timeout retry"
        assert call_count == 2


class TestErrorClassificationForRetry:
    """Verify that error classification drives retry decisions correctly."""

    def test_timeout_error_classified_as_timeout(self):
        from backend.core.error_strategy import classify_error, ErrorCategory
        cat = classify_error(asyncio.TimeoutError("timed out"))
        assert cat == ErrorCategory.TIMEOUT

    def test_runtime_with_timeout_text_classified_as_transient(self):
        """RuntimeError wrapping an Ollama timeout should classify as TRANSIENT (default)."""
        from backend.core.error_strategy import classify_error, ErrorCategory
        cat = classify_error(RuntimeError("Ollama call failed: Ollama call timed out"))
        assert cat == ErrorCategory.TRANSIENT

    def test_file_not_found_classified_as_permanent(self):
        from backend.core.error_strategy import classify_error, ErrorCategory
        cat = classify_error(FileNotFoundError("no such file"))
        assert cat == ErrorCategory.PERMANENT

    def test_connection_error_classified_as_transient(self):
        from backend.core.error_strategy import classify_error, ErrorCategory
        cat = classify_error(ConnectionError("refused"))
        assert cat == ErrorCategory.TRANSIENT
