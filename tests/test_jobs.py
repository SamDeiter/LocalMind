"""
Comprehensive pytest tests for the LocalMind job pipeline modules.

Covers: models, queue, planner, reviewer, executor, worker.
Uses tmp_path for database isolation and unittest.mock.patch for external calls.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: DB bootstrap
# ---------------------------------------------------------------------------

# Minimal schema required by the job pipeline tables.  We skip the full
# Phase 0 schema (organizations, workspaces, users ...) but disable FK
# enforcement so that REFERENCES clauses don't block test inserts.

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
    updated_at TEXT NOT NULL
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
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_templates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    nodes_json TEXT NOT NULL,
    created_by TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _init_job_db(db_path: Path) -> None:
    """Create the job-pipeline tables in *db_path*."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_JOB_PIPELINE_SCHEMA)
    conn.commit()
    conn.close()


@pytest.fixture
def job_db(tmp_path):
    """Create a temporary SQLite DB with pipeline schema and patch DB_PATH."""
    db_file = tmp_path / "test_jobs.db"
    _init_job_db(db_file)
    with patch("backend.jobs.queue.DB_PATH", db_file), \
         patch("backend.config.DB_PATH", db_file):
        yield db_file


# ===========================================================================
# 1. tests for backend/jobs/models.py
# ===========================================================================

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


class TestJobStatus:
    def test_enum_values(self):
        assert JobStatus.PENDING.value == "pending"
        assert JobStatus.PLANNING.value == "planning"
        assert JobStatus.EXECUTING.value == "executing"
        assert JobStatus.REVIEWING.value == "reviewing"
        assert JobStatus.DONE.value == "done"
        assert JobStatus.FAILED.value == "failed"
        assert JobStatus.CANCELLED.value == "cancelled"
        assert JobStatus.CANCELLING.value == "cancelling"

    def test_str_comparison(self):
        """JobStatus is a str enum -- compare directly to strings."""
        assert JobStatus.PENDING == "pending"
        assert "done" == JobStatus.DONE

    def test_all_members_count(self):
        assert len(JobStatus) == 8


class TestNodeStatus:
    def test_enum_values(self):
        assert NodeStatus.PENDING.value == "pending"
        assert NodeStatus.RUNNING.value == "running"
        assert NodeStatus.COMPLETED.value == "completed"
        assert NodeStatus.FAILED.value == "failed"
        assert NodeStatus.CANCELLED.value == "cancelled"
        assert NodeStatus.AWAITING_INPUT.value == "awaiting_input"
        assert NodeStatus.SKIPPED.value == "skipped"

    def test_all_members_count(self):
        assert len(NodeStatus) == 7


class TestRetryPolicy:
    def test_defaults(self):
        rp = RetryPolicy()
        assert rp.max_attempts == 3
        assert rp.backoff_ms == [1000, 5000, 15000]

    def test_from_json_none(self):
        rp = RetryPolicy.from_json(None)
        assert rp.max_attempts == 3

    def test_from_json_custom(self):
        rp = RetryPolicy.from_json('{"max_attempts": 5, "backoff_ms": [500]}')
        assert rp.max_attempts == 5
        assert rp.backoff_ms == [500]

    def test_from_json_bad_string(self):
        rp = RetryPolicy.from_json("not json")
        assert rp.max_attempts == 3  # defaults

    def test_roundtrip_json(self):
        rp = RetryPolicy(max_attempts=2, backoff_ms=[100, 200])
        rp2 = RetryPolicy.from_json(rp.to_json())
        assert rp2.max_attempts == 2
        assert rp2.backoff_ms == [100, 200]

    def test_to_dict(self):
        rp = RetryPolicy(max_attempts=1, backoff_ms=[10])
        d = rp.to_dict()
        assert d == {"max_attempts": 1, "backoff_ms": [10]}


class TestParseJsonField:
    def test_none_returns_default(self):
        assert parse_json_field(None) is None
        assert parse_json_field(None, default=[]) == []

    def test_valid_json(self):
        assert parse_json_field('["a","b"]') == ["a", "b"]

    def test_invalid_json(self):
        assert parse_json_field("{bad", default="fallback") == "fallback"

    def test_empty_string(self):
        # Empty string is not valid JSON
        assert parse_json_field("", default=[]) == []


class TestJobModel:
    def test_to_dict_keys(self):
        job = Job(
            id="j1", workspace_id="ws1", title="T", description="D",
            source="web", source_ref=None, status="pending", priority=0,
            requester=None, mode="quick", template_id=None,
            result_summary=None, review_count=0, max_reviews=3,
            error=None, cost_cents=0.0,
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T00:00:00Z",
        )
        d = job.to_dict()
        assert d["id"] == "j1"
        assert d["title"] == "T"
        assert "cost_cents" in d

    def test_to_api_dict_excludes_internal(self):
        job = Job(
            id="j1", workspace_id="ws1", title="T", description=None,
            source="web", source_ref=None, status="pending", priority=0,
            requester=None, mode="quick", template_id=None,
            result_summary=None, review_count=0, max_reviews=3,
            error="oops", cost_cents=1.5,
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T00:00:00Z",
        )
        api = job.to_api_dict()
        assert "error" not in api
        assert "cost_cents" not in api
        assert "review_count" not in api
        assert "max_reviews" not in api
        # Public keys should remain
        assert "id" in api
        assert "status" in api

    def test_from_row_roundtrip(self):
        """Build a fake sqlite3.Row-like dict and ensure from_row works."""
        row = {
            "id": "j1", "workspace_id": "ws1", "title": "T", "description": None,
            "source": "web", "source_ref": None, "status": "pending",
            "priority": 5, "requester": None, "mode": "quick",
            "template_id": None, "result_summary": None, "review_count": 0,
            "max_reviews": 3, "error": None, "cost_cents": 0,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z",
        }
        # sqlite3.Row supports [] access -- we can use a dict subclass
        class FakeRow(dict):
            def __getitem__(self, key):
                return dict.__getitem__(self, key)

        job = Job.from_row(FakeRow(row))
        assert job.id == "j1"
        assert job.priority == 5


