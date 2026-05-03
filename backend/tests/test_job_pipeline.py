"""
Comprehensive pytest test suite for the LocalMind job pipeline system.

Covers:
  1. Job CRUD (create, get, list, update status, audit entries)
  2. Node CRUD (create, update, ordering, next-pending with DAG deps)
  3. Template lifecycle (job -> complete -> save template -> create from template)
  4. Planner (mock Gemini: quick mode, pipeline mode, fallback, complexity)
  5. Executor (mock Ollama: node execution, tool scoping, output parsing)
  6. Reviewer (mock Gemini: 3-tier QA, pass/fail scenarios)
  7. Worker integration (mock everything: plan -> execute -> review cycle)
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import uuid
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
    """Redirect DB_PATH and JOBS_DIR to a temp directory for every test.

    Also initialises the full schema so all tables exist before any CRUD.
    We patch at the config module level AND in every module that caches
    DB_PATH at import time (queue, db, schema).
    """
    db_path = tmp_path / "test_localmind.db"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    # Patch config values before any schema init
    monkeypatch.setattr("backend.config.DB_PATH", db_path)
    monkeypatch.setattr("backend.config.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("backend.config.WORKSPACE_ROOT", workspace_root)

    # Patch in modules that import DB_PATH at module level
    monkeypatch.setattr("backend.db.DB_PATH", db_path)
    monkeypatch.setattr("backend.core.schema.DB_PATH", db_path)
    monkeypatch.setattr("backend.jobs.queue.DB_PATH", db_path)

    # Initialize full schema (phase 0 + conversations + default tenant)
    from backend.core.schema import ensure_default_tenant, init_phase0_schema
    from backend.db import init_db

    init_db()
    init_phase0_schema()
    ensure_default_tenant()


@pytest.fixture
def queue():
    """Return a fresh JobQueue instance (uses the patched DB)."""
    from backend.jobs.queue import JobQueue
    return JobQueue()


@pytest.fixture
def workspace_id():
    """Return the default workspace ID created by ensure_default_tenant."""
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
    """Create and return a sample job."""
    return queue.create_job(
        workspace_id=workspace_id,
        title="Write a summary report",
        description="Summarise the Q4 earnings call transcript into bullet points.",
        source="web",
        requester=None,
        mode="quick",
        priority=3,
    )


@pytest.fixture
def sample_nodes(queue, sample_job):
    """Create three sequential nodes for sample_job and return them."""
    node_defs = [
        {
            "title": "Research",
            "instructions": "Read the transcript and extract key themes.",
            "tools_allowed": ["read_file"],
            "expected_output": "Research notes in JSON.",
            "depends_on": [],
            "timeout_sec": 120,
        },
        {
            "title": "Draft report",
            "instructions": "Write a bullet-point summary from the research notes.",
            "tools_allowed": ["read_file", "write_file"],
            "expected_output": "A markdown summary.",
            "depends_on": ["Research"],
            "timeout_sec": 180,
        },
        {
            "title": "Finalise",
            "instructions": "Proof-read and save the final report.",
            "tools_allowed": ["write_file"],
            "expected_output": "Final markdown file.",
            "depends_on": ["Draft report"],
            "timeout_sec": 60,
        },
    ]
    return queue.create_nodes(sample_job.id, node_defs)


@pytest.fixture
def mock_tool_registry():
    """Return a lightweight mock ToolRegistry."""
    registry = MagicMock()

    tool_read = MagicMock()
    tool_read.name = "read_file"
    tool_read.description = "Read a file from the workspace."

    tool_write = MagicMock()
    tool_write.name = "write_file"
    tool_write.description = "Write a file to the workspace."

    tool_search = MagicMock()
    tool_search.name = "web_search"
    tool_search.description = "Search the web."

    registry.tools = [tool_read, tool_write, tool_search]
    registry.get_ollama_tools.return_value = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    registry.execute_tool = AsyncMock(
        return_value={"success": True, "result": "mock tool result"}
    )
    return registry


# =========================================================================
# 1. Job CRUD
# =========================================================================


class TestJobCRUD:
    """Tests for creating, reading, listing, and updating jobs."""

    def test_create_job(self, queue, workspace_id):
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Test Job",
            description="A test job.",
            source="api",
            requester=None,
        )
        assert job.id is not None
        assert job.title == "Test Job"
        assert job.description == "A test job."
        assert job.status == "pending"
        assert job.workspace_id == workspace_id
        assert job.mode == "quick"
        assert job.priority == 0
        assert job.review_count == 0
        assert job.max_reviews == 3
        assert job.cost_cents == 0

    def test_get_job(self, queue, sample_job):
        fetched = queue.get_job(sample_job.id)
        assert fetched is not None
        assert fetched.id == sample_job.id
        assert fetched.title == sample_job.title

    def test_get_job_not_found(self, queue):
        assert queue.get_job("nonexistent-id") is None

    def test_list_jobs(self, queue, workspace_id):
        queue.create_job(
            workspace_id=workspace_id, title="J1", description=None,
            source="web", requester=None,
        )
        queue.create_job(
            workspace_id=workspace_id, title="J2", description=None,
            source="web", requester=None,
        )
        jobs = queue.list_jobs(workspace_id)
        assert len(jobs) >= 2
        titles = [j.title for j in jobs]
        assert "J1" in titles
        assert "J2" in titles

    def test_list_jobs_filter_by_status(self, queue, workspace_id):
        job1 = queue.create_job(
            workspace_id=workspace_id, title="Pending",
            description=None, source="web", requester=None,
        )
        job2 = queue.create_job(
            workspace_id=workspace_id, title="Done",
            description=None, source="web", requester=None,
        )
        queue.update_job_status(job2.id, "done")

        pending = queue.list_jobs(workspace_id, statuses=["pending"])
        done = queue.list_jobs(workspace_id, statuses=["done"])
        pending_ids = {j.id for j in pending}
        done_ids = {j.id for j in done}

        assert job1.id in pending_ids
        assert job2.id in done_ids
        assert job2.id not in pending_ids

    def test_update_job_status(self, queue, sample_job):
        queue.update_job_status(sample_job.id, "executing")
        job = queue.get_job(sample_job.id)
        assert job.status == "executing"

    def test_update_job_status_with_error(self, queue, sample_job):
        queue.update_job_status(
            sample_job.id, "failed", error="Something broke"
        )
        job = queue.get_job(sample_job.id)
        assert job.status == "failed"
        assert job.error == "Something broke"

    def test_update_job_status_with_result_summary(self, queue, sample_job):
        queue.update_job_status(
            sample_job.id, "done", result_summary="All good!"
        )
        job = queue.get_job(sample_job.id)
        assert job.status == "done"
        assert job.result_summary == "All good!"

    def test_increment_review_count(self, queue, sample_job):
        assert sample_job.review_count == 0
        count = queue.increment_review_count(sample_job.id)
        assert count == 1
        count = queue.increment_review_count(sample_job.id)
        assert count == 2

    def test_track_cost(self, queue, sample_job):
        queue.track_cost(sample_job.id, 1.5)
        job = queue.get_job(sample_job.id)
        assert job.cost_cents == 1.5
        queue.track_cost(sample_job.id, 2.5)
        job = queue.get_job(sample_job.id)
        assert job.cost_cents == 4.0

    def test_delete_job(self, queue, sample_job, sample_nodes):
        queue.add_audit(sample_job.id, action="test_action")
        assert queue.delete_job(sample_job.id) is True
        assert queue.get_job(sample_job.id) is None
        assert queue.get_nodes(sample_job.id) == []
        assert queue.get_audit_log(sample_job.id) == []

    def test_delete_job_not_found(self, queue):
        assert queue.delete_job("nonexistent") is False

    def test_audit_log(self, queue, sample_job):
        queue.add_audit(
            sample_job.id,
            action="started",
            detail="Beginning work",
            actor="worker",
        )
        queue.add_audit(
            sample_job.id,
            action="completed",
            detail="All done",
            actor="worker",
        )
        logs = queue.get_audit_log(sample_job.id)
        assert len(logs) == 2
        actions = [e.action for e in logs]
        assert "started" in actions
        assert "completed" in actions
        # Newest first
        assert logs[0].action == "completed"

    def test_audit_with_node_id(self, queue, sample_job, sample_nodes):
        node = sample_nodes[0]
        queue.add_audit(
            sample_job.id,
            action="node_started",
            detail="Running node 1",
            actor="worker",
            node_id=node.id,
        )
        logs = queue.get_audit_log(sample_job.id)
        assert len(logs) == 1
        assert logs[0].node_id == node.id

    def test_job_to_dict(self, sample_job):
        d = sample_job.to_dict()
        assert d["id"] == sample_job.id
        assert d["title"] == sample_job.title
        assert "status" in d
        assert "created_at" in d

    def test_job_to_api_dict_excludes_internals(self, sample_job):
        api = sample_job.to_api_dict()
        assert "error" not in api
        assert "cost_cents" not in api
        assert "review_count" not in api
        assert "title" in api


# =========================================================================
# 2. Node CRUD
# =========================================================================


class TestNodeCRUD:
    """Tests for creating, reading, updating, and ordering job nodes."""

    def test_create_nodes(self, sample_nodes):
        assert len(sample_nodes) == 3

    def test_node_sequence_ordering(self, sample_nodes):
        sequences = [n.sequence for n in sample_nodes]
        assert sequences == [1, 2, 3]

    def test_node_titles(self, sample_nodes):
        titles = [n.title for n in sample_nodes]
        assert titles == ["Research", "Draft report", "Finalise"]

    def test_node_depends_on_resolved_to_ids(self, sample_nodes):
        """depends_on title strings should be resolved to node UUIDs."""
        research = sample_nodes[0]
        draft = sample_nodes[1]
        finalise = sample_nodes[2]

        # Research has no dependencies
        assert research.depends_on == []
        # Draft depends on Research (by UUID)
        assert draft.depends_on == [research.id]
        # Finalise depends on Draft report (by UUID)
        assert finalise.depends_on == [draft.id]

    def test_node_tools_allowed(self, sample_nodes):
        assert sample_nodes[0].tools_allowed == ["read_file"]
        assert set(sample_nodes[1].tools_allowed) == {"read_file", "write_file"}
        assert sample_nodes[2].tools_allowed == ["write_file"]

    def test_node_status_default(self, sample_nodes):
        for node in sample_nodes:
            assert node.status == "pending"

    def test_get_nodes(self, queue, sample_job, sample_nodes):
        fetched = queue.get_nodes(sample_job.id)
        assert len(fetched) == 3
        assert fetched[0].sequence < fetched[1].sequence < fetched[2].sequence

    def test_get_node_by_id(self, queue, sample_nodes):
        node = queue.get_node(sample_nodes[1].id)
        assert node is not None
        assert node.title == "Draft report"

    def test_get_node_not_found(self, queue):
        assert queue.get_node("nonexistent") is None

    def test_update_node_status(self, queue, sample_nodes):
        node = sample_nodes[0]
        queue.update_node(node.id, status="running")
        updated = queue.get_node(node.id)
        assert updated.status == "running"

    def test_update_node_output(self, queue, sample_nodes):
        node = sample_nodes[0]
        output = json.dumps({"result": "key themes extracted"})
        queue.update_node(node.id, status="completed", output_json=output)
        updated = queue.get_node(node.id)
        assert updated.status == "completed"
        assert updated.output_json == output

    def test_update_node_error(self, queue, sample_nodes):
        node = sample_nodes[0]
        queue.update_node(node.id, status="failed", error="Timeout")
        updated = queue.get_node(node.id)
        assert updated.status == "failed"
        assert updated.error == "Timeout"

    def test_get_next_pending_node_linear(self, queue, sample_job, sample_nodes):
        """In a linear chain, the first pending node with completed deps is returned."""
        # No deps completed yet => Research (no deps) is first
        next_node = queue.get_next_pending_node(sample_job.id)
        assert next_node is not None
        assert next_node.title == "Research"

    def test_get_next_pending_node_after_completion(self, queue, sample_job, sample_nodes):
        """After completing Research, Draft report should be next (its dep is met)."""
        research = sample_nodes[0]
        queue.update_node(research.id, status="completed")

        next_node = queue.get_next_pending_node(sample_job.id)
        assert next_node is not None
        assert next_node.title == "Draft report"

    def test_get_next_pending_node_blocked(self, queue, sample_job, sample_nodes):
        """Finalise should not be returned while Draft report is still pending."""
        research = sample_nodes[0]
        queue.update_node(research.id, status="completed")

        # Draft is pending (not completed) => Finalise should not come up
        # even though Research is done, because Finalise depends on Draft
        next_node = queue.get_next_pending_node(sample_job.id)
        assert next_node.title == "Draft report"  # Draft, not Finalise

    def test_get_next_pending_node_all_done(self, queue, sample_job, sample_nodes):
        """When all nodes are completed, next pending should be None."""
        for node in sample_nodes:
            queue.update_node(node.id, status="completed")
        assert queue.get_next_pending_node(sample_job.id) is None

    def test_node_to_dict(self, sample_nodes):
        d = sample_nodes[0].to_dict()
        assert d["title"] == "Research"
        assert d["sequence"] == 1
        assert isinstance(d["tools_allowed"], list)
        assert isinstance(d["retry_policy"], dict)

    def test_node_retry_policy_defaults(self, sample_nodes):
        rp = sample_nodes[0].retry_policy
        assert rp.max_attempts == 3
        assert len(rp.backoff_ms) == 3


# =========================================================================
# 3. Template Lifecycle
# =========================================================================


class TestTemplateLifecycle:
    """Test the full create-job -> complete -> save-as-template -> instantiate cycle."""

    def test_save_as_template(self, queue, sample_job, sample_nodes, workspace_id):
        """Save a completed job's nodes as a template."""
        # Mark all nodes completed
        for node in sample_nodes:
            queue.update_node(
                node.id,
                status="completed",
                output_json=json.dumps({"done": True}),
            )

        tmpl = queue.save_as_template(
            job_id=sample_job.id,
            name="Q4 Summary Pipeline",
            workspace_id=workspace_id,
            created_by=None,
        )
        assert tmpl.name == "Q4 Summary Pipeline"
        assert len(tmpl.nodes) == 3
        assert tmpl.use_count == 0
        assert tmpl.workspace_id == workspace_id

    def test_get_template(self, queue, sample_job, sample_nodes, workspace_id):
        for node in sample_nodes:
            queue.update_node(node.id, status="completed")
        tmpl = queue.save_as_template(
            sample_job.id, "Test Tmpl", workspace_id
        )
        fetched = queue.get_template(tmpl.id)
        assert fetched is not None
        assert fetched.id == tmpl.id
        assert fetched.name == "Test Tmpl"

    def test_list_templates(self, queue, sample_job, sample_nodes, workspace_id):
        for node in sample_nodes:
            queue.update_node(node.id, status="completed")
        queue.save_as_template(sample_job.id, "Tmpl A", workspace_id)
        queue.save_as_template(sample_job.id, "Tmpl B", workspace_id)
        templates = queue.list_templates(workspace_id)
        names = [t.name for t in templates]
        assert "Tmpl A" in names
        assert "Tmpl B" in names

    def test_create_job_from_template(self, queue, sample_job, sample_nodes, workspace_id):
        """Instantiate a new job from a saved template."""
        for node in sample_nodes:
            queue.update_node(node.id, status="completed")
        tmpl = queue.save_as_template(
            sample_job.id, "Reusable Pipeline", workspace_id
        )

        new_job = queue.create_job_from_template(
            template_id=tmpl.id,
            workspace_id=workspace_id,
            requester=None,
        )
        assert new_job.title == "Reusable Pipeline"
        assert new_job.mode == "pipeline"
        assert new_job.template_id == tmpl.id
        assert new_job.status == "pending"

        # Nodes should have been created
        nodes = queue.get_nodes(new_job.id)
        assert len(nodes) == 3
        assert nodes[0].title == "Research"

        # Template use_count should have been incremented
        refreshed_tmpl = queue.get_template(tmpl.id)
        assert refreshed_tmpl.use_count == 1

        # Audit should contain the creation event
        audit = queue.get_audit_log(new_job.id)
        assert any(e.action == "job_created_from_template" for e in audit)

    def test_template_preserves_node_structure(self, queue, sample_job, sample_nodes, workspace_id):
        """Template nodes should preserve title, instructions, tools_allowed etc."""
        for node in sample_nodes:
            queue.update_node(node.id, status="completed")
        tmpl = queue.save_as_template(sample_job.id, "Struct Test", workspace_id)
        assert tmpl.nodes[0]["title"] == "Research"
        assert tmpl.nodes[0]["tools_allowed"] == ["read_file"]
        assert tmpl.nodes[1]["title"] == "Draft report"

    def test_create_job_from_template_not_found(self, queue, workspace_id):
        with pytest.raises(ValueError, match="Template not found"):
            queue.create_job_from_template(
                template_id="nonexistent",
                workspace_id=workspace_id,
                requester=None,
            )

    def test_increment_template_use_count(self, queue, sample_job, sample_nodes, workspace_id):
        for node in sample_nodes:
            queue.update_node(node.id, status="completed")
        tmpl = queue.save_as_template(sample_job.id, "Counter", workspace_id)
        queue.increment_template_use(tmpl.id)
        queue.increment_template_use(tmpl.id)
        refreshed = queue.get_template(tmpl.id)
        assert refreshed.use_count == 2


