"""
End-to-end tests for the multi-node pipeline flow.

Exercises the full pipeline path that has never run in production:
  - Multi-node job creation with 4 nodes (research -> analyze -> execute -> review)
  - Output piping between nodes (node N output -> node N+1 input)
  - Partial failure: node 2 fails -> nodes 3-4 skipped, node 1 output preserved
  - Re-run: reset failed node to pending, continue from where it left off
  - Template save from completed pipeline + create new job from template
  - Worker-driven pipeline execution with mocked Ollama/Gemini

All tests use a temp database and workspace directory via the _patch_db_path fixture.
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
    """
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


# ---------------------------------------------------------------------------
# Node definitions for the 4-node pipeline
# ---------------------------------------------------------------------------

PIPELINE_NODE_DEFS = [
    {
        "title": "Research",
        "instructions": "Search the web for information about the topic.",
        "tools_allowed": ["web_search"],
        "expected_output": "Raw research notes as JSON.",
        "depends_on": [],
        "timeout_sec": 120,
    },
    {
        "title": "Analyze",
        "instructions": "Analyze the research data and extract key insights.",
        "tools_allowed": ["read_file", "write_file"],
        "expected_output": "Analysis report in JSON format.",
        "depends_on": ["Research"],
        "timeout_sec": 180,
    },
    {
        "title": "Execute",
        "instructions": "Generate the final deliverable based on the analysis.",
        "tools_allowed": ["read_file", "write_file", "run_code"],
        "expected_output": "Final output file.",
        "depends_on": ["Analyze"],
        "timeout_sec": 300,
    },
    {
        "title": "Review",
        "instructions": "Review the final output for quality and correctness.",
        "tools_allowed": ["read_file"],
        "expected_output": "Review summary with pass/fail.",
        "depends_on": ["Execute"],
        "timeout_sec": 120,
    },
]


# ---------------------------------------------------------------------------
# Test 1: Multi-node job creation and structure
# ---------------------------------------------------------------------------


class TestMultiNodeJobCreation:
    """Verify that a 4-node pipeline job can be created with proper structure."""

    def test_create_pipeline_job_with_4_nodes(self, queue, workspace_id):
        """Create a pipeline job and verify all 4 nodes are created in sequence."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Multi-node pipeline test",
            description="Test pipeline with 4 nodes",
            source="test",
            requester=None,
            mode="pipeline",
        )
        assert job.mode == "pipeline"
        assert job.status == "pending"

        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)
        assert len(nodes) == 4

        # Verify sequence ordering
        for i, node in enumerate(nodes):
            assert node.sequence == i + 1
            assert node.title == PIPELINE_NODE_DEFS[i]["title"]
            assert node.status == "pending"

    def test_node_tools_scoped_correctly(self, queue, workspace_id):
        """Each node must have only its declared tools_allowed."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Tools scoping test",
            description="Verify tool scoping",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        expected_tools = [
            ["web_search"],
            ["read_file", "write_file"],
            ["read_file", "write_file", "run_code"],
            ["read_file"],
        ]
        for node, expected in zip(nodes, expected_tools):
            assert node.tools_allowed == expected, (
                f"Node '{node.title}' has wrong tools: {node.tools_allowed}"
            )

    def test_depends_on_resolved_to_node_ids(self, queue, workspace_id):
        """depends_on title references should be resolved to node UUIDs."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Dependency resolution test",
            description="Test depends_on resolution",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        # Build title -> id map
        title_to_id = {n.title: n.id for n in nodes}

        # Node 0 (Research) has no dependencies
        assert nodes[0].depends_on == []

        # Node 1 (Analyze) depends on Research
        assert nodes[1].depends_on == [title_to_id["Research"]]

        # Node 2 (Execute) depends on Analyze
        assert nodes[2].depends_on == [title_to_id["Analyze"]]

        # Node 3 (Review) depends on Execute
        assert nodes[3].depends_on == [title_to_id["Execute"]]

    def test_get_next_pending_node_respects_dag_order(self, queue, workspace_id):
        """get_next_pending_node should only return nodes whose deps are all completed."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="DAG ordering test",
            description="Test DAG-aware scheduling",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        # Only the first node (Research, no deps) should be eligible
        next_node = queue.get_next_pending_node(job.id)
        assert next_node is not None
        assert next_node.title == "Research"

        # Complete Research
        queue.update_node(nodes[0].id, status="completed", output_json='{"data": "research results"}')

        # Now Analyze should be eligible
        next_node = queue.get_next_pending_node(job.id)
        assert next_node is not None
        assert next_node.title == "Analyze"

        # Without completing Analyze, Execute should NOT be eligible
        # (even though Research is done, Execute depends on Analyze)
        next_node_2 = queue.get_next_pending_node(job.id)
        assert next_node_2.title == "Analyze"  # still Analyze, not Execute


