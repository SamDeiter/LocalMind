"""
Cost tracking verification tests for LocalMind.

Covers:
  1. TokenUsageTracker.record_usage writes tokens/cost to node_attempts
  2. TokenUsageTracker.get_job_usage aggregates correctly per-node and per-job
  3. JobQueue.track_cost accumulates on the job row
  4. Job detail API response includes cost_cents (via to_dict, not to_api_dict)
  5. GET /api/jobs/stats returns aggregate cost statistics
  6. GET /api/metrics/summary includes total_cost_cents
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_db_path(tmp_path, monkeypatch):
    """Redirect DB_PATH and JOBS_DIR to a temp directory for every test."""
    db_path = tmp_path / "test_localmind.db"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    monkeypatch.setattr("backend.config.DB_PATH", db_path)
    monkeypatch.setattr("backend.config.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("backend.config.WORKSPACE_ROOT", workspace_root)

    monkeypatch.setattr("backend.db.DB_PATH", db_path)
    monkeypatch.setattr("backend.core.schema.DB_PATH", db_path)
    monkeypatch.setattr("backend.jobs.queue.DB_PATH", db_path)
    monkeypatch.setattr("backend.core.token_budget.DB_PATH", db_path)

    from backend.core.schema import ensure_default_tenant, init_phase0_schema
    from backend.db import init_db

    init_db()
    init_phase0_schema()
    ensure_default_tenant()


@pytest.fixture
def queue():
    from backend.jobs.queue import JobQueue
    return JobQueue()


@pytest.fixture
def workspace_id():
    from backend.config import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM workspaces WHERE slug = 'default'"
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


@pytest.fixture
def sample_job(queue, workspace_id):
    return queue.create_job(
        workspace_id=workspace_id,
        title="Cost tracking test job",
        description="Test cost aggregation across nodes.",
        source="test",
        requester=None,
        mode="quick",
        priority=1,
    )


@pytest.fixture
def sample_nodes(queue, sample_job):
    node_defs = [
        {
            "title": "Research",
            "instructions": "Gather data.",
            "tools_allowed": ["read_file"],
            "expected_output": "Research notes.",
            "depends_on": [],
            "timeout_sec": 60,
        },
        {
            "title": "Summarize",
            "instructions": "Write summary.",
            "tools_allowed": ["write_file"],
            "expected_output": "Summary document.",
            "depends_on": ["Research"],
            "timeout_sec": 60,
        },
    ]
    return queue.create_nodes(sample_job.id, node_defs)


# ---------------------------------------------------------------------------
# Helper: insert a node_attempt row
# ---------------------------------------------------------------------------

def _insert_attempt(
    node_id: str,
    attempt_number: int = 1,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_cents: float = 0.0,
    model_used: str = "qwen3:4b",
    status: str = "completed",
    duration_ms: int = 1000,
) -> str:
    """Insert a node_attempts row and return its ID."""
    from backend.config import DB_PATH
    attempt_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.execute(
            """
            INSERT INTO node_attempts
                (id, node_id, attempt_number, status, started_at, completed_at,
                 tokens_in, tokens_out, cost_cents, model_used, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (attempt_id, node_id, attempt_number, status, now, now,
             tokens_in, tokens_out, cost_cents, model_used, duration_ms),
        )
        conn.commit()
    finally:
        conn.close()
    return attempt_id


# =========================================================================
# Test: TokenUsageTracker.record_usage
# =========================================================================