# =========================================================================
# 4. Planner (mock Gemini)
# =========================================================================


class TestJobPlanner:
    """Tests for the JobPlanner with mocked Gemini calls."""

    def _make_planner(self, tool_registry=None):
        from backend.jobs.planner import JobPlanner
        return JobPlanner(tool_registry=tool_registry)

    @pytest.mark.asyncio
    async def test_plan_quick_simple_task(self, sample_job, mock_tool_registry):
        """Gemini returns a valid single-node plan for a simple task."""
        planner = self._make_planner(mock_tool_registry)
        gemini_response = json.dumps([
            {
                "title": "Summarise transcript",
                "instructions": "Read the transcript and produce bullet points.",
                "tools_allowed": ["read_file", "write_file"],
                "expected_output": "Markdown summary.",
                "depends_on": [],
                "timeout_sec": 120,
            }
        ])

        with patch("backend.jobs.planner.is_available", return_value=True), \
             patch("backend.jobs.planner.generate", new_callable=AsyncMock, return_value=gemini_response), \
             patch("backend.jobs.planner.DEPLOYMENT_MODE", "hybrid"):
            nodes = await planner.plan(sample_job)

        assert len(nodes) == 1
        assert nodes[0]["title"] == "Summarise transcript"

    @pytest.mark.asyncio
    async def test_plan_quick_multi_node(self, sample_job, mock_tool_registry):
        """Gemini returns a multi-node plan for a complex task."""
        planner = self._make_planner(mock_tool_registry)
        gemini_response = json.dumps([
            {
                "title": "Research",
                "instructions": "Search for data.",
                "tools_allowed": ["web_search"],
                "expected_output": "Raw notes.",
                "depends_on": [],
                "timeout_sec": 180,
            },
            {
                "title": "Write report",
                "instructions": "Draft the report.",
                "tools_allowed": ["write_file"],
                "expected_output": "Markdown report.",
                "depends_on": ["Research"],
                "timeout_sec": 240,
            },
        ])

        with patch("backend.jobs.planner.is_available", return_value=True), \
             patch("backend.jobs.planner.generate", new_callable=AsyncMock, return_value=gemini_response), \
             patch("backend.jobs.planner.DEPLOYMENT_MODE", "hybrid"):
            nodes = await planner.plan(sample_job)

        assert len(nodes) == 2
        assert nodes[0]["title"] == "Research"
        assert nodes[1]["depends_on"] == ["Research"]

    @pytest.mark.asyncio
    async def test_plan_fallback_when_gemini_unavailable(self, sample_job, mock_tool_registry):
        """When Gemini is unavailable, planner falls back to single node."""
        planner = self._make_planner(mock_tool_registry)

        with patch("backend.jobs.planner.is_available", return_value=False), \
             patch("backend.jobs.planner.DEPLOYMENT_MODE", "hybrid"):
            nodes = await planner.plan(sample_job)

        assert len(nodes) == 1
        assert nodes[0]["title"] == "Execute task"

    @pytest.mark.asyncio
    async def test_plan_fallback_strict_local(self, sample_job, mock_tool_registry):
        """strict-local deployment always skips Gemini."""
        planner = self._make_planner(mock_tool_registry)

        with patch("backend.jobs.planner.DEPLOYMENT_MODE", "strict-local"):
            nodes = await planner.plan(sample_job)

        assert len(nodes) == 1
        assert nodes[0]["title"] == "Execute task"

    @pytest.mark.asyncio
    async def test_plan_handles_markdown_fence_in_response(self, sample_job, mock_tool_registry):
        """Planner should parse JSON even if wrapped in markdown fences."""
        planner = self._make_planner(mock_tool_registry)
        gemini_response = (
            "```json\n"
            + json.dumps([{
                "title": "Do it",
                "instructions": "Just do it.",
                "tools_allowed": [],
                "expected_output": "Done.",
                "depends_on": [],
                "timeout_sec": 60,
            }])
            + "\n```"
        )

        with patch("backend.jobs.planner.is_available", return_value=True), \
             patch("backend.jobs.planner.generate", new_callable=AsyncMock, return_value=gemini_response), \
             patch("backend.jobs.planner.DEPLOYMENT_MODE", "hybrid"):
            nodes = await planner.plan(sample_job)

        assert len(nodes) == 1

    def test_estimate_complexity_simple(self, mock_tool_registry):
        planner = self._make_planner(mock_tool_registry)
        assert planner._estimate_complexity("Hello world", has_files=False) == "simple"

    def test_estimate_complexity_medium(self, mock_tool_registry):
        planner = self._make_planner(mock_tool_registry)
        result = planner._estimate_complexity(
            "Research the best Python frameworks and write a report",
            has_files=False,
        )
        assert result == "medium"

    def test_estimate_complexity_complex(self, mock_tool_registry):
        planner = self._make_planner(mock_tool_registry)
        result = planner._estimate_complexity(
            "Refactor the entire authentication module, add integration tests, "
            "deploy to staging, and audit the security configuration thoroughly.",
            has_files=True,
        )
        assert result == "complex"

    def test_validate_plan_valid(self, mock_tool_registry):
        planner = self._make_planner(mock_tool_registry)
        plan = [
            {
                "title": "Step 1",
                "instructions": "Do something.",
                "tools_allowed": ["read_file"],
                "expected_output": "Result.",
                "depends_on": [],
                "timeout_sec": 120,
            }
        ]
        errors = planner._validate_plan(plan)
        assert errors == []

    def test_validate_plan_missing_keys(self, mock_tool_registry):
        planner = self._make_planner(mock_tool_registry)
        plan = [{"title": "Incomplete"}]
        errors = planner._validate_plan(plan)
        assert len(errors) > 0
        assert any("missing keys" in e for e in errors)

    def test_validate_plan_self_dependency(self, mock_tool_registry):
        planner = self._make_planner(mock_tool_registry)
        plan = [
            {
                "title": "Loop",
                "instructions": "x",
                "tools_allowed": [],
                "expected_output": "x",
                "depends_on": ["Loop"],
                "timeout_sec": 60,
            }
        ]
        errors = planner._validate_plan(plan)
        assert any("depends on itself" in e for e in errors)

    def test_validate_plan_circular_dependency(self, mock_tool_registry):
        planner = self._make_planner(mock_tool_registry)
        plan = [
            {
                "title": "A",
                "instructions": "x",
                "tools_allowed": [],
                "expected_output": "x",
                "depends_on": ["B"],
                "timeout_sec": 60,
            },
            {
                "title": "B",
                "instructions": "x",
                "tools_allowed": [],
                "expected_output": "x",
                "depends_on": ["A"],
                "timeout_sec": 60,
            },
        ]
        errors = planner._validate_plan(plan)
        assert any("Circular" in e or "entry point" in e.lower() for e in errors)

    def test_validate_plan_no_entry_point(self, mock_tool_registry):
        planner = self._make_planner(mock_tool_registry)
        plan = [
            {
                "title": "A",
                "instructions": "x",
                "tools_allowed": [],
                "expected_output": "x",
                "depends_on": ["B"],
                "timeout_sec": 60,
            },
            {
                "title": "B",
                "instructions": "x",
                "tools_allowed": [],
                "expected_output": "x",
                "depends_on": ["A"],
                "timeout_sec": 60,
            },
        ]
        errors = planner._validate_plan(plan)
        assert any("entry point" in e.lower() for e in errors)

    @pytest.mark.asyncio
    async def test_plan_pipeline_mode(self, queue, workspace_id, mock_tool_registry):
        """Pipeline mode loads nodes from a template."""
        # Create a template first
        job1 = queue.create_job(
            workspace_id=workspace_id, title="Seed", description=None,
            source="web", requester=None,
        )
        node_defs = [
            {
                "title": "Step A",
                "instructions": "Do A.",
                "tools_allowed": ["read_file"],
                "expected_output": "A done.",
                "depends_on": [],
                "timeout_sec": 120,
            }
        ]
        queue.create_nodes(job1.id, node_defs)
        for n in queue.get_nodes(job1.id):
            queue.update_node(n.id, status="completed")
        tmpl = queue.save_as_template(job1.id, "Pipeline Tmpl", workspace_id)

        # Create a pipeline-mode job from the template
        pipeline_job = queue.create_job(
            workspace_id=workspace_id, title="Pipeline Job",
            description="Use template", source="web", requester=None,
            mode="pipeline", template_id=tmpl.id,
        )

        planner = self._make_planner(mock_tool_registry)
        nodes = await planner.plan(pipeline_job)
        assert len(nodes) >= 1
        assert nodes[0]["title"] == "Step A"


