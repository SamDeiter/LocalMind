"""
Comprehensive tests for the LocalMind enterprise durable execution,
artifacts, and evidence modules.

Covers:
    - backend.core.execution (SourceEvent, NodeAttemptTracker, ToolInvocationLog,
      WorkerLeaseManager, DeadLetterQueue, CancellationManager)
    - backend.core.artifacts (ArtifactManager, PreviewGenerator, DiffEngine,
      compute_shape_fingerprint)
    - backend.core.evidence (EvidenceCollector, SourceSnapshotStore, EvidenceLinker)
"""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _seed_default_tenant(db_path: Path) -> dict:
    """Insert the minimal org -> workspace -> user rows required by FK constraints.

    Returns a dict with org_id, workspace_id, and user_id.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    now = datetime.now(timezone.utc).isoformat()
    org_id = uuid.uuid4().hex
    ws_id = uuid.uuid4().hex
    user_id = uuid.uuid4().hex

    conn.execute(
        "INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (?,?,?,?,?)",
        (org_id, "TestOrg", f"test-{org_id[:8]}", now, now),
    )
    conn.execute(
        "INSERT INTO workspaces (id, org_id, name, slug, deployment_mode, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (ws_id, org_id, "TestWS", f"ws-{ws_id[:8]}", "hybrid", now, now),
    )
    conn.execute(
        "INSERT INTO users (id, org_id, email, display_name, role, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, org_id, "test@localhost", "Tester", "admin", now, now),
    )
    conn.commit()
    conn.close()
    return {"org_id": org_id, "workspace_id": ws_id, "user_id": user_id}


def _seed_job(db_path: Path, workspace_id: str, status: str = "pending") -> str:
    """Insert a minimal jobs row and return its id."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    now = datetime.now(timezone.utc).isoformat()
    job_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO jobs (id, workspace_id, title, source, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (job_id, workspace_id, "Test Job", "web", status, now, now),
    )
    conn.commit()
    conn.close()
    return job_id


# ═══════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def execution_db(tmp_path):
    """Create a temporary DB with the full Phase 0 schema for execution tests.

    Patches DB_PATH in both backend.core.schema and backend.core.execution so
    that all managers hit the isolated temp database.
    """
    db_file = tmp_path / "exec_test.sqlite"
    with patch("backend.core.schema.DB_PATH", db_file), \
         patch("backend.core.execution.DB_PATH", db_file), \
         patch("backend.config.DB_PATH", db_file):
        from backend.core.schema import init_phase0_schema
        init_phase0_schema()
        tenant = _seed_default_tenant(db_file)
        yield {"db": db_file, **tenant}


@pytest.fixture
def artifact_db(tmp_path):
    """Temporary DB + workspace directory for artifact tests."""
    db_file = tmp_path / "artifact_test.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    jobs_dir = workspace / "jobs"
    jobs_dir.mkdir()

    with patch("backend.core.schema.DB_PATH", db_file), \
         patch("backend.core.artifacts.DB_PATH", db_file), \
         patch("backend.core.artifacts.WORKSPACE_ROOT", workspace), \
         patch("backend.core.artifacts.JOBS_DIR", jobs_dir), \
         patch("backend.config.DB_PATH", db_file):
        from backend.core.schema import init_phase0_schema
        init_phase0_schema()
        tenant = _seed_default_tenant(db_file)
        yield {"db": db_file, "workspace": workspace, "jobs_dir": jobs_dir, **tenant}


