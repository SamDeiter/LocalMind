"""API route tests for the swarm endpoints.

Tests exercise the FastAPI route handlers in backend.routes.swarm_routes
by mocking the swarm service classes (DelegationEngine, SharedMemoryStore,
ResourceLockManager, AgentMessageBus) at the module level where they are
instantiated inside each handler.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_test_db():
    """Return an in-memory SQLite connection with the swarm schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, title TEXT, description TEXT,
            status TEXT DEFAULT 'queued', priority INTEGER DEFAULT 5,
            created_at TEXT, parent_job_id TEXT, tree_root_id TEXT
        );
        CREATE TABLE IF NOT EXISTS job_delegation (
            id TEXT PRIMARY KEY, parent_job_id TEXT NOT NULL,
            child_job_id TEXT NOT NULL, delegation_type TEXT NOT NULL DEFAULT 'sub_task',
            context_json TEXT, status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL, completed_at TEXT,
            UNIQUE(parent_job_id, child_job_id)
        );
        CREATE TABLE IF NOT EXISTS swarm_shared_memory (
            id TEXT PRIMARY KEY, tree_root_job_id TEXT NOT NULL,
            key TEXT NOT NULL, value_json TEXT NOT NULL,
            written_by_job_id TEXT NOT NULL, written_by_node_id TEXT,
            version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, UNIQUE(tree_root_job_id, key)
        );
        CREATE TABLE IF NOT EXISTS resource_locks (
            id TEXT PRIMARY KEY, resource_path TEXT NOT NULL,
            lock_type TEXT NOT NULL DEFAULT 'exclusive',
            held_by_job_id TEXT NOT NULL, held_by_node_id TEXT,
            acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            released_at TEXT, UNIQUE(resource_path, held_by_job_id)
        );
        CREATE TABLE IF NOT EXISTS agent_messages (
            id TEXT PRIMARY KEY, tree_root_job_id TEXT NOT NULL,
            from_job_id TEXT NOT NULL, to_job_id TEXT,
            message_type TEXT NOT NULL, subject TEXT,
            body_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL, read_at TEXT
        );
    """)
    return conn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _PersistentConnection:
    """Thin wrapper that silently ignores close() to keep :memory: DB alive."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self):
        pass

    def real_close(self):
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture(autouse=True)
def _patch_db():
    """Patch get_db in every swarm module so they all share one in-memory DB."""
    raw = _init_test_db()
    db = _PersistentConnection(raw)

    targets = [
        "backend.db.get_db",
        "backend.swarm.delegation.get_db",
        "backend.swarm.shared_memory.get_db",
        "backend.swarm.resource_lock.get_db",
        "backend.swarm.messaging.get_db",
    ]
    patches = [patch(t, return_value=db) for t in targets]
    for p in patches:
        p.start()

    yield db

    for p in patches:
        p.stop()
    db.real_close()