# =========================================================================
# 5. Executor (mock Ollama)
# =========================================================================


class TestNodeExecutor:
    """Tests for NodeExecutor with mocked Ollama and tool registry."""

    def _make_executor(self, registry, ollama_url="http://localhost:11434"):
        # Patch out heavy dependencies
        with patch("backend.jobs.executor.PolicyEngine") as MockPE, \
             patch("backend.jobs.executor.PromptGuard") as MockPG:
            mock_pe_instance = MockPE.return_value
            mock_decision = MagicMock()
            mock_decision.result = MagicMock()
            mock_decision.result.name = "ALLOW"
            # Make it pass the PolicyResult.DENY/ALLOW checks
            from backend.core.policy import PolicyResult as PR
            mock_decision.result = PR.ALLOW
            mock_pe_instance.evaluate.return_value = mock_decision

            mock_pg_instance = MockPG.return_value
            mock_pg_instance.reset_job_counters.return_value = None
            mock_pg_instance.validate_tool_call.return_value = MagicMock(valid=True, issues=[])
            mock_pg_instance.check_anomaly.return_value = None
            mock_pg_instance.anomaly_halts_job = False
            mock_pg_instance.sanitize_input.side_effect = lambda x: x
            mock_pg_instance.wrap_untrusted.side_effect = lambda x, **kw: x
            mock_pg_instance.validate_output.return_value = MagicMock(cleaned_text=None)

            from backend.jobs.executor import NodeExecutor
            executor = NodeExecutor(registry, ollama_url)
            # Override the internally created instances
            executor._policy_engine = mock_pe_instance
            executor._prompt_guard = mock_pg_instance
            return executor

    def _make_node(self, **overrides):
        """Create a Node-like object for testing."""
        from backend.jobs.models import Node, RetryPolicy
        defaults = dict(
            id=str(uuid.uuid4()),
            job_id=str(uuid.uuid4()),
            sequence=1,
            title="Test node",
            instructions="Do the thing.",
            tools_allowed=["read_file"],
            expected_output="A result.",
            status="pending",
            input_json=None,
            output_json=None,
            error=None,
            auto_generated=True,
            input_schema_json=None,
            output_schema_json=None,
            side_effects_json=None,
            retry_policy=RetryPolicy(),
            timeout_sec=300,
            rollback_strategy=None,
            acceptance_tests_json=None,
            depends_on=[],
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T00:00:00Z",
        )
        defaults.update(overrides)
        return Node(**defaults)

    def _make_job(self, **overrides):
        """Create a Job-like object for testing."""
        from backend.jobs.models import Job
        defaults = dict(
            id=str(uuid.uuid4()),
            workspace_id="ws-default",
            title="Test Job",
            description="Do something.",
            source="web",
            source_ref=None,
            status="executing",
            priority=3,
            requester=None,
            mode="quick",
            template_id=None,
            result_summary=None,
            review_count=0,
            max_reviews=3,
            error=None,
            cost_cents=0,
            cloud_cost_cents=0.0,
            tokens_in_total=0,
            tokens_out_total=0,
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T00:00:00Z",
        )
        defaults.update(overrides)
        return Job(**defaults)

    @pytest.mark.asyncio
    async def test_execute_node_success(self, mock_tool_registry):
        """A successful node execution returns success=True with output."""
        executor = self._make_executor(mock_tool_registry)
        node = self._make_node()
        job = self._make_job()

        # Mock Ollama to return a final text response (no tool calls)
        ollama_response = {
            "message": {
                "content": json.dumps({"result": "Task completed."}),
                "tool_calls": None,
            },
            "prompt_eval_count": 100,
            "eval_count": 50,
        }

        with patch.object(executor, "_call_ollama", new_callable=AsyncMock, return_value=ollama_response):
            result = await executor.execute_node(node, job)

        assert result.success is True
        assert "result" in result.output
        assert result.tokens_in == 100
        assert result.tokens_out == 50

    @pytest.mark.asyncio
    async def test_execute_node_with_tool_call(self, mock_tool_registry):
        """Node makes a tool call, then produces final output."""
        executor = self._make_executor(mock_tool_registry)
        node = self._make_node()
        job = self._make_job()

        # First call: model requests a tool call
        tool_call_response = {
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "data.txt"},
                        }
                    }
                ],
            },
            "prompt_eval_count": 80,
            "eval_count": 20,
        }
        # Second call: model returns final text
        final_response = {
            "message": {
                "content": json.dumps({"result": "File read and processed."}),
                "tool_calls": None,
            },
            "prompt_eval_count": 120,
            "eval_count": 60,
        }

        with patch.object(
            executor, "_call_ollama",
            new_callable=AsyncMock,
            side_effect=[tool_call_response, final_response],
        ):
            result = await executor.execute_node(node, job)

        assert result.success is True
        assert result.tool_calls_made == 1
        mock_tool_registry.execute_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_node_timeout(self, mock_tool_registry):
        """Node that exceeds its timeout returns success=False."""
        executor = self._make_executor(mock_tool_registry)
        node = self._make_node(timeout_sec=1)  # 1 second timeout
        job = self._make_job()

        async def slow_ollama(*args, **kwargs):
            await asyncio.sleep(5)
            return {"message": {"content": "done", "tool_calls": None}}

        with patch.object(executor, "_call_ollama", side_effect=slow_ollama):
            result = await executor.execute_node(node, job)

        assert result.success is False
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_node_ollama_error(self, mock_tool_registry):
        """If Ollama returns an error, the node should fail."""
        executor = self._make_executor(mock_tool_registry)
        node = self._make_node()
        job = self._make_job()

        ollama_response = {"error": "Model not found"}

        with patch.object(executor, "_call_ollama", new_callable=AsyncMock, return_value=ollama_response):
            result = await executor.execute_node(node, job)

        assert result.success is False
        assert "Ollama" in result.error

    def test_scope_tools_filters_correctly(self, mock_tool_registry):
        """_scope_tools should only return tools in the allowed list + delegate."""
        executor = self._make_executor(mock_tool_registry)
        scoped = executor._scope_tools(["read_file"])

        func_names = [t["function"]["name"] for t in scoped]
        assert "read_file" in func_names
        assert "delegate" in func_names  # always present
        assert "write_file" not in func_names

    def test_scope_tools_wildcard(self, mock_tool_registry):
        """Empty or ['*'] should return all tools."""
        executor = self._make_executor(mock_tool_registry)
        scoped = executor._scope_tools([])
        # Should include all registry tools + delegate
        func_names = [t["function"]["name"] for t in scoped]
        assert "read_file" in func_names
        assert "write_file" in func_names
        assert "delegate" in func_names

    @pytest.mark.asyncio
    async def test_execute_node_text_output_wrapping(self, mock_tool_registry):
        """Plain text output from the LLM should be wrapped in {'result': ...}."""
        executor = self._make_executor(mock_tool_registry)
        node = self._make_node()
        job = self._make_job()

        ollama_response = {
            "message": {
                "content": "Here is your summary in plain text.",
                "tool_calls": None,
            },
            "prompt_eval_count": 50,
            "eval_count": 30,
        }

        with patch.object(executor, "_call_ollama", new_callable=AsyncMock, return_value=ollama_response):
            result = await executor.execute_node(node, job)

        assert result.success is True
        assert "result" in result.output
        assert "summary" in result.output["result"].lower()


