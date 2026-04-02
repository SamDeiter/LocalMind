import sqlite3
import pytest
from unittest.mock import patch
from pathlib import Path

from backend.db import init_db, get_db

@pytest.fixture
def mock_db_path(tmp_path):
    """Fixture to mock DB_PATH to a temporary file."""
    db_file = tmp_path / "test_db.sqlite"
    with patch("backend.db.DB_PATH", db_file):
        yield db_file

def test_init_db_creates_tables(mock_db_path):
    """Test that init_db creates the correct tables and schema."""
    init_db()

    # Connect to the temp DB to verify schema
    conn = sqlite3.connect(str(mock_db_path))
    cursor = conn.cursor()

    # Verify tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "conversations" in tables
    assert "messages" in tables

    # Verify conversations schema
    cursor.execute("PRAGMA table_info(conversations)")
    conv_columns = {row[1]: {"type": row[2], "notnull": row[3], "pk": row[5]} for row in cursor.fetchall()}

    assert conv_columns["id"]["type"] == "TEXT"
    assert conv_columns["id"]["pk"] == 1

    assert conv_columns["title"]["type"] == "TEXT"
    assert conv_columns["title"]["notnull"] == 1

    assert conv_columns["model"]["type"] == "TEXT"
    assert conv_columns["model"]["notnull"] == 1

    assert conv_columns["system_prompt"]["type"] == "TEXT"

    assert conv_columns["created_at"]["type"] == "REAL"
    assert conv_columns["created_at"]["notnull"] == 1

    assert conv_columns["updated_at"]["type"] == "REAL"
    assert conv_columns["updated_at"]["notnull"] == 1

    # Verify messages schema
    cursor.execute("PRAGMA table_info(messages)")
    msg_columns = {row[1]: {"type": row[2], "notnull": row[3], "pk": row[5]} for row in cursor.fetchall()}

    assert msg_columns["id"]["type"] == "INTEGER"
    assert msg_columns["id"]["pk"] == 1

    assert msg_columns["conversation_id"]["type"] == "TEXT"
    assert msg_columns["conversation_id"]["notnull"] == 1

    assert msg_columns["role"]["type"] == "TEXT"
    assert msg_columns["role"]["notnull"] == 1

    assert msg_columns["content"]["type"] == "TEXT"
    assert msg_columns["content"]["notnull"] == 1

    assert msg_columns["created_at"]["type"] == "REAL"
    assert msg_columns["created_at"]["notnull"] == 1

    conn.close()

def test_init_db_idempotency(mock_db_path):
    """Test that calling init_db multiple times does not fail."""
    init_db()
    # Call it a second time
    init_db()

    # Verify tables still exist
    conn = sqlite3.connect(str(mock_db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "conversations" in tables
    assert "messages" in tables
    conn.close()

def test_get_db_configuration(mock_db_path):
    """Test that get_db returns a correctly configured connection."""
    # Initialize the DB first so it exists
    init_db()

    conn = get_db()

    # Check that row_factory is set to sqlite3.Row
    assert conn.row_factory == sqlite3.Row

    # Check PRAGMAs
    cursor = conn.cursor()

    cursor.execute("PRAGMA foreign_keys")
    foreign_keys = cursor.fetchone()[0]
    assert foreign_keys == 1

    cursor.execute("PRAGMA journal_mode")
    journal_mode = cursor.fetchone()[0].upper()
    assert journal_mode == "WAL"

    conn.close()
