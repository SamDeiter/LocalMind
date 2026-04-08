"""
Tests for the AI Internet Learning service (backend/autonomy/skill_learner.py).

Since the implementation may not exist yet, tests define the expected contract
and mock all external dependencies: Ollama LLM, web search, ToolGenerator.
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ollama_response(content: str, status_code: int = 200):
    """Build a mock httpx response that looks like an Ollama /api/chat reply."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"message": {"content": content}}
    resp.text = content
    return resp


def _async_client_mock(response):
    client = AsyncMock()
    client.post.return_value = response
    return client


def _patch_httpx(module_path: str, response):
    """Patch httpx.AsyncClient at the given module path."""
    client = _async_client_mock(response)
    patcher = patch(f"{module_path}.httpx.AsyncClient")
    mock_cls = patcher.start()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return patcher, client


MOD = "backend.autonomy.skill_learner"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    """Redirect JOURNAL_PATH to a temp directory."""
    journal_path = tmp_path / "learning_journal.json"
    monkeypatch.setattr(f"{MOD}.JOURNAL_PATH", journal_path)
    return journal_path


@pytest.fixture
def learner(tmp_journal):
    """Return a fresh SkillLearner with isolated journal path."""
    from backend.autonomy.skill_learner import SkillLearner

    return SkillLearner()


# ---------------------------------------------------------------------------
# TestJournal
# ---------------------------------------------------------------------------


class TestJournal:
    def test_journal_initially_empty(self, learner):
        """Journal starts empty when no file exists."""
        journal = learner.get_journal()
        assert isinstance(journal, list)
        assert len(journal) == 0

    def test_journal_save_and_load(self, learner, tmp_journal):
        """Journal entries persist to disk and reload."""
        entry = {
            "topic": "web scraping",
            "summary": "Learned about BeautifulSoup",
            "applied": False,
            "timestamp": time.time(),
        }
        learner._journal.append(entry)
        learner._save_journal()

        # Should be persisted
        assert tmp_journal.exists()
        raw = json.loads(tmp_journal.read_text(encoding="utf-8"))
        assert len(raw) == 1
        assert raw[0]["topic"] == "web scraping"

        # Should be accessible via getter
        journal = learner.get_journal()
        assert len(journal) == 1
        assert journal[0]["topic"] == "web scraping"

    def test_journal_multiple_entries(self, learner, tmp_journal):
        """Multiple entries accumulate in the journal."""
        for i in range(3):
            learner._journal.append({
                "topic": f"topic_{i}",
                "summary": f"summary_{i}",
                "applied": i % 2 == 0,
                "timestamp": time.time(),
            })
        learner._save_journal()

        journal = learner.get_journal()
        assert len(journal) == 3

    def test_journal_loads_existing_file(self, tmp_journal):
        """Journal should load pre-existing entries from disk at init."""
        existing = [{"topic": "old", "summary": "old entry", "applied": True, "timestamp": 1.0}]
        tmp_journal.write_text(json.dumps(existing), encoding="utf-8")

        from backend.autonomy.skill_learner import SkillLearner

        learner = SkillLearner()
        assert len(learner.get_journal()) == 1
        assert learner.get_journal()[0]["topic"] == "old"


# ---------------------------------------------------------------------------
# TestLearningStats
# ---------------------------------------------------------------------------


class TestLearningStats:
    def test_stats_empty(self, learner):
        """Stats should work with empty journal."""
        stats = learner.get_stats()
        assert isinstance(stats, dict)
        assert stats.get("total_entries", 0) == 0
        assert stats.get("applied_count", 0) == 0

    def test_stats_counts_applied(self, learner):
        """Stats should count applied entries correctly."""
        learner._journal.append({"topic": "a", "summary": "s", "applied": True, "timestamp": 1.0})
        learner._journal.append({"topic": "b", "summary": "s", "applied": False, "timestamp": 2.0})
        learner._journal.append({"topic": "c", "summary": "s", "applied": True, "timestamp": 3.0})

        stats = learner.get_stats()
        assert stats["total_entries"] == 3
        assert stats["applied_count"] == 2