# =========================================================================
# 6. Reviewer (mock Gemini)
# =========================================================================


class TestJobReviewer:
    """Tests for JobReviewer with mocked Gemini and file checks."""

    def _make_reviewer(self):
        with patch("backend.jobs.reviewer.ReflectionService") as MockRS:
            mock_rs = MockRS.return_value
            mock_rs.log_step_failure = MagicMock()
            from backend.jobs.reviewer import JobReviewer
            reviewer = JobReviewer(reflection_service=mock_rs)
            return reviewer

    def _make_job(self, **overrides):
        from backend.jobs.models import Job
        defaults = dict(
            id=str(uuid.uuid4()),
            workspace_id="ws-default",
            title="Test Job",
            description="Create a summary report.",
            source="web",
            source_ref=None,
            status="reviewing",
            priority=3,
            requester=None,
            mode="quick",
            template_id=None,
            result_summary=None,
            review_count=0,
            max_reviews=3,
            error=None,
            cost_cents=0,
            cloud_cost_cents=0.0,
            tokens_in_total=0,
            tokens_out_total=0,
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T00:00:00Z",
        )
        defaults.update(overrides)
        return Job(**defaults)

    def _make_node(self, status="completed", output_json=None, **overrides):
        from backend.jobs.models import Node, RetryPolicy
        defaults = dict(
            id=str(uuid.uuid4()),
            job_id=str(uuid.uuid4()),
            sequence=1,
            title="Test Node",
            instructions="Do something.",
            tools_allowed=["read_file"],
            expected_output="A result.",
            status=status,
            input_json=None,
            output_json=output_json or json.dumps({"result": "done"}),
            error=None,
            auto_generated=True,
            input_schema_json=None,
            output_schema_json=None,
            side_effects_json=None,
            retry_policy=RetryPolicy(),
            timeout_sec=300,
            rollback_strategy=None,
            acceptance_tests_json=None,
            depends_on=[],
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T00:00:00Z",
        )
        defaults.update(overrides)
        return Node(**defaults)

    @pytest.mark.asyncio
    async def test_review_passes_all_nodes_completed(self):
        """All nodes completed with valid output => review passes."""
        reviewer = self._make_reviewer()
        job = self._make_job()
        nodes = [
            self._make_node(sequence=1, title="Research"),
            self._make_node(sequence=2, title="Write"),
        ]

        # Mock Gemini to return a high score
        gemini_response = json.dumps({
            "score": 0.95,
            "passed": True,
            "issues": [],
            "summary": "Excellent work.",
        })

        with patch("backend.jobs.reviewer.is_available", return_value=True), \
             patch("backend.jobs.reviewer.generate", new_callable=AsyncMock, return_value=gemini_response):
            result = await reviewer.review(job, nodes)

        assert result.passed is True
        assert result.score > 0.7
        assert result.tier1_passed is True
        assert result.tier2_passed is True

    @pytest.mark.asyncio
    async def test_review_fails_on_failed_node(self):
        """A failed node should trigger a Tier 1 critical issue."""
        reviewer = self._make_reviewer()
        job = self._make_job()
        nodes = [
            self._make_node(sequence=1, title="Broken", status="failed", error="crash"),
        ]

        # Even if Gemini gives a high score, tier1 critical should fail
        gemini_response = json.dumps({
            "score": 0.9,
            "passed": True,
            "issues": [],
            "summary": "Looks fine.",
        })

        with patch("backend.jobs.reviewer.is_available", return_value=True), \
             patch("backend.jobs.reviewer.generate", new_callable=AsyncMock, return_value=gemini_response):
            result = await reviewer.review(job, nodes)

        assert result.passed is False
        assert result.tier1_passed is False
        assert any(i.severity == "critical" for i in result.issues)

    @pytest.mark.asyncio
    async def test_review_fails_on_empty_output(self):
        """A completed node with no output should fail Tier 1."""
        reviewer = self._make_reviewer()
        job = self._make_job()
        nodes = [
            self._make_node(sequence=1, title="Empty", output_json="{}"),
        ]

        gemini_response = json.dumps({
            "score": 0.8,
            "passed": True,
            "issues": [],
            "summary": "OK.",
        })

        with patch("backend.jobs.reviewer.is_available", return_value=True), \
             patch("backend.jobs.reviewer.generate", new_callable=AsyncMock, return_value=gemini_response):
            result = await reviewer.review(job, nodes)

        assert result.tier1_passed is False

    @pytest.mark.asyncio
    async def test_review_tier2_low_score_fails(self):
        """Tier 2 score below threshold => review fails."""
        reviewer = self._make_reviewer()
        job = self._make_job()
        nodes = [
            self._make_node(sequence=1, title="Mediocre"),
        ]

        gemini_response = json.dumps({
            "score": 0.3,
            "passed": False,
            "issues": [
                {
                    "severity": "critical",
                    "category": "completeness",
                    "description": "Missing key sections.",
                    "node_sequence": 1,
                    "suggestion": "Add more detail.",
                }
            ],
            "summary": "Incomplete work.",
        })

        with patch("backend.jobs.reviewer.is_available", return_value=True), \
             patch("backend.jobs.reviewer.generate", new_callable=AsyncMock, return_value=gemini_response):
            result = await reviewer.review(job, nodes)

        assert result.passed is False
        assert result.tier2_passed is False
        assert result.score < 0.7

    @pytest.mark.asyncio
    async def test_review_gemini_unavailable_passes_tier2(self):
        """When Gemini is unavailable, Tier 2 defaults to pass (score=1.0)."""
        reviewer = self._make_reviewer()
        job = self._make_job()
        nodes = [
            self._make_node(sequence=1, title="Good node"),
        ]

        with patch("backend.jobs.reviewer.is_available", return_value=False):
            result = await reviewer.review(job, nodes)

        assert result.tier2_passed is True

    @pytest.mark.asyncio
    async def test_review_human_flag_high_priority(self):
        """High-priority jobs should always be flagged for human review."""
        reviewer = self._make_reviewer()
        job = self._make_job(priority=9)
        nodes = [
            self._make_node(sequence=1, title="Good node"),
        ]

        gemini_response = json.dumps({
            "score": 0.95,
            "passed": True,
            "issues": [],
            "summary": "Great.",
        })

        with patch("backend.jobs.reviewer.is_available", return_value=True), \
             patch("backend.jobs.reviewer.generate", new_callable=AsyncMock, return_value=gemini_response):
            result = await reviewer.review(job, nodes)

        assert result.needs_human_review is True

    @pytest.mark.asyncio
    async def test_review_human_flag_last_attempt(self):
        """Last review attempt should flag for human review."""
        reviewer = self._make_reviewer()
        # review_count=2, max_reviews=3 => last attempt
        job = self._make_job(review_count=2, max_reviews=3)
        nodes = [
            self._make_node(sequence=1, title="Node"),
        ]

        gemini_response = json.dumps({
            "score": 0.95,
            "passed": True,
            "issues": [],
            "summary": "OK.",
        })

        with patch("backend.jobs.reviewer.is_available", return_value=True), \
             patch("backend.jobs.reviewer.generate", new_callable=AsyncMock, return_value=gemini_response):
            result = await reviewer.review(job, nodes)

        assert result.needs_human_review is True

    @pytest.mark.asyncio
    async def test_review_result_to_dict(self):
        """ReviewResult.to_dict() should include all fields."""
        reviewer = self._make_reviewer()
        job = self._make_job()
        nodes = [self._make_node(sequence=1)]

        gemini_response = json.dumps({
            "score": 0.85,
            "passed": True,
            "issues": [],
            "summary": "All good.",
        })

        with patch("backend.jobs.reviewer.is_available", return_value=True), \
             patch("backend.jobs.reviewer.generate", new_callable=AsyncMock, return_value=gemini_response):
            result = await reviewer.review(job, nodes)

        d = result.to_dict()
        assert "passed" in d
        assert "score" in d
        assert "feedback" in d
        assert "issues" in d
        assert "needs_human_review" in d
        assert "tier1_passed" in d
        assert "tier2_passed" in d


