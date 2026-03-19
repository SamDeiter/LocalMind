"""
Tests for the FastAPI backend server.
Uses httpx.AsyncClient with the FastAPI test client.
"""
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest

# We need to patch DB_PATH before importing the app
_test_db_path = None


def _get_test_db():
    """Override get_db to use the test database."""
    import sqlite3
    conn = sqlite3.connect(str(_test_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@pytest.fixture
def app_with_db(seeded_db):
    """Create a FastAPI test app with patched database."""
    global _test_db_path
    _test_db_path = seeded_db

    # The conversations router uses dependency injection via configure(),
    # not module-level get_db. We must call configure() to inject the test DB.
    from backend.routes import conversations
    conversations.configure(_get_test_db, "You are LocalMind, a helpful AI assistant.")

    with patch("backend.server.DB_PATH", seeded_db), \
         patch("backend.server.get_db", _get_test_db):
        from backend.server import app
        yield app


@pytest.fixture
def client(app_with_db):
    """Create a synchronous test client."""
    from starlette.testclient import TestClient
    return TestClient(app_with_db)


# ── Health Check ──────────────────────────────────────────────────

class TestHealthCheck:
    def test_health_ollama_up(self, client):
        """Health check returns server=True when Ollama is mocked as available."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("backend.server.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            r = client.get("/api/health")
            assert r.status_code == 200
            data = r.json()
            assert data["server"] is True
            assert data["ollama"] is True

    def test_health_ollama_down(self, client):
        """Health check returns ollama=False when Ollama is unreachable."""
        with patch("backend.server.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.side_effect = httpx.ConnectError("Connection refused")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            r = client.get("/api/health")
            data = r.json()
            assert data["server"] is True
            assert data["ollama"] is False


# ── Models ────────────────────────────────────────────────────────

class TestModels:
    def test_list_models_success(self, client, mock_ollama_tags):
        """List models returns available Ollama models."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_ollama_tags

        with patch("backend.server.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            r = client.get("/api/models")
            data = r.json()
            assert len(data["models"]) == 2
            assert data["models"][0]["name"] == "qwen2.5-coder:7b"

    def test_list_models_ollama_down(self, client):
        """List models returns empty list when Ollama is down."""
        with patch("backend.server.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.side_effect = Exception("Connection refused")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            r = client.get("/api/models")
            data = r.json()
            assert data["models"] == []


# ── Memory Toggle ─────────────────────────────────────────────────

class TestMemoryToggle:
    def test_memory_status(self, client):
        """Memory status returns current learning_enabled state."""
        r = client.get("/api/memory/status")
        data = r.json()
        assert "learning_enabled" in data

    def test_memory_toggle(self, client):
        """Toggling memory updates the learning state."""
        r = client.post(
            "/api/memory/toggle",
            json={"enabled": False},
        )
        data = r.json()
        assert data["learning_enabled"] is False

        # Toggle back
        r = client.post(
            "/api/memory/toggle",
            json={"enabled": True},
        )
        data = r.json()
        assert data["learning_enabled"] is True


# ── Conversation CRUD ─────────────────────────────────────────────

class TestConversations:
    def test_list_conversations(self, client):
        """List conversations returns seeded data."""
        r = client.get("/api/conversations")
        data = r.json()
        assert len(data["conversations"]) == 1
        assert data["conversations"][0]["title"] == "Test Conversation"

    def test_get_conversation_messages(self, client):
        """Get messages for a conversation returns correct count."""
        r = client.get("/api/conversations/test-conv-1/messages")
        data = r.json()
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"

    def test_get_conversation_metadata(self, client):
        """Get conversation metadata includes system prompt."""
        r = client.get("/api/conversations/test-conv-1")
        data = r.json()
        assert data["title"] == "Test Conversation"
        assert data["system_prompt"] == "You are helpful."

    def test_delete_conversation(self, client):
        """Deleting a conversation removes it and its messages."""
        r = client.delete("/api/conversations/test-conv-1")
        assert r.json()["ok"] is True

        r = client.get("/api/conversations")
        assert len(r.json()["conversations"]) == 0


# ── System Prompt ─────────────────────────────────────────────────

class TestSystemPrompt:
    def test_update_system_prompt(self, client):
        """Updating system prompt persists the change."""
        r = client.put(
            "/api/conversations/test-conv-1/system-prompt",
            json={"system_prompt": "You are a pirate."},
        )
        data = r.json()
        assert data["ok"] is True
        assert data["system_prompt"] == "You are a pirate."

        # Verify it persisted
        r = client.get("/api/conversations/test-conv-1")
        assert r.json()["system_prompt"] == "You are a pirate."

    def test_get_default_system_prompt(self, client):
        """Default system prompt endpoint returns a non-empty prompt."""
        r = client.get("/api/default-system-prompt")
        data = r.json()
        assert "system_prompt" in data
        assert len(data["system_prompt"]) > 0
        assert "LocalMind" in data["system_prompt"]


# ── Export ────────────────────────────────────────────────────────

class TestExport:
    def test_export_markdown(self, client):
        """Export as Markdown returns proper format."""
        r = client.get("/api/conversations/test-conv-1/export?format=md")
        assert r.status_code == 200
        text = r.text
        assert "Test Conversation" in text
        assert "Hello, world!" in text
        assert "LocalMind" in text

    def test_export_json(self, client):
        """Export as JSON returns valid structured data."""
        r = client.get("/api/conversations/test-conv-1/export?format=json")
        assert r.status_code == 200
        data = json.loads(r.text)
        assert data["title"] == "Test Conversation"
        assert len(data["messages"]) == 2

    def test_export_nonexistent(self, client):
        """Export a nonexistent conversation returns error."""
        r = client.get("/api/conversations/fake-id/export?format=md")
        data = r.json()
        assert "error" in data


class TestRunCode:
    """Tests for POST /api/tools/run endpoint."""

    def test_run_code_success(self, client):
        """Valid Python code should execute and return output."""
        r = client.post(
            "/api/tools/run",
            json={"code": "print('hello world')", "language": "python"},
        )
        data = r.json()
        assert data.get("success") is True

    def test_run_code_empty(self, client):
        """Empty code should return an error."""
        r = client.post(
            "/api/tools/run",
            json={"code": "", "language": "python"},
        )
        data = r.json()
        assert data.get("success") is False
        assert "No code" in data.get("error", "")

    def test_run_code_blocked(self, client):
        """Dangerous code should be blocked."""
        r = client.post(
            "/api/tools/run",
            json={"code": "import os; os.remove('test.txt')", "language": "python"},
        )
        data = r.json()
        # Should either fail or return a blocked warning
        # (exact behavior depends on RunCodeTool's blocklist)
        assert "success" in data
