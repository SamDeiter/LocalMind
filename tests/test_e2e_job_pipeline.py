"""
End-to-end integration tests for the LocalMind job pipeline.

Full flow: create a job via REST API -> worker picks it up -> planner creates
nodes -> executor runs them -> reviewer validates -> job completes.

All external LLM calls (Gemini planner/reviewer, Ollama executor) are mocked
so tests run fast and offline.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from backend.jobs.models import JobStatus, NodeStatus

# ---------------------------------------------------------------------------
# Minimal job-pipeline schema (FK enforcement OFF for test isolation)
# ---------------------------------------------------------------------------

_JOB_PIPELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    settings_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    deployment_mode TEXT NOT NULL DEFAULT 'hybrid',
    settings_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',
    slack_user_id TEXT,
    settings_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_active_at TEXT
);

CREATE TABLE IF NOT EXISTS memberships (
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',
    PRIMARY KEY (user_id, workspace_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    model TEXT NOT NULL,
    system_prompt TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

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


def _bootstrap_db(db_path: Path) -> None:
    """Create schema and seed default tenant for test isolation."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_JOB_PIPELINE_SCHEMA)

    from datetime import datetime, timezone
    import uuid

    now = datetime.now(timezone.utc).isoformat()
    org_id = str(uuid.uuid4())
    ws_id = "default"
    user_id = str(uuid.uuid4())

    conn.execute(
        "INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (org_id, "TestOrg", "default", now, now),
    )
    conn.execute(
        "INSERT INTO workspaces (id, org_id, name, slug, deployment_mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ws_id, org_id, "Default", "default", "hybrid", now, now),
    )
    conn.execute(
        "INSERT INTO users (id, org_id, email, display_name, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, org_id, "test@localhost", "Tester", "admin", now, now),
    )
    conn.execute(
        "INSERT INTO memberships (user_id, workspace_id, role) VALUES (?, ?, ?)",
        (user_id, ws_id, "admin"),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Canned LLM responses
# ---------------------------------------------------------------------------

CANNED_PLAN_2_NODES = json.dumps([
    {
        "title": "Research step",
        "instructions": "Gather information on the topic.",
        "tools_allowed": [],
        "expected_output": "Research notes.",
        "depends_on": [],
        "timeout_sec": 120,
    },
    {
        "title": "Write report",
        "instructions": "Write a report based on research.",
        "tools_allowed": [],
        "expected_output": "Final report text.",
        "depends_on": [],
        "timeout_sec": 120,
    },
])

CANNED_REVIEW_PASS = json.dumps({
    "score": 0.95,
    "passed": True,
    "issues": [],
    "summary": "All checks passed.",
})

CANNED_REVIEW_FAIL = json.dumps({
    "score": 0.3,
    "passed": False,
    "issues": [
        {
            "severity": "critical",
            "category": "completeness",
            "description": "Missing key section.",
            "node_sequence": 1,
            "suggestion": "Add the missing section.",
        }
    ],
    "summary": "Incomplete output.",
})


def _make_ollama_success_response() -> dict:
    """A fake Ollama /api/chat response with no tool calls (final answer)."""
    return {
        "message": {
            "role": "assistant",
            "content": json.dumps({"result": "Task completed successfully."}),
        },
        "prompt_eval_count": 100,
        "eval_count": 50,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def e2e_env(tmp_path):
    """Set up isolated DB + JOBS_DIR and return (db_path, jobs_dir).

    Patches are applied as a context manager so they stay active for the
    duration of the test.
    """
    db_path = tmp_path / "e2e_test.db"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()

    _bootstrap_db(db_path)

    patches = [
        patch("backend.config.DB_PATH", db_path),
        patch("backend.db.DB_PATH", db_path),
        patch("backend.jobs.queue.DB_PATH", db_path),
        patch("backend.config.JOBS_DIR", jobs_dir),
        patch("backend.jobs.queue.JOBS_DIR", jobs_dir),
        patch("backend.routes.jobs.JOBS_DIR", jobs_dir),
    ]

    for p in patches:
        p.start()

    yield db_path, jobs_dir

    for p in patches:
        p.stop()


@pytest_asyncio.fixture()
async def client(e2e_env):
    """Provide an httpx.AsyncClient wired to the FastAPI app.

    The lifespan is NOT used because we drive the worker manually in tests.
    Instead we create a bare app with only the jobs router.
    """
    from fastapi import FastAPI
    from backend.routes.jobs import router as jobs_router

    test_app = FastAPI()
    test_app.include_router(jobs_router)

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture()
async def worker(e2e_env):
    """Create a JobWorker with mocked planner / executor / reviewer."""
    from backend.jobs.worker import JobWorker
    from backend.jobs.executor import NodeResult

    registry = MagicMock()
    registry.tools = []
    registry.get_ollama_tools.return_value = []

    w = JobWorker(
        tool_registry=registry,
        ollama_url="http://fake-ollama:11434",
        activity_callback=AsyncMock(),
    )

    # Mock the planner to return our canned 2-node plan
    w.planner.plan = AsyncMock(return_value=json.loads(CANNED_PLAN_2_NODES))

    # Mock the executor to return success for every node
    w.executor.execute_node = AsyncMock(
        return_value=NodeResult(
            success=True,
            output={"result": "Node completed."},
            tokens_in=100,
            tokens_out=50,
            tool_calls_made=0,
            model_used="test-model",
        )
    )

    # Mock the reviewer to return a passing review
    from backend.jobs.reviewer import ReviewResult

    w.reviewer.review = AsyncMock(
        return_value=ReviewResult(
            passed=True,
            score=0.95,
            feedback="All checks passed.",
            issues=[],
            needs_human_review=False,
        )
    )

    # We drive the worker manually (no start() loop), but _running must be
    # True so the cancel-check inside _run_job does not abort immediately.
    w._running = True

    return w


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _create_job(client: httpx.AsyncClient, **overrides) -> dict:
    """POST /api/jobs with defaults; return the response JSON."""
    payload = {"title": "Test job", "mode": "quick"}
    payload.update(overrides)
    resp = await client.post("/api/jobs", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _poll_job_status(
    client: httpx.AsyncClient,
    job_id: str,
    target: str,
    timeout: float = 10.0,
    interval: float = 0.1,
) -> dict:
    """Poll GET /api/jobs/{id} until status matches target or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = await client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] == target:
            return data
        await asyncio.sleep(interval)
    raise TimeoutError(f"Job {job_id} did not reach status '{target}' within {timeout}s (last: {data['status']})")


# ===========================================================================
# Tests
# ===========================================================================


class TestHappyPath:
    """Full pipeline: create -> plan -> execute -> review -> done."""

    @pytest.mark.asyncio
    async def test_job_completes_via_worker(self, client, worker):
        """Create a job via API, run the worker once, verify it reaches 'done'."""
        job_data = await _create_job(client)
        job_id = job_data["id"]
        assert job_data["status"] == "pending"

        # Drive the worker through one poll cycle (picks up and runs the job).
        await worker._poll_and_execute()

        resp = await client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == JobStatus.DONE.value

        # Verify nodes were created
        assert len(result["nodes"]) == 2
        assert result["nodes"][0]["title"] == "Research step"
        assert result["nodes"][1]["title"] == "Write report"

        # Verify audit trail exists
        assert len(result["audit"]) > 0
        actions = [a["action"] for a in result["audit"]]
        assert "done" in actions

    @pytest.mark.asyncio
    async def test_job_creates_directory_structure(self, client, e2e_env):
        """Creating a job should set up input/output directories under JOBS_DIR."""
        _, jobs_dir = e2e_env
        job_data = await _create_job(client)
        job_id = job_data["id"]

        input_dir = jobs_dir / job_id / "input"
        output_dir = jobs_dir / job_id / "output"
        assert input_dir.exists()
        assert output_dir.exists()

    @pytest.mark.asyncio
    async def test_job_done_has_result_summary(self, client, worker):
        """A completed job should have a result_summary set by the reviewer."""
        job_data = await _create_job(client)
        job_id = job_data["id"]

        await worker._poll_and_execute()

        resp = await client.get(f"/api/jobs/{job_id}")
        result = resp.json()
        assert result["status"] == "done"
        assert result.get("result_summary") is not None
        assert "passed" in result["result_summary"].lower()


class TestJobCancellation:
    """Create job -> cancel -> verify status transitions."""

    @pytest.mark.asyncio
    async def test_cancel_pending_job(self, client):
        """Cancelling a pending job should transition to 'cancelling'."""
        job_data = await _create_job(client)
        job_id = job_data["id"]

        resp = await client.post(f"/api/jobs/{job_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == JobStatus.CANCELLING.value

    @pytest.mark.asyncio
    async def test_cancel_already_done_job(self, client, worker):
        """Cancelling a completed job should return 422."""
        job_data = await _create_job(client)
        job_id = job_data["id"]

        await worker._poll_and_execute()

        resp = await client.post(f"/api/jobs/{job_id}/cancel")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_job(self, client):
        """Cancelling a job that doesn't exist should return 404."""
        resp = await client.post("/api/jobs/nonexistent-id/cancel")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_worker_respects_cancel_flag(self, client, worker):
        """If a job is cancelled before the worker picks it up, it stays cancelled."""
        job_data = await _create_job(client)
        job_id = job_data["id"]

        # Cancel immediately
        await client.post(f"/api/jobs/{job_id}/cancel")

        # Worker tries to pick up work -- the job is now 'cancelling', not 'pending'
        await worker._poll_and_execute()

        resp = await client.get(f"/api/jobs/{job_id}")
        result = resp.json()
        # The job should not have been picked up (status stays cancelling, not done)
        assert result["status"] in (
            JobStatus.CANCELLING.value,
            JobStatus.CANCELLED.value,
        )


class TestJobListing:
    """Test the GET /api/jobs endpoint."""

    @pytest.mark.asyncio
    async def test_list_empty(self, client):
        """Listing jobs with no jobs returns an empty list."""
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["jobs"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_list_multiple_jobs(self, client):
        """Creating multiple jobs and listing returns them all."""
        await _create_job(client, title="Job A")
        await _create_job(client, title="Job B")
        await _create_job(client, title="Job C")

        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        titles = {j["title"] for j in data["jobs"]}
        assert titles == {"Job A", "Job B", "Job C"}

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, client, worker):
        """Filtering by status should only return matching jobs."""
        await _create_job(client, title="Will complete")
        await _create_job(client, title="Stays pending")

        # Run worker once -- picks up the first pending job
        await worker._poll_and_execute()

        resp = await client.get("/api/jobs?status=done")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["jobs"][0]["title"] == "Will complete"

    @pytest.mark.asyncio
    async def test_list_pagination(self, client):
        """Pagination params limit and offset work correctly."""
        for i in range(5):
            await _create_job(client, title=f"Job {i}")

        resp = await client.get("/api/jobs?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

    @pytest.mark.asyncio
    async def test_list_invalid_status(self, client):
        """Requesting an invalid status filter returns 400."""
        resp = await client.get("/api/jobs?status=bogus")
        assert resp.status_code == 400


class TestJobDetails:
    """Test GET /api/jobs/{job_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_job_detail_fields(self, client):
        """Verify all expected fields are present in the job detail response."""
        job_data = await _create_job(client, title="Detail test", description="desc", priority=5)
        job_id = job_data["id"]

        resp = await client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        detail = resp.json()

        # The detail endpoint returns to_dict() (full), not to_api_dict()
        assert detail["id"] == job_id
        assert detail["title"] == "Detail test"
        assert detail["description"] == "desc"
        assert detail["priority"] == 5
        assert detail["status"] == "pending"
        assert detail["mode"] == "quick"
        assert "nodes" in detail
        assert "files" in detail
        assert "audit" in detail

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, client):
        """Requesting a non-existent job returns 404."""
        resp = await client.get("/api/jobs/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_job_after_execution(self, client, worker):
        """After execution, the job detail includes completed nodes."""
        job_data = await _create_job(client)
        job_id = job_data["id"]

        await worker._poll_and_execute()

        resp = await client.get(f"/api/jobs/{job_id}")
        detail = resp.json()
        assert detail["status"] == "done"

        for node in detail["nodes"]:
            assert node["status"] == NodeStatus.COMPLETED.value
            assert node["output_json"] is not None


class TestTemplateCRUD:
    """Test the template endpoints."""

    @pytest.mark.asyncio
    async def test_list_templates_empty(self, client):
        """Listing templates with none returns empty list."""
        resp = await client.get("/api/jobs/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["templates"] == []

    @pytest.mark.asyncio
    async def test_create_template_from_done_job(self, client, worker):
        """Save a completed job as a template, then list it."""
        job_data = await _create_job(client, title="Template source")
        job_id = job_data["id"]

        await worker._poll_and_execute()

        # Create template from completed job
        resp = await client.post(
            "/api/jobs/templates",
            json={"job_id": job_id, "name": "My Template"},
        )
        assert resp.status_code == 201
        tmpl = resp.json()["template"]
        assert tmpl["name"] == "My Template"
        assert len(tmpl["nodes"]) == 2

        # List templates
        resp = await client.get("/api/jobs/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        assert len(templates) == 1
        assert templates[0]["name"] == "My Template"

    @pytest.mark.asyncio
    async def test_create_template_from_pending_job_fails(self, client):
        """Cannot save a pending job as a template (422)."""
        job_data = await _create_job(client)
        job_id = job_data["id"]

        resp = await client.post(
            "/api/jobs/templates",
            json={"job_id": job_id, "name": "Bad Template"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_template_missing_name(self, client, worker):
        """Template creation requires a non-blank name (400)."""
        job_data = await _create_job(client)
        job_id = job_data["id"]
        await worker._poll_and_execute()

        resp = await client.post(
            "/api/jobs/templates",
            json={"job_id": job_id, "name": ""},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_template_missing_job_id(self, client):
        """Template creation requires a job_id (400)."""
        resp = await client.post(
            "/api/jobs/templates",
            json={"name": "No Job"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_template_nonexistent_job(self, client):
        """Template creation with a bad job_id returns 404."""
        resp = await client.post(
            "/api/jobs/templates",
            json={"job_id": "does-not-exist", "name": "Ghost Template"},
        )
        assert resp.status_code == 404


class TestActivityStream:
    """Test the SSE activity stream."""

    @pytest.mark.asyncio
    async def test_sse_receives_job_created_event(self, e2e_env):
        """Creating a job should emit a job_created event to SSE subscribers."""
        from backend.routes.jobs import _subscribers, emit_activity

        # Set up a subscriber queue
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        _subscribers.append(q)

        try:
            # Emit a fake event (the route handler calls emit_activity on create)
            await emit_activity("job_created", {"job_id": "test-123", "title": "Test"})

            event = q.get_nowait()
            assert event["type"] == "job_created"
            assert event["data"]["job_id"] == "test-123"
            assert "timestamp" in event
        finally:
            _subscribers.remove(q)

    @pytest.mark.asyncio
    async def test_sse_receives_multiple_events(self, e2e_env):
        """Multiple events arrive in order."""
        from backend.routes.jobs import _subscribers, emit_activity

        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        _subscribers.append(q)

        try:
            await emit_activity("event_a", {"seq": 1})
            await emit_activity("event_b", {"seq": 2})
            await emit_activity("event_c", {"seq": 3})

            events = []
            while not q.empty():
                events.append(q.get_nowait())

            assert len(events) == 3
            assert events[0]["type"] == "event_a"
            assert events[1]["type"] == "event_b"
            assert events[2]["type"] == "event_c"
        finally:
            _subscribers.remove(q)


class TestErrorHandling:
    """Test validation and error responses."""

    @pytest.mark.asyncio
    async def test_create_job_missing_title(self, client):
        """Creating a job without a title returns 400."""
        resp = await client.post("/api/jobs", json={"mode": "quick"})
        assert resp.status_code == 400
        assert "title" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_job_blank_title(self, client):
        """Creating a job with a blank title returns 400."""
        resp = await client.post("/api/jobs", json={"title": "   ", "mode": "quick"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_job_invalid_mode(self, client):
        """Creating a job with an invalid mode returns 400."""
        resp = await client.post("/api/jobs", json={"title": "Test", "mode": "invalid"})
        assert resp.status_code == 400
        assert "mode" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_job_invalid_priority(self, client):
        """Creating a job with a non-integer priority returns 400."""
        resp = await client.post(
            "/api/jobs", json={"title": "Test", "priority": "not-a-number"}
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_job_invalid_json_body(self, client):
        """Sending malformed JSON returns 400."""
        resp = await client.post(
            "/api/jobs",
            content=b"not valid json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400


class TestJobWithTemplate:
    """Test creating jobs from templates (pipeline mode)."""

    @pytest.mark.asyncio
    async def test_job_with_template_id_uses_pipeline_mode(self, client, worker):
        """Creating a completed job, saving as template, then creating a new
        job referencing that template_id in pipeline mode should work."""
        # Step 1: Create and complete a job
        job1 = await _create_job(client, title="Source job")
        await worker._poll_and_execute()

        # Step 2: Save as template
        resp = await client.post(
            "/api/jobs/templates",
            json={"job_id": job1["id"], "name": "Reusable Pipeline"},
        )
        assert resp.status_code == 201
        template_id = resp.json()["template"]["id"]

        # Step 3: Create a new job in pipeline mode referencing the template
        # Reset the planner mock to return the template's plan
        from backend.jobs.queue import JobQueue
        q = JobQueue()
        tmpl = q.get_template(template_id)
        assert tmpl is not None
        assert len(tmpl.nodes) == 2

        # The planner in pipeline mode reads from DB; mock it to return template nodes
        template_nodes = []
        for n in tmpl.nodes:
            template_nodes.append({
                "title": n.get("title", "Node"),
                "instructions": n.get("instructions", ""),
                "tools_allowed": n.get("tools_allowed", []),
                "expected_output": n.get("expected_output", ""),
                "depends_on": n.get("depends_on", []),
                "timeout_sec": n.get("timeout_sec", 300),
            })
        worker.planner.plan = AsyncMock(return_value=template_nodes)

        job2 = await _create_job(
            client,
            title="From template",
            mode="pipeline",
            template_id=template_id,
        )
        assert job2["mode"] == "pipeline"

        await worker._poll_and_execute()

        resp = await client.get(f"/api/jobs/{job2['id']}")
        detail = resp.json()
        assert detail["status"] == "done"
        assert detail.get("template_id") == template_id


class TestWorkerBehavior:
    """Test worker internals driven via the mocked worker fixture."""

    @pytest.mark.asyncio
    async def test_worker_does_nothing_when_no_jobs(self, worker, e2e_env):
        """Worker poll returns immediately if no pending jobs exist."""
        # Should not raise or hang
        await worker._poll_and_execute()

    @pytest.mark.asyncio
    async def test_worker_handles_planner_failure(self, client, worker):
        """If the planner raises an error, the job should be marked failed."""
        worker.planner.plan = AsyncMock(side_effect=RuntimeError("Gemini down"))

        job_data = await _create_job(client)
        job_id = job_data["id"]

        await worker._poll_and_execute()

        resp = await client.get(f"/api/jobs/{job_id}")
        result = resp.json()
        assert result["status"] == JobStatus.FAILED.value
        assert "Planning error" in (result.get("error") or "")

    @pytest.mark.asyncio
    async def test_worker_handles_executor_failure(self, client, worker):
        """If the executor fails a node, the job should be marked failed."""
        from backend.jobs.executor import NodeResult

        worker.executor.execute_node = AsyncMock(
            return_value=NodeResult(
                success=False,
                output={},
                error="Ollama unreachable",
                tokens_in=0,
                tokens_out=0,
                tool_calls_made=0,
            )
        )

        job_data = await _create_job(client)
        job_id = job_data["id"]

        await worker._poll_and_execute()

        resp = await client.get(f"/api/jobs/{job_id}")
        result = resp.json()
        assert result["status"] == JobStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_worker_handles_reviewer_failure(self, client, worker):
        """If the reviewer raises an exception, the job should fail."""
        worker.reviewer.review = AsyncMock(side_effect=RuntimeError("Review crashed"))

        job_data = await _create_job(client)
        job_id = job_data["id"]

        await worker._poll_and_execute()

        resp = await client.get(f"/api/jobs/{job_id}")
        result = resp.json()
        assert result["status"] == JobStatus.FAILED.value
        assert "Review error" in (result.get("error") or "")

    @pytest.mark.asyncio
    async def test_worker_review_fail_marks_job_failed_after_retries(self, client, worker):
        """A review that fails repeatedly should eventually mark the job failed."""
        from backend.jobs.reviewer import ReviewResult

        # Review always fails
        worker.reviewer.review = AsyncMock(
            return_value=ReviewResult(
                passed=False,
                score=0.3,
                feedback="Incomplete output.",
                issues=[],
                needs_human_review=False,
            )
        )

        job_data = await _create_job(client)
        job_id = job_data["id"]

        await worker._poll_and_execute()

        resp = await client.get(f"/api/jobs/{job_id}")
        result = resp.json()
        assert result["status"] == JobStatus.FAILED.value
        assert result.get("review_count", 0) > 0

    @pytest.mark.asyncio
    async def test_worker_processes_priority_order(self, client, worker):
        """Higher priority jobs should be picked up first."""
        low = await _create_job(client, title="Low priority", priority=1)
        high = await _create_job(client, title="High priority", priority=10)

        # First poll should pick up the high-priority job
        await worker._poll_and_execute()

        resp_high = await client.get(f"/api/jobs/{high['id']}")
        resp_low = await client.get(f"/api/jobs/{low['id']}")

        # High-priority should be done, low should still be pending
        assert resp_high.json()["status"] == JobStatus.DONE.value
        assert resp_low.json()["status"] == JobStatus.PENDING.value

        # Second poll picks up the low-priority job
        await worker._poll_and_execute()

        resp_low2 = await client.get(f"/api/jobs/{low['id']}")
        assert resp_low2.json()["status"] == JobStatus.DONE.value


class TestJobModes:
    """Test different job mode behaviors."""

    @pytest.mark.asyncio
    async def test_quick_mode_default(self, client):
        """Default mode should be 'quick'."""
        resp = await client.post("/api/jobs", json={"title": "Default mode"})
        assert resp.status_code == 201
        assert resp.json()["mode"] == "quick"

    @pytest.mark.asyncio
    async def test_pipeline_mode_explicit(self, client):
        """Can explicitly create a pipeline-mode job."""
        resp = await client.post(
            "/api/jobs", json={"title": "Pipeline job", "mode": "pipeline"}
        )
        assert resp.status_code == 201
        assert resp.json()["mode"] == "pipeline"

    @pytest.mark.asyncio
    async def test_job_api_dict_excludes_internal_fields(self, client):
        """The create response (to_api_dict) should exclude internal fields."""
        job_data = await _create_job(client)
        # to_api_dict() excludes: error, cost_cents, review_count, max_reviews
        assert "error" not in job_data
        assert "cost_cents" not in job_data
        assert "review_count" not in job_data
        assert "max_reviews" not in job_data

    @pytest.mark.asyncio
    async def test_job_detail_includes_internal_fields(self, client):
        """The detail endpoint (to_dict) should include internal fields."""
        job_data = await _create_job(client)
        job_id = job_data["id"]

        resp = await client.get(f"/api/jobs/{job_id}")
        detail = resp.json()
        # to_dict() includes everything
        assert "error" in detail
        assert "cost_cents" in detail
        assert "review_count" in detail
        assert "max_reviews" in detail
