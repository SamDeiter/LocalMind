"""Tests for DelegationEngine (backend.swarm.delegation)."""

import json
import sqlite3
from unittest.mock import patch

import pytest


class _UnclosableConnection:
    """Wraps a sqlite3.Connection so that .close() is a no-op.

    The production code calls conn.close() in finally blocks.  For in-memory
    test databases that would destroy the data between calls, so we suppress it.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self):
        """Intentional no-op."""

    def real_close(self):
        """Call when the test is actually done."""
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _init_test_db():
    """Create an in-memory SQLite DB with all swarm tables."""
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'queued',
            priority INTEGER DEFAULT 5,
            created_at TEXT,
            parent_job_id TEXT,
            tree_root_id TEXT
        );
        CREATE TABLE IF NOT EXISTS job_delegation (
            id TEXT PRIMARY KEY,
            parent_job_id TEXT NOT NULL,
            child_job_id TEXT NOT NULL,
            delegation_type TEXT DEFAULT 'sub_task',
            context_json TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS swarm_shared_memory (
            id TEXT PRIMARY KEY,
            tree_root_job_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT DEFAULT '{}',
            written_by_job_id TEXT NOT NULL,
            written_by_node_id TEXT,
            version INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS resource_locks (
            id TEXT PRIMARY KEY,
            resource_path TEXT NOT NULL,
            lock_type TEXT DEFAULT 'exclusive',
            held_by_job_id TEXT NOT NULL,
            held_by_node_id TEXT,
            acquired_at TEXT,
            expires_at TEXT,
            released_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_messages (
            id TEXT PRIMARY KEY,
            tree_root_job_id TEXT NOT NULL,
            from_job_id TEXT NOT NULL,
            to_job_id TEXT,
            message_type TEXT DEFAULT 'data',
            subject TEXT,
            body_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            read_at TEXT
        );
    """)
    return _UnclosableConnection(raw)


def _insert_job(conn, job_id, title="Test Job", status="queued"):
    """Helper to insert a job row directly."""
    conn.execute(
        "INSERT INTO jobs (id, title, description, status, priority, created_at) "
        "VALUES (?, ?, 'desc', ?, 5, '2026-01-01T00:00:00+00:00')",
        (job_id, title, status),
    )
    conn.commit()


@pytest.fixture
def test_conn():
    conn = _init_test_db()
    yield conn
    conn.real_close()


class TestSpawnChildJob:
    """Tests for DelegationEngine.spawn_child_job."""

    def test_spawn_creates_job_and_delegation(self, test_conn):
        _insert_job(test_conn, "parent-1")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job("parent-1", "Child", "Do work")

        assert d.parent_job_id == "parent-1"
        assert d.child_job_id  # non-empty
        assert d.status == "active"
        assert d.delegation_type == "sub_task"

        # Verify job row was created
        job = test_conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (d.child_job_id,)
        ).fetchone()
        assert job is not None
        assert job["title"] == "Child"
        assert job["status"] == "queued"

    def test_spawn_creates_delegation_record(self, test_conn):
        _insert_job(test_conn, "parent-2")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job("parent-2", "Child", "desc")

        row = test_conn.execute(
            "SELECT * FROM job_delegation WHERE id = ?", (d.id,)
        ).fetchone()
        assert row is not None
        assert row["parent_job_id"] == "parent-2"
        assert row["child_job_id"] == d.child_job_id

    def test_spawn_enforces_max_children(self, test_conn):
        _insert_job(test_conn, "parent-3")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn), \
             patch("backend.swarm.delegation.MAX_CHILD_JOBS_PER_PARENT", 2):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            eng.spawn_child_job("parent-3", "C1", "d")
            eng.spawn_child_job("parent-3", "C2", "d")
            with pytest.raises(ValueError, match="already has 2 children"):
                eng.spawn_child_job("parent-3", "C3", "d")

    def test_spawn_enforces_max_depth(self, test_conn):
        _insert_job(test_conn, "root")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn), \
             patch("backend.swarm.delegation.MAX_DELEGATION_DEPTH", 2):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d1 = eng.spawn_child_job("root", "L1", "d")
            with pytest.raises(ValueError, match="would reach depth"):
                eng.spawn_child_job(d1.child_job_id, "L2", "d")

    def test_spawn_raises_for_missing_parent(self, test_conn):
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            with pytest.raises(ValueError, match="does not exist"):
                eng.spawn_child_job("nonexistent", "C", "d")

    def test_spawn_with_parallel_type(self, test_conn):
        _insert_job(test_conn, "parent-p")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job(
                "parent-p", "Parallel", "d", delegation_type="parallel"
            )
        assert d.delegation_type == "parallel"

    def test_spawn_with_context(self, test_conn):
        _insert_job(test_conn, "parent-ctx")
        ctx = {"input_file": "/data/in.csv", "mode": "fast"}
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job(
                "parent-ctx", "WithCtx", "d", context=ctx
            )
        assert d.context_json is not None
        assert json.loads(d.context_json) == ctx

    def test_spawn_with_priority(self, test_conn):
        _insert_job(test_conn, "parent-pri")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job("parent-pri", "Hi-Pri", "d", priority=1)

        job = test_conn.execute(
            "SELECT priority FROM jobs WHERE id = ?", (d.child_job_id,)
        ).fetchone()
        assert job["priority"] == 1

    def test_spawn_default_context_is_none(self, test_conn):
        _insert_job(test_conn, "parent-nc")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job("parent-nc", "NoCtx", "d")
        assert d.context_json is None


