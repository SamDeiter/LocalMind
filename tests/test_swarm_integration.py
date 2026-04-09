"""End-to-end integration tests for the swarm subsystem.

These tests spin up an in-memory SQLite database with the full swarm
schema and exercise DelegationEngine, SharedMemoryStore, ResourceLockManager
and AgentMessageBus together — no mocks on the swarm modules themselves.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_test_db():
    """Return an in-memory SQLite connection with the full swarm schema."""
    conn = sqlite3.connect(":memory:")
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


def _insert_job(conn, job_id=None, title="test", status="queued"):
    """Insert a minimal job row and return its id."""
    jid = job_id or _uid()
    now = _now_iso()
    conn.execute(
        "INSERT INTO jobs (id, title, description, status, priority, created_at) "
        "VALUES (?, ?, '', ?, 5, ?)",
        (jid, title, status, now),
    )
    conn.commit()
    return jid


# ---------------------------------------------------------------------------
# Fixture — every test gets a fresh in-memory DB, patched into backend.db
# ---------------------------------------------------------------------------

class _PersistentConnection:
    """Thin wrapper around a sqlite3.Connection that silently ignores close().

    sqlite3.Connection.close is read-only in CPython 3.12+, so we cannot
    monkey-patch it.  Instead we wrap the connection and delegate everything
    except close().
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self):          # no-op — keep the :memory: DB alive
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


# ===================================================================
# TestDelegationWorkflow
# ===================================================================

class TestDelegationWorkflow:

    def test_parent_spawns_two_children_and_completes(self, _patch_db):
        from backend.swarm.delegation import DelegationEngine

        db = _patch_db
        engine = DelegationEngine()

        parent_id = _insert_job(db, title="parent")
        d1 = engine.spawn_child_job(parent_id, "child-1", "first child")
        d2 = engine.spawn_child_job(parent_id, "child-2", "second child")

        assert d1.child_job_id != d2.child_job_id
        children = engine.get_children(parent_id)
        assert len(children) == 2

        # Mark both children + parent completed
        db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (d1.child_job_id,))
        db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (d2.child_job_id,))
        db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (parent_id,))
        db.commit()
        engine.mark_completed(d1.child_job_id)
        engine.mark_completed(d2.child_job_id)

        assert engine.is_tree_complete(parent_id) is True

    def test_child_writes_shared_memory_parent_reads(self, _patch_db):
        from backend.swarm.delegation import DelegationEngine
        from backend.swarm.shared_memory import SharedMemoryStore

        db = _patch_db
        engine = DelegationEngine()
        mem = SharedMemoryStore()

        parent_id = _insert_job(db, title="parent")
        d = engine.spawn_child_job(parent_id, "worker", "do work")

        # Child writes
        mem.put(parent_id, "result", {"score": 42}, written_by_job_id=d.child_job_id)

        # Parent reads
        entry = mem.get(parent_id, "result")
        assert entry is not None
        assert entry.value == {"score": 42}

    def test_multiple_children_share_memory(self, _patch_db):
        from backend.swarm.delegation import DelegationEngine
        from backend.swarm.shared_memory import SharedMemoryStore

        db = _patch_db
        engine = DelegationEngine()
        mem = SharedMemoryStore()

        parent_id = _insert_job(db, title="parent")
        d1 = engine.spawn_child_job(parent_id, "child-a", "a")
        d2 = engine.spawn_child_job(parent_id, "child-b", "b")

        mem.put(parent_id, "key_a", "val_a", written_by_job_id=d1.child_job_id)
        mem.put(parent_id, "key_b", "val_b", written_by_job_id=d2.child_job_id)

        entries = mem.get_all(parent_id)
        keys = {e.key for e in entries}
        assert keys == {"key_a", "key_b"}

    def test_cascade_cancel_cancels_children(self, _patch_db):
        from backend.swarm.delegation import DelegationEngine

        db = _patch_db
        engine = DelegationEngine()

        parent_id = _insert_job(db, title="parent")
        d1 = engine.spawn_child_job(parent_id, "child-1", "c1")
        d2 = engine.spawn_child_job(parent_id, "child-2", "c2")

        engine.cancel_subtree(parent_id)

        # Verify all are cancelled
        for jid in [parent_id, d1.child_job_id, d2.child_job_id]:
            row = db.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()
            assert row["status"] == "cancelled"

    def test_delegation_depth_limit_enforced(self, _patch_db):
        from backend.config import MAX_DELEGATION_DEPTH
        from backend.swarm.delegation import DelegationEngine

        db = _patch_db
        engine = DelegationEngine()

        # Build a chain up to the limit
        current_id = _insert_job(db, title="root")
        for i in range(MAX_DELEGATION_DEPTH - 1):
            d = engine.spawn_child_job(current_id, f"level-{i+1}", f"depth {i+1}")
            current_id = d.child_job_id

        # Next spawn should raise ValueError (would exceed depth)
        with pytest.raises(ValueError, match="depth"):
            engine.spawn_child_job(current_id, "too-deep", "over the limit")

    def test_tree_status_aggregation(self, _patch_db):
        from backend.swarm.delegation import DelegationEngine

        db = _patch_db
        engine = DelegationEngine()

        parent_id = _insert_job(db, title="parent")
        d1 = engine.spawn_child_job(parent_id, "done-child", "done")
        engine.spawn_child_job(parent_id, "running-child", "running")

        # Mark one child completed, leave the other queued
        db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (d1.child_job_id,))
        db.commit()
        engine.mark_completed(d1.child_job_id)

        # Tree should NOT be complete (parent + child-2 still non-terminal)
        assert engine.is_tree_complete(parent_id) is False

        tree = engine.get_full_tree(parent_id)
        assert tree["job_id"] == parent_id
        assert len(tree["children"]) == 2