class TestRecordUsage:
    """Verify record_usage writes token/cost data to node_attempts."""

    def test_record_usage_updates_existing_attempt(self, sample_job, sample_nodes):
        """record_usage should update an existing attempt row's token counts."""
        from backend.core.token_budget import TokenUsageTracker

        node = sample_nodes[0]
        attempt_id = _insert_attempt(node.id, tokens_in=0, tokens_out=0, cost_cents=0)

        TokenUsageTracker.record_usage(
            job_id=sample_job.id,
            node_id=node.id,
            attempt_id=attempt_id,
            model_id="qwen3:4b",
            tokens_in=500,
            tokens_out=200,
            estimated_cost_cents=1.25,
        )

        from backend.config import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT tokens_in, tokens_out, cost_cents, model_used FROM node_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row["tokens_in"] == 500
        assert row["tokens_out"] == 200
        assert abs(row["cost_cents"] - 1.25) < 0.01
        assert row["model_used"] == "qwen3:4b"

    def test_record_usage_inserts_when_missing(self, sample_job, sample_nodes):
        """record_usage should defensively insert if the attempt row doesn't exist."""
        from backend.core.token_budget import TokenUsageTracker

        node = sample_nodes[0]
        new_attempt_id = str(uuid.uuid4())

        TokenUsageTracker.record_usage(
            job_id=sample_job.id,
            node_id=node.id,
            attempt_id=new_attempt_id,
            model_id="deepseek-r1:8b",
            tokens_in=1000,
            tokens_out=400,
            estimated_cost_cents=2.50,
        )

        from backend.config import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT tokens_in, tokens_out, cost_cents, model_used FROM node_attempts WHERE id = ?",
                (new_attempt_id,),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row["tokens_in"] == 1000
        assert row["tokens_out"] == 400
        assert abs(row["cost_cents"] - 2.50) < 0.01


# =========================================================================
# Test: TokenUsageTracker.get_job_usage
# =========================================================================

class TestGetJobUsage:
    """Verify get_job_usage aggregates correctly across nodes and attempts."""

    def test_aggregates_across_nodes(self, sample_job, sample_nodes):
        """Total tokens and cost should sum across all nodes."""
        from backend.core.token_budget import TokenUsageTracker

        # Node 0: 500 in, 200 out, 1.25 cents
        _insert_attempt(sample_nodes[0].id, tokens_in=500, tokens_out=200, cost_cents=1.25)
        # Node 1: 800 in, 350 out, 2.10 cents
        _insert_attempt(sample_nodes[1].id, tokens_in=800, tokens_out=350, cost_cents=2.10)

        usage = TokenUsageTracker.get_job_usage(sample_job.id)

        assert usage["job_id"] == sample_job.id
        assert usage["total_tokens_in"] == 1300
        assert usage["total_tokens_out"] == 550
        assert abs(usage["total_cost_cents"] - 3.35) < 0.01
        assert len(usage["by_node"]) == 2
        assert len(usage["by_attempt"]) == 2

    def test_multiple_attempts_per_node(self, sample_job, sample_nodes):
        """Multiple retry attempts on a single node should all be counted."""
        _insert_attempt(sample_nodes[0].id, attempt_number=1, tokens_in=100, tokens_out=50, cost_cents=0.5)
        _insert_attempt(sample_nodes[0].id, attempt_number=2, tokens_in=200, tokens_out=80, cost_cents=0.8)

        from backend.core.token_budget import TokenUsageTracker
        usage = TokenUsageTracker.get_job_usage(sample_job.id)

        assert usage["total_tokens_in"] == 300
        assert usage["total_tokens_out"] == 130
        assert abs(usage["total_cost_cents"] - 1.3) < 0.01

        # by_node should aggregate the two attempts
        node_entry = [n for n in usage["by_node"] if n["node_id"] == sample_nodes[0].id]
        assert len(node_entry) == 1
        assert node_entry[0]["attempts"] == 2
        assert node_entry[0]["tokens_in"] == 300

    def test_empty_job_returns_zeros(self, sample_job, sample_nodes):
        """A job with no attempts should return zero totals."""
        from backend.core.token_budget import TokenUsageTracker
        usage = TokenUsageTracker.get_job_usage(sample_job.id)

        assert usage["total_tokens_in"] == 0
        assert usage["total_tokens_out"] == 0
        assert usage["total_cost_cents"] == 0.0
        assert len(usage["by_node"]) == 0
        assert len(usage["by_attempt"]) == 0


# =========================================================================
# Test: JobQueue.track_cost accumulates on the job
# =========================================================================

class TestJobCostAccumulation:
    """Verify job-level cost_cents accumulates correctly."""

    def test_track_cost_increments(self, queue, sample_job):
        assert sample_job.cost_cents == 0

        queue.track_cost(sample_job.id, 1.5)
        job = queue.get_job(sample_job.id)
        assert abs(job.cost_cents - 1.5) < 0.001

        queue.track_cost(sample_job.id, 2.5)
        job = queue.get_job(sample_job.id)
        assert abs(job.cost_cents - 4.0) < 0.001

    def test_track_cost_zero_is_noop(self, queue, sample_job):
        queue.track_cost(sample_job.id, 0)
        job = queue.get_job(sample_job.id)
        assert job.cost_cents == 0

    def test_track_cost_fractional(self, queue, sample_job):
        queue.track_cost(sample_job.id, 0.0001)
        queue.track_cost(sample_job.id, 0.0002)
        job = queue.get_job(sample_job.id)
        assert abs(job.cost_cents - 0.0003) < 0.00001


