"""
End-to-end tests for the LocalMind job pipeline.

Validates the full lifecycle:
  API (create job) → Planner (generate nodes) → Executor (run nodes)
  → Reviewer (QA) → Worker (orchestration) → DB (final state)

Uses a temporary SQLite DB, mocked Ollama, and the real planner fallback
path (single-node) so the test runs without external services.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# DB bootstrap (same schema as test_jobs.py)
# ---------------------------------------------------------------------------

_SCHEMA = """
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

CREATE TABLE IF NOT EXISTS job_delegation (
    id TEXT PRIMARY KEY,
    parent_job_id TEXT NOT NULL,
    child_job_id TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);
"""


def _init_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()


@pytest.fixture
def job_db(tmp_path):
    """Temp DB with full pipeline schema; patches DB_PATH globally."""
    db_file = tmp_path / "e2e.db"
    _init_db(db_file)
    with patch("backend.jobs.queue.DB_PATH", db_file), \
         patch("backend.config.DB_PATH", db_file):
        yield db_file


@pytest.fixture
def jobs_dir(tmp_path):
    """Temp jobs directory; patches JOBS_DIR."""
    d = tmp_path / "jobs"
    d.mkdir()
    with patch("backend.jobs.worker.JOBS_DIR", d), \
         patch("backend.jobs.executor.JOBS_DIR", d), \
         patch("backend.config.JOBS_DIR", d), \
         patch("backend.routes.jobs.JOBS_DIR", d):
        yield d


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _make_ollama_response(content: str, tool_calls=None) -> dict:
    """Build a fake Ollama /api/chat response."""
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "model": "test-model",
        "message": msg,
        "prompt_eval_count": 50,
        "eval_count": 100,
    }


def _mock_tool_registry():
    """Return a mock ToolRegistry with a few basic tools."""
    registry = MagicMock()
    registry.tools = [
        MagicMock(name="read_file", description="Read a file from disk"),
        MagicMock(name="write_file", description="Write content to a file"),
        MagicMock(name="web_search", description="Search the web"),
    ]
    # Fix .name on the mock tools (MagicMock.name is special)
    for tool, name in zip(registry.tools, ["read_file", "write_file", "web_search"]):
        type(tool).name = property(lambda self, n=name: n)

    registry.get_ollama_tools.return_value = [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    ]

    async def mock_execute(name, args):
        return {"success": True, "result": f"Executed {name}"}

    registry.execute_tool = AsyncMock(side_effect=mock_execute)
    return registry


# ===========================================================================
# 1. Queue → Planner → Nodes (no external deps)
# ===========================================================================

from backend.jobs.queue import JobQueue
from backend.jobs.models import Job, JobStatus, NodeStatus, Node
from backend.jobs.planner import JobPlanner
from backend.jobs.reviewer import JobReviewer


class TestQueuePlannerIntegration:
    """Test that creating a job and planning it produces valid nodes."""

    def test_create_job_and_plan_fallback(self, job_db):
        """Create job → planner (Gemini unavailable) → single-node fallback."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Summarise meeting notes",
            description="Read the meeting notes and produce a bullet-point summary.",
            source="test",
            requester="e2e-test",
            mode="quick",
        )

        assert job.id is not None
        assert job.status == JobStatus.PENDING.value
        assert job.mode == "quick"

        # Plan with Gemini unavailable → should fallback to single node
        planner = JobPlanner(tool_registry=_mock_tool_registry())

        with patch("backend.jobs.planner.is_available", return_value=False):
            nodes = asyncio.get_event_loop().run_until_complete(
                planner.plan(job, input_files=[])
            )

        assert len(nodes) >= 1
        assert nodes[0]["title"] == "Execute task"
        assert "Summarise meeting notes" in nodes[0]["instructions"] or \
               "Read the meeting notes" in nodes[0]["instructions"]
        assert isinstance(nodes[0]["tools_allowed"], list)
        assert nodes[0]["timeout_sec"] > 0

        # Persist nodes to DB
        db_nodes = queue.create_nodes(job.id, nodes)
        assert len(db_nodes) == len(nodes)
        assert db_nodes[0].status == NodeStatus.PENDING.value

    def test_create_job_plan_with_gemini(self, job_db):
        """Create job → planner (mocked Gemini) → multi-node plan."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Research Python async frameworks",
            description="Research best Python async frameworks and write a comparison report",
            source="test",
            requester="e2e-test",
        )

        fake_plan = json.dumps([
            {
                "title": "Web research",
                "instructions": "Search the web for top Python async frameworks.",
                "tools_allowed": ["web_search"],
                "expected_output": "Raw research notes.",
                "depends_on": [],
                "timeout_sec": 180,
            },
            {
                "title": "Draft report",
                "instructions": "Write a structured comparison report.",
                "tools_allowed": ["write_file"],
                "expected_output": "Markdown report.",
                "depends_on": ["Web research"],
                "timeout_sec": 240,
            },
        ])

        planner = JobPlanner(tool_registry=_mock_tool_registry())

        with patch("backend.jobs.planner.is_available", return_value=True), \
             patch("backend.jobs.planner.generate", new_callable=AsyncMock, return_value=fake_plan):
            nodes = asyncio.get_event_loop().run_until_complete(
                planner.plan(job, input_files=[])
            )

        assert len(nodes) == 2
        assert nodes[0]["title"] == "Web research"
        assert nodes[1]["title"] == "Draft report"
        assert nodes[1]["depends_on"] == ["Web research"]

        # Persist and verify
        db_nodes = queue.create_nodes(job.id, nodes)
        assert len(db_nodes) == 2
        assert db_nodes[0].sequence == 1
        assert db_nodes[1].sequence == 2

        # Verify get_next_pending_node returns the entry-point node
        next_node = queue.get_next_pending_node(job.id)
        assert next_node is not None
        assert next_node.title == "Web research"


# ===========================================================================
# 2. Reviewer standalone (Tier 1 deterministic checks)
# ===========================================================================


class TestReviewerTier1:
    """Tier 1 format checks without any external service."""

    def test_all_nodes_completed_passes_tier1(self, job_db):
        """Review passes when all nodes completed with valid output."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Test task",
            description="A test task",
            source="test",
            requester="e2e-test",
        )

        nodes = queue.create_nodes(job.id, [
            {
                "title": "Step 1",
                "instructions": "Do something",
                "tools_allowed": [],
                "expected_output": "Result text",
                "depends_on": [],
                "timeout_sec": 60,
            },
        ])

        # Mark node as completed with valid output
        queue.update_node(
            nodes[0].id,
            status=NodeStatus.COMPLETED.value,
            output_json=json.dumps({"result": "Task completed successfully"}),
        )

        reviewer = JobReviewer()
        refreshed_nodes = queue.get_nodes(job.id)

        # Gemini unavailable → Tier 2 auto-passes (score=1.0)
        with patch("backend.jobs.reviewer.is_available", return_value=False):
            result = asyncio.get_event_loop().run_until_complete(
                reviewer.review(job, refreshed_nodes)
            )

        assert result.passed is True
        assert result.tier1_passed is True
        assert result.score > 0.5

    def test_failed_node_fails_tier1(self, job_db):
        """Review fails when a node is in failed status."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Failing task",
            description="This will fail",
            source="test",
            requester="e2e-test",
        )

        nodes = queue.create_nodes(job.id, [
            {
                "title": "Broken step",
                "instructions": "This will fail",
                "tools_allowed": [],
                "expected_output": "Nothing",
                "depends_on": [],
                "timeout_sec": 60,
            },
        ])

        queue.update_node(
            nodes[0].id,
            status=NodeStatus.FAILED.value,
            error="Timeout exceeded",
        )

        reviewer = JobReviewer()
        refreshed_nodes = queue.get_nodes(job.id)

        with patch("backend.jobs.reviewer.is_available", return_value=False):
            result = asyncio.get_event_loop().run_until_complete(
                reviewer.review(job, refreshed_nodes)
            )

        assert result.passed is False
        assert result.tier1_passed is False
        assert any("did not complete" in i.description for i in result.issues)

    def test_empty_output_fails_tier1(self, job_db):
        """Review fails when a completed node has empty output."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Empty output task",
            description="Node produces nothing",
            source="test",
            requester="e2e-test",
        )

        nodes = queue.create_nodes(job.id, [
            {
                "title": "Empty step",
                "instructions": "Produce something",
                "tools_allowed": [],
                "expected_output": "A result",
                "depends_on": [],
                "timeout_sec": 60,
            },
        ])

        queue.update_node(
            nodes[0].id,
            status=NodeStatus.COMPLETED.value,
            output_json="{}",
        )

        reviewer = JobReviewer()
        refreshed_nodes = queue.get_nodes(job.id)

        with patch("backend.jobs.reviewer.is_available", return_value=False):
            result = asyncio.get_event_loop().run_until_complete(
                reviewer.review(job, refreshed_nodes)
            )

        assert result.tier1_passed is False
        assert any("no output" in i.description for i in result.issues)