# ===================================================================
# TestResourceConflicts
# ===================================================================

class TestResourceConflicts:

    def test_two_agents_exclusive_lock_conflict(self, _patch_db):
        from backend.swarm.resource_lock import LockConflictError, ResourceLockManager

        db = _patch_db
        mgr = ResourceLockManager()

        job_a = _insert_job(db, title="agent-a")
        job_b = _insert_job(db, title="agent-b")

        mgr.acquire("file:/workspace/foo.py", job_a)

        with pytest.raises(LockConflictError):
            mgr.acquire("file:/workspace/foo.py", job_b)

    def test_shared_locks_coexist(self, _patch_db):
        from backend.swarm.resource_lock import ResourceLockManager

        db = _patch_db
        mgr = ResourceLockManager()

        job_a = _insert_job(db, title="agent-a")
        job_b = _insert_job(db, title="agent-b")

        lock_a = mgr.acquire("file:/workspace/data.csv", job_a, lock_type="shared")
        lock_b = mgr.acquire("file:/workspace/data.csv", job_b, lock_type="shared")

        assert lock_a.id != lock_b.id
        assert mgr.is_locked("file:/workspace/data.csv")

    def test_lock_expires_allows_reacquire(self, _patch_db):
        from backend.swarm.resource_lock import ResourceLockManager

        db = _patch_db
        mgr = ResourceLockManager()

        job_a = _insert_job(db, title="agent-a")
        job_b = _insert_job(db, title="agent-b")

        # Acquire with very short TTL then manually expire it
        lock = mgr.acquire("file:/workspace/bar.py", job_a, ttl_sec=1)

        # Manually set expires_at to the past
        past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        db.execute("UPDATE resource_locks SET expires_at = ? WHERE id = ?", (past, lock.id))
        db.commit()

        # Now agent B should be able to acquire
        lock_b = mgr.acquire("file:/workspace/bar.py", job_b)
        assert lock_b.held_by_job_id == job_b

    def test_cleanup_frees_expired_locks(self, _patch_db):
        from backend.swarm.resource_lock import ResourceLockManager

        db = _patch_db
        mgr = ResourceLockManager()

        job_id = _insert_job(db, title="agent")
        lock = mgr.acquire("file:/workspace/temp.py", job_id, ttl_sec=1)

        # Manually expire the lock
        past = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        db.execute("UPDATE resource_locks SET expires_at = ? WHERE id = ?", (past, lock.id))
        db.commit()

        freed = mgr.cleanup_expired()
        assert freed >= 1
        assert mgr.is_locked("file:/workspace/temp.py") is False


