"""
Shared test fixtures for LocalMind test suite.
"""
import os
import sys
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# Add project root to path so imports work
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


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
    import time
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