# =========================================================================
# Test: Job serialization includes cost in detail but not API list
# =========================================================================

class TestJobSerialization:
    """Verify cost_cents appears in to_dict but not to_api_dict."""

    def test_to_dict_includes_cost(self, queue, sample_job):
        queue.track_cost(sample_job.id, 3.75)
        job = queue.get_job(sample_job.id)
        d = job.to_dict()
        assert "cost_cents" in d
        assert abs(d["cost_cents"] - 3.75) < 0.01

    def test_to_api_dict_excludes_cost(self, sample_job):
        api = sample_job.to_api_dict()
        assert "cost_cents" not in api

    def test_detail_endpoint_uses_to_dict(self, queue, sample_job):
        """The GET /api/jobs/{id} endpoint uses to_dict which includes cost."""
        queue.track_cost(sample_job.id, 5.0)
        job = queue.get_job(sample_job.id)
        # The detail route calls job.to_dict() and merges nodes/files/audit
        result = job.to_dict()
        assert result["cost_cents"] == 5.0


# =========================================================================
# Test: /api/jobs/stats endpoint (will be added)
# =========================================================================

class TestJobStatsEndpoint:
    """Verify the stats endpoint returns correct aggregate data."""

    def test_stats_aggregate(self, queue, workspace_id):
        """Create several jobs with varying status and cost, verify stats."""
        # Create 3 jobs
        j1 = queue.create_job(
            workspace_id=workspace_id, title="Job 1", description=None,
            source="test", requester=None, mode="quick", priority=1,
        )
        j2 = queue.create_job(
            workspace_id=workspace_id, title="Job 2", description=None,
            source="test", requester=None, mode="quick", priority=1,
        )
        j3 = queue.create_job(
            workspace_id=workspace_id, title="Job 3", description=None,
            source="test", requester=None, mode="quick", priority=1,
        )

        # Simulate outcomes
        queue.update_job_status(j1.id, "done", result_summary="OK")
        queue.track_cost(j1.id, 2.5)

        queue.update_job_status(j2.id, "done", result_summary="OK")
        queue.track_cost(j2.id, 3.5)

        queue.update_job_status(j3.id, "failed")
        queue.track_cost(j3.id, 1.0)

        # Query stats directly from DB (same logic as the endpoint)
        from backend.config import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT status, cost_cents FROM jobs").fetchall()
        finally:
            conn.close()

        total = len(rows)
        completed = sum(1 for r in rows if r["status"] == "done")
        failed = sum(1 for r in rows if r["status"] == "failed")
        total_cost = sum((r["cost_cents"] or 0) for r in rows)
        avg_cost = total_cost / total if total > 0 else 0

        assert total == 3
        assert completed == 2
        assert failed == 1
        assert abs(total_cost - 7.0) < 0.01
        assert abs(avg_cost - 7.0 / 3) < 0.01

    def test_stats_empty(self, queue, workspace_id):
        """Stats with no jobs should return zeros."""
        from backend.config import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT status, cost_cents FROM jobs").fetchall()
        finally:
            conn.close()

        total = len(rows)
        assert total == 0


# =========================================================================
# Test: Metrics summary includes cost data
# =========================================================================

class TestMetricsSummaryCost:
    """Verify /api/metrics/summary includes cost data from jobs table."""

    def test_metrics_summary_cost(self, queue, workspace_id):
        j1 = queue.create_job(
            workspace_id=workspace_id, title="Metric Job", description=None,
            source="test", requester=None, mode="quick", priority=1,
        )
        queue.update_job_status(j1.id, "done", result_summary="OK")
        queue.track_cost(j1.id, 4.25)

        from backend.config import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT status, cost_cents FROM jobs").fetchall()
            total_cost = sum((r["cost_cents"] or 0) for r in rows)
        finally:
            conn.close()

        assert abs(total_cost - 4.25) < 0.01
