import sqlite3
import pytest
from unittest.mock import MagicMock
from backend.logic.chat_service import ChatService


@pytest.fixture
def chat_service(seeded_db):
    # Create a db connection factory for the seeded test db
    def db_factory():
        conn = sqlite3.connect(str(seeded_db))
        conn.row_factory = sqlite3.Row
        return conn

    # Minimal mocks for registry, metacog_controller
    registry = MagicMock()
    metacog_controller = MagicMock()

    service = ChatService(
        db_factory=db_factory,
        registry=registry,
        metacog_controller=metacog_controller,
    )
    return service


@pytest.mark.asyncio
async def test_create_conversation(chat_service, seeded_db):
    message = "How to write a thread-safe SQLite connection?"
    model = "test-model-1"
    system_prompt = "You are a database expert."

    # Call the async _create_conversation method
    cid = await chat_service._create_conversation(message, model, system_prompt)

    # Verify conversation is created in database
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    conn.close()

    assert row is not None
    assert row["id"] == cid
    assert row["model"] == model
    assert row["system_prompt"] == system_prompt
    assert row["title"] == message


@pytest.mark.asyncio
async def test_get_history(chat_service):
    # The seeded DB has a conversation "test-conv-1" with 2 messages
    history = await chat_service._get_history("test-conv-1")

    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Hello, world!"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "Hi there! How can I help you today?"


@pytest.mark.asyncio
async def test_save_msg(chat_service, seeded_db):
    conversation_id = "test-conv-1"
    role = "user"
    content = "This is a test message to save"

    await chat_service._save_msg(conversation_id, role, content)

    # Verify message is saved
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conversation_id,)
    ).fetchall()
    conn.close()

    # Seeded DB had 2 messages, now should be 3
    assert len(rows) == 3
    saved_msg = next((r for r in rows if r["content"] == content), None)
    assert saved_msg is not None
    assert saved_msg["role"] == role