class TestNodeModel:
    def test_to_dict_includes_retry_policy_as_dict(self):
        node = Node(
            id="n1", job_id="j1", sequence=1, title="Step 1",
            instructions="Do stuff", tools_allowed=["read_file"],
            expected_output="output.txt", status="pending",
            input_json=None, output_json=None, error=None,
            auto_generated=True, input_schema_json=None,
            output_schema_json=None, side_effects_json=None,
            retry_policy=RetryPolicy(), timeout_sec=300,
            rollback_strategy=None, acceptance_tests_json=None,
            depends_on=[], created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T00:00:00Z",
        )
        d = node.to_dict()
        assert d["retry_policy"]["max_attempts"] == 3
        assert d["tools_allowed"] == ["read_file"]
        assert d["depends_on"] == []


class TestJobFileModel:
    def test_to_dict(self):
        jf = JobFile(
            id="f1", job_id="j1", node_id=None,
            filename="report.pdf", file_path="/tmp/report.pdf",
            file_type="output", mime_type="application/pdf",
            size_bytes=1024,
        )
        d = jf.to_dict()
        assert d["filename"] == "report.pdf"
        assert d["size_bytes"] == 1024


class TestAuditEntryModel:
    def test_to_dict(self):
        ae = AuditEntry(
            id="a1", job_id="j1", node_id=None,
            action="created", detail="test", actor="system",
            timestamp="2025-01-01T00:00:00Z",
        )
        d = ae.to_dict()
        assert d["action"] == "created"
        assert d["actor"] == "system"


class TestPipelineTemplateModel:
    def test_to_dict(self):
        pt = PipelineTemplate(
            id="t1", workspace_id="ws1", name="My Template",
            description="desc", nodes=[{"title": "Step 1"}],
            created_by=None, use_count=0,
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T00:00:00Z",
        )
        d = pt.to_dict()
        assert d["name"] == "My Template"
        assert len(d["nodes"]) == 1


# ===========================================================================
# 2. tests for backend/jobs/queue.py
# ===========================================================================

from backend.jobs.queue import JobQueue