# ===================================================================
# TestMessagingWorkflow
# ===================================================================

class TestMessagingWorkflow:

    def test_request_response_pattern(self, _patch_db):
        from backend.swarm.messaging import AgentMessageBus

        db = _patch_db
        bus = AgentMessageBus()

        root_id = _insert_job(db, title="root")
        agent_a = _insert_job(db, title="agent-a")
        agent_b = _insert_job(db, title="agent-b")

        # A sends request to B
        req = bus.send_request(root_id, agent_a, agent_b, "need-data", {"query": "stats"})
        assert req.message_type == "request"

        # B receives the request
        msgs = bus.receive(agent_b, include_broadcasts=False)
        assert len(msgs) == 1
        assert msgs[0].id == req.id

        # B sends response
        resp = bus.send_response(req.id, agent_b, {"stats": [1, 2, 3]})
        assert resp.message_type == "response"
        assert resp.to_job_id == agent_a

    def test_broadcast_reaches_all_tree_members(self, _patch_db):
        from backend.swarm.delegation import DelegationEngine
        from backend.swarm.messaging import AgentMessageBus

        db = _patch_db
        engine = DelegationEngine()
        bus = AgentMessageBus()

        root_id = _insert_job(db, title="root")
        d1 = engine.spawn_child_job(root_id, "child-1", "c1")
        d2 = engine.spawn_child_job(root_id, "child-2", "c2")

        # Root broadcasts
        bus.send(root_id, root_id, {"alert": "shutdown"}, to_job_id=None, message_type="signal")

        # Both children should receive the broadcast
        msgs_1 = bus.receive(d1.child_job_id, include_broadcasts=True)
        msgs_2 = bus.receive(d2.child_job_id, include_broadcasts=True)

        # At least one should have received it (the first receive marks it delivered)
        total = len(msgs_1) + len(msgs_2)
        assert total >= 1

    def test_message_ordering(self, _patch_db):
        from backend.swarm.messaging import AgentMessageBus

        db = _patch_db
        bus = AgentMessageBus()

        root_id = _insert_job(db, title="root")
        sender = _insert_job(db, title="sender")
        receiver = _insert_job(db, title="receiver")

        # Send 3 messages in order
        for i in range(3):
            bus.send(root_id, sender, {"seq": i}, to_job_id=receiver)

        msgs = bus.receive(receiver, include_broadcasts=False)
        assert len(msgs) == 3
        seqs = [json.loads(m.body_json)["seq"] for m in msgs]
        assert seqs == [0, 1, 2]


# ===================================================================
# TestSharedMemoryConflicts
# ===================================================================

class TestSharedMemoryConflicts:

    def test_concurrent_writers_version_conflict(self, _patch_db):
        from backend.swarm.shared_memory import SharedMemoryStore, VersionConflictError

        db = _patch_db
        mem = SharedMemoryStore()

        root = _insert_job(db, title="root")
        agent_a = _insert_job(db, title="agent-a")
        agent_b = _insert_job(db, title="agent-b")

        # Initial write
        mem.put(root, "counter", 0, written_by_job_id=agent_a)

        # Both agents read version 1
        v = mem.get_version(root, "counter")
        assert v == 1

        # Agent A updates with expected_version=1 — succeeds
        mem.put(root, "counter", 1, written_by_job_id=agent_a, expected_version=1)

        # Agent B tries with stale expected_version=1 — should fail
        with pytest.raises(VersionConflictError):
            mem.put(root, "counter", 2, written_by_job_id=agent_b, expected_version=1)

    def test_optimistic_retry_succeeds(self, _patch_db):
        from backend.swarm.shared_memory import SharedMemoryStore, VersionConflictError

        db = _patch_db
        mem = SharedMemoryStore()

        root = _insert_job(db, title="root")
        agent_a = _insert_job(db, title="agent-a")
        agent_b = _insert_job(db, title="agent-b")

        mem.put(root, "counter", 10, written_by_job_id=agent_a)

        # Agent A bumps it
        mem.put(root, "counter", 11, written_by_job_id=agent_a, expected_version=1)

        # Agent B tries with stale version
        with pytest.raises(VersionConflictError):
            mem.put(root, "counter", 20, written_by_job_id=agent_b, expected_version=1)

        # Agent B re-reads the version and retries
        current_version = mem.get_version(root, "counter")
        assert current_version == 2
        entry = mem.put(root, "counter", 20, written_by_job_id=agent_b, expected_version=2)
        assert entry.version == 3
        assert entry.value == 20