# ===========================================================================
# 3. Executor (mocked Ollama)
# ===========================================================================

from backend.jobs.executor import NodeExecutor, NodeResult


class TestExecutorMocked:
    """Execute a node with mocked Ollama calls."""

    def test_single_node_no_tools(self, job_db, jobs_dir):
        """Node completes when LLM returns text without tool calls."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Simple task",
            description="Write hello",
            source="test",
            requester="e2e-test",
        )

        nodes = queue.create_nodes(job.id, [
            {
                "title": "Greet",
                "instructions": "Say hello",
                "tools_allowed": [],
                "expected_output": "A greeting",
                "depends_on": [],
                "timeout_sec": 30,
            },
        ])
        node = nodes[0]

        registry = _mock_tool_registry()
        executor = NodeExecutor(registry, "http://localhost:11434")

        # Mock Ollama to return text directly (no tool calls)
        fake_response = _make_ollama_response(
            json.dumps({"result": "Hello, world!"})
        )

        with patch.object(executor, "_call_ollama", new_callable=AsyncMock, return_value=fake_response):
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute_node(node=node, job=job)
            )

        assert result.success is True
        assert "Hello" in str(result.output) or "result" in result.output

    def test_node_with_tool_call(self, job_db, jobs_dir):
        """Node completes after a tool call + final text response."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="File write task",
            description="Write a file",
            source="test",
            requester="e2e-test",
        )

        # Create the job output dir so PromptGuard path checks pass
        job_dir = jobs_dir / job.id
        job_dir.mkdir(parents=True, exist_ok=True)

        nodes = queue.create_nodes(job.id, [
            {
                "title": "Write file",
                "instructions": "Write hello.txt",
                "tools_allowed": ["write_file"],
                "expected_output": "File written",
                "depends_on": [],
                "timeout_sec": 30,
            },
        ])
        node = nodes[0]

        registry = _mock_tool_registry()
        executor = NodeExecutor(registry, "http://localhost:11434")

        # Use a path inside the job directory so PromptGuard doesn't block it
        write_path = str(job_dir / "hello.txt")

        # First call: tool call
        tool_response = _make_ollama_response("", tool_calls=[
            {
                "function": {
                    "name": "write_file",
                    "arguments": {"path": write_path, "content": "Hello!"},
                }
            }
        ])
        # Second call: final text
        final_response = _make_ollama_response(
            json.dumps({"result": "File written successfully"})
        )

        call_count = 0

        async def mock_ollama(messages, tools, model):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tool_response
            return final_response

        # Mock PromptGuard so Windows backslash paths aren't flagged
        mock_guard_result = MagicMock()
        mock_guard_result.valid = True
        mock_guard_result.issues = []

        with patch.object(executor, "_call_ollama", side_effect=mock_ollama), \
             patch.object(executor._prompt_guard, "validate_tool_call", return_value=mock_guard_result):
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute_node(node=node, job=job)
            )

        assert result.success is True
        assert result.tool_calls_made >= 1
        assert registry.execute_tool.await_count >= 1

    def test_node_timeout(self, job_db, jobs_dir):
        """Node fails cleanly on timeout."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Slow task",
            description="This will timeout",
            source="test",
            requester="e2e-test",
        )

        nodes = queue.create_nodes(job.id, [
            {
                "title": "Slow step",
                "instructions": "Take forever",
                "tools_allowed": [],
                "expected_output": "Nothing",
                "depends_on": [],
                "timeout_sec": 1,  # Very short timeout
            },
        ])
        node = nodes[0]

        registry = _mock_tool_registry()
        executor = NodeExecutor(registry, "http://localhost:11434")

        async def slow_ollama(messages, tools, model):
            await asyncio.sleep(10)  # Longer than timeout
            return _make_ollama_response("done")

        with patch.object(executor, "_call_ollama", side_effect=slow_ollama):
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute_node(node=node, job=job)
            )

        assert result.success is False
        assert "timed out" in result.error.lower()


# ===========================================================================
# 4. Full Worker E2E (Plan → Execute → Review → Done)
# ===========================================================================

from backend.jobs.worker import JobWorker


class TestWorkerE2E:
    """Full pipeline: pending job → planning → execution → review → done."""

    def test_full_pipeline_success(self, job_db, jobs_dir):
        """Job goes through the full lifecycle and lands in 'done' status."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="E2E test task",
            description="Write a greeting file",
            source="test",
            requester="e2e-test",
        )

        assert job.status == JobStatus.PENDING.value

        registry = _mock_tool_registry()
        activity_events = []

        async def capture_activity(event_type, data):
            activity_events.append({"type": event_type, "data": data})

        worker = JobWorker(
            tool_registry=registry,
            activity_callback=capture_activity,
        )
        worker._running = True  # Normally set by start(); needed for _run_job

        # Mock planner to return a single node
        fake_plan = [
            {
                "title": "Execute task",
                "instructions": "Write a greeting.",
                "tools_allowed": ["write_file"],
                "expected_output": "A greeting file.",
                "depends_on": [],
                "timeout_sec": 60,
            },
        ]

        # Mock executor to succeed immediately
        fake_ollama_response = _make_ollama_response(
            json.dumps({"result": "Task completed. Greeting written."})
        )

        # Mock reviewer to pass
        async def mock_generate(prompt, **kwargs):
            return json.dumps({
                "score": 0.95,
                "passed": True,
                "issues": [],
                "summary": "Looks good.",
            })

        with patch("backend.jobs.planner.is_available", return_value=False), \
             patch("backend.jobs.executor.DEPLOYMENT_MODE", "strict-local"), \
             patch.object(worker.executor, "_call_ollama",
                         new_callable=AsyncMock, return_value=fake_ollama_response), \
             patch("backend.jobs.reviewer.is_available", return_value=False), \
             patch.object(worker._delegation_engine, "get_children", return_value=[]):

            asyncio.get_event_loop().run_until_complete(
                worker._run_job(job)
            )

        # Verify final state
        final_job = queue.get_job(job.id)
        assert final_job is not None
        assert final_job.status == JobStatus.DONE.value, \
            f"Expected 'done', got '{final_job.status}' — error: {final_job.error}"

        # Verify nodes were created and completed
        nodes = queue.get_nodes(job.id)
        assert len(nodes) >= 1
        completed_nodes = [n for n in nodes if n.status == NodeStatus.COMPLETED.value]
        assert len(completed_nodes) >= 1

        # Verify audit trail exists
        audit = queue.get_audit_log(job.id)
        actions = [a.action for a in audit]
        assert "planning" in actions
        assert "executing" in actions
        assert "done" in actions

        # Verify activity events were emitted
        event_types = [e["type"] for e in activity_events]
        assert "job_planning" in event_types
        assert "job_executing" in event_types
        assert "job_done" in event_types

    def test_full_pipeline_failure(self, job_db, jobs_dir):
        """Job fails when executor raises and lands in 'failed' status."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Failing E2E task",
            description="This job will fail during execution",
            source="test",
            requester="e2e-test",
        )

        registry = _mock_tool_registry()
        worker = JobWorker(tool_registry=registry)
        worker._running = True

        # Mock executor to fail
        async def failing_ollama(messages, tools, model):
            return {"error": "Model not found: test-model"}

        with patch("backend.jobs.planner.is_available", return_value=False), \
             patch("backend.jobs.executor.DEPLOYMENT_MODE", "strict-local"), \
             patch.object(worker.executor, "_call_ollama",
                         new_callable=AsyncMock, side_effect=failing_ollama), \
             patch("backend.jobs.reviewer.is_available", return_value=False), \
             patch.object(worker._delegation_engine, "get_children", return_value=[]):

            asyncio.get_event_loop().run_until_complete(
                worker._run_job(job)
            )

        final_job = queue.get_job(job.id)
        assert final_job is not None
        assert final_job.status == JobStatus.FAILED.value
        assert final_job.error is not None

    def test_circuit_breaker_opens_after_consecutive_failures(self, job_db, jobs_dir):
        """Circuit breaker opens after 3 consecutive job failures."""
        queue = JobQueue()
        registry = _mock_tool_registry()
        worker = JobWorker(tool_registry=registry)
        worker._running = True

        assert worker._consecutive_failures == 0
        assert worker._circuit_open_until == 0.0

        # Simulate 3 failures
        for i in range(3):
            job = queue.create_job(
                workspace_id="ws-test",
                title=f"Failing job {i+1}",
                description="Will fail",
                source="test",
                requester="e2e-test",
            )

            async def failing_ollama(messages, tools, model):
                return {"error": "Model crashed"}

            with patch("backend.jobs.planner.is_available", return_value=False), \
                 patch("backend.jobs.executor.DEPLOYMENT_MODE", "strict-local"), \
                 patch.object(worker.executor, "_call_ollama",
                             new_callable=AsyncMock, side_effect=failing_ollama), \
                 patch("backend.jobs.reviewer.is_available", return_value=False), \
                 patch.object(worker._delegation_engine, "get_children", return_value=[]):

                asyncio.get_event_loop().run_until_complete(
                    worker._run_job(job)
                )

        assert worker._consecutive_failures >= 3
        assert worker._circuit_open_until > time.monotonic()


# ===========================================================================
# 5. Queue operations E2E
# ===========================================================================


class TestQueueE2E:
    """Test queue operations: create → list → status transitions → audit."""

    def test_job_lifecycle(self, job_db):
        """Job transitions through all statuses correctly."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Lifecycle test",
            description="Test all transitions",
            source="api",
            requester="e2e",
        )

        assert queue.get_job(job.id).status == "pending"

        queue.update_job_status(job.id, "planning")
        assert queue.get_job(job.id).status == "planning"

        queue.update_job_status(job.id, "executing")
        assert queue.get_job(job.id).status == "executing"

        queue.update_job_status(job.id, "reviewing")
        assert queue.get_job(job.id).status == "reviewing"

        queue.update_job_status(job.id, "done", result_summary="All good")
        final = queue.get_job(job.id)
        assert final.status == "done"
        assert final.result_summary == "All good"

    def test_node_dependency_ordering(self, job_db):
        """get_next_pending_node respects depends_on."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="DAG test",
            description="Test dependency ordering",
            source="test",
            requester="e2e",
        )

        nodes = queue.create_nodes(job.id, [
            {
                "title": "Step A",
                "instructions": "First",
                "tools_allowed": [],
                "expected_output": "A output",
                "depends_on": [],
                "timeout_sec": 60,
            },
            {
                "title": "Step B",
                "instructions": "Second, depends on A",
                "tools_allowed": [],
                "expected_output": "B output",
                "depends_on": [nodes[0].id if False else "Step A"],  # Title-based
                "timeout_sec": 60,
            },
        ])

        # First pending should be Step A (no deps)
        next_node = queue.get_next_pending_node(job.id)
        assert next_node.title == "Step A"

        # Complete Step A
        queue.update_node(nodes[0].id, status=NodeStatus.COMPLETED.value)

        # Now Step B should be available (dependency resolved by sequence,
        # since depends_on uses title strings matched at creation time —
        # the queue resolves by node ID internally after create_nodes)
        next_node = queue.get_next_pending_node(job.id)
        assert next_node is not None
        assert next_node.title == "Step B"

    def test_template_save_and_instantiate(self, job_db):
        """Save a completed job as template, then create a new job from it."""
        queue = JobQueue()

        # Create and complete original job
        job = queue.create_job(
            workspace_id="ws-test",
            title="Template source",
            description="Will become a template",
            source="test",
            requester="e2e",
        )
        nodes = queue.create_nodes(job.id, [
            {
                "title": "Research",
                "instructions": "Do research",
                "tools_allowed": ["web_search"],
                "expected_output": "Research notes",
                "depends_on": [],
                "timeout_sec": 180,
            },
            {
                "title": "Write report",
                "instructions": "Write the report",
                "tools_allowed": ["write_file"],
                "expected_output": "Markdown report",
                "depends_on": ["Research"],
                "timeout_sec": 240,
            },
        ])

        for n in nodes:
            queue.update_node(n.id, status=NodeStatus.COMPLETED.value,
                            output_json=json.dumps({"result": "done"}))

        queue.update_job_status(job.id, JobStatus.DONE.value)

        # Save as template
        template = queue.save_as_template(
            job_id=job.id,
            name="Research Report",
            workspace_id="ws-test",
        )
        assert template.name == "Research Report"
        assert len(template.nodes) == 2

        # List templates
        templates = queue.list_templates("ws-test")
        assert len(templates) == 1
        assert templates[0].id == template.id

        # Create job from template
        new_job = queue.create_job_from_template(
            template_id=template.id,
            workspace_id="ws-test",
            requester="e2e",
        )
        assert new_job.mode == "pipeline"
        assert new_job.template_id == template.id

        new_nodes = queue.get_nodes(new_job.id)
        assert len(new_nodes) == 2
        assert new_nodes[0].title == "Research"
        assert new_nodes[1].title == "Write report"

    def test_cost_tracking(self, job_db):
        """Cost accumulates correctly across multiple track_cost calls."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Cost test",
            description="Track costs",
            source="test",
            requester="e2e",
        )

        queue.track_cost(job.id, 5.0)
        queue.track_cost(job.id, 3.5)

        updated = queue.get_job(job.id)
        assert updated.cost_cents == 8.5

    def test_interrupted_job_detection(self, job_db):
        """Jobs stuck in 'executing' status are detected on restart."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Interrupted job",
            description="Will be stuck",
            source="test",
            requester="e2e",
        )
        queue.update_job_status(job.id, JobStatus.EXECUTING.value)

        interrupted = queue.get_interrupted_jobs()
        assert len(interrupted) >= 1
        assert any(j.id == job.id for j in interrupted)

    def test_audit_trail(self, job_db):
        """Audit log records all actions correctly."""
        queue = JobQueue()
        job = queue.create_job(
            workspace_id="ws-test",
            title="Audit test",
            description="Test audit",
            source="test",
            requester="e2e",
        )

        queue.add_audit(job.id, action="created", detail="mode=quick", actor="system")
        queue.add_audit(job.id, action="planning", actor="worker")
        queue.add_audit(job.id, action="executing", detail="attempt=1", actor="worker")
        queue.add_audit(job.id, action="done", detail="score=0.95", actor="worker")

        audit = queue.get_audit_log(job.id)
        assert len(audit) == 4
        actions = [a.action for a in audit]
        assert "created" in actions
        assert "done" in actions


# ===========================================================================
# 6. Planner complexity estimation
# ===========================================================================


class TestPlannerComplexity:
    """Verify the planner's complexity heuristic."""

    def test_simple_task(self):
        planner = JobPlanner()
        assert planner._estimate_complexity("hello", has_files=False) == "simple"

    def test_medium_task(self):
        planner = JobPlanner()
        result = planner._estimate_complexity(
            "research and summarize the latest trends",
            has_files=False,
        )
        assert result in ("medium", "complex")

    def test_complex_task(self):
        planner = JobPlanner()
        result = planner._estimate_complexity(
            "refactor the authentication module, add unit tests for every "
            "function, run the test suite, benchmark performance before and "
            "after, then deploy to staging",
            has_files=True,
        )
        assert result == "complex"

    def test_file_presence_upgrades_complexity(self):
        planner = JobPlanner()
        without = planner._estimate_complexity("update the deck", has_files=False)
        with_files = planner._estimate_complexity("update the deck", has_files=True)
        assert with_files in ("medium", "complex")