@pytest.fixture()
def client():
    """Create a FastAPI TestClient wrapping only the swarm router."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.routes.swarm_routes import router

    app = FastAPI()
    app.include_router(router)

    # The /status, /agents, /scan, /research, /test, /scale endpoints need
    # request.app.state.autonomy_engine — provide a stub.
    app.state.autonomy_engine = MagicMock()

    return TestClient(app)


# ===================================================================
# TestSwarmTreeEndpoint
# ===================================================================

class TestSwarmTreeEndpoint:

    def test_get_tree_returns_structure(self, client, _patch_db):
        db = _patch_db
        now = _now_iso()
        job_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'root', '', 'queued', 5, ?)",
            (job_id, now),
        )
        db.commit()

        resp = client.get(f"/api/swarm/tree/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "tree" in data
        assert data["tree"]["job_id"] == job_id

    def test_get_tree_unknown_job_returns_tree_with_unknown_status(self, client):
        """When the job doesn't exist the engine still returns a tree node
        with status 'unknown' (no exception is raised)."""
        fake_id = _uid()
        resp = client.get(f"/api/swarm/tree/{fake_id}")
        # The route catches generic exceptions; with a non-existent job
        # get_full_tree returns a node with status "unknown" rather than raising.
        assert resp.status_code == 200
        data = resp.json()
        assert data["tree"]["status"] == "unknown"


# ===================================================================
# TestSwarmMemoryEndpoint
# ===================================================================

class TestSwarmMemoryEndpoint:

    def test_get_memory_returns_entries(self, client, _patch_db):
        db = _patch_db
        now = _now_iso()
        job_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'root', '', 'queued', 5, ?)",
            (job_id, now),
        )
        # Insert a shared memory entry
        db.execute(
            "INSERT INTO swarm_shared_memory "
            "(id, tree_root_job_id, key, value_json, written_by_job_id, version, created_at, updated_at) "
            "VALUES (?, ?, 'foo', '\"bar\"', ?, 1, ?, ?)",
            (_uid(), job_id, job_id, now, now),
        )
        db.commit()

        resp = client.get(f"/api/swarm/memory/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["key"] == "foo"

    def test_get_memory_empty_tree(self, client, _patch_db):
        db = _patch_db
        now = _now_iso()
        job_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'root', '', 'queued', 5, ?)",
            (job_id, now),
        )
        db.commit()

        resp = client.get(f"/api/swarm/memory/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["entries"] == []


# ===================================================================
# TestSwarmDelegateEndpoint
# ===================================================================

class TestSwarmDelegateEndpoint:

    def test_delegate_creates_child_job(self, client, _patch_db):
        db = _patch_db
        now = _now_iso()
        parent_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'parent', '', 'queued', 5, ?)",
            (parent_id, now),
        )
        db.commit()

        resp = client.post("/api/swarm/delegate", json={
            "parent_job_id": parent_id,
            "title": "child-task",
            "description": "some work",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["parent_job_id"] == parent_id
        assert data["delegation_type"] == "sub_task"
        assert data["status"] == "active"

    def test_delegate_missing_fields_returns_400(self, client):
        resp = client.post("/api/swarm/delegate", json={
            "description": "missing parent_job_id and title",
        })
        assert resp.status_code == 400
        assert "required" in resp.json()["error"].lower()

    def test_delegate_nonexistent_parent_returns_400(self, client):
        fake_parent = _uid()
        resp = client.post("/api/swarm/delegate", json={
            "parent_job_id": fake_parent,
            "title": "orphan",
        })
        # DelegationEngine raises ValueError which the route catches as 400
        assert resp.status_code == 400
        assert "does not exist" in resp.json()["error"]

    def test_delegate_with_custom_priority(self, client, _patch_db):
        db = _patch_db
        now = _now_iso()
        parent_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'parent', '', 'queued', 5, ?)",
            (parent_id, now),
        )
        db.commit()

        resp = client.post("/api/swarm/delegate", json={
            "parent_job_id": parent_id,
            "title": "urgent-task",
            "priority": 1,
            "delegation_type": "parallel",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["delegation_type"] == "parallel"


# ===================================================================
# TestSwarmMessagesEndpoint
# ===================================================================

class TestSwarmMessagesEndpoint:

    def test_get_messages_returns_log(self, client, _patch_db):
        db = _patch_db
        now = _now_iso()
        job_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'root', '', 'queued', 5, ?)",
            (job_id, now),
        )
        db.execute(
            "INSERT INTO agent_messages "
            "(id, tree_root_job_id, from_job_id, to_job_id, message_type, "
            "body_json, status, created_at) VALUES (?, ?, ?, NULL, 'data', "
            "'{\"hello\": 1}', 'pending', ?)",
            (_uid(), job_id, job_id, now),
        )
        db.commit()

        resp = client.get(f"/api/swarm/messages/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["body"] == {"hello": 1}

    def test_get_messages_empty(self, client, _patch_db):
        db = _patch_db
        now = _now_iso()
        job_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'root', '', 'queued', 5, ?)",
            (job_id, now),
        )
        db.commit()

        resp = client.get(f"/api/swarm/messages/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["messages"] == []


# ===================================================================
# TestSwarmLocksEndpoint
# ===================================================================

class TestSwarmLocksEndpoint:

    def test_list_locks_returns_active(self, client, _patch_db):
        from backend.swarm.resource_lock import ResourceLockManager

        db = _patch_db
        now = _now_iso()
        job_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'agent', '', 'running', 5, ?)",
            (job_id, now),
        )
        db.commit()

        # Acquire a lock through the manager
        mgr = ResourceLockManager()
        mgr.acquire("file:/workspace/main.py", job_id, ttl_sec=600)

        resp = client.get("/api/swarm/locks")
        assert resp.status_code == 200
        locks = resp.json()["locks"]
        assert len(locks) >= 1
        assert locks[0]["resource_path"] == "file:/workspace/main.py"

    def test_force_release_lock(self, client, _patch_db):
        from backend.swarm.resource_lock import ResourceLockManager

        db = _patch_db
        now = _now_iso()
        job_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'agent', '', 'running', 5, ?)",
            (job_id, now),
        )
        db.commit()

        mgr = ResourceLockManager()
        lock = mgr.acquire("file:/workspace/util.py", job_id, ttl_sec=600)

        resp = client.post(f"/api/swarm/locks/{lock.id}/release")
        assert resp.status_code == 200
        assert resp.json()["released"] is True

        # Verify it's gone from active list
        resp2 = client.get("/api/swarm/locks")
        paths = [lock_info["resource_path"] for lock_info in resp2.json()["locks"]]
        assert "file:/workspace/util.py" not in paths

    def test_force_release_unknown_lock(self, client):
        fake_lock_id = _uid()
        resp = client.post(f"/api/swarm/locks/{fake_lock_id}/release")
        assert resp.status_code == 200
        assert resp.json()["released"] is False


# ===================================================================
# TestJobTreeEndpoint (from jobs routes, tested via swarm router's /tree)
# ===================================================================

class TestJobTreeEndpoint:

    def test_get_job_tree(self, client, _patch_db):
        from backend.swarm.delegation import DelegationEngine

        db = _patch_db
        now = _now_iso()
        root_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'root-job', '', 'running', 5, ?)",
            (root_id, now),
        )
        db.commit()

        # Spawn a child via the engine
        engine = DelegationEngine()
        d = engine.spawn_child_job(root_id, "sub-job", "sub-task")

        resp = client.get(f"/api/swarm/tree/{root_id}")
        assert resp.status_code == 200
        tree = resp.json()["tree"]
        assert tree["job_id"] == root_id
        assert len(tree["children"]) == 1
        assert tree["children"][0]["job_id"] == d.child_job_id

    def test_get_job_tree_unknown_job(self, client):
        fake_id = _uid()
        resp = client.get(f"/api/swarm/tree/{fake_id}")
        # With no job in DB, get_full_tree returns a node with "unknown" status
        assert resp.status_code == 200
        assert resp.json()["tree"]["status"] == "unknown"

    def test_delegate_then_read_tree(self, client, _patch_db):
        """Round-trip: delegate via API, then read the tree via API."""
        db = _patch_db
        now = _now_iso()
        parent_id = _uid()
        db.execute(
            "INSERT INTO jobs (id, title, description, status, priority, created_at) "
            "VALUES (?, 'parent', '', 'running', 5, ?)",
            (parent_id, now),
        )
        db.commit()

        # Delegate via the API
        resp = client.post("/api/swarm/delegate", json={
            "parent_job_id": parent_id,
            "title": "api-child",
        })
        assert resp.status_code == 201
        child_job_id = resp.json()["child_job_id"]

        # Read the tree
        tree_resp = client.get(f"/api/swarm/tree/{parent_id}")
        assert tree_resp.status_code == 200
        tree = tree_resp.json()["tree"]
        child_ids = [c["job_id"] for c in tree["children"]]
        assert child_job_id in child_ids
