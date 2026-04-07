"""
Tests for memory tools — SaveMemoryTool, RecallMemoriesTool, get_recent_memories.

Uses mocked retriever/store so tests run without a real SQLite FTS5 database.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Helpers ──────────────────────────────────────────────────────

def _reset_memory_globals():
    """Reset the module-level singletons so each test starts clean."""
    import backend.tools.memory as mem
    mem._fts_store = None
    mem._retriever = None
    mem._learning_enabled = True


@pytest.fixture(autouse=True)
def clean_memory_state():
    """Ensure memory module globals are reset before and after every test."""
    _reset_memory_globals()
    yield
    _reset_memory_globals()


def _make_memory(content="fact", category="semantic", subcategory="fact",
                 relevance=0.95, created_at="2025-01-01"):
    """Build a mock memory object with the attributes the tools expect."""
    m = MagicMock()
    m.content = content
    m.category = category
    m.subcategory = subcategory
    m.relevance_score = relevance
    m.created_at = created_at
    return m


# ── SaveMemoryTool ──────────────────────────────────────────────

class TestSaveMemoryTool:
    @pytest.mark.asyncio
    @patch("backend.tools.memory._get_retriever")
    async def test_save_valid_content(self, mock_get_retriever):
        from backend.tools.memory import SaveMemoryTool

        mock_retriever = MagicMock()
        mock_retriever.save_from_conversation.return_value = "mem-123"
        mock_get_retriever.return_value = mock_retriever

        tool = SaveMemoryTool()
        result = await tool.execute(content="User likes dark mode", category="preference")

        assert result["success"] is True
        assert "mem-123" == result["id"]
        assert "Memory saved" in result["result"]
        mock_retriever.save_from_conversation.assert_called_once_with(
            content="User likes dark mode",
            category="semantic",
            subcategory="preference",
            source="backend_chat",
        )

    @pytest.mark.asyncio
    async def test_save_empty_content_returns_error(self):
        from backend.tools.memory import SaveMemoryTool

        tool = SaveMemoryTool()
        result = await tool.execute(content="   ", category="fact")

        assert result["success"] is False
        assert "empty" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_save_when_learning_disabled(self):
        from backend.tools.memory import SaveMemoryTool, set_learning_enabled

        set_learning_enabled(False)
        tool = SaveMemoryTool()
        result = await tool.execute(content="Important fact", category="fact")

        assert result["success"] is True
        assert "paused" in result["result"].lower()

    @pytest.mark.asyncio
    @patch("backend.tools.memory._get_retriever", return_value=None)
    async def test_save_when_retriever_unavailable(self, _):
        from backend.tools.memory import SaveMemoryTool

        tool = SaveMemoryTool()
        result = await tool.execute(content="Something", category="fact")

        assert result["success"] is False
        assert "unavailable" in result["error"].lower()


# ── RecallMemoriesTool ──────────────────────────────────────────

class TestRecallMemoriesTool:
    @pytest.mark.asyncio
    async def test_recall_empty_query_returns_error(self):
        from backend.tools.memory import RecallMemoriesTool

        tool = RecallMemoriesTool()
        result = await tool.execute(query="  ")

        assert result["success"] is False
        assert "empty" in result["error"].lower()

    @pytest.mark.asyncio
    @patch("backend.tools.memory._get_retriever", return_value=None)
    async def test_recall_when_retriever_unavailable(self, _):
        from backend.tools.memory import RecallMemoriesTool

        tool = RecallMemoriesTool()
        result = await tool.execute(query="dark mode")

        assert result["success"] is True
        assert result["memories"] == []
        assert "No relevant memories" in result["result"]

    @pytest.mark.asyncio
    @patch("backend.tools.memory._get_retriever")
    async def test_recall_returns_formatted_memories(self, mock_get_retriever):
        from backend.tools.memory import RecallMemoriesTool

        mock_retriever = MagicMock()
        mock_retriever.retrieve_for_query.return_value = [
            _make_memory("User prefers dark mode", subcategory="preference", relevance=0.9),
            _make_memory("User is a Python dev", subcategory="fact", relevance=0.7),
        ]
        mock_get_retriever.return_value = mock_retriever

        tool = RecallMemoriesTool()
        result = await tool.execute(query="preferences")

        assert result["success"] is True
        assert len(result["memories"]) == 2
        assert result["memories"][0]["content"] == "User prefers dark mode"
        assert result["memories"][0]["source"] == "fts5"

    @pytest.mark.asyncio
    @patch("backend.tools.memory._get_retriever")
    async def test_recall_empty_results(self, mock_get_retriever):
        from backend.tools.memory import RecallMemoriesTool

        mock_retriever = MagicMock()
        mock_retriever.retrieve_for_query.return_value = []
        mock_get_retriever.return_value = mock_retriever

        tool = RecallMemoriesTool()
        result = await tool.execute(query="nonexistent topic")

        assert result["success"] is True
        assert result["memories"] == []


# ── get_recent_memories ─────────────────────────────────────────

class TestGetRecentMemories:
    @patch("backend.tools.memory._get_fts_store", return_value=None)
    def test_store_unavailable_returns_empty_list(self, _):
        from backend.tools.memory import get_recent_memories

        result = get_recent_memories(n=5)
        assert result == []

    @patch("backend.tools.memory._get_fts_store")
    def test_returns_formatted_memories(self, mock_get_store):
        from backend.tools.memory import get_recent_memories

        mock_store = MagicMock()
        mock_store.get_recent.return_value = [
            _make_memory("Fact one", subcategory="fact"),
            _make_memory("Fact two", subcategory=None, category="general"),
        ]
        mock_get_store.return_value = mock_store

        result = get_recent_memories(n=2)
        assert len(result) == 2
        assert result[0]["content"] == "Fact one"
        assert result[0]["category"] == "fact"
        # When subcategory is None, falls back to category
        assert result[1]["category"] == "general"

    @patch("backend.tools.memory._get_fts_store")
    def test_exception_returns_empty_list(self, mock_get_store):
        from backend.tools.memory import get_recent_memories

        mock_store = MagicMock()
        mock_store.get_recent.side_effect = RuntimeError("DB locked")
        mock_get_store.return_value = mock_store

        result = get_recent_memories(n=5)
        assert result == []
