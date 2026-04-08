"""Tests for SharedMemoryStore and VersionConflictError (backend.swarm.shared_memory)."""

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


TREE = "tree-root-1"
JOB = "writer-job-1"


class TestPut:
    def test_creates_new_entry_with_version_1(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            entry = store.put(TREE, "key1", "hello", JOB)
        assert entry.version == 1
        assert entry.key == "key1"
        assert entry.tree_root_job_id == TREE

    def test_updates_existing_entry_increments_version(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(TREE, "key2", "v1", JOB)
            entry = store.put(TREE, "key2", "v2", JOB)
        assert entry.version == 2
        assert json.loads(entry.value_json) == "v2"

    def test_put_expected_version_succeeds(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(TREE, "key3", "v1", JOB)
            entry = store.put(TREE, "key3", "v2", JOB, expected_version=1)
        assert entry.version == 2

    def test_put_expected_version_mismatch_raises(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore, VersionConflictError
            store = SharedMemoryStore()
            store.put(TREE, "key4", "v1", JOB)
            with pytest.raises(VersionConflictError, match="Expected version 99"):
                store.put(TREE, "key4", "v2", JOB, expected_version=99)

    def test_put_expected_version_on_new_key_raises(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore, VersionConflictError
            store = SharedMemoryStore()
            with pytest.raises(VersionConflictError, match="does not exist"):
                store.put(TREE, "no-such-key", "v", JOB, expected_version=1)

    def test_put_handles_complex_nested_json(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            complex_val = {
                "results": [1, 2, 3],
                "meta": {"nested": {"deep": True}},
                "tags": ["a", "b"],
            }
            entry = store.put(TREE, "complex", complex_val, JOB)
        assert json.loads(entry.value_json) == complex_val

    def test_put_with_node_id(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            entry = store.put(TREE, "keyn", "val", JOB, written_by_node_id="node-42")
        assert entry.written_by_node_id == "node-42"


class TestGet:
    def test_returns_entry(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(TREE, "gk1", "val1", JOB)
            entry = store.get(TREE, "gk1")
        assert entry is not None
        assert entry.key == "gk1"
        assert json.loads(entry.value_json) == "val1"

    def test_returns_none_for_missing_key(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            assert store.get(TREE, "nonexistent") is None


class TestGetAll:
    def test_returns_all_entries_for_tree(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(TREE, "a", 1, JOB)
            store.put(TREE, "b", 2, JOB)
            store.put(TREE, "c", 3, JOB)
            entries = store.get_all(TREE)
        assert len(entries) == 3
        keys = {e.key for e in entries}
        assert keys == {"a", "b", "c"}

    def test_returns_empty_for_unknown_tree(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            assert store.get_all("unknown-tree") == []


class TestDelete:
    def test_removes_entry_returns_true(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(TREE, "del1", "v", JOB)
            assert store.delete(TREE, "del1") is True
            assert store.get(TREE, "del1") is None

    def test_returns_false_for_missing_key(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            assert store.delete(TREE, "nope") is False


class TestGetVersion:
    def test_returns_version_number(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(TREE, "vk1", "a", JOB)
            store.put(TREE, "vk1", "b", JOB)
            assert store.get_version(TREE, "vk1") == 2

    def test_returns_none_for_missing_key(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            assert store.get_version(TREE, "missing") is None


class TestClear:
    def test_removes_all_entries_for_tree(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(TREE, "x", 1, JOB)
            store.put(TREE, "y", 2, JOB)
            store.clear(TREE)
            assert store.get_all(TREE) == []


class TestTreeScoping:
    def test_different_trees_dont_see_each_other(self, test_conn):
        tree_a = "tree-A"
        tree_b = "tree-B"
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(tree_a, "shared-key", "value-A", JOB)
            store.put(tree_b, "shared-key", "value-B", JOB)

            entry_a = store.get(tree_a, "shared-key")
            entry_b = store.get(tree_b, "shared-key")
        assert json.loads(entry_a.value_json) == "value-A"
        assert json.loads(entry_b.value_json) == "value-B"

    def test_clear_only_affects_target_tree(self, test_conn):
        tree_a = "tree-clear-A"
        tree_b = "tree-clear-B"
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(tree_a, "k", "vA", JOB)
            store.put(tree_b, "k", "vB", JOB)
            store.clear(tree_a)
            assert store.get(tree_a, "k") is None
            assert store.get(tree_b, "k") is not None


class TestValueProperty:
    def test_value_deserializes_json(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(TREE, "vprop", {"a": [1, 2]}, JOB)
            entry = store.get(TREE, "vprop")
        assert entry.value == {"a": [1, 2]}

    def test_value_property_with_string(self, test_conn):
        with patch("backend.swarm.shared_memory.get_db", return_value=test_conn):
            from backend.swarm.shared_memory import SharedMemoryStore
            store = SharedMemoryStore()
            store.put(TREE, "vs", "plain string", JOB)
            entry = store.get(TREE, "vs")
        assert entry.value == "plain string"
