"""Tests for AgentMessageBus (backend.swarm.messaging)."""

import json
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


def _insert_delegation(conn, parent_id, child_id):
    """Helper to create a delegation record so tree-root walking works."""
    conn.execute(
        "INSERT INTO job_delegation (id, parent_job_id, child_job_id, status, created_at) "
        "VALUES (?, ?, ?, 'active', '2026-01-01')",
        (f"del-{child_id}", parent_id, child_id),
    )
    conn.commit()


@pytest.fixture
def test_conn():
    conn = _init_test_db()
    yield conn
    conn.real_close()


TREE = "tree-root-msg"
JOB_A = "agent-a"
JOB_B = "agent-b"
JOB_C = "agent-c"


class TestSend:
    def test_creates_message(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            msg = bus.send(TREE, JOB_A, {"hello": "world"}, to_job_id=JOB_B)
        assert msg.id
        assert msg.tree_root_job_id == TREE
        assert msg.from_job_id == JOB_A
        assert msg.to_job_id == JOB_B
        assert msg.status == "pending"
        assert msg.message_type == "data"
        assert json.loads(msg.body_json) == {"hello": "world"}

    def test_broadcast_has_null_to_job_id(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            msg = bus.send(TREE, JOB_A, "broadcast payload")
        assert msg.to_job_id is None

    def test_send_with_subject(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            msg = bus.send(TREE, JOB_A, "x", subject="status-update")
        assert msg.subject == "status-update"

    def test_send_with_message_type(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            msg = bus.send(TREE, JOB_A, "x", message_type="signal")
        assert msg.message_type == "signal"


class TestReceive:
    def test_gets_messages_for_a_job(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            bus.send(TREE, JOB_A, "msg1", to_job_id=JOB_B)
            bus.send(TREE, JOB_A, "msg2", to_job_id=JOB_B)
            messages = bus.receive(JOB_B, include_broadcasts=False)
        assert len(messages) == 2

    def test_receive_includes_broadcasts(self, test_conn):
        # Set up delegation so the bus can resolve tree roots for JOB_B
        _insert_delegation(test_conn, TREE, JOB_B)

        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            bus.send(TREE, JOB_A, "direct", to_job_id=JOB_B)
            bus.send(TREE, JOB_A, "broadcast")  # broadcast (to_job_id=None)
            messages = bus.receive(JOB_B, include_broadcasts=True)
        assert len(messages) == 2

    def test_receive_excludes_broadcasts_when_false(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            bus.send(TREE, JOB_A, "direct", to_job_id=JOB_B)
            bus.send(TREE, JOB_A, "broadcast")  # broadcast
            messages = bus.receive(JOB_B, include_broadcasts=False)
        assert len(messages) == 1
        assert json.loads(messages[0].body_json) == "direct"

    def test_receive_marks_messages_as_delivered(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            bus.send(TREE, JOB_A, "data", to_job_id=JOB_B)
            bus.receive(JOB_B, include_broadcasts=False)

        row = test_conn.execute(
            "SELECT status FROM agent_messages WHERE to_job_id = ?", (JOB_B,)
        ).fetchone()
        assert row["status"] == "delivered"

    def test_receive_does_not_return_own_messages(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            bus.send(TREE, JOB_A, "self-talk", to_job_id=JOB_A)
            messages = bus.receive(JOB_A, include_broadcasts=False)
        assert messages == []

    def test_receive_returns_empty_when_none_pending(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            messages = bus.receive(JOB_B, include_broadcasts=False)
        assert messages == []


class TestMarkRead:
    def test_updates_status_and_read_at(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            msg = bus.send(TREE, JOB_A, "read me", to_job_id=JOB_B)
            bus.mark_read(msg.id)

        row = test_conn.execute(
            "SELECT status, read_at FROM agent_messages WHERE id = ?", (msg.id,)
        ).fetchone()
        assert row["status"] == "read"
        assert row["read_at"] is not None

    def test_mark_read_idempotent(self, test_conn):
        """Marking an already-read message as read does not error."""
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            msg = bus.send(TREE, JOB_A, "x", to_job_id=JOB_B)
            bus.mark_read(msg.id)
            bus.mark_read(msg.id)  # should not raise


class TestSendRequest:
    def test_sets_message_type_to_request(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            msg = bus.send_request(TREE, JOB_A, JOB_B, "help", {"q": "how?"})
        assert msg.message_type == "request"
        assert msg.to_job_id == JOB_B
        assert msg.subject == "help"


class TestSendResponse:
    def test_references_the_request(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            req = bus.send_request(TREE, JOB_A, JOB_B, "help", {"q": "how?"})
            resp = bus.send_response(req.id, JOB_B, {"answer": "like this"})

        assert resp.message_type == "response"
        assert resp.subject == req.id  # subject references the request
        assert resp.to_job_id == JOB_A  # sent back to original requester

    def test_send_response_for_missing_request_raises(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            with pytest.raises(ValueError, match="not found"):
                bus.send_response("nonexistent-req-id", JOB_B, "nope")


class TestGetMessagesForTree:
    def test_returns_all_messages(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            bus.send(TREE, JOB_A, "m1", to_job_id=JOB_B)
            bus.send(TREE, JOB_B, "m2", to_job_id=JOB_A)
            bus.send(TREE, JOB_A, "broadcast")
            messages = bus.get_messages_for_tree(TREE)
        assert len(messages) == 3

    def test_returns_empty_for_unknown_tree(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            assert bus.get_messages_for_tree("unknown-tree") == []

    def test_scoped_to_tree(self, test_conn):
        """Messages from tree X should not appear in tree Y results."""
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            bus.send("tree-X", JOB_A, "x-msg", to_job_id=JOB_B)
            bus.send("tree-Y", JOB_A, "y-msg", to_job_id=JOB_B)
            x_msgs = bus.get_messages_for_tree("tree-X")
            y_msgs = bus.get_messages_for_tree("tree-Y")
        assert len(x_msgs) == 1
        assert len(y_msgs) == 1
        assert json.loads(x_msgs[0].body_json) == "x-msg"
        assert json.loads(y_msgs[0].body_json) == "y-msg"


class TestCleanupExpired:
    def test_marks_old_messages_as_expired(self, test_conn):
        old_time = (
            datetime.now(timezone.utc) - timedelta(seconds=7200)
        ).isoformat()
        test_conn.execute(
            "INSERT INTO agent_messages "
            "(id, tree_root_job_id, from_job_id, to_job_id, message_type, "
            "subject, body_json, status, created_at, read_at) "
            "VALUES ('old-1', ?, ?, ?, 'data', NULL, '\"old\"', 'pending', ?, NULL)",
            (TREE, JOB_A, JOB_B, old_time),
        )
        test_conn.commit()

        with patch("backend.swarm.messaging.get_db", return_value=test_conn), \
             patch("backend.swarm.messaging.SWARM_MESSAGE_TTL_SEC", 3600):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            count = bus.cleanup_expired()
        assert count == 1

        row = test_conn.execute(
            "SELECT status FROM agent_messages WHERE id = 'old-1'"
        ).fetchone()
        assert row["status"] == "expired"

    def test_does_not_expire_recent_messages(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            bus.send(TREE, JOB_A, "fresh", to_job_id=JOB_B)
            count = bus.cleanup_expired()
        assert count == 0


class TestBodyProperty:
    def test_body_deserializes_json(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            msg = bus.send(TREE, JOB_A, {"key": [1, 2, 3]}, to_job_id=JOB_B)
        assert msg.body == {"key": [1, 2, 3]}

    def test_body_with_string_payload(self, test_conn):
        with patch("backend.swarm.messaging.get_db", return_value=test_conn):
            from backend.swarm.messaging import AgentMessageBus
            bus = AgentMessageBus()
            msg = bus.send(TREE, JOB_A, "simple string")
        assert msg.body == "simple string"