class TestGetChildren:
    def test_returns_correct_children(self, test_conn):
        _insert_job(test_conn, "parent-gc")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d1 = eng.spawn_child_job("parent-gc", "C1", "d")
            d2 = eng.spawn_child_job("parent-gc", "C2", "d")
            children = eng.get_children("parent-gc")

        assert len(children) == 2
        child_ids = {c.child_job_id for c in children}
        assert d1.child_job_id in child_ids
        assert d2.child_job_id in child_ids

    def test_returns_empty_for_no_children(self, test_conn):
        _insert_job(test_conn, "lonely")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            children = eng.get_children("lonely")
        assert children == []


class TestGetParent:
    def test_returns_correct_parent(self, test_conn):
        _insert_job(test_conn, "p-gp")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job("p-gp", "C", "d")
            parent = eng.get_parent(d.child_job_id)

        assert parent is not None
        assert parent.parent_job_id == "p-gp"
        assert parent.child_job_id == d.child_job_id

    def test_returns_none_for_root(self, test_conn):
        _insert_job(test_conn, "root-gp")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            parent = eng.get_parent("root-gp")
        assert parent is None


class TestGetTreeRoot:
    def test_walks_to_root(self, test_conn):
        _insert_job(test_conn, "root-tr")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d1 = eng.spawn_child_job("root-tr", "L1", "d")
            d2 = eng.spawn_child_job(d1.child_job_id, "L2", "d")
            root = eng.get_tree_root(d2.child_job_id)
        assert root == "root-tr"

    def test_returns_self_for_root(self, test_conn):
        _insert_job(test_conn, "self-root")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            root = eng.get_tree_root("self-root")
        assert root == "self-root"


class TestGetDepth:
    def test_root_is_depth_zero(self, test_conn):
        _insert_job(test_conn, "root-d")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            assert eng.get_depth("root-d") == 0

    def test_child_depth_is_correct(self, test_conn):
        _insert_job(test_conn, "root-d2")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d1 = eng.spawn_child_job("root-d2", "L1", "d")
            d2 = eng.spawn_child_job(d1.child_job_id, "L2", "d")
            assert eng.get_depth(d1.child_job_id) == 1
            assert eng.get_depth(d2.child_job_id) == 2