# ---------------------------------------------------------------------------
# Test 2: Output piping between nodes
# ---------------------------------------------------------------------------


class TestOutputPiping:
    """Verify that output_json from node N flows to node N+1 as input_json."""

    def test_resolve_input_dag_mode(self, queue, workspace_id):
        """_resolve_input should merge outputs from all dependencies."""
        from backend.jobs.worker import JobWorker

        job = queue.create_job(
            workspace_id=workspace_id,
            title="Output piping test",
            description="Test output piping",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        # Simulate that Research (node 0) completed with output
        research_output = {"topics": ["AI", "ML"], "source_count": 5}
        node_outputs = {nodes[0].id: research_output}

        # Create a mock worker to test _resolve_input
        mock_registry = MagicMock()
        mock_registry.tools = []
        worker = JobWorker(tool_registry=mock_registry)

        # Analyze (node 1) depends on Research (node 0)
        input_for_analyze = worker._resolve_input(nodes[1], node_outputs)
        assert input_for_analyze is not None
        assert input_for_analyze == research_output

    def test_resolve_input_linear_fallback(self, queue, workspace_id):
        """For nodes without depends_on, use the last completed node's output."""
        from backend.jobs.worker import JobWorker

        job = queue.create_job(
            workspace_id=workspace_id,
            title="Linear piping test",
            description="Test linear output piping",
            source="test",
            requester=None,
            mode="pipeline",
        )
        # Create nodes WITHOUT depends_on (linear chain)
        linear_node_defs = [
            {
                "title": "Step 1",
                "instructions": "First step.",
                "tools_allowed": [],
                "expected_output": "Step 1 result.",
                "depends_on": [],
                "timeout_sec": 60,
            },
            {
                "title": "Step 2",
                "instructions": "Second step.",
                "tools_allowed": [],
                "expected_output": "Step 2 result.",
                "depends_on": [],
                "timeout_sec": 60,
            },
        ]
        nodes = queue.create_nodes(job.id, linear_node_defs)

        mock_registry = MagicMock()
        mock_registry.tools = []
        worker = JobWorker(tool_registry=mock_registry)

        step1_output = {"result": "step 1 done"}
        node_outputs = {nodes[0].id: step1_output}

        # Node 2 (sequence=2, no depends_on) should get last output
        input_for_step2 = worker._resolve_input(nodes[1], node_outputs)
        assert input_for_step2 is not None
        assert input_for_step2 == step1_output

    def test_resolve_input_first_node_gets_none(self, queue, workspace_id):
        """The first node (sequence=1, no deps) should get None as input."""
        from backend.jobs.worker import JobWorker

        job = queue.create_job(
            workspace_id=workspace_id,
            title="First node input test",
            description="First node should get no input",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        mock_registry = MagicMock()
        mock_registry.tools = []
        worker = JobWorker(tool_registry=mock_registry)

        input_for_first = worker._resolve_input(nodes[0], {})
        assert input_for_first is None


# ---------------------------------------------------------------------------
# Test 3: Partial failure — node 2 fails, node 1 output preserved
# ---------------------------------------------------------------------------


class TestPartialFailure:
    """Verify that when a middle node fails, earlier outputs are preserved
    and subsequent nodes are not executed."""

    def test_node_failure_stops_pipeline(self, queue, workspace_id):
        """If node 2 (Analyze) fails, node 3 (Execute) and 4 (Review) stay pending."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Partial failure test",
            description="Test partial failure handling",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        # Complete Research
        queue.update_node(
            nodes[0].id,
            status="completed",
            output_json=json.dumps({"research": "data"}),
        )

        # Fail Analyze
        queue.update_node(
            nodes[1].id,
            status="failed",
            error="LLM call timed out",
        )

        # Verify state
        refreshed_nodes = queue.get_nodes(job.id)
        assert refreshed_nodes[0].status == "completed"
        assert refreshed_nodes[1].status == "failed"
        assert refreshed_nodes[2].status == "pending"
        assert refreshed_nodes[3].status == "pending"

        # Node 1's output should still be preserved
        assert refreshed_nodes[0].output_json is not None
        parsed = json.loads(refreshed_nodes[0].output_json)
        assert parsed == {"research": "data"}

        # get_next_pending_node should return None because
        # Analyze failed, and Execute depends on Analyze
        next_node = queue.get_next_pending_node(job.id)
        # Next pending should be Execute or Review, but their deps aren't met
        # Actually, nodes[2] depends on nodes[1] (Analyze) which is FAILED, not COMPLETED
        # So get_next_pending_node should return None
        assert next_node is None

    def test_first_failed_node_detection(self, queue, workspace_id):
        """_first_failed_node should return the earliest failed node by sequence."""
        from backend.jobs.worker import JobWorker

        job = queue.create_job(
            workspace_id=workspace_id,
            title="Failed node detection",
            description="Test",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        queue.update_node(nodes[0].id, status="completed")
        queue.update_node(nodes[1].id, status="failed", error="error 1")
        # Even if we also mark node 3 as failed
        queue.update_node(nodes[2].id, status="failed", error="error 2")

        refreshed = queue.get_nodes(job.id)
        first_failed = JobWorker._first_failed_node(refreshed)
        assert first_failed is not None
        assert first_failed.title == "Analyze"  # seq=2 before Execute seq=3


# ---------------------------------------------------------------------------
# Test 4: Re-run — reset failed node, continue pipeline
# ---------------------------------------------------------------------------


class TestRerun:
    """Verify that a failed pipeline can be resumed from the failed node."""

    def test_reset_failed_node_and_resume(self, queue, workspace_id):
        """Reset a failed node back to pending; pipeline should continue from there."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Re-run test",
            description="Test pipeline resume",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        # Complete Research
        queue.update_node(
            nodes[0].id,
            status="completed",
            output_json=json.dumps({"research": "data"}),
        )

        # Fail Analyze
        queue.update_node(
            nodes[1].id,
            status="failed",
            error="Transient Ollama error",
        )

        # Verify pipeline is stuck
        next_node = queue.get_next_pending_node(job.id)
        assert next_node is None

        # Reset failed node back to pending (simulating re-run)
        queue.update_node(nodes[1].id, status="pending", error="")

        # Now Analyze should be eligible again
        next_node = queue.get_next_pending_node(job.id)
        assert next_node is not None
        assert next_node.title == "Analyze"

        # Complete Analyze
        queue.update_node(
            nodes[1].id,
            status="completed",
            output_json=json.dumps({"analysis": "insights"}),
        )

        # Execute should now be eligible
        next_node = queue.get_next_pending_node(job.id)
        assert next_node is not None
        assert next_node.title == "Execute"

    def test_completed_outputs_preserved_after_rerun(self, queue, workspace_id):
        """Outputs from already-completed nodes survive a failed-node reset."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Output preservation test",
            description="Outputs survive rerun",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        research_output = {"sources": ["arxiv", "wikipedia"]}
        queue.update_node(
            nodes[0].id,
            status="completed",
            output_json=json.dumps(research_output),
        )

        # Fail and reset Analyze
        queue.update_node(nodes[1].id, status="failed", error="timeout")
        queue.update_node(nodes[1].id, status="pending", error="")

        # Research output must still be intact
        refreshed = queue.get_nodes(job.id)
        assert refreshed[0].status == "completed"
        assert json.loads(refreshed[0].output_json) == research_output


# ---------------------------------------------------------------------------
# Test 5: Template save + create from template
# ---------------------------------------------------------------------------


class TestTemplateSaveAndLoad:
    """Verify save_as_template and create_job_from_template round-trip."""

    def test_save_completed_pipeline_as_template(self, queue, workspace_id):
        """Save a completed pipeline job as a template, verify node structure."""
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Template source job",
            description="Job that becomes a template",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)

        # Complete all nodes
        for i, node in enumerate(nodes):
            queue.update_node(
                node.id,
                status="completed",
                output_json=json.dumps({"step": i, "result": f"output_{i}"}),
            )
        queue.update_job_status(job.id, "done", result_summary="All nodes passed")

        # Save as template
        template = queue.save_as_template(
            job_id=job.id,
            name="Research Pipeline Template",
            workspace_id=workspace_id,
            created_by=None,
        )

        assert template is not None
        assert template.name == "Research Pipeline Template"
        assert len(template.nodes) == 4
        assert template.use_count == 0

        # Verify template node structure matches original
        for i, tnode in enumerate(template.nodes):
            assert tnode["title"] == PIPELINE_NODE_DEFS[i]["title"]
            assert tnode["instructions"] == PIPELINE_NODE_DEFS[i]["instructions"]
            assert tnode["tools_allowed"] == PIPELINE_NODE_DEFS[i]["tools_allowed"]
            assert tnode["timeout_sec"] == PIPELINE_NODE_DEFS[i]["timeout_sec"]

    def test_create_job_from_template(self, queue, workspace_id):
        """Create a new job from a saved template and verify node structure."""
        # First create and complete the source job
        source_job = queue.create_job(
            workspace_id=workspace_id,
            title="Source job",
            description="Source for template",
            source="test",
            requester=None,
            mode="pipeline",
        )
        source_nodes = queue.create_nodes(source_job.id, PIPELINE_NODE_DEFS)
        for node in source_nodes:
            queue.update_node(node.id, status="completed", output_json='{"ok": true}')

        # Save template
        template = queue.save_as_template(
            job_id=source_job.id,
            name="Reusable Pipeline",
            workspace_id=workspace_id,
        )

        # Create new job from template (requester=None to avoid FK on users)
        new_job = queue.create_job_from_template(
            template_id=template.id,
            workspace_id=workspace_id,
            requester=None,
        )

        assert new_job.mode == "pipeline"
        assert new_job.template_id == template.id
        assert new_job.title == "Reusable Pipeline"

        # Verify new job has the same 4 nodes
        new_nodes = queue.get_nodes(new_job.id)
        assert len(new_nodes) == 4

        for i, node in enumerate(new_nodes):
            assert node.title == PIPELINE_NODE_DEFS[i]["title"]
            assert node.tools_allowed == PIPELINE_NODE_DEFS[i]["tools_allowed"]
            assert node.status == "pending"  # All fresh
            assert node.output_json is None  # No output yet

        # Template use_count should be incremented
        refreshed_template = queue.get_template(template.id)
        assert refreshed_template.use_count == 1

    def test_template_depends_on_preserved(self, queue, workspace_id):
        """Template nodes should preserve depends_on references."""
        source_job = queue.create_job(
            workspace_id=workspace_id,
            title="Deps preservation test",
            description="Test",
            source="test",
            requester=None,
            mode="pipeline",
        )
        source_nodes = queue.create_nodes(source_job.id, PIPELINE_NODE_DEFS)
        for node in source_nodes:
            queue.update_node(node.id, status="completed", output_json='{"ok": true}')

        template = queue.save_as_template(
            job_id=source_job.id,
            name="Deps Template",
            workspace_id=workspace_id,
        )

        # Template nodes store depends_on as the resolved UUIDs from the source job.
        # When creating a new job from this template, create_nodes should
        # resolve title references again.

        # Verify the template captured depends_on (they will be UUIDs from source)
        assert template.nodes[0]["depends_on"] == []
        # The other nodes should have depends_on filled (UUIDs or titles)
        # Depends_on was stored as resolved UUIDs from the source job's nodes.
        # When creating a new job from the template, they won't match the new node UUIDs.
        # However, create_nodes resolves title references, so as long as the
        # template includes title-based depends_on, it works.

        # BUG CHECK: save_as_template stores depends_on as resolved UUIDs from
        # the source job. When create_job_from_template creates new nodes, those
        # UUIDs won't match the new nodes. The title resolution in create_nodes
        # only works if depends_on contains TITLES, not UUIDs.
        # Let's verify whether this is actually broken.
        new_job = queue.create_job_from_template(
            template_id=template.id,
            workspace_id=workspace_id,
            requester=None,
        )
        new_nodes = queue.get_nodes(new_job.id)

        # Check if depends_on was correctly resolved for the new nodes
        title_to_new_id = {n.title: n.id for n in new_nodes}

        # First node should have no deps
        assert new_nodes[0].depends_on == []

        # Second node (Analyze) should depend on Research's NEW id
        # If this fails, it means save_as_template stores UUIDs instead of titles
        # and create_nodes can't resolve them for the new job.
        analyze_deps = new_nodes[1].depends_on
        if analyze_deps and analyze_deps[0] != title_to_new_id.get("Research"):
            # This is the bug: depends_on contains OLD UUIDs from the source job
            pytest.fail(
                f"BUG: Template depends_on contains stale UUID {analyze_deps[0]!r} "
                f"instead of new node ID {title_to_new_id.get('Research')!r}. "
                f"save_as_template should store title strings, not resolved UUIDs."
            )


# ---------------------------------------------------------------------------
# Test 6: Worker-driven pipeline execution (mocked LLM)
# ---------------------------------------------------------------------------


def _make_ollama_response(content: str, tool_calls: list | None = None) -> dict:
    """Build a mock Ollama /api/chat response."""
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "message": msg,
        "prompt_eval_count": 100,
        "eval_count": 50,
    }


def _make_mock_executor_result(output: dict, success: bool = True):
    """Create a mock NodeResult."""
    from backend.jobs.executor import NodeResult
    return NodeResult(
        success=success,
        output=output,
        error=None if success else "Mock error",
        tokens_in=100,
        tokens_out=50,
        duration_ms=1000,
        tool_calls_made=1,
        model_used="test-model",
    )


class TestWorkerPipelineExecution:
    """Verify the full worker pipeline flow with mocked LLM calls."""

    @pytest.mark.asyncio
    async def test_full_4_node_pipeline_execution(self, queue, workspace_id):
        """Worker should execute all 4 nodes in sequence, piping outputs."""
        from backend.jobs.executor import NodeExecutor, NodeResult
        from backend.jobs.worker import JobWorker

        # Create the pipeline job with nodes
        job = queue.create_job(
            workspace_id=workspace_id,
            title="Full pipeline test",
            description="Test full pipeline execution",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)
        queue.update_job_status(job.id, "executing")

        # Mock tool registry
        mock_registry = MagicMock()
        mock_registry.tools = []
        mock_registry.get_ollama_tools.return_value = []

        # Define outputs for each node
        node_outputs_sequence = [
            {"topics": ["AI", "ML"], "sources": 5},       # Research
            {"insights": ["trend1", "trend2"]},             # Analyze
            {"deliverable": "report.md", "status": "ok"},   # Execute
            {"review": "pass", "score": 0.95},              # Review
        ]

        call_count = 0

        async def mock_execute_node(node, job, input_data=None, progress_callback=None):
            nonlocal call_count
            idx = call_count
            call_count += 1
            return NodeResult(
                success=True,
                output=node_outputs_sequence[idx],
                error=None,
                tokens_in=100,
                tokens_out=50,
                duration_ms=500,
                tool_calls_made=1,
                model_used="test-model",
            )

        worker = JobWorker(tool_registry=mock_registry)
        worker._running = True  # Worker must be "running" for _execute_nodes to proceed
        worker.executor.execute_node = mock_execute_node

        # Execute all nodes
        result = await worker._execute_nodes(job, nodes)
        assert result is True
        assert call_count == 4

        # Verify all nodes completed with correct output
        final_nodes = queue.get_nodes(job.id)
        for i, node in enumerate(final_nodes):
            assert node.status == "completed", (
                f"Node {node.title} should be completed, got {node.status}"
            )
            parsed_output = json.loads(node.output_json)
            assert parsed_output == node_outputs_sequence[i]

    @pytest.mark.asyncio
    async def test_pipeline_execution_pipes_output_to_next_node(self, queue, workspace_id):
        """Verify that each node receives the previous node's output as input."""
        from backend.jobs.executor import NodeResult
        from backend.jobs.worker import JobWorker

        job = queue.create_job(
            workspace_id=workspace_id,
            title="Piping verification test",
            description="Verify output piping",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)
        queue.update_job_status(job.id, "executing")

        mock_registry = MagicMock()
        mock_registry.tools = []
        mock_registry.get_ollama_tools.return_value = []

        # Track what input each node receives
        received_inputs: list[dict | None] = []

        node_outputs_seq = [
            {"research_data": "raw data"},
            {"analysis": "processed"},
            {"output": "final"},
            {"review": "approved"},
        ]
        call_idx = 0

        async def mock_execute_node(node, job, input_data=None, progress_callback=None):
            nonlocal call_idx
            received_inputs.append(input_data)
            idx = call_idx
            call_idx += 1
            return NodeResult(
                success=True,
                output=node_outputs_seq[idx],
                error=None,
                tokens_in=50,
                tokens_out=25,
                duration_ms=200,
                tool_calls_made=0,
                model_used="test-model",
            )

        worker = JobWorker(tool_registry=mock_registry)
        worker._running = True
        worker.executor.execute_node = mock_execute_node

        result = await worker._execute_nodes(job, nodes)
        assert result is True

        # Node 1 (Research) should get None (no predecessor)
        assert received_inputs[0] is None

        # Node 2 (Analyze) should get Research's output
        assert received_inputs[1] == {"research_data": "raw data"}

        # Node 3 (Execute) should get Analyze's output
        assert received_inputs[2] == {"analysis": "processed"}

        # Node 4 (Review) should get Execute's output
        assert received_inputs[3] == {"output": "final"}

    @pytest.mark.asyncio
    async def test_pipeline_stops_on_node_failure(self, queue, workspace_id):
        """Pipeline should stop when a node fails and not execute subsequent nodes."""
        from backend.jobs.executor import NodeResult
        from backend.jobs.worker import JobWorker

        job = queue.create_job(
            workspace_id=workspace_id,
            title="Failure test",
            description="Test pipeline stops on failure",
            source="test",
            requester=None,
            mode="pipeline",
        )
        nodes = queue.create_nodes(job.id, PIPELINE_NODE_DEFS)
        queue.update_job_status(job.id, "executing")

        mock_registry = MagicMock()
        mock_registry.tools = []
        mock_registry.get_ollama_tools.return_value = []

        call_count = 0

        async def mock_execute_node(node, job, input_data=None, progress_callback=None):
            nonlocal call_count
            call_count += 1
            if node.title == "Analyze":
                return NodeResult(
                    success=False,
                    output={},
                    error="Ollama connection refused",
                    tokens_in=0,
                    tokens_out=0,
                    duration_ms=100,
                    tool_calls_made=0,
                    model_used="test-model",
                )
            return NodeResult(
                success=True,
                output={"result": f"{node.title} done"},
                error=None,
                tokens_in=50,
                tokens_out=25,
                duration_ms=200,
                tool_calls_made=0,
                model_used="test-model",
            )

        worker = JobWorker(tool_registry=mock_registry)
        worker._running = True
        worker.executor.execute_node = mock_execute_node

        # Patch retry policy to 1 attempt to speed up test
        for n in nodes:
            queue.update_node(n.id, status="pending")
        from backend.config import DB_PATH
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE job_nodes SET retry_policy_json = ? WHERE job_id = ?",
            (json.dumps({"max_attempts": 1, "backoff_ms": [0]}), job.id),
        )
        conn.commit()
        conn.close()

        # Re-fetch nodes with updated retry policy
        nodes = queue.get_nodes(job.id)
        result = await worker._execute_nodes(job, nodes)
        assert result is False

        # Research should have been called (seq 1) and Analyze (seq 2)
        assert call_count == 2  # Research OK, Analyze failed

        # Execute and Review should still be pending
        final_nodes = queue.get_nodes(job.id)
        assert final_nodes[0].status == "completed"  # Research
        assert final_nodes[1].status == "failed"      # Analyze
        assert final_nodes[2].status == "pending"      # Execute (never ran)
        assert final_nodes[3].status == "pending"      # Review (never ran)

    @pytest.mark.asyncio
    async def test_template_based_job_through_worker(self, queue, workspace_id):
        """Create a job from template, then run it through the worker."""
        from backend.jobs.executor import NodeResult
        from backend.jobs.worker import JobWorker

        # Create and complete a source job to make a template
        source_job = queue.create_job(
            workspace_id=workspace_id,
            title="Template source",
            description="Source for template",
            source="test",
            requester=None,
            mode="pipeline",
        )
        source_nodes = queue.create_nodes(source_job.id, PIPELINE_NODE_DEFS)
        for node in source_nodes:
            queue.update_node(node.id, status="completed", output_json='{"ok": true}')

        # Save template
        template = queue.save_as_template(
            job_id=source_job.id,
            name="Worker Test Template",
            workspace_id=workspace_id,
        )

        # Create new job from template
        new_job = queue.create_job_from_template(
            template_id=template.id,
            workspace_id=workspace_id,
            requester=None,
        )
        queue.update_job_status(new_job.id, "executing")

        mock_registry = MagicMock()
        mock_registry.tools = []
        mock_registry.get_ollama_tools.return_value = []

        call_count = 0

        async def mock_execute_node(node, job, input_data=None, progress_callback=None):
            nonlocal call_count
            call_count += 1
            return NodeResult(
                success=True,
                output={"step": call_count, "result": f"{node.title} completed"},
                error=None,
                tokens_in=100,
                tokens_out=50,
                duration_ms=500,
                tool_calls_made=1,
                model_used="test-model",
            )

        worker = JobWorker(tool_registry=mock_registry)
        worker._running = True
        worker.executor.execute_node = mock_execute_node

        new_nodes = queue.get_nodes(new_job.id)
        result = await worker._execute_nodes(new_job, new_nodes)
        assert result is True
        assert call_count == 4

        # All nodes should be completed
        final_nodes = queue.get_nodes(new_job.id)
        for node in final_nodes:
            assert node.status == "completed"


# ---------------------------------------------------------------------------
# Test 7: Template depends_on bug detection and fix verification
# ---------------------------------------------------------------------------


class TestTemplateDependsBug:
    """The save_as_template method stores depends_on as resolved UUIDs from the
    source job. When create_job_from_template instantiates new nodes, those
    old UUIDs won't match the new node IDs. This test suite detects the bug
    and verifies the fix (if applied)."""

    def test_template_stores_depends_on_as_node_ids(self, queue, workspace_id):
        """Detect whether depends_on in template contains UUIDs or titles."""
        source_job = queue.create_job(
            workspace_id=workspace_id,
            title="Bug detection job",
            description="Test",
            source="test",
            requester=None,
            mode="pipeline",
        )
        source_nodes = queue.create_nodes(source_job.id, PIPELINE_NODE_DEFS)
        for node in source_nodes:
            queue.update_node(node.id, status="completed", output_json='{"ok": true}')

        template = queue.save_as_template(
            job_id=source_job.id,
            name="Bug Detection Template",
            workspace_id=workspace_id,
        )

        # The template's Analyze node should depend on Research.
        # Check whether it stored a UUID (bug) or a title (correct).
        analyze_tmpl = template.nodes[1]
        deps = analyze_tmpl.get("depends_on", [])

        if deps:
            source_title_to_id = {n.title: n.id for n in source_nodes}
            # If the dep is the Research node's UUID from the source job, that's the bug
            if deps[0] == source_title_to_id.get("Research"):
                # This is the bug -- depends_on has source job UUIDs
                # The fix should convert them to titles before saving
                pytest.skip(
                    "KNOWN BUG: save_as_template stores depends_on as source job UUIDs. "
                    "Run the template round-trip test to confirm the impact."
                )
            else:
                # deps[0] is a title string -- this is correct
                assert deps[0] == "Research"

    def test_template_roundtrip_dag_integrity(self, queue, workspace_id):
        """End-to-end: create job -> complete -> template -> new job -> verify DAG."""
        source_job = queue.create_job(
            workspace_id=workspace_id,
            title="Roundtrip test",
            description="DAG integrity",
            source="test",
            requester=None,
            mode="pipeline",
        )
        source_nodes = queue.create_nodes(source_job.id, PIPELINE_NODE_DEFS)
        for node in source_nodes:
            queue.update_node(node.id, status="completed", output_json='{"ok": true}')

        template = queue.save_as_template(
            job_id=source_job.id,
            name="DAG Roundtrip",
            workspace_id=workspace_id,
        )

        new_job = queue.create_job_from_template(
            template_id=template.id,
            workspace_id=workspace_id,
            requester=None,
        )
        new_nodes = queue.get_nodes(new_job.id)
        new_title_to_id = {n.title: n.id for n in new_nodes}

        # Verify DAG is correctly wired for the new job
        assert new_nodes[0].depends_on == [], "Research should have no deps"

        # Check if the pipeline scheduler works correctly
        next_node = queue.get_next_pending_node(new_job.id)
        assert next_node is not None
        assert next_node.title == "Research"

        # Complete Research and check that Analyze becomes eligible
        queue.update_node(new_nodes[0].id, status="completed", output_json='{"ok": true}')
        next_node = queue.get_next_pending_node(new_job.id)
        assert next_node is not None
        assert next_node.title == "Analyze"

        # Complete Analyze and check Execute
        queue.update_node(new_nodes[1].id, status="completed", output_json='{"ok": true}')
        next_node = queue.get_next_pending_node(new_job.id)
        assert next_node is not None
        assert next_node.title == "Execute"


# ---------------------------------------------------------------------------
# Test 8: Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    """Verify that pipeline operations produce proper audit entries."""

    def test_audit_entries_created_for_pipeline_operations(self, queue, workspace_id):
        """Template creation and job-from-template should audit."""
        source_job = queue.create_job(
            workspace_id=workspace_id,
            title="Audit trail test",
            description="Test audit",
            source="test",
            requester=None,
            mode="pipeline",
        )
        source_nodes = queue.create_nodes(source_job.id, PIPELINE_NODE_DEFS)
        for node in source_nodes:
            queue.update_node(node.id, status="completed", output_json='{"ok": true}')

        template = queue.save_as_template(
            job_id=source_job.id,
            name="Audit Template",
            workspace_id=workspace_id,
        )

        new_job = queue.create_job_from_template(
            template_id=template.id,
            workspace_id=workspace_id,
            requester=None,
        )

        # Check that the new job has an audit entry for template creation
        audit_log = queue.get_audit_log(new_job.id)
        actions = [e.action for e in audit_log]
        assert "job_created_from_template" in actions