# ---------------------------------------------------------------------------
# TestGapIdentification
# ---------------------------------------------------------------------------


class TestGapIdentification:
    @pytest.mark.asyncio
    async def test_identifies_gap(self, learner):
        """Should identify a learning gap by calling the LLM."""
        patcher, client = _patch_httpx(MOD, _ollama_response('"data visualization with matplotlib"'))
        try:
            topic = await learner._identify_learning_gap()
            assert isinstance(topic, str)
            assert len(topic) > 0
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_identifies_gap_fallback(self, learner):
        """Should return a fallback topic when LLM fails."""
        patcher, client = _patch_httpx(MOD, _ollama_response("", status_code=500))
        try:
            topic = await learner._identify_learning_gap()
            assert isinstance(topic, str)
            assert len(topic) > 0
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# TestWebResearch
# ---------------------------------------------------------------------------


class TestWebResearch:
    @pytest.mark.asyncio
    async def test_researches_topic(self, learner):
        """Should search the web for the topic and return findings."""
        mock_response = {
            "success": True,
            "results": [
                {"title": "Matplotlib Guide", "snippet": "How to plot data", "url": "https://example.com/plot"}
            ],
        }

        with patch("backend.tools.web_search.WebSearchTool") as MockWST:
            instance = AsyncMock()
            instance.execute.return_value = mock_response
            MockWST.return_value = instance

            findings = await learner._research_topic("matplotlib plotting")
            assert isinstance(findings, list)
            assert len(findings) > 0
            assert findings[0]["title"] == "Matplotlib Guide"

    @pytest.mark.asyncio
    async def test_handles_search_failure(self, learner):
        """Should handle web search failures gracefully."""
        with patch("backend.tools.web_search.WebSearchTool") as MockWST:
            instance = AsyncMock()
            instance.execute.side_effect = RuntimeError("Network timeout")
            MockWST.return_value = instance

            findings = await learner._research_topic("anything")
            assert isinstance(findings, list)
            # Empty results on failure, no crash
            assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_handles_no_results(self, learner):
        """Should handle a successful search that returns no results."""
        with patch("backend.tools.web_search.WebSearchTool") as MockWST:
            instance = AsyncMock()
            instance.execute.return_value = {"success": True, "results": []}
            MockWST.return_value = instance

            findings = await learner._research_topic("very obscure topic")
            assert isinstance(findings, list)
            assert len(findings) == 0


# ---------------------------------------------------------------------------
# TestSynthesis
# ---------------------------------------------------------------------------


class TestSynthesis:
    @pytest.mark.asyncio
    async def test_synthesizes_findings(self, learner):
        """Should produce a summary from web findings."""
        findings = [
            {"title": "Guide", "snippet": "Step by step tutorial", "url": "https://example.com"}
        ]
        summary_text = "SUMMARY: Matplotlib is a Python library for creating visualizations.\n\nTOOL_NAME: NONE\n\n```python\nNONE\n```"

        patcher, client = _patch_httpx(MOD, _ollama_response(summary_text))
        try:
            result = await learner._synthesize_findings("matplotlib", findings)
            assert isinstance(result, dict)
            assert "summary" in result
            assert len(result["summary"]) > 0
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_synthesis_with_code(self, learner):
        """Should extract code block when LLM provides one."""
        llm_output = (
            "SUMMARY: Matplotlib makes it easy to create bar charts in Python.\n\n"
            "TOOL_NAME: chart_maker\n\n"
            "```python\n"
            "from backend.tools.base import BaseTool\n"
            "from backend.config import WORKSPACE_ROOT\n\n"
            "class ChartMakerTool(BaseTool):\n"
            "    @property\n"
            "    def name(self): return 'chart_maker'\n"
            "    @property\n"
            "    def description(self): return 'Create charts'\n"
            "    @property\n"
            "    def parameters(self): return {'type': 'object', 'properties': {}}\n"
            "    async def execute(self, **kw): return {'success': True}\n"
            "```\n\n"
            "This creates a simple bar chart."
        )

        patcher, client = _patch_httpx(MOD, _ollama_response(llm_output))
        try:
            result = await learner._synthesize_findings("matplotlib bar charts", [])
            assert isinstance(result, dict)
            assert "summary" in result
            assert result["code"] is not None
            assert "class " in result["code"]
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_synthesis_handles_llm_failure(self, learner):
        """Should handle LLM failure gracefully during synthesis."""
        patcher, client = _patch_httpx(MOD, _ollama_response("", status_code=500))
        try:
            result = await learner._synthesize_findings("topic", [])
            assert isinstance(result, dict)
            assert "summary" in result
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# TestToolApplication
# ---------------------------------------------------------------------------