@pytest.fixture
def evidence_db(tmp_path):
    """Temporary DB + snapshots directory for evidence tests."""
    db_file = tmp_path / "evidence_test.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshots_dir = workspace / "snapshots"
    snapshots_dir.mkdir()

    with patch("backend.core.schema.DB_PATH", db_file), \
         patch("backend.core.evidence.DB_PATH", db_file), \
         patch("backend.core.evidence.SNAPSHOTS_DIR", snapshots_dir), \
         patch("backend.core.evidence.WORKSPACE_ROOT", workspace), \
         patch("backend.config.DB_PATH", db_file):
        from backend.core.schema import init_phase0_schema
        init_phase0_schema()
        tenant = _seed_default_tenant(db_file)
        yield {
            "db": db_file,
            "workspace": workspace,
            "snapshots_dir": snapshots_dir,
            **tenant,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  1. execution.py — SourceEvent
# ═══════════════════════════════════════════════════════════════════════════════

def test_source_event_record_new_event(execution_db):
    """Recording a new event returns a non-None hex ID."""
    from backend.core.execution import SourceEvent

    se = SourceEvent()
    eid = se.record_event("slack", "evt_001", execution_db["workspace_id"])
    assert eid is not None
    assert len(eid) == 32  # uuid4().hex


def test_source_event_duplicate_returns_none(execution_db):
    """Recording the same (source, source_event_id) pair twice returns None."""
    from backend.core.execution import SourceEvent

    se = SourceEvent()
    first = se.record_event("slack", "evt_dup", execution_db["workspace_id"])
    second = se.record_event("slack", "evt_dup", execution_db["workspace_id"])
    assert first is not None
    assert second is None


def test_source_event_different_sources_not_duplicate(execution_db):
    """Same source_event_id but different source is NOT a duplicate."""
    from backend.core.execution import SourceEvent

    se = SourceEvent()
    a = se.record_event("slack", "shared_id", execution_db["workspace_id"])
    b = se.record_event("web", "shared_id", execution_db["workspace_id"])
    assert a is not None
    assert b is not None
    assert a != b


def test_source_event_mark_processed(execution_db):
    """mark_processed updates processed_at and result_json in the DB."""
    from backend.core.execution import SourceEvent

    se = SourceEvent()
    eid = se.record_event("api", "evt_proc", execution_db["workspace_id"])
    se.mark_processed(eid, '{"ok": true}')

    conn = sqlite3.connect(str(execution_db["db"]))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM source_events WHERE id = ?", (eid,)).fetchone()
    conn.close()

    assert row["processed_at"] is not None
    assert row["result_json"] == '{"ok": true}'


# ═══════════════════════════════════════════════════════════════════════════════
#  2. execution.py — NodeAttemptTracker
# ═══════════════════════════════════════════════════════════════════════════════

def test_node_attempt_start_and_get(execution_db):
    """start_attempt creates a row retrievable by get_attempts."""
    from backend.core.execution import NodeAttemptTracker

    tracker = NodeAttemptTracker()
    node_id = uuid.uuid4().hex
    aid = tracker.start_attempt(node_id, 1, input_json='{"x":1}', model_used="gemma4:e4b")

    attempts = tracker.get_attempts(node_id)
    assert len(attempts) == 1
    assert attempts[0]["id"] == aid
    assert attempts[0]["status"] == "running"
    assert attempts[0]["attempt_number"] == 1
    assert attempts[0]["model_used"] == "gemma4:e4b"


def test_node_attempt_complete(execution_db):
    """complete_attempt sets status, output, token counts, cost, duration."""
    from backend.core.execution import NodeAttemptTracker

    tracker = NodeAttemptTracker()
    node_id = uuid.uuid4().hex
    aid = tracker.start_attempt(node_id, 1)
    tracker.complete_attempt(aid, '{"result":"done"}', 100, 50, 0.05, 1234)

    latest = tracker.get_latest_attempt(node_id)
    assert latest["status"] == "completed"
    assert latest["output_json"] == '{"result":"done"}'
    assert latest["tokens_in"] == 100
    assert latest["tokens_out"] == 50
    assert latest["cost_cents"] == pytest.approx(0.05)
    assert latest["duration_ms"] == 1234
    assert latest["completed_at"] is not None


def test_node_attempt_fail(execution_db):
    """fail_attempt records error and error_category."""
    from backend.core.execution import NodeAttemptTracker

    tracker = NodeAttemptTracker()
    node_id = uuid.uuid4().hex
    aid = tracker.start_attempt(node_id, 1)
    tracker.fail_attempt(aid, "Connection timed out", "timeout")

    latest = tracker.get_latest_attempt(node_id)
    assert latest["status"] == "failed"
    assert latest["error"] == "Connection timed out"
    assert latest["error_category"] == "timeout"


def test_node_attempt_fail_invalid_category_defaults_to_transient(execution_db):
    """An unrecognised error_category is silently replaced with 'transient'."""
    from backend.core.execution import NodeAttemptTracker

    tracker = NodeAttemptTracker()
    node_id = uuid.uuid4().hex
    aid = tracker.start_attempt(node_id, 1)
    tracker.fail_attempt(aid, "Unknown error", "made_up_category")

    latest = tracker.get_latest_attempt(node_id)
    assert latest["error_category"] == "transient"


def test_node_attempt_multiple_attempts_ordering(execution_db):
    """get_attempts returns rows ordered by attempt_number ascending."""
    from backend.core.execution import NodeAttemptTracker

    tracker = NodeAttemptTracker()
    node_id = uuid.uuid4().hex
    a1 = tracker.start_attempt(node_id, 1)
    a2 = tracker.start_attempt(node_id, 2)
    a3 = tracker.start_attempt(node_id, 3)

    attempts = tracker.get_attempts(node_id)
    assert len(attempts) == 3
    assert [a["attempt_number"] for a in attempts] == [1, 2, 3]
    assert [a["id"] for a in attempts] == [a1, a2, a3]


def test_node_attempt_get_latest(execution_db):
    """get_latest_attempt returns the highest attempt_number row."""
    from backend.core.execution import NodeAttemptTracker

    tracker = NodeAttemptTracker()
    node_id = uuid.uuid4().hex
    tracker.start_attempt(node_id, 1)
    a2 = tracker.start_attempt(node_id, 2)

    latest = tracker.get_latest_attempt(node_id)
    assert latest["id"] == a2


def test_node_attempt_get_latest_none(execution_db):
    """get_latest_attempt returns None for a node with no attempts."""
    from backend.core.execution import NodeAttemptTracker

    tracker = NodeAttemptTracker()
    assert tracker.get_latest_attempt("nonexistent_node") is None


# ═══════════════════════════════════════════════════════════════════════════════
#  3. execution.py — ToolInvocationLog
# ═══════════════════════════════════════════════════════════════════════════════

def _make_attempt(execution_db) -> str:
    """Helper: create a node attempt and return its id."""
    from backend.core.execution import NodeAttemptTracker

    tracker = NodeAttemptTracker()
    return tracker.start_attempt(uuid.uuid4().hex, 1)


def test_tool_invocation_log_and_update(execution_db):
    """log_invocation + update_invocation records a full invocation lifecycle."""
    from backend.core.execution import ToolInvocationLog

    attempt_id = _make_attempt(execution_db)
    log = ToolInvocationLog()
    inv_id = log.log_invocation(attempt_id, "web_search", '{"q":"test"}')
    assert inv_id is not None

    log.update_invocation(inv_id, '{"results":[]}', "success", duration_ms=456)

    invocations = log.get_invocations(attempt_id)
    assert len(invocations) == 1
    assert invocations[0]["tool_name"] == "web_search"
    assert invocations[0]["status"] == "success"
    assert invocations[0]["duration_ms"] == 456


def test_tool_invocation_idempotency_cache_hit(execution_db):
    """get_cached_result returns a result for a completed invocation with a matching key."""
    from backend.core.execution import ToolInvocationLog

    attempt_id = _make_attempt(execution_db)
    log = ToolInvocationLog()
    inv_id = log.log_invocation(
        attempt_id, "write_file", '{"path":"a.txt"}', idempotency_key="write-a-txt-v1"
    )
    log.update_invocation(inv_id, '{"written":true}', "success")

    cached = log.get_cached_result("write-a-txt-v1")
    assert cached is not None
    assert cached["result_json"] == '{"written":true}'


def test_tool_invocation_idempotency_cache_miss(execution_db):
    """get_cached_result returns None when no matching key exists."""
    from backend.core.execution import ToolInvocationLog

    log = ToolInvocationLog()
    assert log.get_cached_result("nonexistent-key") is None


def test_tool_invocation_cache_ignores_non_success(execution_db):
    """get_cached_result only returns invocations with status='success'."""
    from backend.core.execution import ToolInvocationLog

    attempt_id = _make_attempt(execution_db)
    log = ToolInvocationLog()
    inv_id = log.log_invocation(
        attempt_id, "api_call", '{}', idempotency_key="api-call-v1"
    )
    log.update_invocation(inv_id, '{"error":"timeout"}', "error")

    assert log.get_cached_result("api-call-v1") is None


def test_tool_invocation_multiple_per_attempt(execution_db):
    """Multiple invocations within the same attempt are all retrievable."""
    from backend.core.execution import ToolInvocationLog

    attempt_id = _make_attempt(execution_db)
    log = ToolInvocationLog()
    log.log_invocation(attempt_id, "read_file", '{"path":"a.txt"}')
    log.log_invocation(attempt_id, "write_file", '{"path":"b.txt"}')
    log.log_invocation(attempt_id, "web_search", '{"q":"x"}')

    invocations = log.get_invocations(attempt_id)
    assert len(invocations) == 3
    assert {inv["tool_name"] for inv in invocations} == {"read_file", "write_file", "web_search"}


# ═══════════════════════════════════════════════════════════════════════════════
#  4. execution.py — WorkerLeaseManager
# ═══════════════════════════════════════════════════════════════════════════════

def test_worker_acquire_lease(execution_db):
    """acquire_lease returns True and creates an active lease row."""
    from backend.core.execution import WorkerLeaseManager

    mgr = WorkerLeaseManager()
    assert mgr.acquire_lease("w1", "host-a", 1234) is True

    conn = sqlite3.connect(str(execution_db["db"]))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM worker_leases WHERE worker_id='w1'").fetchone()
    conn.close()

    assert row["status"] == "active"
    assert row["hostname"] == "host-a"
    assert row["pid"] == 1234


def test_worker_acquire_lease_re_acquire(execution_db):
    """Re-acquiring a lease for the same worker_id updates metadata."""
    from backend.core.execution import WorkerLeaseManager

    mgr = WorkerLeaseManager()
    mgr.acquire_lease("w1", "host-a", 100)
    mgr.acquire_lease("w1", "host-b", 200)

    conn = sqlite3.connect(str(execution_db["db"]))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM worker_leases WHERE worker_id='w1'").fetchone()
    conn.close()

    assert row["hostname"] == "host-b"
    assert row["pid"] == 200
    assert row["status"] == "active"


def test_worker_heartbeat(execution_db):
    """heartbeat updates last_heartbeat and current task info."""
    from backend.core.execution import WorkerLeaseManager

    mgr = WorkerLeaseManager()
    mgr.acquire_lease("w1", "host-a", 1234)
    mgr.heartbeat("w1", current_job_id="job_x", current_node_id="node_y")

    conn = sqlite3.connect(str(execution_db["db"]))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM worker_leases WHERE worker_id='w1'").fetchone()
    conn.close()

    assert row["current_job_id"] == "job_x"
    assert row["current_node_id"] == "node_y"


def test_worker_release_lease(execution_db):
    """release_lease marks status as 'released' and clears task info."""
    from backend.core.execution import WorkerLeaseManager

    mgr = WorkerLeaseManager()
    mgr.acquire_lease("w1", "host-a", 1234)
    mgr.heartbeat("w1", current_job_id="job_x")
    mgr.release_lease("w1")

    conn = sqlite3.connect(str(execution_db["db"]))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM worker_leases WHERE worker_id='w1'").fetchone()
    conn.close()

    assert row["status"] == "released"
    assert row["current_job_id"] is None


def test_worker_find_dead_workers_none(execution_db):
    """find_dead_workers returns empty list when all workers are fresh."""
    from backend.core.execution import WorkerLeaseManager

    mgr = WorkerLeaseManager()
    mgr.acquire_lease("w1", "host-a", 1)
    assert mgr.find_dead_workers(timeout_sec=90) == []


def test_worker_find_dead_workers_stale(execution_db):
    """find_dead_workers detects workers with old heartbeats."""
    from backend.core.execution import WorkerLeaseManager

    mgr = WorkerLeaseManager()
    mgr.acquire_lease("w_stale", "host-a", 1)

    # Manually backdate the heartbeat to simulate staleness
    old_ts = (datetime.utcnow() - timedelta(seconds=200)).isoformat()
    conn = sqlite3.connect(str(execution_db["db"]))
    conn.execute(
        "UPDATE worker_leases SET last_heartbeat = ? WHERE worker_id = ?",
        (old_ts, "w_stale"),
    )
    conn.commit()
    conn.close()

    dead = mgr.find_dead_workers(timeout_sec=90)
    assert "w_stale" in dead


def test_worker_claim_orphaned_jobs(execution_db):
    """claim_orphaned_jobs marks dead workers and reclaims their jobs."""
    from backend.core.execution import WorkerLeaseManager

    mgr = WorkerLeaseManager()
    mgr.acquire_lease("w_dead", "host-a", 1)

    # Insert a running job associated with the worker
    job_id = _seed_job(execution_db["db"], execution_db["workspace_id"], status="running")

    # Assign the job to the worker
    conn = sqlite3.connect(str(execution_db["db"]))
    conn.execute(
        "UPDATE worker_leases SET current_job_id = ? WHERE worker_id = ?",
        (job_id, "w_dead"),
    )
    conn.commit()
    conn.close()

    reclaimed = mgr.claim_orphaned_jobs(["w_dead"])
    assert job_id in reclaimed

    # Verify worker is now marked dead
    conn = sqlite3.connect(str(execution_db["db"]))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status FROM worker_leases WHERE worker_id='w_dead'").fetchone()
    conn.close()
    assert row["status"] == "dead"


def test_worker_claim_orphaned_jobs_empty_list(execution_db):
    """claim_orphaned_jobs with empty list returns empty list."""
    from backend.core.execution import WorkerLeaseManager

    mgr = WorkerLeaseManager()
    assert mgr.claim_orphaned_jobs([]) == []


# ═══════════════════════════════════════════════════════════════════════════════
#  5. execution.py — DeadLetterQueue
# ═══════════════════════════════════════════════════════════════════════════════

def test_dead_letter_add_and_list(execution_db):
    """add_dead_letter creates a row and list_dead_letters returns it."""
    from backend.core.execution import DeadLetterQueue

    dlq = DeadLetterQueue()
    dl_id = dlq.add_dead_letter(
        job_id="job_1",
        node_id="node_1",
        attempt_id="attempt_1",
        error="All retries exhausted",
        error_category="permanent",
        payload_json='{"data":"test"}',
    )
    assert dl_id is not None

    unresolved = dlq.list_dead_letters(resolved=False)
    assert len(unresolved) == 1
    assert unresolved[0]["id"] == dl_id
    assert unresolved[0]["error"] == "All retries exhausted"
    assert unresolved[0]["error_category"] == "permanent"


def test_dead_letter_resolve(execution_db):
    """resolve_dead_letter marks the item resolved and moves it to the resolved list."""
    from backend.core.execution import DeadLetterQueue

    dlq = DeadLetterQueue()
    dl_id = dlq.add_dead_letter("job_2", None, None, "oops", "transient")

    user_id = execution_db["user_id"]
    dlq.resolve_dead_letter(dl_id, user_id, "retried")

    unresolved = dlq.list_dead_letters(resolved=False)
    resolved = dlq.list_dead_letters(resolved=True)
    assert len(unresolved) == 0
    assert len(resolved) == 1
    assert resolved[0]["resolved_by"] == user_id
    assert resolved[0]["resolution"] == "retried"


def test_dead_letter_resolve_invalid_resolution(execution_db):
    """resolve_dead_letter raises ValueError for invalid resolution strings."""
    from backend.core.execution import DeadLetterQueue

    dlq = DeadLetterQueue()
    dl_id = dlq.add_dead_letter("job_3", None, None, "err", "permanent")

    with pytest.raises(ValueError, match="Invalid resolution"):
        dlq.resolve_dead_letter(dl_id, "admin", "delete_it")


def test_dead_letter_multiple_unresolved(execution_db):
    """Multiple dead letters appear in the unresolved list ordered newest first."""
    from backend.core.execution import DeadLetterQueue

    dlq = DeadLetterQueue()
    id1 = dlq.add_dead_letter("j1", None, None, "err1", "transient")
    id2 = dlq.add_dead_letter("j2", None, None, "err2", "permanent")

    items = dlq.list_dead_letters(resolved=False)
    assert len(items) == 2
    # Newest first
    assert items[0]["id"] == id2
    assert items[1]["id"] == id1


# ═══════════════════════════════════════════════════════════════════════════════
#  6. execution.py — CancellationManager
# ═══════════════════════════════════════════════════════════════════════════════

def test_cancellation_request_and_check(execution_db):
    """request_cancellation sets status to 'cancelling' and is_cancelling detects it."""
    from backend.core.execution import CancellationManager

    job_id = _seed_job(execution_db["db"], execution_db["workspace_id"], status="running")
    cm = CancellationManager()

    assert cm.is_cancelling(job_id) is False
    cm.request_cancellation(job_id)
    assert cm.is_cancelling(job_id) is True


def test_cancellation_complete(execution_db):
    """complete_cancellation sets status to 'cancelled'."""
    from backend.core.execution import CancellationManager

    job_id = _seed_job(execution_db["db"], execution_db["workspace_id"], status="running")
    cm = CancellationManager()
    cm.request_cancellation(job_id)

    # Patch JOBS_DIR / RECYCLE_DIR so the file-move logic doesn't fail
    with patch("backend.core.execution.JOBS_DIR", execution_db["db"].parent / "jobs"), \
         patch("backend.core.execution.RECYCLE_DIR", execution_db["db"].parent / "recycle"):
        cm.complete_cancellation(job_id)

    conn = sqlite3.connect(str(execution_db["db"]))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row["status"] == "cancelled"


def test_cancellation_is_cancelling_nonexistent_job(execution_db):
    """is_cancelling returns False for a job ID that doesn't exist."""
    from backend.core.execution import CancellationManager

    cm = CancellationManager()
    assert cm.is_cancelling("nonexistent_job_id") is False


# ═══════════════════════════════════════════════════════════════════════════════
#  7. artifacts.py — ArtifactManager
# ═══════════════════════════════════════════════════════════════════════════════

def test_artifact_create_and_get(artifact_db):
    """create_artifact + get_artifact round-trips correctly."""
    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    art_id = mgr.create_artifact(
        job_id="job_a",
        workspace_id=artifact_db["workspace_id"],
        name="Q4 Report",
        artifact_type="pptx",
    )
    assert art_id is not None

    art = mgr.get_artifact(art_id)
    assert art is not None
    assert art["name"] == "Q4 Report"
    assert art["artifact_type"] == "pptx"
    assert art["job_id"] == "job_a"


def test_artifact_get_nonexistent(artifact_db):
    """get_artifact returns None for an unknown ID."""
    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    assert mgr.get_artifact("no_such_artifact") is None


def test_artifact_add_version(artifact_db):
    """add_version records file metadata and updates current_version_id."""
    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    art_id = mgr.create_artifact("job_a", artifact_db["workspace_id"], "Report", "txt")

    # Create a real file
    fpath = artifact_db["workspace"] / "report_v1.txt"
    fpath.write_text("Hello, this is version 1.")

    ver_id = mgr.add_version(art_id, str(fpath), "system")
    assert ver_id is not None

    ver = mgr.get_version(ver_id)
    assert ver["version_number"] == 1
    assert ver["file_size_bytes"] == len("Hello, this is version 1.")
    assert ver["sha256"] is not None
    assert len(ver["sha256"]) == 64  # hex SHA-256

    # current_version_id should be updated
    art = mgr.get_artifact(art_id)
    assert art["current_version_id"] == ver_id


def test_artifact_add_multiple_versions(artifact_db):
    """Adding multiple versions increments version_number correctly."""
    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    art_id = mgr.create_artifact("job_a", artifact_db["workspace_id"], "Report", "txt")

    for i in range(1, 4):
        fpath = artifact_db["workspace"] / f"report_v{i}.txt"
        fpath.write_text(f"Version {i} content.")
        mgr.add_version(art_id, str(fpath), "system")

    versions = mgr.get_versions(art_id)
    assert len(versions) == 3
    assert [v["version_number"] for v in versions] == [1, 2, 3]


def test_artifact_get_current_version(artifact_db):
    """get_current_version returns the latest version added."""
    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    art_id = mgr.create_artifact("job_a", artifact_db["workspace_id"], "Doc", "txt")

    f1 = artifact_db["workspace"] / "doc_v1.txt"
    f1.write_text("v1")
    mgr.add_version(art_id, str(f1), "user")

    f2 = artifact_db["workspace"] / "doc_v2.txt"
    f2.write_text("v2 content")
    v2_id = mgr.add_version(art_id, str(f2), "user")

    current = mgr.get_current_version(art_id)
    assert current is not None
    assert current["id"] == v2_id
    assert current["version_number"] == 2


def test_artifact_add_version_file_not_found(artifact_db):
    """add_version raises FileNotFoundError for a missing file."""
    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    art_id = mgr.create_artifact("job_a", artifact_db["workspace_id"], "Doc", "txt")

    with pytest.raises(FileNotFoundError):
        mgr.add_version(art_id, "/nonexistent/file.txt", "system")


def test_artifact_get_artifacts_by_job(artifact_db):
    """get_artifacts_by_job returns all artifacts for a specific job."""
    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    mgr.create_artifact("job_x", artifact_db["workspace_id"], "A1", "txt")
    mgr.create_artifact("job_x", artifact_db["workspace_id"], "A2", "pptx")
    mgr.create_artifact("job_y", artifact_db["workspace_id"], "B1", "xlsx")

    job_x_arts = mgr.get_artifacts_by_job("job_x")
    assert len(job_x_arts) == 2
    assert {a["name"] for a in job_x_arts} == {"A1", "A2"}


def test_artifact_version_sha256_changes_with_content(artifact_db):
    """Different file contents produce different SHA-256 hashes."""
    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    art_id = mgr.create_artifact("job_a", artifact_db["workspace_id"], "Doc", "txt")

    f1 = artifact_db["workspace"] / "sha_v1.txt"
    f1.write_text("content A")
    v1_id = mgr.add_version(art_id, str(f1), "user")

    f2 = artifact_db["workspace"] / "sha_v2.txt"
    f2.write_text("content B")
    v2_id = mgr.add_version(art_id, str(f2), "user")

    v1 = mgr.get_version(v1_id)
    v2 = mgr.get_version(v2_id)
    assert v1["sha256"] != v2["sha256"]


# ═══════════════════════════════════════════════════════════════════════════════
#  8. artifacts.py — PreviewGenerator
# ═══════════════════════════════════════════════════════════════════════════════

def test_preview_generate_plain_text(artifact_db):
    """generate_preview extracts text from a plain text file."""
    from backend.core.artifacts import ArtifactManager, PreviewGenerator

    mgr = ArtifactManager()
    art_id = mgr.create_artifact("job_a", artifact_db["workspace_id"], "Notes", "txt")

    fpath = artifact_db["workspace"] / "notes.txt"
    fpath.write_text("These are my notes.\nLine two.")
    ver_id = mgr.add_version(art_id, str(fpath), "user")

    pg = PreviewGenerator()
    preview_id = pg.generate_preview(ver_id)
    assert preview_id is not None

    preview = pg.get_preview(ver_id)
    assert preview is not None
    assert "These are my notes." in preview["content_text"]
    assert preview["preview_type"] == "text_extract"


def test_preview_generate_json_file(artifact_db):
    """generate_preview extracts content from a JSON file."""
    from backend.core.artifacts import ArtifactManager, PreviewGenerator

    mgr = ArtifactManager()
    art_id = mgr.create_artifact("job_a", artifact_db["workspace_id"], "Config", "json")

    fpath = artifact_db["workspace"] / "config.json"
    fpath.write_text(json.dumps({"key": "value", "count": 42}))
    ver_id = mgr.add_version(art_id, str(fpath), "user")

    pg = PreviewGenerator()
    pg.generate_preview(ver_id)
    preview = pg.get_preview(ver_id)
    assert preview is not None
    assert "key" in preview["content_text"]


def test_preview_nonexistent_version(artifact_db):
    """generate_preview raises ValueError for a nonexistent version ID."""
    from backend.core.artifacts import PreviewGenerator

    pg = PreviewGenerator()
    with pytest.raises(ValueError, match="not found"):
        pg.generate_preview("nonexistent_version")


# ═══════════════════════════════════════════════════════════════════════════════
#  9. artifacts.py — DiffEngine
# ═══════════════════════════════════════════════════════════════════════════════

def test_diff_engine_text_diff(artifact_db):
    """compute_diff produces a text diff with correct stats."""
    from backend.core.artifacts import ArtifactManager, DiffEngine

    mgr = ArtifactManager()
    art_id = mgr.create_artifact("job_a", artifact_db["workspace_id"], "Doc", "txt")

    f1 = artifact_db["workspace"] / "diff_v1.txt"
    f1.write_text("Line one\nLine two\nLine three\n")
    v1_id = mgr.add_version(art_id, str(f1), "user")

    f2 = artifact_db["workspace"] / "diff_v2.txt"
    f2.write_text("Line one\nLine two CHANGED\nLine three\nLine four\n")
    v2_id = mgr.add_version(art_id, str(f2), "user")

    de = DiffEngine()
    diff_id = de.compute_diff(v1_id, v2_id, diff_type="text")
    assert diff_id is not None

    diff = de.get_diff(diff_id)
    assert diff is not None
    assert diff["diff_type"] == "text"

    diff_data = json.loads(diff["diff_json"])
    assert diff_data["type"] == "text"
    assert diff_data["stats"]["lines_added"] > 0 or diff_data["stats"]["lines_removed"] > 0


def test_diff_engine_get_nonexistent(artifact_db):
    """get_diff returns None for an unknown diff ID."""
    from backend.core.artifacts import DiffEngine

    de = DiffEngine()
    assert de.get_diff("no_such_diff") is None


def test_diff_engine_missing_version(artifact_db):
    """compute_diff raises ValueError when a version doesn't exist."""
    from backend.core.artifacts import DiffEngine

    de = DiffEngine()
    with pytest.raises(ValueError, match="not found"):
        de.compute_diff("missing_v1", "missing_v2")


# ═══════════════════════════════════════════════════════════════════════════════
#  10. artifacts.py — compute_shape_fingerprint
# ═══════════════════════════════════════════════════════════════════════════════

def test_shape_fingerprint_deterministic():
    """Same inputs produce the same fingerprint."""
    from backend.core.artifacts import compute_shape_fingerprint

    pos = {"left": 100, "top": 200, "width": 300, "height": 400}
    fp1 = compute_shape_fingerprint("rId1", "Title 1", pos)
    fp2 = compute_shape_fingerprint("rId1", "Title 1", pos)
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex digest


def test_shape_fingerprint_differs_on_position_change():
    """Changing any position value changes the fingerprint."""
    from backend.core.artifacts import compute_shape_fingerprint

    pos_a = {"left": 100, "top": 200, "width": 300, "height": 400}
    pos_b = {"left": 101, "top": 200, "width": 300, "height": 400}
    assert compute_shape_fingerprint("rId1", "Title 1", pos_a) != \
           compute_shape_fingerprint("rId1", "Title 1", pos_b)


# ═══════════════════════════════════════════════════════════════════════════════
#  11. evidence.py — EvidenceCollector
# ═══════════════════════════════════════════════════════════════════════════════

def test_evidence_record_and_get(evidence_db):
    """record_evidence creates a row and get_evidence retrieves it."""
    from backend.core.evidence import EvidenceCollector

    ec = EvidenceCollector()
    eid = ec.record_evidence(
        job_id="job_ev1",
        node_attempt_id=None,
        source_type="web_page",
        extracted_text="Q1 revenue was $4.2M",
        source_uri="https://example.com/report",
        confidence=0.95,
    )
    assert eid is not None

    ev = ec.get_evidence(eid)
    assert ev is not None
    assert ev["extracted_text"] == "Q1 revenue was $4.2M"
    assert ev["source_type"] == "web_page"
    assert ev["confidence"] == pytest.approx(0.95)
    assert ev["source_uri"] == "https://example.com/report"


def test_evidence_invalid_source_type(evidence_db):
    """record_evidence raises ValueError for an invalid source_type."""
    from backend.core.evidence import EvidenceCollector

    ec = EvidenceCollector()
    with pytest.raises(ValueError, match="Invalid source_type"):
        ec.record_evidence("job_1", None, "bad_type", "text")


def test_evidence_invalid_confidence(evidence_db):
    """record_evidence raises ValueError if confidence is outside [0, 1]."""
    from backend.core.evidence import EvidenceCollector

    ec = EvidenceCollector()
    with pytest.raises(ValueError, match="confidence"):
        ec.record_evidence("job_1", None, "web_page", "text", confidence=1.5)
    with pytest.raises(ValueError, match="confidence"):
        ec.record_evidence("job_1", None, "web_page", "text", confidence=-0.1)


def test_evidence_get_for_job(evidence_db):
    """get_evidence_for_job returns all evidence for that job."""
    from backend.core.evidence import EvidenceCollector

    ec = EvidenceCollector()
    ec.record_evidence("job_multi", None, "web_page", "fact 1")
    ec.record_evidence("job_multi", None, "file_extract", "fact 2")
    ec.record_evidence("other_job", None, "user_input", "unrelated")

    items = ec.get_evidence_for_job("job_multi")
    assert len(items) == 2
    assert {i["extracted_text"] for i in items} == {"fact 1", "fact 2"}


def test_evidence_get_for_attempt(evidence_db):
    """get_evidence_for_attempt returns evidence scoped to a node attempt."""
    from backend.core.evidence import EvidenceCollector

    # Create a real node_attempt to satisfy FK (if enforced)
    # For evidence_items, node_attempt_id is nullable so we use synthetic IDs
    # but the FK references node_attempts(id). We need to seed one.
    conn = sqlite3.connect(str(evidence_db["db"]))
    conn.execute("PRAGMA foreign_keys=ON")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO node_attempts (id, node_id, attempt_number, status, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("attempt_ev1", "node_1", 1, "running", now),
    )
    conn.commit()
    conn.close()

    ec = EvidenceCollector()
    ec.record_evidence("job_a", "attempt_ev1", "api_response", "data from API")
    ec.record_evidence("job_a", None, "user_input", "user said something")

    items = ec.get_evidence_for_attempt("attempt_ev1")
    assert len(items) == 1
    assert items[0]["extracted_text"] == "data from API"


def test_evidence_get_nonexistent(evidence_db):
    """get_evidence returns None for a nonexistent evidence ID."""
    from backend.core.evidence import EvidenceCollector

    ec = EvidenceCollector()
    assert ec.get_evidence("no_such_id") is None


def test_evidence_metadata_json_defaults_to_empty_object(evidence_db):
    """When metadata_json is omitted, it defaults to '{}'."""
    from backend.core.evidence import EvidenceCollector

    ec = EvidenceCollector()
    eid = ec.record_evidence("job_meta", None, "memory", "recalled fact")
    ev = ec.get_evidence(eid)
    assert ev["metadata_json"] == "{}"


def test_evidence_custom_metadata_json(evidence_db):
    """Custom metadata_json is stored correctly."""
    from backend.core.evidence import EvidenceCollector

    ec = EvidenceCollector()
    meta = json.dumps({"page_number": 3, "selector": "#revenue-table"})
    eid = ec.record_evidence("job_meta2", None, "web_page", "data", metadata_json=meta)
    ev = ec.get_evidence(eid)
    parsed = json.loads(ev["metadata_json"])
    assert parsed["page_number"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
#  12. evidence.py — SourceSnapshotStore
# ═══════════════════════════════════════════════════════════════════════════════

def test_snapshot_save_and_get(evidence_db):
    """save_snapshot writes file to disk and creates a DB record."""
    from backend.core.evidence import SourceSnapshotStore

    store = SourceSnapshotStore()
    content = b"<html><body>Hello World</body></html>"
    snap_id = store.save_snapshot("https://example.com", "html", content)
    assert snap_id is not None

    snap = store.get_snapshot(snap_id)
    assert snap is not None
    assert snap["uri"] == "https://example.com"
    assert snap["snapshot_type"] == "html"
    assert snap["size_bytes"] == len(content)
    assert len(snap["content_hash"]) == 64  # SHA-256 hex

    # Verify file exists on disk
    fpath = Path(snap["file_path"])
    assert fpath.exists()
    assert fpath.read_bytes() == content


def test_snapshot_invalid_type(evidence_db):
    """save_snapshot raises ValueError for invalid snapshot_type."""
    from backend.core.evidence import SourceSnapshotStore

    store = SourceSnapshotStore()
    with pytest.raises(ValueError, match="Invalid snapshot_type"):
        store.save_snapshot("https://example.com", "bmp", b"data")


def test_snapshot_empty_content(evidence_db):
    """save_snapshot raises ValueError for empty content_bytes."""
    from backend.core.evidence import SourceSnapshotStore

    store = SourceSnapshotStore()
    with pytest.raises(ValueError, match="must not be empty"):
        store.save_snapshot("https://example.com", "text", b"")


def test_snapshot_get_nonexistent(evidence_db):
    """get_snapshot returns None for a nonexistent snapshot ID."""
    from backend.core.evidence import SourceSnapshotStore

    store = SourceSnapshotStore()
    assert store.get_snapshot("no_such_snapshot") is None


def test_snapshot_link_evidence_to_snapshot(evidence_db):
    """link_evidence_to_snapshot updates the source_snapshot_id on the evidence item."""
    from backend.core.evidence import EvidenceCollector, SourceSnapshotStore

    ec = EvidenceCollector()
    store = SourceSnapshotStore()

    eid = ec.record_evidence("job_link", None, "web_page", "some data", source_uri="https://x.com")
    snap_id = store.save_snapshot("https://x.com", "html", b"<html>content</html>")
    store.link_evidence_to_snapshot(eid, snap_id)

    ev = ec.get_evidence(eid)
    assert ev["source_snapshot_id"] == snap_id


def test_snapshot_link_missing_evidence_raises(evidence_db):
    """link_evidence_to_snapshot raises ValueError for nonexistent evidence."""
    from backend.core.evidence import SourceSnapshotStore

    store = SourceSnapshotStore()
    snap_id = store.save_snapshot("https://x.com", "text", b"hello")

    with pytest.raises(ValueError, match="Evidence item not found"):
        store.link_evidence_to_snapshot("bad_evidence_id", snap_id)


def test_snapshot_link_missing_snapshot_raises(evidence_db):
    """link_evidence_to_snapshot raises ValueError for nonexistent snapshot."""
    from backend.core.evidence import EvidenceCollector, SourceSnapshotStore

    ec = EvidenceCollector()
    eid = ec.record_evidence("job_link2", None, "web_page", "data")
    store = SourceSnapshotStore()

    with pytest.raises(ValueError, match="Snapshot not found"):
        store.link_evidence_to_snapshot(eid, "bad_snapshot_id")


def test_snapshot_content_hash_is_sha256(evidence_db):
    """The stored content_hash matches the SHA-256 of the content."""
    import hashlib
    from backend.core.evidence import SourceSnapshotStore

    store = SourceSnapshotStore()
    content = b"The quick brown fox jumps over the lazy dog"
    expected_hash = hashlib.sha256(content).hexdigest()
    snap_id = store.save_snapshot("file:///test.txt", "text", content)

    snap = store.get_snapshot(snap_id)
    assert snap["content_hash"] == expected_hash


# ═══════════════════════════════════════════════════════════════════════════════
#  13. evidence.py — EvidenceLinker
# ═══════════════════════════════════════════════════════════════════════════════

def _make_artifact_version(db_path: Path, workspace: Path, ws_id: str) -> str:
    """Helper: create an artifact + version and return the version ID.

    Inserts directly into the DB to avoid cross-module patching issues.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    now = datetime.now(timezone.utc).isoformat()
    art_id = uuid.uuid4().hex
    ver_id = uuid.uuid4().hex

    fpath = workspace / f"file_{ver_id}.txt"
    fpath.write_text("artifact content")

    conn.execute(
        "INSERT INTO artifacts (id, job_id, workspace_id, name, artifact_type, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (art_id, "job_linker", ws_id, "TestArt", "txt", now),
    )
    conn.execute(
        "INSERT INTO artifact_versions "
        "(id, artifact_id, version_number, file_path, file_size_bytes, sha256, "
        "mime_type, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (ver_id, art_id, 1, str(fpath), fpath.stat().st_size,
         "a" * 64, "text/plain", "system", now),
    )
    conn.commit()
    conn.close()
    return ver_id


def test_evidence_linker_link_to_artifact(evidence_db):
    """link_to_artifact creates a link between evidence and an artifact version."""
    from backend.core.evidence import EvidenceCollector, EvidenceLinker

    ec = EvidenceCollector()
    eid = ec.record_evidence("job_linker", None, "web_page", "revenue data")

    ver_id = _make_artifact_version(
        evidence_db["db"], evidence_db["workspace"], evidence_db["workspace_id"]
    )

    linker = EvidenceLinker()
    linker.link_to_artifact(eid, ver_id, target_location="slide:1:shape:Title", description="Revenue figure")

    items = linker.get_evidence_for_artifact(ver_id)
    assert len(items) == 1
    assert items[0]["extracted_text"] == "revenue data"
    assert items[0]["target_location"] == "slide:1:shape:Title"
    assert items[0]["link_description"] == "Revenue figure"


def test_evidence_linker_missing_evidence_raises(evidence_db):
    """link_to_artifact raises ValueError for nonexistent evidence."""
    from backend.core.evidence import EvidenceLinker

    ver_id = _make_artifact_version(
        evidence_db["db"], evidence_db["workspace"], evidence_db["workspace_id"]
    )
    linker = EvidenceLinker()

    with pytest.raises(ValueError, match="Evidence item not found"):
        linker.link_to_artifact("bad_evidence", ver_id)


def test_evidence_linker_missing_artifact_version_raises(evidence_db):
    """link_to_artifact raises ValueError for nonexistent artifact version."""
    from backend.core.evidence import EvidenceCollector, EvidenceLinker

    ec = EvidenceCollector()
    eid = ec.record_evidence("job_linker2", None, "user_input", "some claim")
    linker = EvidenceLinker()

    with pytest.raises(ValueError, match="Artifact version not found"):
        linker.link_to_artifact(eid, "bad_version_id")


def test_evidence_linker_upsert_on_conflict(evidence_db):
    """Linking the same evidence+version pair twice updates instead of failing."""
    from backend.core.evidence import EvidenceCollector, EvidenceLinker

    ec = EvidenceCollector()
    eid = ec.record_evidence("job_linker3", None, "web_page", "claim A")

    ver_id = _make_artifact_version(
        evidence_db["db"], evidence_db["workspace"], evidence_db["workspace_id"]
    )

    linker = EvidenceLinker()
    linker.link_to_artifact(eid, ver_id, description="first")
    linker.link_to_artifact(eid, ver_id, description="updated")

    items = linker.get_evidence_for_artifact(ver_id)
    assert len(items) == 1
    assert items[0]["link_description"] == "updated"


def test_evidence_linker_get_provenance_chain(evidence_db):
    """get_provenance_chain returns the full chain including snapshot info."""
    from backend.core.evidence import (
        EvidenceCollector, EvidenceLinker, SourceSnapshotStore,
    )

    ec = EvidenceCollector()
    store = SourceSnapshotStore()

    # Create evidence with a linked snapshot
    eid = ec.record_evidence("job_prov", None, "web_page", "Q1 = $4.2M",
                              source_uri="https://sec.gov/10k", confidence=0.9)
    snap_id = store.save_snapshot("https://sec.gov/10k", "html", b"<html>10-K filing</html>")
    store.link_evidence_to_snapshot(eid, snap_id)

    ver_id = _make_artifact_version(
        evidence_db["db"], evidence_db["workspace"], evidence_db["workspace_id"]
    )
    linker = EvidenceLinker()
    linker.link_to_artifact(eid, ver_id, target_location="slide:3:shape:Revenue",
                            description="Q1 revenue figure")

    chain = linker.get_provenance_chain(ver_id)
    assert len(chain) == 1

    entry = chain[0]
    assert entry["evidence_id"] == eid
    assert entry["source_type"] == "web_page"
    assert entry["extracted_text"] == "Q1 = $4.2M"
    assert entry["snapshot_id"] == snap_id
    assert entry["snapshot_uri"] == "https://sec.gov/10k"
    assert entry["snapshot_type"] == "html"
    assert entry["target_location"] == "slide:3:shape:Revenue"
    assert entry["link_description"] == "Q1 revenue figure"


def test_evidence_linker_provenance_chain_without_snapshot(evidence_db):
    """get_provenance_chain works even when no snapshot is linked (LEFT JOIN)."""
    from backend.core.evidence import EvidenceCollector, EvidenceLinker

    ec = EvidenceCollector()
    eid = ec.record_evidence("job_prov2", None, "user_input", "user said X")

    ver_id = _make_artifact_version(
        evidence_db["db"], evidence_db["workspace"], evidence_db["workspace_id"]
    )
    linker = EvidenceLinker()
    linker.link_to_artifact(eid, ver_id)

    chain = linker.get_provenance_chain(ver_id)
    assert len(chain) == 1
    assert chain[0]["snapshot_id"] is None
    assert chain[0]["evidence_id"] == eid


def test_evidence_linker_get_evidence_for_artifact_empty(evidence_db):
    """get_evidence_for_artifact returns empty list when no links exist."""
    from backend.core.evidence import EvidenceLinker

    ver_id = _make_artifact_version(
        evidence_db["db"], evidence_db["workspace"], evidence_db["workspace_id"]
    )
    linker = EvidenceLinker()
    assert linker.get_evidence_for_artifact(ver_id) == []
