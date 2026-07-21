import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from backend.logic.chat_service import ChatService
from backend.routes import conversations


def _get_test_db_factory(db_path):
    def factory():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    return factory


@pytest.mark.asyncio
async def test_conversations_routes_offloaded(seeded_db):
    # Configure the conversations module to use our test database
    get_db_func = _get_test_db_factory(seeded_db)
    conversations.configure(get_db_func, "You are a helpful assistant.")

    # 1. Test list_conversations
    result = await conversations.list_conversations()
    assert "conversations" in result
    assert len(result["conversations"]) == 1
    assert result["conversations"][0]["id"] == "test-conv-1"

    # 2. Test get_conversation_messages
    result = await conversations.get_conversation_messages("test-conv-1")
    assert "messages" in result
    assert len(result["messages"]) == 2
    assert result["messages"][0]["role"] == "user"

    # 3. Test get_conversation
    result = await conversations.get_conversation("test-conv-1")
    assert result["id"] == "test-conv-1"
    assert result["title"] == "Test Conversation"

    # 4. Test update_system_prompt
    mock_request = MagicMock()
    async def mock_json():
        return {"system_prompt": "New system prompt"}
    mock_request.json = mock_json

    result = await conversations.update_system_prompt("test-conv-1", mock_request)
    assert result["ok"] is True
    assert result["system_prompt"] == "New system prompt"

    # Verify updated prompt in DB
    result = await conversations.get_conversation("test-conv-1")
    assert result["system_prompt"] == "New system prompt"

    # 5. Test export_conversation (json)
    response = await conversations.export_conversation("test-conv-1", format="json")
    assert response.media_type == "application/json"

    # 6. Test export_conversation (md)
    response = await conversations.export_conversation("test-conv-1", format="md")
    assert response.media_type == "text/markdown"

    # 7. Test delete_conversation
    result = await conversations.delete_conversation("test-conv-1")
    assert result["ok"] is True

    # Verify it is deleted
    result = await conversations.list_conversations()
    assert len(result["conversations"]) == 0


@pytest.mark.asyncio
async def test_chat_service_offloaded(seeded_db):
    db_factory = _get_test_db_factory(seeded_db)

    # Instantiate ChatService with mocked registry/dependencies so __init__ doesn't raise
    with patch("backend.logic.chat_service.LLMClient"), \
         patch("backend.logic.chat_service.ContextBuilder"), \
         patch("backend.logic.chat_service.ToolDispatcher"), \
         patch("backend.logic.chat_service.TokenManager"), \
         patch("backend.logic.chat_service.Summarizer"), \
         patch("backend.logic.chat_service.ReflectionService"):

        chat_service = ChatService(db_factory=db_factory, registry=MagicMock())

    # 1. Test _get_history
    history = await chat_service._get_history("test-conv-1")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"

    # 2. Test _save_msg
    await chat_service._save_msg("test-conv-1", "user", "Next message")
    history = await chat_service._get_history("test-conv-1")
    assert len(history) == 3
    contents = [m["content"] for m in history]
    assert "Next message" in contents

    # 3. Test _create_conversation
    cid = await chat_service._create_conversation("New chat title", "qwen2.5-coder:7b", "Custom prompt")
    assert cid is not None

    # Get history of new chat
    history = await chat_service._get_history(cid)
    assert len(history) == 0
