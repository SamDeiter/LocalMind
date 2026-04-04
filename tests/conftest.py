"""
Shared test fixtures for LocalMind test suite.
"""
import os
import sys
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# Add project root to path so imports work
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary SQLite database with the schema."""
    db_path = tmp_path / "test_conversations.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            system_prompt TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def seeded_db(temp_db):
    """Create a temp DB with a sample conversation and messages."""
    conn = sqlite3.connect(str(temp_db))
    now = time.time()
    conn.execute(
        "INSERT INTO conversations (id, title, model, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("test-conv-1", "Test Conversation", "qwen2.5-coder:7b", "You are helpful.", now, now),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("test-conv-1", "user", "Hello, world!", now),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("test-conv-1", "assistant", "Hi there! How can I help you today?", now + 1),
    )
    conn.commit()
    conn.close()
    return temp_db


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory for file tool tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Create some test files
    (workspace / "hello.py").write_text("print('hello world')\n")
    (workspace / "data.json").write_text('{"key": "value"}\n')
    sub = workspace / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested file content\n")
    return workspace


@pytest.fixture
def mock_ollama_tags():
    """Mock response for Ollama /api/tags endpoint."""
    return {
        "models": [
            {"name": "qwen2.5-coder:7b", "size": 4700000000},
            {"name": "gemma3:4b", "size": 3100000000},
        ]
    }


# ---------------------------------------------------------------------------
# Helpers used by both test_server.py and test_api_routes.py
# ---------------------------------------------------------------------------

def make_test_db_factory(db_path):
    """Return a get_db callable that connects to *db_path*."""
    def _get_test_db():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    return _get_test_db


# ---------------------------------------------------------------------------
# Mock autonomy engine (lightweight stand-in for AutonomyEngine)
# ---------------------------------------------------------------------------

def _build_mock_autonomy_engine():
    """Build a MagicMock that satisfies the contract used by route modules."""
    import asyncio

    engine = MagicMock()
    engine.mode = "supervised"
    engine.enabled = True
    engine._start_time = time.time()
    engine._recent_events = []

    engine.get_status.return_value = {
        "enabled": True,
        "mode": "supervised",
        "started_at": engine._start_time,
        "current_activity": None,
        "health_check": {"last_run": None, "ollama_ok": False, "model_loaded": False},
        "reflection": {"last_run": None, "proposals_logged": 0},
        "execution": {"last_run": None, "proposals_executed": 0, "last_result": None},
        "auto_test": {"last_run": None, "passed": 0, "failed": 0},
        "research": {"last_run": 0},
        "agent_loop": {"active": False, "current_agent": None},
    }

    engine.toggle.return_value = False  # returns new enabled state
    engine.set_mode.side_effect = lambda m: m  # echo back
    engine.trigger_reflection.return_value = None
    engine.trigger_execution.return_value = None

    engine.list_proposals.return_value = []
    engine.approve_proposal.return_value = {"id": "p1", "status": "approved"}
    engine.deny_proposal.return_value = {"id": "p1", "status": "denied"}
    engine.retry_proposal.return_value = {"id": "p1", "status": "approved"}

    # Priority queue mock
    pq = MagicMock()
    pq.list_all.return_value = []
    pq.add.return_value = {"id": "prio-1", "description": "test", "priority": "high"}
    pq.remove.return_value = True
    engine.priority_queue = pq

    # Activity subscription (SSE)
    engine.subscribe_activity.return_value = asyncio.Queue()
    engine.unsubscribe_activity.return_value = None

    # Reset
    engine.reset_engine.return_value = {"archived": 0, "retried": 0, "mode": "autonomous"}

    # Swarm coordinator attribute -- default None (no swarm)
    engine.coordinator = None

    return engine


@pytest.fixture
def mock_engine():
    """A lightweight mock AutonomyEngine usable by any test module."""
    return _build_mock_autonomy_engine()


# ---------------------------------------------------------------------------
# Mock swarm coordinator
# ---------------------------------------------------------------------------

def _build_mock_coordinator():
    """Build a MagicMock that satisfies HiveCoordinator's API contract."""
    coord = MagicMock()
    coord.get_status.return_value = {
        "running": True,
        "uptime": 120,
        "timestamp": "2026-04-03T12:00:00+00:00",
        "queue": {"pending": 0, "in_progress": 0, "completed": 5, "failed": 0, "peak_depth": 3},
        "recent_improvements": [],
        "agents": {"total": 4, "active": 2, "idle": 2},
        "gpu": {"in_use": 1, "available": 2, "max": 3},
        "metrics": {"tasks_processed": 10, "tasks_failed": 0},
    }
    coord.get_agent_details.return_value = [
        {"id": "scanner-0", "type": "cpu", "is_running": True, "current_task": None},
        {"id": "llm-0", "type": "gpu", "is_running": False, "current_task": None},
    ]
    coord.submit_parallel_scan.return_value = ["task-1", "task-2"]
    coord.submit_research.return_value = "task-r1"
    coord.submit_test.return_value = "task-t1"
    return coord


@pytest.fixture
def mock_coordinator():
    """A lightweight mock HiveCoordinator."""
    return _build_mock_coordinator()