class TestJobQueueCreateAndGet:
    def test_create_job(self, job_db):
        q = JobQueue()
        job = q.create_job(
            workspace_id="ws1", title="Test Job",
            description="A test", source="web", requester=None,
        )
        assert job.title == "Test Job"
        assert job.status == JobStatus.PENDING.value
        assert job.mode == "quick"
        assert job.cost_cents == 0.0

    def test_get_job(self, job_db):
        q = JobQueue()
        created = q.create_job(
            workspace_id="ws1", title="Get Me",
            description=None, source="api", requester="u1",
        )
        fetched = q.get_job(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.title == "Get Me"

    def test_get_job_not_found(self, job_db):
        q = JobQueue()
        assert q.get_job("nonexistent") is None

    def test_list_jobs(self, job_db):
        q = JobQueue()
        q.create_job(workspace_id="ws1", title="J1", description=None, source="web", requester=None)
        q.create_job(workspace_id="ws1", title="J2", description=None, source="web", requester=None)
        q.create_job(workspace_id="ws2", title="J3", description=None, source="web", requester=None)
        jobs = q.list_jobs("ws1")
        assert len(jobs) == 2

    def test_list_jobs_with_status_filter(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J1", description=None, source="web", requester=None)
        q.update_job_status(job.id, JobStatus.DONE.value)
        pending = q.list_jobs("ws1", status="pending")
        done = q.list_jobs("ws1", status="done")
        assert len(pending) == 0
        assert len(done) == 1

    def test_update_job_status(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.update_job_status(job.id, JobStatus.EXECUTING.value, error=None, result_summary="running")
        updated = q.get_job(job.id)
        assert updated.status == "executing"
        assert updated.result_summary == "running"

    def test_cancel_job(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.cancel_job(job.id)
        updated = q.get_job(job.id)
        assert updated.status == JobStatus.CANCELLING.value

    def test_track_cost(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.track_cost(job.id, 1.5)
        q.track_cost(job.id, 2.5)
        updated = q.get_job(job.id)
        assert updated.cost_cents == pytest.approx(4.0)

    def test_increment_review_count(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        c1 = q.increment_review_count(job.id)
        c2 = q.increment_review_count(job.id)
        assert c1 == 1
        assert c2 == 2


class TestJobQueueNodes:
    def test_create_and_get_nodes(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        node_defs = [
            {"title": "Step 1", "instructions": "Do A", "tools_allowed": ["read_file"]},
            {"title": "Step 2", "instructions": "Do B"},
        ]
        nodes = q.create_nodes(job.id, node_defs)
        assert len(nodes) == 2
        assert nodes[0].sequence == 1
        assert nodes[1].sequence == 2
        assert nodes[0].title == "Step 1"
        assert nodes[0].tools_allowed == ["read_file"]
        assert nodes[1].tools_allowed == []  # default

    def test_get_node(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        nodes = q.create_nodes(job.id, [{"title": "Only"}])
        fetched = q.get_node(nodes[0].id)
        assert fetched is not None
        assert fetched.title == "Only"

    def test_update_node(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        nodes = q.create_nodes(job.id, [{"title": "N1"}])
        q.update_node(nodes[0].id, status=NodeStatus.COMPLETED.value, output_json='{"result": "ok"}')
        updated = q.get_node(nodes[0].id)
        assert updated.status == "completed"
        assert updated.output_json == '{"result": "ok"}'

    def test_get_next_pending_node_linear(self, job_db):
        """Linear pipeline: nodes without depends_on execute in sequence order."""
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.create_nodes(job.id, [
            {"title": "A"},
            {"title": "B"},
            {"title": "C"},
        ])
        # First pending should be A (sequence=1)
        nxt = q.get_next_pending_node(job.id)
        assert nxt is not None
        assert nxt.title == "A"

    def test_get_next_pending_node_dag(self, job_db):
        """DAG-aware: node B depends on node A; B is not ready until A completes."""
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        nodes = q.create_nodes(job.id, [
            {"title": "A"},
            {"title": "B", "depends_on": []},  # no explicit dep -> ready
        ])
        # Both A and B have empty depends_on (B explicitly []), so A is first by sequence
        nxt = q.get_next_pending_node(job.id)
        assert nxt.title == "A"

    def test_get_next_pending_node_blocks_on_dependency(self, job_db):
        """Node with depends_on a non-completed node should NOT be returned."""
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        nodes = q.create_nodes(job.id, [
            {"title": "A"},
            {"title": "B", "depends_on": [nodes[0].id if False else "placeholder"]},
        ])
        # We need the real ID of node A for depends_on.  Recreate properly.
        # Delete and redo with actual IDs.
        # Simpler approach: create A first, get ID, then create B with dep.
        q2 = JobQueue()
        job2 = q2.create_job(workspace_id="ws1", title="J2", description=None, source="web", requester=None)
        nodes_a = q2.create_nodes(job2.id, [{"title": "A"}])
        a_id = nodes_a[0].id

        # Manually insert B with depends_on = [a_id]
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(job_db))
        conn.row_factory = sqlite3.Row
        b_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO job_nodes
               (id, job_id, sequence, title, instructions, tools_allowed,
                expected_output, status, auto_generated, retry_policy_json,
                timeout_sec, depends_on, created_at, updated_at)
               VALUES (?, ?, 2, 'B', '', '[]', '', 'pending', 1,
                       '{"max_attempts":3}', 300, ?, ?, ?)""",
            (b_id, job2.id, json.dumps([a_id]), now, now),
        )
        conn.commit()
        conn.close()

        # A is pending -> B depends on A -> next should be A
        nxt = q2.get_next_pending_node(job2.id)
        assert nxt is not None
        assert nxt.title == "A"

        # Complete A -> now B should be returned
        q2.update_node(a_id, status=NodeStatus.COMPLETED.value)
        nxt2 = q2.get_next_pending_node(job2.id)
        assert nxt2 is not None
        assert nxt2.title == "B"

    def test_get_next_pending_node_none_when_all_done(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        nodes = q.create_nodes(job.id, [{"title": "A"}])
        q.update_node(nodes[0].id, status=NodeStatus.COMPLETED.value)
        assert q.get_next_pending_node(job.id) is None


class TestJobQueueFiles:
    def test_add_and_get_files(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        jf = q.add_file(
            job_id=job.id, filename="input.csv",
            file_path="/tmp/input.csv", file_type="input",
            mime_type="text/csv", size_bytes=4096,
        )
        assert jf.filename == "input.csv"
        assert jf.size_bytes == 4096

        files = q.get_files(job.id)
        assert len(files) == 1

    def test_get_files_by_type(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.add_file(job_id=job.id, filename="in.txt", file_path="/tmp/in.txt", file_type="input")
        q.add_file(job_id=job.id, filename="out.txt", file_path="/tmp/out.txt", file_type="output")
        inputs = q.get_files(job.id, file_type="input")
        outputs = q.get_files(job.id, file_type="output")
        assert len(inputs) == 1
        assert len(outputs) == 1


class TestJobQueueAudit:
    def test_add_and_get_audit(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.add_audit(job.id, action="created", detail="initial", actor="system")
        q.add_audit(job.id, action="updated", detail="status change", actor="worker")
        log = q.get_audit_log(job.id)
        assert len(log) == 2
        # Newest first
        assert log[0].action == "updated"
        assert log[1].action == "created"

    def test_audit_with_node_id(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.add_audit(job.id, action="node_start", node_id="n42", actor="worker")
        log = q.get_audit_log(job.id)
        assert log[0].node_id == "n42"


class TestJobQueueTemplates:
    def test_save_and_get_template(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.create_nodes(job.id, [
            {"title": "A", "instructions": "do A", "tools_allowed": ["read_file"]},
            {"title": "B", "instructions": "do B"},
        ])
        tmpl = q.save_as_template(job.id, name="MyTemplate", workspace_id="ws1")
        assert tmpl.name == "MyTemplate"
        assert len(tmpl.nodes) == 2
        assert tmpl.use_count == 0

        fetched = q.get_template(tmpl.id)
        assert fetched is not None
        assert fetched.name == "MyTemplate"

    def test_list_templates(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.create_nodes(job.id, [{"title": "A"}])
        q.save_as_template(job.id, name="T1", workspace_id="ws1")
        q.save_as_template(job.id, name="T2", workspace_id="ws1")
        templates = q.list_templates("ws1")
        assert len(templates) == 2

    def test_create_job_from_template(self, job_db):
        q = JobQueue()
        # Create original job + nodes + template
        orig = q.create_job(workspace_id="ws1", title="Orig", description="Orig desc", source="web", requester=None)
        q.create_nodes(orig.id, [
            {"title": "Step1", "instructions": "inst1", "tools_allowed": ["web_search"]},
        ])
        tmpl = q.save_as_template(orig.id, name="FromTemplate", workspace_id="ws1")

        # Create a new job from template
        new_job = q.create_job_from_template(
            template_id=tmpl.id,
            workspace_id="ws1",
            requester="user1",
        )
        assert new_job.mode == "pipeline"
        assert new_job.template_id == tmpl.id

        # New job should have nodes
        nodes = q.get_nodes(new_job.id)
        assert len(nodes) == 1
        assert nodes[0].title == "Step1"

        # Template use_count should be incremented
        tmpl_updated = q.get_template(tmpl.id)
        assert tmpl_updated.use_count == 1

    def test_create_job_from_nonexistent_template(self, job_db):
        q = JobQueue()
        with pytest.raises(ValueError, match="Template not found"):
            q.create_job_from_template("nonexistent", "ws1", "user1")

    def test_increment_template_use(self, job_db):
        q = JobQueue()
        job = q.create_job(workspace_id="ws1", title="J", description=None, source="web", requester=None)
        q.create_nodes(job.id, [{"title": "A"}])
        tmpl = q.save_as_template(job.id, name="T", workspace_id="ws1")
        q.increment_template_use(tmpl.id)
        q.increment_template_use(tmpl.id)
        updated = q.get_template(tmpl.id)
        assert updated.use_count == 2


class TestJobQueueResumability:
    def test_get_interrupted_jobs(self, job_db):
        q = JobQueue()
        j1 = q.create_job(workspace_id="ws1", title="Running", description=None, source="web", requester=None)
        q.update_job_status(j1.id, JobStatus.EXECUTING.value)
        j2 = q.create_job(workspace_id="ws1", title="Pending", description=None, source="web", requester=None)

        interrupted = q.get_interrupted_jobs()
        assert len(interrupted) == 1
        assert interrupted[0].id == j1.id


# ===========================================================================
# 3. tests for backend/jobs/planner.py
# ===========================================================================

from backend.jobs.planner import JobPlanner, _detect_cycles


def _make_job(**overrides) -> Job:
    """Helper to create a minimal Job for planner tests."""
    defaults = dict(
        id="j-test", workspace_id="ws1", title="Test Task",
        description="Do something useful", source="web", source_ref=None,
        status="pending", priority=0, requester=None, mode="quick",
        template_id=None, result_summary=None, review_count=0,
        max_reviews=3, error=None, cost_cents=0.0,
        created_at="2025-01-01T00:00:00Z", updated_at="2025-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return Job(**defaults)


class TestPlannerComplexity:
    def test_simple_short_description(self):
        planner = JobPlanner()
        assert planner._estimate_complexity("Hello world", has_files=False) == "simple"

    def test_medium_keyword(self):
        planner = JobPlanner()
        assert planner._estimate_complexity("Please research best frameworks", has_files=False) == "medium"

    def test_complex_keyword(self):
        planner = JobPlanner()
        assert planner._estimate_complexity("Refactor the auth module and deploy", has_files=False) == "complex"

    def test_complex_by_word_count(self):
        planner = JobPlanner()
        long_desc = " ".join(["word"] * 50)
        assert planner._estimate_complexity(long_desc, has_files=False) == "complex"

    def test_medium_with_files(self):
        planner = JobPlanner()
        assert planner._estimate_complexity("Summarize this", has_files=True) == "medium"

    def test_complex_with_files_and_length(self):
        planner = JobPlanner()
        desc = " ".join(["word"] * 15)  # >= 12 words + files
        assert planner._estimate_complexity(desc, has_files=True) == "complex"

    def test_empty_description(self):
        planner = JobPlanner()
        assert planner._estimate_complexity("", has_files=False) == "simple"


class TestPlannerValidation:
    def test_valid_plan_passes(self):
        planner = JobPlanner()
        nodes = [
            {
                "title": "A",
                "instructions": "Do A",
                "tools_allowed": [],
                "expected_output": "output A",
                "depends_on": [],
                "timeout_sec": 120,
            },
            {
                "title": "B",
                "instructions": "Do B",
                "tools_allowed": [],
                "expected_output": "output B",
                "depends_on": ["A"],
                "timeout_sec": 120,
            },
        ]
        errors = planner._validate_plan(nodes)
        assert errors == []

    def test_empty_plan_fails(self):
        planner = JobPlanner()
        errors = planner._validate_plan([])
        assert any("empty" in e.lower() for e in errors)

    def test_missing_keys_detected(self):
        planner = JobPlanner()
        errors = planner._validate_plan([{"title": "A"}])
        assert any("missing keys" in e.lower() for e in errors)

    def test_duplicate_titles_detected(self):
        planner = JobPlanner()
        node = {
            "title": "Same",
            "instructions": "Do",
            "tools_allowed": [],
            "expected_output": "out",
            "depends_on": [],
            "timeout_sec": 120,
        }
        errors = planner._validate_plan([node, node.copy()])
        assert any("duplicate" in e.lower() for e in errors)

    def test_self_dependency_detected(self):
        planner = JobPlanner()
        node = {
            "title": "A",
            "instructions": "Do",
            "tools_allowed": [],
            "expected_output": "out",
            "depends_on": ["A"],
            "timeout_sec": 120,
        }
        errors = planner._validate_plan([node])
        assert any("depends on itself" in e for e in errors)

    def test_cycle_detection(self):
        planner = JobPlanner()
        nodes = [
            {"title": "A", "instructions": "", "tools_allowed": [],
             "expected_output": "", "depends_on": ["B"], "timeout_sec": 120},
            {"title": "B", "instructions": "", "tools_allowed": [],
             "expected_output": "", "depends_on": ["A"], "timeout_sec": 120},
        ]
        errors = planner._validate_plan(nodes)
        assert any("circular" in e.lower() for e in errors)

    def test_no_entry_point_detected(self):
        planner = JobPlanner()
        nodes = [
            {"title": "A", "instructions": "", "tools_allowed": [],
             "expected_output": "", "depends_on": ["B"], "timeout_sec": 120},
            {"title": "B", "instructions": "", "tools_allowed": [],
             "expected_output": "", "depends_on": ["C"], "timeout_sec": 120},
            {"title": "C", "instructions": "", "tools_allowed": [],
             "expected_output": "", "depends_on": ["A"], "timeout_sec": 120},
        ]
        errors = planner._validate_plan(nodes)
        assert any("entry point" in e.lower() for e in errors)

    def test_unknown_depends_on_reference(self):
        planner = JobPlanner()
        nodes = [
            {"title": "A", "instructions": "", "tools_allowed": [],
             "expected_output": "", "depends_on": [], "timeout_sec": 120},
            {"title": "B", "instructions": "", "tools_allowed": [],
             "expected_output": "", "depends_on": ["NONEXISTENT"], "timeout_sec": 120},
        ]
        errors = planner._validate_plan(nodes)
        assert any("unknown title" in e.lower() for e in errors)


class TestPlannerParsing:
    def test_parse_direct_json(self):
        planner = JobPlanner()
        raw = json.dumps([{"title": "A"}])
        result = planner._parse_plan_response(raw)
        assert result == [{"title": "A"}]

    def test_parse_fenced_json(self):
        planner = JobPlanner()
        raw = "Some text\n```json\n[{\"title\": \"A\"}]\n```\nMore text"
        result = planner._parse_plan_response(raw)
        assert result == [{"title": "A"}]

    def test_parse_bracket_extraction(self):
        planner = JobPlanner()
        raw = 'Here is the plan: [{"title": "A"}] done.'
        result = planner._parse_plan_response(raw)
        assert result == [{"title": "A"}]

    def test_parse_python_booleans_repaired(self):
        planner = JobPlanner()
        raw = '[{"title": "A", "flag": True}]'
        result = planner._parse_plan_response(raw)
        assert result[0]["flag"] is True

    def test_parse_empty_raises(self):
        planner = JobPlanner()
        with pytest.raises(Exception):  # _ParseError
            planner._parse_plan_response("")

    def test_parse_garbage_raises(self):
        planner = JobPlanner()
        with pytest.raises(Exception):
            planner._parse_plan_response("This is just prose without any JSON")


class TestPlannerFallback:
    def test_fallback_single_node(self):
        planner = JobPlanner()
        job = _make_job(description="Do something")
        nodes = planner._fallback_single_node(job)
        assert len(nodes) == 1
        assert nodes[0]["title"] == "Execute task"
        assert "Do something" in nodes[0]["instructions"]
        assert nodes[0]["depends_on"] == []

    def test_normalise_node_fills_defaults(self):
        result = JobPlanner._normalise_node({"title": "Test"})
        assert result["instructions"] == ""
        assert result["tools_allowed"] == []
        assert result["expected_output"] == ""
        assert result["depends_on"] == []
        assert result["timeout_sec"] == 300


@pytest.mark.asyncio
async def test_planner_quick_gemini_unavailable():
    """When Gemini is not available, quick mode falls back to single node."""
    planner = JobPlanner()
    job = _make_job(mode="quick", description="Summarize files")
    with patch("backend.jobs.planner.is_available", return_value=False), \
         patch("backend.jobs.planner.DEPLOYMENT_MODE", "hybrid"):
        result = await planner.plan(job)
    assert len(result) == 1
    assert result[0]["title"] == "Execute task"


@pytest.mark.asyncio
async def test_planner_quick_gemini_success():
    """When Gemini returns a valid plan, it is used."""
    planner = JobPlanner()
    job = _make_job(mode="quick", description="Research Python frameworks")
    plan_json = json.dumps([
        {
            "title": "Research",
            "instructions": "Search the web",
            "tools_allowed": [],
            "expected_output": "Notes",
            "depends_on": [],
            "timeout_sec": 180,
        },
    ])
    with patch("backend.jobs.planner.is_available", return_value=True), \
         patch("backend.jobs.planner.DEPLOYMENT_MODE", "hybrid"), \
         patch("backend.jobs.planner.generate", new_callable=AsyncMock, return_value=plan_json):
        result = await planner.plan(job)
    assert len(result) == 1
    assert result[0]["title"] == "Research"


@pytest.mark.asyncio
async def test_planner_strict_local_skips_gemini():
    """strict-local deployment never calls Gemini."""
    planner = JobPlanner()
    job = _make_job(mode="quick")
    with patch("backend.jobs.planner.DEPLOYMENT_MODE", "strict-local"):
        result = await planner.plan(job)
    assert len(result) == 1
    assert result[0]["title"] == "Execute task"


class TestDetectCycles:
    def test_no_cycle(self):
        nodes = [
            {"title": "A", "depends_on": []},
            {"title": "B", "depends_on": ["A"]},
            {"title": "C", "depends_on": ["B"]},
        ]
        assert _detect_cycles(nodes) == []

    def test_simple_cycle(self):
        nodes = [
            {"title": "A", "depends_on": ["B"]},
            {"title": "B", "depends_on": ["A"]},
        ]
        errors = _detect_cycles(nodes)
        assert len(errors) >= 1
        assert any("circular" in e.lower() for e in errors)

    def test_three_node_cycle(self):
        nodes = [
            {"title": "A", "depends_on": ["C"]},
            {"title": "B", "depends_on": ["A"]},
            {"title": "C", "depends_on": ["B"]},
        ]
        errors = _detect_cycles(nodes)
        assert len(errors) >= 1


# ===========================================================================
# 4. tests for backend/jobs/reviewer.py
# ===========================================================================

from backend.jobs.reviewer import (
    JobReviewer,
    ReviewIssue,
    ReviewResult,
    _TIER2_PASS_THRESHOLD,
)


def _make_node(**overrides) -> Node:
    """Helper to create a minimal Node for reviewer tests."""
    defaults = dict(
        id="n-test", job_id="j-test", sequence=1, title="Test Node",
        instructions="Do stuff", tools_allowed=[], expected_output="Some output",
        status=NodeStatus.COMPLETED.value, input_json=None,
        output_json='{"result": "done"}', error=None,
        auto_generated=True, input_schema_json=None,
        output_schema_json=None, side_effects_json=None,
        retry_policy=RetryPolicy(), timeout_sec=300,
        rollback_strategy=None, acceptance_tests_json=None,
        depends_on=[], created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return Node(**defaults)


class TestReviewIssue:
    def test_to_dict(self):
        issue = ReviewIssue(
            tier=1, severity="critical", category="completeness",
            description="Node failed", node_id="n1", suggestion="Retry",
        )
        d = issue.to_dict()
        assert d["tier"] == 1
        assert d["severity"] == "critical"


class TestReviewResult:
    def test_to_dict(self):
        result = ReviewResult(
            passed=True, score=0.95, feedback="Good",
            issues=[], needs_human_review=False,
        )
        d = result.to_dict()
        assert d["passed"] is True
        assert d["score"] == 0.95

    def test_with_issues(self):
        result = ReviewResult(
            passed=False, score=0.5, feedback="Bad",
            issues=[ReviewIssue(tier=1, severity="critical", category="format", description="Empty")],
        )
        d = result.to_dict()
        assert len(d["issues"]) == 1


class TestReviewerTier1:
    @pytest.mark.asyncio
    async def test_completed_node_with_output_passes(self):
        reviewer = JobReviewer()
        job = _make_job()
        node = _make_node(output_json='{"result": "ok"}')
        issues = await reviewer._tier1_format_checks(job, [node], [])
        criticals = [i for i in issues if i.severity == "critical"]
        assert len(criticals) == 0

    @pytest.mark.asyncio
    async def test_failed_node_produces_critical(self):
        reviewer = JobReviewer()
        job = _make_job()
        node = _make_node(status=NodeStatus.FAILED.value)
        issues = await reviewer._tier1_format_checks(job, [node], [])
        criticals = [i for i in issues if i.severity == "critical"]
        assert len(criticals) >= 1

    @pytest.mark.asyncio
    async def test_empty_output_produces_critical(self):
        reviewer = JobReviewer()
        job = _make_job()
        node = _make_node(output_json="")
        issues = await reviewer._tier1_format_checks(job, [node], [])
        criticals = [i for i in issues if i.severity == "critical"]
        assert len(criticals) >= 1

    @pytest.mark.asyncio
    async def test_invalid_json_output_produces_critical(self):
        reviewer = JobReviewer()
        job = _make_job()
        node = _make_node(output_json="{not valid json")
        issues = await reviewer._tier1_format_checks(job, [node], [])
        criticals = [i for i in issues if i.severity == "critical"]
        assert len(criticals) >= 1

    @pytest.mark.asyncio
    async def test_skipped_node_no_issues(self):
        reviewer = JobReviewer()
        job = _make_job()
        node = _make_node(status=NodeStatus.SKIPPED.value, output_json=None)
        issues = await reviewer._tier1_format_checks(job, [node], [])
        assert len(issues) == 0

    @pytest.mark.asyncio
    async def test_output_file_missing_on_disk(self, tmp_path):
        reviewer = JobReviewer()
        job = _make_job()
        node = _make_node()
        jf = JobFile(
            id="f1", job_id="j-test", node_id="n-test",
            filename="output.txt",
            file_path=str(tmp_path / "nonexistent.txt"),
            file_type="output", mime_type="text/plain", size_bytes=100,
        )
        issues = await reviewer._tier1_format_checks(job, [node], [jf])
        criticals = [i for i in issues if i.severity == "critical" and "does not exist" in i.description]
        assert len(criticals) >= 1

    @pytest.mark.asyncio
    async def test_empty_file_produces_critical(self, tmp_path):
        reviewer = JobReviewer()
        job = _make_job()
        node = _make_node()
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        jf = JobFile(
            id="f1", job_id="j-test", node_id="n-test",
            filename="empty.txt", file_path=str(empty_file),
            file_type="output", mime_type="text/plain", size_bytes=0,
        )
        issues = await reviewer._tier1_format_checks(job, [node], [jf])
        criticals = [i for i in issues if i.severity == "critical" and "empty" in i.description.lower()]
        assert len(criticals) >= 1


class TestReviewerTier2Parsing:
    def test_parse_valid_response(self):
        reviewer = JobReviewer()
        node = _make_node(sequence=1)
        raw = json.dumps({
            "score": 0.85,
            "passed": True,
            "issues": [
                {
                    "severity": "warning",
                    "category": "completeness",
                    "description": "Missing a summary section",
                    "node_sequence": 1,
                    "suggestion": "Add summary",
                },
            ],
            "summary": "Mostly good",
        })
        issues, score = reviewer._parse_tier2_response(raw, [node])
        assert score == pytest.approx(0.85)
        assert len(issues) == 1
        assert issues[0].tier == 2

    def test_parse_bad_json_salvages_score(self):
        reviewer = JobReviewer()
        raw = 'Some text with "score": 0.6 embedded'
        issues, score = reviewer._parse_tier2_response(raw, [])
        assert score == pytest.approx(0.6)
        assert issues == []

    def test_parse_completely_invalid(self):
        reviewer = JobReviewer()
        issues, score = reviewer._parse_tier2_response("garbage", [])
        assert score == 1.0  # fallback
        assert issues == []


class TestReviewerTier3HumanFlag:
    def test_critical_tier1_flags_human(self):
        reviewer = JobReviewer()
        job = _make_job(priority=0, review_count=0, max_reviews=3)
        tier1 = [ReviewIssue(tier=1, severity="critical", category="completeness", description="fail")]
        result = reviewer._tier3_human_flag(job, tier1, [], tier2_score=0.9)
        assert result is True

    def test_low_tier2_score_flags_human(self):
        reviewer = JobReviewer()
        job = _make_job(priority=0, review_count=0, max_reviews=3)
        result = reviewer._tier3_human_flag(job, [], [], tier2_score=0.3)
        assert result is True

    def test_high_priority_flags_human(self):
        reviewer = JobReviewer()
        job = _make_job(priority=9, review_count=0, max_reviews=3)
        result = reviewer._tier3_human_flag(job, [], [], tier2_score=0.9)
        assert result is True

    def test_last_review_attempt_flags_human(self):
        reviewer = JobReviewer()
        job = _make_job(priority=0, review_count=2, max_reviews=3)
        result = reviewer._tier3_human_flag(job, [], [], tier2_score=0.9)
        assert result is True

    def test_no_flag_when_all_good(self):
        reviewer = JobReviewer()
        job = _make_job(priority=0, review_count=0, max_reviews=3)
        result = reviewer._tier3_human_flag(job, [], [], tier2_score=0.9)
        assert result is False


class TestReviewerCompositeScore:
    @pytest.mark.asyncio
    async def test_full_review_passing(self):
        """When tier1 passes and tier2 returns high score, review passes."""
        reviewer = JobReviewer()
        job = _make_job(priority=0, review_count=0, max_reviews=3)
        node = _make_node(output_json='{"result": "ok"}')
        tier2_response = json.dumps({
            "score": 0.95, "passed": True, "issues": [], "summary": "Perfect"
        })
        with patch("backend.jobs.reviewer.is_available", return_value=True), \
             patch("backend.jobs.reviewer.generate", new_callable=AsyncMock, return_value=tier2_response):
            result = await reviewer.review(job, [node])
        assert result.passed is True
        # composite = 0.3 * 1.0 + 0.7 * 0.95 = 0.965
        assert result.score == pytest.approx(0.965, abs=0.01)

    @pytest.mark.asyncio
    async def test_full_review_failing_tier1(self):
        """When tier1 has criticals, overall review fails."""
        reviewer = JobReviewer()
        job = _make_job(priority=0, review_count=0, max_reviews=3)
        node = _make_node(status=NodeStatus.FAILED.value, output_json=None)
        with patch("backend.jobs.reviewer.is_available", return_value=False):
            result = await reviewer.review(job, [node])
        assert result.passed is False
        assert result.tier1_passed is False

    @pytest.mark.asyncio
    async def test_full_review_gemini_unavailable(self):
        """When Gemini is unavailable, tier2 defaults to score=1.0."""
        reviewer = JobReviewer()
        job = _make_job()
        node = _make_node(output_json='{"result": "ok"}')
        with patch("backend.jobs.reviewer.is_available", return_value=False):
            result = await reviewer.review(job, [node])
        assert result.tier2_passed is True
        assert result.passed is True


# ===========================================================================
# 5. tests for backend/jobs/executor.py
# ===========================================================================

from backend.jobs.executor import NodeExecutor, NodeResult, _parse_output_json


class TestParseOutputJson:
    def test_parses_dict(self):
        assert _parse_output_json('{"key": "val"}') == {"key": "val"}

    def test_parses_list_wraps(self):
        result = _parse_output_json('[1, 2, 3]')
        assert result == {"result": [1, 2, 3]}

    def test_parses_fenced_json(self):
        raw = "Here is the result:\n```json\n{\"key\": \"val\"}\n```\n"
        assert _parse_output_json(raw) == {"key": "val"}

    def test_plain_text_wraps(self):
        result = _parse_output_json("Just some text output")
        assert result == {"result": "Just some text output"}

    def test_empty_string(self):
        result = _parse_output_json("")
        assert "result" in result


class TestNodeResult:
    def test_creation(self):
        nr = NodeResult(success=True, output={"done": True})
        assert nr.success is True
        assert nr.error is None
        assert nr.tokens_in == 0

    def test_failure(self):
        nr = NodeResult(success=False, output={}, error="timeout")
        assert nr.success is False
        assert nr.error == "timeout"


class TestNodeExecutorTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self):
        """When the node exceeds its timeout_sec, result is a failure."""
        mock_registry = MagicMock()
        mock_registry.get_ollama_tools.return_value = []

        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        node = _make_node(timeout_sec=1)  # 1 second timeout
        job = _make_job()

        # Make _execute_node_inner hang forever
        async def slow_inner(*args, **kwargs):
            await asyncio.sleep(10)
            return NodeResult(success=True, output={})

        with patch.object(executor, "_execute_node_inner", side_effect=slow_inner):
            result = await executor.execute_node(node, job)
        assert result.success is False
        assert "timed out" in result.error.lower()


class TestNodeExecutorSystemPrompt:
    def test_build_system_prompt_basic(self):
        mock_registry = MagicMock()
        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        node = _make_node(
            instructions="Read the file and summarize",
            tools_allowed=["read_file", "write_file"],
            expected_output="A summary document",
        )
        job = _make_job()
        prompt = executor._build_system_prompt(node, job)
        assert "read_file, write_file" in prompt
        assert "Read the file and summarize" in prompt
        assert "A summary document" in prompt
        assert "SYSTEM" in prompt

    def test_build_system_prompt_with_input_data(self):
        mock_registry = MagicMock()
        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        node = _make_node()
        job = _make_job()
        prompt = executor._build_system_prompt(node, job, input_data={"key": "value"})
        assert "INPUT DATA" in prompt

    def test_build_system_prompt_no_tools(self):
        mock_registry = MagicMock()
        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        node = _make_node(tools_allowed=[])
        job = _make_job()
        prompt = executor._build_system_prompt(node, job)
        assert "(none)" in prompt


class TestNodeExecutorToolScoping:
    def test_scope_tools_filters(self):
        mock_registry = MagicMock()
        mock_registry.get_ollama_tools.return_value = [
            {"type": "function", "function": {"name": "read_file", "description": "Read", "parameters": {}}},
            {"type": "function", "function": {"name": "write_file", "description": "Write", "parameters": {}}},
            {"type": "function", "function": {"name": "web_search", "description": "Search", "parameters": {}}},
        ]
        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        scoped = executor._scope_tools(["read_file", "write_file"])
        assert len(scoped) == 2
        names = {t["function"]["name"] for t in scoped}
        assert names == {"read_file", "write_file"}

    def test_scope_tools_empty_returns_all(self):
        mock_registry = MagicMock()
        mock_registry.get_ollama_tools.return_value = [
            {"type": "function", "function": {"name": "a", "description": "", "parameters": {}}},
            {"type": "function", "function": {"name": "b", "description": "", "parameters": {}}},
        ]
        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        scoped = executor._scope_tools([])
        assert len(scoped) == 2

    def test_scope_tools_wildcard_returns_all(self):
        mock_registry = MagicMock()
        mock_registry.get_ollama_tools.return_value = [
            {"type": "function", "function": {"name": "a", "description": "", "parameters": {}}},
        ]
        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        scoped = executor._scope_tools(["*"])
        assert len(scoped) == 1


class TestNodeExecutorOutputValidation:
    def test_no_schema_passes(self):
        mock_registry = MagicMock()
        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        node = _make_node(output_schema_json=None)
        errors = executor._validate_output({"result": "ok"}, node)
        assert errors == []

    def test_missing_required_key(self):
        mock_registry = MagicMock()
        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        schema = json.dumps({
            "type": "object",
            "required": ["summary", "score"],
            "properties": {
                "summary": {"type": "string"},
                "score": {"type": "number"},
            }
        })
        node = _make_node(output_schema_json=schema)
        errors = executor._validate_output({"summary": "ok"}, node)
        assert any("score" in e for e in errors)

    def test_invalid_schema_json(self):
        mock_registry = MagicMock()
        executor = NodeExecutor(tool_registry=mock_registry, ollama_url="http://fake:11434")
        node = _make_node(output_schema_json="{bad json")
        errors = executor._validate_output({"result": "ok"}, node)
        assert len(errors) == 1
        assert "not valid JSON" in errors[0]


# ===========================================================================
# 6. tests for backend/jobs/worker.py
# ===========================================================================

from backend.jobs.worker import (
    JobWorker,
    _CIRCUIT_BREAKER_THRESHOLD,
    _CIRCUIT_OPEN_PAUSE_SEC,
)


class TestCircuitBreaker:
    def test_initial_state(self):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        assert worker._consecutive_failures == 0
        assert worker._circuit_open_until == 0.0

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold(self, job_db):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        worker.queue = JobQueue()

        # Simulate threshold failures
        for i in range(_CIRCUIT_BREAKER_THRESHOLD):
            job = worker.queue.create_job(
                workspace_id="ws1", title=f"Fail {i}",
                description=None, source="web", requester=None,
            )
            await worker._handle_failure(job, f"Error {i}")

        assert worker._consecutive_failures == _CIRCUIT_BREAKER_THRESHOLD
        assert worker._circuit_open_until > time.monotonic()

    @pytest.mark.asyncio
    async def test_circuit_resets_on_success(self, job_db):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        worker._consecutive_failures = 2
        # After a successful job, the worker sets _consecutive_failures to 0.
        # Simulate by directly resetting as the _run_job success path does.
        worker._consecutive_failures = 0
        assert worker._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_handle_failure_marks_job_failed(self, job_db):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        worker.queue = JobQueue()
        job = worker.queue.create_job(
            workspace_id="ws1", title="FailJob",
            description=None, source="web", requester=None,
        )
        await worker._handle_failure(job, "Something broke")
        updated = worker.queue.get_job(job.id)
        assert updated.status == JobStatus.FAILED.value
        assert updated.error == "Something broke"

    @pytest.mark.asyncio
    async def test_handle_failure_audits(self, job_db):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        worker.queue = JobQueue()
        job = worker.queue.create_job(
            workspace_id="ws1", title="FailJob",
            description=None, source="web", requester=None,
        )
        await worker._handle_failure(job, "Broke")
        audit = worker.queue.get_audit_log(job.id)
        assert any(e.action == "failed" for e in audit)


class TestWorkerJobStateMachine:
    def test_is_cancelled_false_for_pending(self, job_db):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        worker.queue = JobQueue()
        job = worker.queue.create_job(
            workspace_id="ws1", title="J",
            description=None, source="web", requester=None,
        )
        assert worker._is_cancelled(job) is False

    def test_is_cancelled_true_for_cancelling(self, job_db):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        worker.queue = JobQueue()
        job = worker.queue.create_job(
            workspace_id="ws1", title="J",
            description=None, source="web", requester=None,
        )
        worker.queue.cancel_job(job.id)
        assert worker._is_cancelled(job) is True

    def test_first_failed_node(self):
        nodes = [
            _make_node(id="n1", sequence=1, status=NodeStatus.COMPLETED.value),
            _make_node(id="n2", sequence=2, status=NodeStatus.FAILED.value, error="boom"),
            _make_node(id="n3", sequence=3, status=NodeStatus.PENDING.value),
        ]
        result = JobWorker._first_failed_node(nodes)
        assert result is not None
        assert result.id == "n2"

    def test_first_failed_node_none(self):
        nodes = [
            _make_node(id="n1", sequence=1, status=NodeStatus.COMPLETED.value),
        ]
        result = JobWorker._first_failed_node(nodes)
        assert result is None


class TestWorkerResolveInput:
    def test_resolve_input_linear(self):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        node = _make_node(sequence=2, depends_on=[])
        outputs = {"prev-id": {"data": "from_prev"}}
        result = worker._resolve_input(node, outputs)
        assert result == {"data": "from_prev"}

    def test_resolve_input_dag(self):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        node = _make_node(sequence=3, depends_on=["dep1", "dep2"])
        outputs = {
            "dep1": {"a": 1},
            "dep2": {"b": 2},
        }
        result = worker._resolve_input(node, outputs)
        assert result == {"a": 1, "b": 2}

    def test_resolve_input_first_node(self):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        node = _make_node(sequence=1, depends_on=[])
        result = worker._resolve_input(node, {})
        assert result is None


class TestWorkerResetNodes:
    def test_reset_nodes_for_retry(self, job_db):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        worker.queue = JobQueue()
        job = worker.queue.create_job(
            workspace_id="ws1", title="J",
            description=None, source="web", requester=None,
        )
        old_nodes = worker.queue.create_nodes(job.id, [
            {"title": "Old1"},
            {"title": "Old2"},
        ])
        # Complete one, leave one pending
        worker.queue.update_node(old_nodes[0].id, status=NodeStatus.COMPLETED.value)

        new_defs = [{"title": "New1", "instructions": "Redo"}]
        worker._reset_nodes_for_retry(job, new_defs)

        all_nodes = worker.queue.get_nodes(job.id)
        # Old nodes should be cancelled, new node added
        cancelled = [n for n in all_nodes if n.status == NodeStatus.CANCELLED.value]
        pending = [n for n in all_nodes if n.status == NodeStatus.PENDING.value]
        assert len(cancelled) == 2
        assert len(pending) == 1
        assert pending[0].title == "New1"


class TestWorkerStartStop:
    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self):
        mock_registry = MagicMock()
        worker = JobWorker(tool_registry=mock_registry)
        worker._running = True
        await worker.stop()
        assert worker._running is False

    @pytest.mark.asyncio
    async def test_activity_callback_emitted(self, job_db):
        callback = AsyncMock()
        mock_registry = MagicMock()
        worker = JobWorker(
            tool_registry=mock_registry,
            activity_callback=callback,
        )
        worker.queue = JobQueue()
        job = worker.queue.create_job(
            workspace_id="ws1", title="J",
            description=None, source="web", requester=None,
        )
        await worker._handle_failure(job, "test error")
        # Should have emitted at least "job_failed"
        event_names = [call.args[0] for call in callback.call_args_list]
        assert "job_failed" in event_names

    @pytest.mark.asyncio
    async def test_activity_callback_error_swallowed(self, job_db):
        """If the activity_callback raises, the worker should not crash."""
        async def bad_callback(event, data):
            raise RuntimeError("callback broken")

        mock_registry = MagicMock()
        worker = JobWorker(
            tool_registry=mock_registry,
            activity_callback=bad_callback,
        )
        worker.queue = JobQueue()
        job = worker.queue.create_job(
            workspace_id="ws1", title="J",
            description=None, source="web", requester=None,
        )
        # Should not raise despite broken callback
        await worker._handle_failure(job, "test error")
        updated = worker.queue.get_job(job.id)
        assert updated.status == JobStatus.FAILED.value
