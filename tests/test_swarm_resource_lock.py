"""Tests for ResourceLockManager and LockConflictError (backend.swarm.resource_lock)."""

import sqlite3
from datetime import datetime, timedelta, timezone
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
        pass

    def real_close(self):
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


@pytest.fixture
def test_conn():
    conn = _init_test_db()
    yield conn
    conn.real_close()


RES = "file:/workspace/foo.py"


class TestAcquire:
    def test_creates_lock(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock = mgr.acquire(RES, "job-1")
        assert lock.resource_path == RES
        assert lock.held_by_job_id == "job-1"
        assert lock.lock_type == "exclusive"
        assert lock.released_at is None

    def test_returns_existing_lock_for_same_job(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock1 = mgr.acquire(RES, "job-dup")
            lock2 = mgr.acquire(RES, "job-dup")
        assert lock1.id == lock2.id

    def test_raises_lock_conflict_for_exclusive(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import LockConflictError, ResourceLockManager
            mgr = ResourceLockManager()
            mgr.acquire(RES, "job-a")
            with pytest.raises(LockConflictError) as exc_info:
                mgr.acquire(RES, "job-b")
            assert exc_info.value.held_by == "job-a"
            assert exc_info.value.resource_path == RES

    def test_shared_alongside_shared_succeeds(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock1 = mgr.acquire(RES, "job-s1", lock_type="shared")
            lock2 = mgr.acquire(RES, "job-s2", lock_type="shared")
        assert lock1.id != lock2.id
        assert lock1.lock_type == "shared"
        assert lock2.lock_type == "shared"

    def test_exclusive_fails_when_shared_exists(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import LockConflictError, ResourceLockManager
            mgr = ResourceLockManager()
            mgr.acquire(RES, "job-shared", lock_type="shared")
            with pytest.raises(LockConflictError):
                mgr.acquire(RES, "job-excl", lock_type="exclusive")

    def test_shared_fails_when_exclusive_exists(self, test_conn):
        """Acquiring shared when another job holds exclusive should fail."""
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import LockConflictError, ResourceLockManager
            mgr = ResourceLockManager()
            mgr.acquire(RES, "job-excl-holder", lock_type="exclusive")
            with pytest.raises(LockConflictError):
                mgr.acquire(RES, "job-shared-req", lock_type="shared")

    def test_acquire_with_node_id(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock = mgr.acquire(RES, "job-n", node_id="node-7")
        assert lock.held_by_node_id == "node-7"

    def test_custom_ttl_is_respected(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock = mgr.acquire(RES, "job-ttl", ttl_sec=10)
        # expires_at should be roughly 10 seconds from now (not the default 300)
        acquired = datetime.fromisoformat(lock.acquired_at)
        expires = datetime.fromisoformat(lock.expires_at)
        delta = (expires - acquired).total_seconds()
        assert 5 <= delta <= 15  # generous tolerance


class TestRelease:
    def test_sets_released_at(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock = mgr.acquire(RES, "job-r")
            result = mgr.release(lock.id)
        assert result is True
        row = test_conn.execute(
            "SELECT released_at FROM resource_locks WHERE id = ?", (lock.id,)
        ).fetchone()
        assert row["released_at"] is not None

    def test_returns_false_for_unknown_lock(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            assert mgr.release("nonexistent-lock-id") is False

    def test_release_idempotent(self, test_conn):
        """Releasing an already-released lock returns False."""
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock = mgr.acquire(RES, "job-ri")
            assert mgr.release(lock.id) is True
            assert mgr.release(lock.id) is False


class TestReleaseAll:
    def test_releases_all_locks_for_job(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            mgr.acquire("file:/a.py", "job-ra")
            mgr.acquire("file:/b.py", "job-ra")
            mgr.acquire("file:/c.py", "job-other")
            mgr.release_all("job-ra")

        rows = test_conn.execute(
            "SELECT * FROM resource_locks WHERE held_by_job_id = 'job-ra' AND released_at IS NULL"
        ).fetchall()
        assert len(rows) == 0

        # Other job's lock untouched
        rows_other = test_conn.execute(
            "SELECT * FROM resource_locks WHERE held_by_job_id = 'job-other' AND released_at IS NULL"
        ).fetchall()
        assert len(rows_other) == 1


class TestIsLocked:
    def test_returns_true_for_active_lock(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            mgr.acquire(RES, "job-il")
            assert mgr.is_locked(RES) is True

    def test_returns_false_after_release(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock = mgr.acquire(RES, "job-il2")
            mgr.release(lock.id)
            assert mgr.is_locked(RES) is False

    def test_returns_false_for_never_locked(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            assert mgr.is_locked("file:/never.py") is False


class TestGetLockHolder:
    def test_returns_exclusive_lock_holder(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock = mgr.acquire(RES, "job-glh")
            holder = mgr.get_lock_holder(RES)
        assert holder is not None
        assert holder.held_by_job_id == "job-glh"
        assert holder.id == lock.id

    def test_returns_none_when_no_exclusive(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            # Only shared lock
            mgr.acquire(RES, "job-sh", lock_type="shared")
            assert mgr.get_lock_holder(RES) is None


class TestGetLocksForJob:
    def test_returns_all_locks(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            mgr.acquire("file:/x.py", "job-gfl")
            mgr.acquire("file:/y.py", "job-gfl")
            locks = mgr.get_locks_for_job("job-gfl")
        assert len(locks) == 2
        paths = {lk.resource_path for lk in locks}
        assert paths == {"file:/x.py", "file:/y.py"}


class TestForceRelease:
    def test_force_release_works(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock = mgr.acquire(RES, "job-fr")
            assert mgr.force_release(lock.id) is True
            assert mgr.is_locked(RES) is False

    def test_force_release_returns_false_for_missing(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            assert mgr.force_release("no-such-lock") is False


class TestCleanupExpired:
    def test_releases_expired_locks(self, test_conn):
        """Insert a lock that has already expired and verify cleanup catches it."""
        past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        test_conn.execute(
            "INSERT INTO resource_locks "
            "(id, resource_path, lock_type, held_by_job_id, held_by_node_id, "
            "acquired_at, expires_at, released_at) "
            "VALUES ('expired-1', 'file:/old.py', 'exclusive', 'job-exp', NULL, ?, ?, NULL)",
            (past, past),
        )
        test_conn.commit()

        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            count = mgr.cleanup_expired()
        assert count == 1

        row = test_conn.execute(
            "SELECT released_at FROM resource_locks WHERE id = 'expired-1'"
        ).fetchone()
        assert row["released_at"] is not None

    def test_does_not_release_active_locks(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            mgr.acquire(RES, "job-active", ttl_sec=9999)
            count = mgr.cleanup_expired()
        assert count == 0


class TestListActive:
    def test_returns_only_active_locks(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            lock1 = mgr.acquire("file:/a.py", "job-la1")
            mgr.acquire("file:/b.py", "job-la2")
            mgr.release(lock1.id)
            active = mgr.list_active()
        assert len(active) == 1
        assert active[0].resource_path == "file:/b.py"

    def test_returns_empty_when_none_active(self, test_conn):
        with patch("backend.swarm.resource_lock.get_db", return_value=test_conn):
            from backend.swarm.resource_lock import ResourceLockManager
            mgr = ResourceLockManager()
            assert mgr.list_active() == []