class TestGetFullTree:
    def test_returns_nested_structure(self, test_conn):
        _insert_job(test_conn, "root-ft")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d1 = eng.spawn_child_job("root-ft", "C1", "d")
            eng.spawn_child_job("root-ft", "C2", "d")
            eng.spawn_child_job(d1.child_job_id, "GC1", "d")
            tree = eng.get_full_tree("root-ft")

        assert tree["job_id"] == "root-ft"
        assert tree["delegation_type"] is None
        assert len(tree["children"]) == 2
        # First child should have one grandchild
        first_child = tree["children"][0]
        assert len(first_child["children"]) == 1
        # Second child has no children
        assert len(tree["children"][1]["children"]) == 0


class TestCancelSubtree:
    def test_cancels_all_descendants(self, test_conn):
        _insert_job(test_conn, "root-cs")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d1 = eng.spawn_child_job("root-cs", "C1", "d")
            d2 = eng.spawn_child_job(d1.child_job_id, "GC1", "d")
            eng.cancel_subtree("root-cs")

        # All jobs should be cancelled
        for jid in ["root-cs", d1.child_job_id, d2.child_job_id]:
            row = test_conn.execute(
                "SELECT status FROM jobs WHERE id = ?", (jid,)
            ).fetchone()
            assert row["status"] == "cancelled"

    def test_cancel_does_not_affect_completed(self, test_conn):
        _insert_job(test_conn, "root-cc", status="completed")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            eng.cancel_subtree("root-cc")

        row = test_conn.execute(
            "SELECT status FROM jobs WHERE id = ?", ("root-cc",)
        ).fetchone()
        assert row["status"] == "completed"


class TestIsTreeComplete:
    def test_returns_true_when_all_done(self, test_conn):
        _insert_job(test_conn, "root-tc", status="completed")
        _insert_job(test_conn, "child-tc", status="completed")
        test_conn.execute(
            "INSERT INTO job_delegation (id, parent_job_id, child_job_id, status, created_at) "
            "VALUES ('del-tc', 'root-tc', 'child-tc', 'completed', '2026-01-01')"
        )
        test_conn.commit()

        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            assert eng.is_tree_complete("root-tc") is True

    def test_returns_false_when_some_active(self, test_conn):
        _insert_job(test_conn, "root-ta", status="completed")
        _insert_job(test_conn, "child-ta", status="running")
        test_conn.execute(
            "INSERT INTO job_delegation (id, parent_job_id, child_job_id, status, created_at) "
            "VALUES ('del-ta', 'root-ta', 'child-ta', 'active', '2026-01-01')"
        )
        test_conn.commit()

        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            assert eng.is_tree_complete("root-ta") is False


class TestMarkCompleted:
    def test_updates_delegation_record(self, test_conn):
        _insert_job(test_conn, "root-mc")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job("root-mc", "C", "d")
            eng.mark_completed(d.child_job_id, status="completed")

        row = test_conn.execute(
            "SELECT status, completed_at FROM job_delegation WHERE child_job_id = ?",
            (d.child_job_id,),
        ).fetchone()
        assert row["status"] == "completed"
        assert row["completed_at"] is not None

    def test_mark_completed_failed(self, test_conn):
        _insert_job(test_conn, "root-mf")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job("root-mf", "C", "d")
            eng.mark_completed(d.child_job_id, status="failed")

        row = test_conn.execute(
            "SELECT status FROM job_delegation WHERE child_job_id = ?",
            (d.child_job_id,),
        ).fetchone()
        assert row["status"] == "failed"

    def test_mark_completed_invalid_status_raises(self, test_conn):
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            with pytest.raises(ValueError, match="Invalid delegation status"):
                eng.mark_completed("whatever", status="bogus")

    def test_mark_completed_idempotent_on_already_completed(self, test_conn):
        """Marking a delegation that is already completed does not error."""
        _insert_job(test_conn, "root-mi")
        with patch("backend.swarm.delegation.get_db", return_value=test_conn):
            from backend.swarm.delegation import DelegationEngine
            eng = DelegationEngine()
            d = eng.spawn_child_job("root-mi", "C", "d")
            eng.mark_completed(d.child_job_id, status="completed")
            # Second call silently does nothing (rowcount==0 logged as warning)
            eng.mark_completed(d.child_job_id, status="completed")