class TestToolApplication:
    @pytest.mark.asyncio
    async def test_applies_valid_tool(self, learner):
        """Should save valid tool code via ToolGenerator."""
        synthesis = {
            "summary": "Created a CSV parser tool",
            "code": (
                'from backend.tools.base import BaseTool\n'
                'from backend.config import WORKSPACE_ROOT\n'
                '\n'
                'class CsvParserTool(BaseTool):\n'
                '    @property\n'
                '    def name(self): return "csv_parser"\n'
                '    @property\n'
                '    def description(self): return "Parse CSV files"\n'
                '    @property\n'
                '    def parameters(self): return {"type": "object", "properties": {}}\n'
                '    async def execute(self, **kw): return {"success": True}\n'
            ),
        }

        with patch("backend.tools.tool_generator.ToolGenerator") as MockTG:
            tg = MagicMock()
            tg.validate_code.return_value = {"valid": True, "class_name": "CsvParserTool", "tool_name": "csv_parser"}
            tg.generate_tool.return_value = {"ok": True, "tool_name": "csv_parser"}
            MockTG.return_value = tg

            result = await learner._try_apply_learning(synthesis)
            assert result["applied"] is True
            assert result["tool_name"] == "csv_parser"
            tg.validate_code.assert_called_once()
            tg.generate_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_invalid_tool(self, learner):
        """Should not apply tool code that fails validation."""
        synthesis = {
            "summary": "Bad tool",
            "code": "def not_a_tool(): pass",
        }

        with patch("backend.tools.tool_generator.ToolGenerator") as MockTG:
            tg = MagicMock()
            tg.validate_code.return_value = {"valid": False, "errors": ["No BaseTool subclass found."]}
            MockTG.return_value = tg

            result = await learner._try_apply_learning(synthesis)
            assert result["applied"] is False
            assert result["tool_name"] is None
            tg.generate_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_no_code(self, learner):
        """Should handle synthesis with no code gracefully."""
        synthesis = {
            "summary": "Just information, no tool",
        }
        # Should not crash, just return {"applied": False, ...}
        result = await learner._try_apply_learning(synthesis)
        assert result["applied"] is False


# ---------------------------------------------------------------------------
# TestFullLearningCycle
# ---------------------------------------------------------------------------


