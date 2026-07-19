import os
import sqlite3
import time
import pytest
import asyncio
from unittest.mock import patch, MagicMock

from backend.db import init_db
from backend.routes import conversations

@pytest.fixture
def test_db_path(tmp_path):
    """Fixture to create and initialize a temporary database."""
    db_file = tmp_path / "test_conversations.db"
    # Mock DB_PATH in backend.db
    with patch("backend.db.DB_PATH", db_file):
        init_db()
        yield db_file

@pytest.fixture
def db_conn_factory(test_db_path):
    """Fixture that returns a connection factory for the test DB."""
    def _get_db():
        conn = sqlite3.connect(str(test_db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    return _get_db

@pytest.mark.asyncio
async def test_conversations_endpoints(test_db_path, db_conn_factory):
    # Configure the router
    conversations.configure(db_conn_factory, "Default system prompt")

    # 1. Insert seed data
    db = db_conn_factory()
    now = time.time()
    db.execute(
        "INSERT INTO conversations (id, title, model, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("conv1", "First conversation", "test-model", "System prompt 1", now, now)
    )
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("conv1", "user", "Hello assistant", now)
    )
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("conv1", "assistant", "Hello user", now + 1)
    )
    db.commit()
    db.close()

    # 2. Test list_conversations
    res = await conversations.list_conversations()
    assert "conversations" in res
    assert len(res["conversations"]) == 1
    assert res["conversations"][0]["id"] == "conv1"
    assert res["conversations"][0]["title"] == "First conversation"

    # 3. Test get_conversation_messages
    res_msgs = await conversations.get_conversation_messages("conv1")
    assert "messages" in res_msgs
    assert len(res_msgs["messages"]) == 2
    assert res_msgs["messages"][0]["role"] == "user"
    assert res_msgs["messages"][0]["content"] == "Hello assistant"

    # 4. Test get_conversation
    res_conv = await conversations.get_conversation("conv1")
    assert res_conv["id"] == "conv1"
    assert res_conv["system_prompt"] == "System prompt 1"

    # 5. Test get_conversation not found
    res_not_found = await conversations.get_conversation("nonexistent")
    assert "error" in res_not_found

    # 6. Test update_system_prompt
    mock_request = MagicMock()
    async def mock_json():
        return {"system_prompt": "New updated system prompt"}
    mock_request.json = mock_json

    res_update = await conversations.update_system_prompt("conv1", mock_request)
    assert res_update["ok"] is True
    assert res_update["system_prompt"] == "New updated system prompt"

    # Verify updated prompt in DB
    res_conv_updated = await conversations.get_conversation("conv1")
    assert res_conv_updated["system_prompt"] == "New updated system prompt"

    # 7. Test export_conversation (Markdown format)
    res_export_md = await conversations.export_conversation("conv1", format="md")
    assert res_export_md.media_type == "text/markdown"
    assert b"First conversation" in res_export_md.body
    assert b"Hello assistant" in res_export_md.body

    # 8. Test export_conversation (JSON format)
    res_export_json = await conversations.export_conversation("conv1", format="json")
    assert res_export_json.media_type == "application/json"
    assert b"First conversation" in res_export_json.body

    # 9. Test delete_conversation
    res_del = await conversations.delete_conversation("conv1")
    assert res_del["ok"] is True

    # Verify deleted
    res_list_after = await conversations.list_conversations()
    assert len(res_list_after["conversations"]) == 0