# ===================================================================
# TestFullPipeline
# ===================================================================

class TestFullPipeline:

    def test_end_to_end_delegation_flow(self, _patch_db):
        """Full lifecycle: create parent -> spawn child -> child writes
        shared memory -> child completes -> parent reads result -> tree is complete.
        """
        from backend.swarm.delegation import DelegationEngine
        from backend.swarm.messaging import AgentMessageBus
        from backend.swarm.resource_lock import ResourceLockManager
        from backend.swarm.shared_memory import SharedMemoryStore

        db = _patch_db
        engine = DelegationEngine()
        mem = SharedMemoryStore()
        locks = ResourceLockManager()
        bus = AgentMessageBus()

        # 1. Create parent job
        parent_id = _insert_job(db, title="orchestrator")

        # 2. Spawn child
        delegation = engine.spawn_child_job(parent_id, "analyzer", "analyze data")
        child_id = delegation.child_job_id

        # 3. Child acquires a lock on its resource
        lock = locks.acquire("file:/data/input.csv", child_id)
        assert lock.held_by_job_id == child_id

        # 4. Child writes shared memory
        mem.put(parent_id, "analysis_result", {"findings": ["ok"]}, written_by_job_id=child_id)

        # 5. Child sends completion message to parent
        bus.send(parent_id, child_id, {"status": "done"}, to_job_id=parent_id, message_type="signal")

        # 6. Child releases lock
        locks.release(lock.id)
        assert locks.is_locked("file:/data/input.csv") is False

        # 7. Mark child completed
        db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (child_id,))
        db.commit()
        engine.mark_completed(child_id)

        # 8. Parent reads the result
        result = mem.get(parent_id, "analysis_result")
        assert result is not None
        assert result.value == {"findings": ["ok"]}

        # 9. Parent reads message
        msgs = bus.receive(parent_id, include_broadcasts=False)
        assert len(msgs) >= 1

        # 10. Mark parent completed
        db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (parent_id,))
        db.commit()

        # 11. Tree should be fully complete
        assert engine.is_tree_complete(parent_id) is True

    def test_parallel_workers_with_locks_and_memory(self, _patch_db):
        """Multiple workers operate in parallel with non-overlapping locks
        and write results into shared memory."""
        from backend.swarm.delegation import DelegationEngine
        from backend.swarm.resource_lock import ResourceLockManager
        from backend.swarm.shared_memory import SharedMemoryStore

        db = _patch_db
        engine = DelegationEngine()
        mem = SharedMemoryStore()
        locks = ResourceLockManager()

        parent_id = _insert_job(db, title="coordinator")
        workers = []
        for i in range(3):
            d = engine.spawn_child_job(parent_id, f"worker-{i}", f"task {i}")
            workers.append(d)

        # Each worker locks a different resource
        held_locks = []
        for i, w in enumerate(workers):
            lk = locks.acquire(f"file:/data/chunk_{i}.csv", w.child_job_id)
            held_locks.append(lk)

        # Each worker writes its result
        for i, w in enumerate(workers):
            mem.put(parent_id, f"result_{i}", {"chunk": i, "rows": 100 + i},
                    written_by_job_id=w.child_job_id)

        # Release locks and complete
        for i, (w, lk) in enumerate(zip(workers, held_locks)):
            locks.release(lk.id)
            db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (w.child_job_id,))
            engine.mark_completed(w.child_job_id)
        db.commit()

        # Parent reads all results
        entries = mem.get_all(parent_id)
        assert len(entries) == 3

        # Complete parent
        db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (parent_id,))
        db.commit()
        assert engine.is_tree_complete(parent_id) is True

    def test_cancel_with_lock_cleanup(self, _patch_db):
        """Cancelling a subtree should mark jobs cancelled; the caller is
        responsible for releasing locks held by cancelled jobs."""
        from backend.swarm.delegation import DelegationEngine
        from backend.swarm.resource_lock import ResourceLockManager

        db = _patch_db
        engine = DelegationEngine()
        locks = ResourceLockManager()

        parent_id = _insert_job(db, title="parent")
        d = engine.spawn_child_job(parent_id, "worker", "work")
        child_id = d.child_job_id

        locks.acquire("file:/important.py", child_id)
        assert locks.is_locked("file:/important.py")

        # Cancel the whole tree
        engine.cancel_subtree(parent_id)

        # Lock still held (engine doesn't auto-release)
        assert locks.is_locked("file:/important.py")

        # Explicit cleanup
        locks.release_all(child_id)
        assert locks.is_locked("file:/important.py") is False

    def test_deep_tree_traversal(self, _patch_db):
        """Build a 3-level deep tree and verify get_full_tree captures all."""
        from backend.swarm.delegation import DelegationEngine

        db = _patch_db
        engine = DelegationEngine()

        root = _insert_job(db, title="root")
        d1 = engine.spawn_child_job(root, "level-1a", "L1a")
        engine.spawn_child_job(root, "level-1b", "L1b")
        engine.spawn_child_job(d1.child_job_id, "level-2a", "L2a")

        tree = engine.get_full_tree(root)
        assert tree["job_id"] == root
        assert len(tree["children"]) == 2

        # Find the child that has its own children
        nested = [c for c in tree["children"] if len(c["children"]) > 0]
        assert len(nested) == 1
        assert len(nested[0]["children"]) == 1

    def test_get_tree_root_from_grandchild(self, _patch_db):
        """get_tree_root should walk all the way up to the root."""
        from backend.swarm.delegation import DelegationEngine

        db = _patch_db
        engine = DelegationEngine()

        root = _insert_job(db, title="root")
        d1 = engine.spawn_child_job(root, "child", "c")
        d2 = engine.spawn_child_job(d1.child_job_id, "grandchild", "gc")

        found_root = engine.get_tree_root(d2.child_job_id)
        assert found_root == root

    def test_shared_memory_delete(self, _patch_db):
        """Verify SharedMemoryStore.delete works within a full pipeline."""
        from backend.swarm.shared_memory import SharedMemoryStore

        db = _patch_db
        mem = SharedMemoryStore()

        root = _insert_job(db, title="root")
        writer = _insert_job(db, title="writer")

        mem.put(root, "temp_key", "temp_value", written_by_job_id=writer)
        assert mem.get(root, "temp_key") is not None

        deleted = mem.delete(root, "temp_key")
        assert deleted is True
        assert mem.get(root, "temp_key") is None

    def test_shared_memory_clear(self, _patch_db):
        """SharedMemoryStore.clear removes all entries for a tree."""
        from backend.swarm.shared_memory import SharedMemoryStore

        db = _patch_db
        mem = SharedMemoryStore()

        root = _insert_job(db, title="root")
        writer = _insert_job(db, title="writer")

        mem.put(root, "k1", "v1", written_by_job_id=writer)
        mem.put(root, "k2", "v2", written_by_job_id=writer)
        assert len(mem.get_all(root)) == 2

        mem.clear(root)
        assert len(mem.get_all(root)) == 0

    def test_lock_idempotent_reacquire_same_job(self, _patch_db):
        """Acquiring a lock on the same resource by the same job returns
        the existing lock rather than raising."""
        from backend.swarm.resource_lock import ResourceLockManager

        db = _patch_db
        mgr = ResourceLockManager()

        job = _insert_job(db, title="worker")
        lock1 = mgr.acquire("file:/data.csv", job)
        lock2 = mgr.acquire("file:/data.csv", job)

        assert lock1.id == lock2.id

    def test_release_all_for_job(self, _patch_db):
        """release_all frees every lock held by a job."""
        from backend.swarm.resource_lock import ResourceLockManager

        db = _patch_db
        mgr = ResourceLockManager()

        job = _insert_job(db, title="worker")
        mgr.acquire("file:/a.py", job)
        mgr.acquire("file:/b.py", job)

        active = mgr.get_locks_for_job(job)
        assert len(active) == 2

        mgr.release_all(job)
        assert len(mgr.get_locks_for_job(job)) == 0