# =========================================================================
# 7. Worker Integration
# =========================================================================


class TestJobWorker:
    """Integration tests for JobWorker with all external services mocked."""

    @pytest.mark.asyncio
    async def test_worker_runs_full_cycle(self, queue, workspace_id, mock_tool_registry):
        """Worker picks up a pending job, plans, executes, reviews, marks done."""
        # Create a pending job
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Integration test job",
            description="Summarise a document.",
            source="web",
            requester=None,
        )

        # Planner will return a simple 1-node plan
        plan_result = [
            {
                "title": "Summarise",
                "instructions": "Read and summarise.",
                "tools_allowed": ["read_file"],
                "expected_output": "A summary.",
                "depends_on": [],
                "timeout_sec": 120,
            }
        ]

        # Executor will succeed
        from backend.jobs.executor import NodeResult
        exec_result = NodeResult(
            success=True,
            output={"result": "Summary of the document."},
            tokens_in=100,
            tokens_out=50,
            duration_ms=500,
            tool_calls_made=1,
            model_used="test-model",
        )

        # Reviewer will pass
        from backend.jobs.reviewer import ReviewResult
        review_result = ReviewResult(
            passed=True,
            score=0.95,
            feedback="All checks passed.",
            issues=[],
            needs_human_review=False,
            tier1_passed=True,
            tier2_passed=True,
        )

        with patch("backend.jobs.worker.ReflectionService") as MockRS, \
             patch("backend.jobs.worker.DelegationEngine") as MockDE, \
             patch("backend.jobs.worker.SharedMemoryStore") as MockSMS:

            mock_rs = MockRS.return_value
            mock_rs.log_step_failure = MagicMock()
            mock_rs.analyze_failure = AsyncMock(return_value={"error": "no trace"})

            mock_de = MockDE.return_value
            mock_de.get_children.return_value = []
            mock_de.is_tree_complete.return_value = True

            from backend.jobs.worker import JobWorker
            worker = JobWorker(
                tool_registry=mock_tool_registry,
                ollama_url="http://localhost:11434",
            )
            # Override the internally created sub-components
            worker.reflection = mock_rs

            worker.planner.plan = AsyncMock(return_value=plan_result)
            worker.executor.execute_node = AsyncMock(return_value=exec_result)
            worker.reviewer.review = AsyncMock(return_value=review_result)
            worker._delegation_engine = mock_de

            # Mark worker as running (normally done by start())
            worker._running = True

            # Run the job directly (not via the polling loop)
            await worker._run_job(job)

        # Verify the job reached 'done' status
        final_job = queue.get_job(job.id)
        assert final_job.status == "done"
        assert final_job.result_summary is not None

        # Verify nodes were created
        nodes = queue.get_nodes(job.id)
        assert len(nodes) == 1

        # Verify audit trail
        audit = queue.get_audit_log(job.id)
        actions = [e.action for e in audit]
        assert "planning" in actions
        assert "executing" in actions
        assert "reviewing" in actions
        assert "done" in actions

    @pytest.mark.asyncio
    async def test_worker_handles_planning_failure(self, queue, workspace_id, mock_tool_registry):
        """If planning raises an exception, the job should be marked failed."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Bad plan job",
            description="This will fail planning.",
            source="web",
            requester=None,
        )

        with patch("backend.jobs.worker.ReflectionService") as MockRS, \
             patch("backend.jobs.worker.DelegationEngine") as MockDE, \
             patch("backend.jobs.worker.SharedMemoryStore"):

            mock_rs = MockRS.return_value
            mock_rs.log_step_failure = MagicMock()
            mock_rs.analyze_failure = AsyncMock(return_value={"error": "no trace"})

            mock_de = MockDE.return_value
            mock_de.get_children.return_value = []

            from backend.jobs.worker import JobWorker
            worker = JobWorker(
                tool_registry=mock_tool_registry,
                ollama_url="http://localhost:11434",
            )
            worker.reflection = mock_rs
            worker._delegation_engine = mock_de

            worker.planner.plan = AsyncMock(
                side_effect=RuntimeError("Gemini exploded")
            )
            worker._running = True

            await worker._run_job(job)

        final_job = queue.get_job(job.id)
        assert final_job.status == "failed"
        assert "Planning error" in final_job.error

    @pytest.mark.asyncio
    async def test_worker_handles_review_failure_retry(self, queue, workspace_id, mock_tool_registry):
        """If review fails, worker should re-plan and retry (up to max_reviews)."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Retry job",
            description="This will fail review once then pass.",
            source="web",
            requester=None,
        )

        plan_result = [
            {
                "title": "Do work",
                "instructions": "Work hard.",
                "tools_allowed": [],
                "expected_output": "Results.",
                "depends_on": [],
                "timeout_sec": 120,
            }
        ]

        from backend.jobs.executor import NodeResult
        exec_result = NodeResult(
            success=True,
            output={"result": "Done."},
            tokens_in=50,
            tokens_out=30,
            duration_ms=200,
        )

        from backend.jobs.reviewer import ReviewResult
        fail_review = ReviewResult(
            passed=False, score=0.4, feedback="Incomplete.",
            issues=[], tier1_passed=True, tier2_passed=False,
        )
        pass_review = ReviewResult(
            passed=True, score=0.9, feedback="Good now.",
            issues=[], tier1_passed=True, tier2_passed=True,
        )

        with patch("backend.jobs.worker.ReflectionService") as MockRS, \
             patch("backend.jobs.worker.DelegationEngine") as MockDE, \
             patch("backend.jobs.worker.SharedMemoryStore"):

            mock_rs = MockRS.return_value
            mock_rs.log_step_failure = MagicMock()
            mock_rs.analyze_failure = AsyncMock(return_value={"error": "no trace"})

            mock_de = MockDE.return_value
            mock_de.get_children.return_value = []

            from backend.jobs.worker import JobWorker
            worker = JobWorker(
                tool_registry=mock_tool_registry,
                ollama_url="http://localhost:11434",
            )
            worker.reflection = mock_rs
            worker._delegation_engine = mock_de

            worker.planner.plan = AsyncMock(return_value=plan_result)
            worker.executor.execute_node = AsyncMock(return_value=exec_result)
            # First review fails, second passes
            worker.reviewer.review = AsyncMock(
                side_effect=[fail_review, pass_review]
            )
            worker._running = True

            await worker._run_job(job)

        final_job = queue.get_job(job.id)
        assert final_job.status == "done"
        # Review count should have been incremented at least once
        assert final_job.review_count >= 1

    @pytest.mark.asyncio
    async def test_worker_handles_node_execution_failure(self, queue, workspace_id, mock_tool_registry):
        """If node execution fails, the job should be marked failed."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Exec fail job",
            description="Node will fail.",
            source="web",
            requester=None,
        )

        plan_result = [
            {
                "title": "Failing node",
                "instructions": "This will fail.",
                "tools_allowed": [],
                "expected_output": "Nothing.",
                "depends_on": [],
                "timeout_sec": 120,
            }
        ]

        from backend.jobs.executor import NodeResult
        fail_result = NodeResult(
            success=False,
            output={},
            error="LLM returned nonsense.",
            tokens_in=10,
            tokens_out=5,
            duration_ms=100,
        )

        with patch("backend.jobs.worker.ReflectionService") as MockRS, \
             patch("backend.jobs.worker.DelegationEngine") as MockDE, \
             patch("backend.jobs.worker.SharedMemoryStore"):

            mock_rs = MockRS.return_value
            mock_rs.log_step_failure = MagicMock()
            mock_rs.analyze_failure = AsyncMock(return_value={"error": "no trace"})

            mock_de = MockDE.return_value
            mock_de.get_children.return_value = []

            from backend.jobs.worker import JobWorker
            worker = JobWorker(
                tool_registry=mock_tool_registry,
                ollama_url="http://localhost:11434",
            )
            worker.reflection = mock_rs
            worker._delegation_engine = mock_de

            worker.planner.plan = AsyncMock(return_value=plan_result)
            worker.executor.execute_node = AsyncMock(return_value=fail_result)
            worker._running = True

            await worker._run_job(job)

        final_job = queue.get_job(job.id)
        assert final_job.status == "failed"

    @pytest.mark.asyncio
    async def test_worker_circuit_breaker(self, queue, workspace_id, mock_tool_registry):
        """After 3 consecutive failures, the circuit breaker should open."""
        with patch("backend.jobs.worker.ReflectionService") as MockRS, \
             patch("backend.jobs.worker.DelegationEngine") as MockDE, \
             patch("backend.jobs.worker.SharedMemoryStore"):

            mock_rs = MockRS.return_value
            mock_rs.log_step_failure = MagicMock()
            mock_rs.analyze_failure = AsyncMock(return_value={"error": "no trace"})

            mock_de = MockDE.return_value
            mock_de.get_children.return_value = []

            from backend.jobs.worker import JobWorker
            worker = JobWorker(
                tool_registry=mock_tool_registry,
                ollama_url="http://localhost:11434",
            )
            worker.reflection = mock_rs
            worker._delegation_engine = mock_de

            worker.planner.plan = AsyncMock(
                side_effect=RuntimeError("Always fails")
            )
            worker._running = True

            # Run 3 failing jobs
            for i in range(3):
                job = queue.create_job(
                    workspace_id=workspace_id,
                    title=f"Fail {i}",
                    description=None,
                    source="web",
                    requester=None,
                )
                await worker._run_job(job)

            assert worker._consecutive_failures >= 3
            assert worker._circuit_open_until > 0

    @pytest.mark.asyncio
    async def test_worker_dequeue_claims_job(self, queue, workspace_id, mock_tool_registry):
        """_dequeue_next_pending should pick the highest-priority pending job."""
        low = queue.create_job(
            workspace_id=workspace_id, title="Low Priority",
            description=None, source="web", requester=None, priority=1,
        )
        high = queue.create_job(
            workspace_id=workspace_id, title="High Priority",
            description=None, source="web", requester=None, priority=10,
        )

        with patch("backend.jobs.worker.ReflectionService") as MockRS, \
             patch("backend.jobs.worker.DelegationEngine") as MockDE, \
             patch("backend.jobs.worker.SharedMemoryStore"):

            mock_rs = MockRS.return_value
            mock_de = MockDE.return_value

            from backend.jobs.worker import JobWorker
            worker = JobWorker(
                tool_registry=mock_tool_registry,
                ollama_url="http://localhost:11434",
            )
            worker.reflection = mock_rs
            worker._delegation_engine = mock_de

            claimed = worker._dequeue_next_pending()

        # Should claim the higher-priority job
        assert claimed is not None
        assert claimed.id == high.id

        # The claimed job should now be in 'planning' status
        refreshed = queue.get_job(high.id)
        assert refreshed.status == "planning"


# =========================================================================
# Model unit tests
# =========================================================================


class TestModels:
    """Unit tests for the data models themselves."""

    def test_job_status_enum_values(self):
        from backend.jobs.models import JobStatus
        assert JobStatus.PENDING.value == "pending"
        assert JobStatus.DONE.value == "done"
        assert JobStatus.FAILED.value == "failed"
        assert JobStatus.CANCELLED.value == "cancelled"

    def test_node_status_enum_values(self):
        from backend.jobs.models import NodeStatus
        assert NodeStatus.PENDING.value == "pending"
        assert NodeStatus.RUNNING.value == "running"
        assert NodeStatus.COMPLETED.value == "completed"
        assert NodeStatus.FAILED.value == "failed"

    def test_retry_policy_defaults(self):
        from backend.jobs.models import RetryPolicy
        rp = RetryPolicy()
        assert rp.max_attempts == 3
        assert len(rp.backoff_ms) == 3

    def test_retry_policy_from_json(self):
        from backend.jobs.models import RetryPolicy
        rp = RetryPolicy.from_json('{"max_attempts": 5, "backoff_ms": [100, 200]}')
        assert rp.max_attempts == 5
        assert rp.backoff_ms == [100, 200]

    def test_retry_policy_from_json_none(self):
        from backend.jobs.models import RetryPolicy
        rp = RetryPolicy.from_json(None)
        assert rp.max_attempts == 3

    def test_retry_policy_roundtrip(self):
        from backend.jobs.models import RetryPolicy
        original = RetryPolicy(max_attempts=2, backoff_ms=[500])
        serialized = original.to_json()
        restored = RetryPolicy.from_json(serialized)
        assert restored.max_attempts == 2
        assert restored.backoff_ms == [500]

    def test_parse_json_field_valid(self):
        from backend.jobs.models import parse_json_field
        assert parse_json_field('["a","b"]') == ["a", "b"]
        assert parse_json_field('{"k": 1}') == {"k": 1}

    def test_parse_json_field_none(self):
        from backend.jobs.models import parse_json_field
        assert parse_json_field(None) is None
        assert parse_json_field(None, default=[]) == []

    def test_parse_json_field_invalid(self):
        from backend.jobs.models import parse_json_field
        assert parse_json_field("not json", default="fallback") == "fallback"


# =========================================================================
# File operations
# =========================================================================


class TestFileOperations:
    """Tests for job file CRUD."""

    def test_add_and_get_files(self, queue, sample_job):
        jf = queue.add_file(
            job_id=sample_job.id,
            filename="report.md",
            file_path="/tmp/report.md",
            file_type="output",
            mime_type="text/markdown",
            size_bytes=1024,
        )
        assert jf.filename == "report.md"
        assert jf.file_type == "output"

        files = queue.get_files(sample_job.id)
        assert len(files) == 1
        assert files[0].id == jf.id

    def test_get_files_filter_by_type(self, queue, sample_job):
        queue.add_file(
            sample_job.id, "input.txt", "/tmp/input.txt", "input"
        )
        queue.add_file(
            sample_job.id, "output.txt", "/tmp/output.txt", "output"
        )

        inputs = queue.get_files(sample_job.id, file_type="input")
        outputs = queue.get_files(sample_job.id, file_type="output")
        assert len(inputs) == 1
        assert len(outputs) == 1
        assert inputs[0].filename == "input.txt"
        assert outputs[0].filename == "output.txt"

    def test_add_file_with_node_id(self, queue, sample_job, sample_nodes):
        jf = queue.add_file(
            job_id=sample_job.id,
            filename="node_output.json",
            file_path="/tmp/node_output.json",
            file_type="output",
            node_id=sample_nodes[0].id,
        )
        assert jf.node_id == sample_nodes[0].id

    def test_job_file_to_dict(self, queue, sample_job):
        jf = queue.add_file(
            sample_job.id, "test.txt", "/tmp/test.txt", "output"
        )
        d = jf.to_dict()
        assert d["filename"] == "test.txt"
        assert d["file_type"] == "output"
        assert "id" in d
        assert "job_id" in d