class TestFullLearningCycle:
    @pytest.mark.asyncio
    async def test_learn_with_specific_topic(self, learner):
        """Should learn about a specific topic when provided."""
        # Mock all internal methods
        learner._research_topic = AsyncMock(return_value=[
            {"title": "Guide", "snippet": "Info", "url": "https://example.com"}
        ])
        learner._synthesize_findings = AsyncMock(return_value={
            "summary": "Learned about testing",
            "code": None,
        })
        learner._try_apply_learning = AsyncMock(return_value={"applied": False, "tool_name": None})

        result = await learner.learn(topic="pytest fixtures")
        assert isinstance(result, dict)
        assert result.get("topic") == "pytest fixtures"
        learner._research_topic.assert_called_once()

    @pytest.mark.asyncio
    async def test_learn_auto_topic(self, learner):
        """Should auto-detect topic when none provided."""
        learner._identify_learning_gap = AsyncMock(return_value="data serialization")
        learner._research_topic = AsyncMock(return_value=[])
        learner._synthesize_findings = AsyncMock(return_value={"summary": "Summary", "code": None})
        learner._try_apply_learning = AsyncMock(return_value={"applied": False, "tool_name": None})

        result = await learner.learn()
        assert isinstance(result, dict)
        assert result.get("topic") == "data serialization"
        learner._identify_learning_gap.assert_called_once()

    @pytest.mark.asyncio
    async def test_learn_adds_journal_entry(self, learner, tmp_journal):
        """Learning cycle should create a journal entry."""
        learner._identify_learning_gap = AsyncMock(return_value="caching strategies")
        learner._research_topic = AsyncMock(return_value=[
            {"title": "Cache Guide", "snippet": "How to cache", "url": "https://example.com"}
        ])
        learner._synthesize_findings = AsyncMock(return_value={
            "summary": "Caching improves performance using memoization",
            "code": None,
        })
        learner._try_apply_learning = AsyncMock(return_value={"applied": False, "tool_name": None})

        await learner.learn()

        journal = learner.get_journal()
        assert len(journal) >= 1
        last_entry = journal[-1]
        assert last_entry["topic"] == "caching strategies"
        assert "caching" in last_entry["summary"].lower() or "Cache" in last_entry["summary"]

    @pytest.mark.asyncio
    async def test_learn_marks_applied_when_tool_created(self, learner, tmp_journal):
        """Journal entry should be marked applied when a tool is created."""
        learner._identify_learning_gap = AsyncMock(return_value="csv parsing")
        learner._research_topic = AsyncMock(return_value=[
            {"title": "CSV Guide", "snippet": "CSV parsing info", "url": "https://example.com"}
        ])
        learner._synthesize_findings = AsyncMock(return_value={
            "summary": "Built a CSV tool",
            "code": "class CsvTool: pass",
        })
        learner._try_apply_learning = AsyncMock(return_value={"applied": True, "tool_name": "csv_tool"})

        await learner.learn()

        journal = learner.get_journal()
        assert len(journal) >= 1
        assert journal[-1]["applied"] is True


# ---------------------------------------------------------------------------
# TestSuggestions
# ---------------------------------------------------------------------------


class TestSuggestions:
    @pytest.mark.asyncio
    async def test_suggests_topics(self, learner):
        """Should suggest learning topics via LLM."""
        topics_json = '[{"topic": "web scraping", "reasoning": "Useful for data collection", "difficulty": "medium"}, {"topic": "data visualization", "reasoning": "Helps present data", "difficulty": "easy"}, {"topic": "API integration", "reasoning": "Connect services", "difficulty": "hard"}]'
        patcher, client = _patch_httpx(MOD, _ollama_response(topics_json))
        try:
            suggestions = await learner.suggest_topics()
            assert isinstance(suggestions, list)
            assert len(suggestions) > 0
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_suggests_topics_fallback(self, learner):
        """Should return fallback suggestions when LLM fails."""
        patcher, client = _patch_httpx(MOD, _ollama_response("", status_code=500))
        try:
            suggestions = await learner.suggest_topics()
            assert isinstance(suggestions, list)
            # Should still return something even on failure
            assert len(suggestions) > 0
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# TestSingleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_skill_learner_returns_instance(self, tmp_journal):
        """Module-level accessor should return a SkillLearner."""
        from backend.autonomy.skill_learner import get_skill_learner

        svc = get_skill_learner()
        assert svc is not None

    def test_get_skill_learner_returns_same_instance(self, tmp_journal):
        """Repeated calls should return the same singleton."""
        from backend.autonomy.skill_learner import get_skill_learner

        # Reset the singleton so our monkeypatch-ed journal path is used
        import backend.autonomy.skill_learner as mod
        mod._learner = None

        svc1 = get_skill_learner()
        svc2 = get_skill_learner()
        assert svc1 is svc2
