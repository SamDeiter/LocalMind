"""
Integration tests for Phase 4 inference modules and Google credential wiring.

Covers:
  - ModelSelector (task classification, tier selection, best-of-N count)
  - BestOfNSampler (parallel sampling, scoring, error handling)
  - LoRAManager (adapter CRUD, SQLite persistence)
  - NodeExecutor inference wiring (model selection, best-of-N, Google creds)
  - JobWorker pass-through of model_selector / best_of_n_sampler
  - JobScheduler model selection delegation
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# sys.path + mock guard (same pattern as test_security.py)
# ---------------------------------------------------------------------------

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

for _mock_mod in ("fastapi", "httpx"):
    if _mock_mod not in sys.modules:
        try:
            __import__(_mock_mod)
        except ImportError:
            sys.modules[_mock_mod] = MagicMock()

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from backend.inference.model_selector import (
    ModelSelector,
    ModelSelection,
    _estimate_vram,
    _RESEARCH_KEYWORDS,
    _CODE_KEYWORDS,
    _DOCUMENT_KEYWORDS,
    _CODE_TOOLS,
    _DOCUMENT_TOOLS,
    _RESEARCH_TOOLS,
    _VRAM_ESTIMATES,
    _LORA_OVERHEAD_MB,
)
from backend.inference.best_of_n import (
    BestOfNSampler,
    SampleResult,
    _Candidate,
    _parse_judge_response,
    _is_valid_json,
)
from backend.inference.lora_manager import (
    LoRAAdapter,
    LoRAManager,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_TEST_MODEL_TIERS = {
    "light": "gemma4:e4b",
    "medium": "qwen2.5-coder:14b",
    "heavy": "qwen2.5-coder:32b",
    "ultra": "qwen2.5-coder:70b",
}


def _make_node(
    title="Test Node",
    instructions="Do something",
    tools_allowed=None,
    output_schema_json=None,
    depends_on=None,
):
    """Build a minimal node dict for ModelSelector tests."""
    return {
        "title": title,
        "instructions": instructions,
        "tools_allowed": tools_allowed or [],
        "output_schema_json": output_schema_json,
        "depends_on": depends_on or [],
    }


def _make_job(priority=0):
    """Build a minimal job dict for ModelSelector tests."""
    return {"priority": priority}


# ===========================================================================
# 1. TestModelSelector
# ===========================================================================


class TestModelSelector:
    """Tests for backend.inference.model_selector.ModelSelector."""

    def setup_method(self):
        self.selector = ModelSelector(model_tiers=_TEST_MODEL_TIERS)

    # --- Task classification ---

    def test_classify_research_by_keywords(self):
        node = _make_node(title="Research market trends", instructions="Investigate the competition")
        sel = self.selector.select_model(node, _make_job())
        assert "research" in sel.reasoning

    def test_classify_code_by_keywords(self):
        node = _make_node(title="Implement login", instructions="Write a function for auth")
        sel = self.selector.select_model(node, _make_job())
        assert "code" in sel.reasoning

    def test_classify_document_by_keywords(self):
        node = _make_node(title="Draft a presentation", instructions="Create a powerpoint slide deck")
        sel = self.selector.select_model(node, _make_job())
        assert "document" in sel.reasoning

    def test_classify_general_no_keywords(self):
        node = _make_node(title="Do stuff", instructions="Make it happen")
        sel = self.selector.select_model(node, _make_job())
        assert "general" in sel.reasoning

    def test_classify_code_by_tools(self):
        node = _make_node(title="A task", instructions="Go", tools_allowed=["run_code", "git_status"])
        sel = self.selector.select_model(node, _make_job())
        assert "code" in sel.reasoning

    def test_classify_document_by_tools(self):
        node = _make_node(title="A task", instructions="Go", tools_allowed=["pptx_create", "excel_edit"])
        sel = self.selector.select_model(node, _make_job())
        assert "document" in sel.reasoning

    def test_classify_research_by_tools(self):
        node = _make_node(title="A task", instructions="Go", tools_allowed=["web_search", "browser"])
        sel = self.selector.select_model(node, _make_job())
        assert "research" in sel.reasoning

    def test_tools_have_higher_weight_than_keywords(self):
        """Tool matches (2x weight) should dominate when keyword scores tie."""
        node = _make_node(
            title="research code document",
            instructions="",
            tools_allowed=["run_code"],
        )
        sel = self.selector.select_model(node, _make_job())
        assert "code" in sel.reasoning

    # --- Model tier selection ---

    def test_high_priority_gets_heavy_tier(self):
        node = _make_node()
        sel = self.selector.select_model(node, _make_job(priority=9))
        assert sel.model_id == _TEST_MODEL_TIERS["heavy"]
        assert "tier=heavy" in sel.reasoning

    def test_medium_priority_gets_medium_tier(self):
        node = _make_node(instructions="x" * 500, tools_allowed=["a", "b", "c"])
        sel = self.selector.select_model(node, _make_job(priority=5))
        assert sel.model_id == _TEST_MODEL_TIERS["medium"]

    def test_low_priority_simple_gets_light_tier(self):
        node = _make_node(title="Simple task", instructions="Short")
        sel = self.selector.select_model(node, _make_job(priority=0))
        assert sel.model_id == _TEST_MODEL_TIERS["light"]

    def test_high_complexity_code_gets_heavy(self):
        """Complexity >= 7 and code task_type should select heavy."""
        node = _make_node(
            title="Implement complex system",
            instructions="x" * 1500,
            tools_allowed=["run_code", "git_status", "read_file", "write_file", "project_context"],
            output_schema_json='{"type": "object"}',
            depends_on=["dep1", "dep2"],
        )
        sel = self.selector.select_model(node, _make_job(priority=7))
        assert sel.model_id == _TEST_MODEL_TIERS["heavy"]

    def test_fallback_when_tier_missing(self):
        """If the matched tier is absent, falls back to 'light' then default."""
        selector = ModelSelector(model_tiers={"light": "fallback-model"})
        node = _make_node()
        sel = selector.select_model(node, _make_job(priority=0))
        assert sel.model_id == "fallback-model"

    def test_fallback_when_all_tiers_missing(self):
        """When no tiers are configured, uses the hardcoded default."""
        selector = ModelSelector(model_tiers={})
        node = _make_node()
        sel = selector.select_model(node, _make_job(priority=0))
        assert sel.model_id == "gemma4:e4b"

    # --- Best-of-N count ---

    def test_best_of_n_easy_complexity(self):
        node = _make_node(title="Simple", instructions="Short")
        sel = self.selector.select_model(node, _make_job(priority=0))
        # Low complexity (1-3) -> best_of_n = 1
        assert sel.best_of_n == 1

    def test_best_of_n_medium_complexity(self):
        node = _make_node(
            instructions="x" * 500,
            tools_allowed=["a", "b", "c"],
        )
        sel = self.selector.select_model(node, _make_job(priority=0))
        assert sel.best_of_n == 4

    def test_best_of_n_hard_complexity(self):
        node = _make_node(
            instructions="x" * 1500,
            tools_allowed=["a", "b", "c", "d", "e"],
            output_schema_json='{"type":"object"}',
            depends_on=["d1", "d2"],
        )
        sel = self.selector.select_model(node, _make_job(priority=7))
        assert sel.best_of_n in (8, 16)

    def test_best_of_n_very_hard_is_16(self):
        node = _make_node(
            instructions="x" * 2000,
            tools_allowed=["a", "b", "c", "d", "e", "f"],
            output_schema_json='{"type":"object"}',
            depends_on=["d1", "d2", "d3"],
        )
        sel = self.selector.select_model(node, _make_job(priority=9))
        assert sel.best_of_n == 16

    # --- LoRA adapter selection ---

    def test_lora_selection_when_available(self):
        mock_lora_mgr = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.adapter_id = "research-lora-v2"
        mock_adapter.vram_overhead_mb = 128
        mock_lora_mgr.get_adapter.return_value = mock_adapter
        mock_lora_mgr.get_adapter_by_id.return_value = mock_adapter

        selector = ModelSelector(model_tiers=_TEST_MODEL_TIERS, lora_manager=mock_lora_mgr)
        node = _make_node(title="Research AI trends", instructions="Investigate deep learning")
        sel = selector.select_model(node, _make_job())
        assert sel.lora_id == "research-lora-v2"
        assert "lora=research-lora-v2" in sel.reasoning

    def test_lora_none_when_manager_absent(self):
        sel = self.selector.select_model(_make_node(), _make_job())
        assert sel.lora_id is None

    def test_lora_none_when_no_match(self):
        mock_lora_mgr = MagicMock()
        mock_lora_mgr.get_adapter.return_value = None
        selector = ModelSelector(model_tiers=_TEST_MODEL_TIERS, lora_manager=mock_lora_mgr)
        sel = selector.select_model(_make_node(), _make_job())
        assert sel.lora_id is None

    def test_lora_lookup_exception_graceful(self):
        mock_lora_mgr = MagicMock()
        mock_lora_mgr.get_adapter.side_effect = RuntimeError("DB offline")
        selector = ModelSelector(model_tiers=_TEST_MODEL_TIERS, lora_manager=mock_lora_mgr)
        sel = selector.select_model(_make_node(), _make_job())
        assert sel.lora_id is None

    # --- VRAM estimation ---

    def test_vram_known_model(self):
        assert _estimate_vram("qwen2.5-coder:14b") == 8500

    def test_vram_unknown_model_default(self):
        assert _estimate_vram("unknown-model:999b") == 4000

    def test_vram_with_lora_overhead(self):
        assert _estimate_vram("qwen2.5-coder:14b", lora_id="some-lora") == 8500 + _LORA_OVERHEAD_MB

    def test_vram_no_lora_no_overhead(self):
        assert _estimate_vram("qwen2.5-coder:14b", lora_id=None) == 8500

    # --- ModelSelection data class ---

    def test_model_selection_to_dict(self):
        sel = ModelSelection(
            model_id="qwen3:8b",
            lora_id="test-lora",
            vram_mb=5256,
            best_of_n=4,
            reasoning="test reason",
        )
        d = sel.to_dict()
        assert d["model_id"] == "qwen3:8b"
        assert d["lora_id"] == "test-lora"
        assert d["best_of_n"] == 4
        assert d["reasoning"] == "test reason"

    # --- Complexity estimation ---

    def test_complexity_baseline_minimal(self):
        node = _make_node(title="simple", instructions="hi")
        job = _make_job(priority=0)
        complexity = self.selector._estimate_complexity(node, job)
        assert complexity >= 1
        assert complexity <= 3

    def test_complexity_increases_with_tools(self):
        node_few = _make_node(tools_allowed=["a"])
        node_many = _make_node(tools_allowed=["a", "b", "c", "d", "e"])
        c_few = self.selector._estimate_complexity(node_few, _make_job())
        c_many = self.selector._estimate_complexity(node_many, _make_job())
        assert c_many > c_few

    def test_complexity_increases_with_long_instructions(self):
        node_short = _make_node(instructions="short")
        node_long = _make_node(instructions="x" * 1500)
        c_short = self.selector._estimate_complexity(node_short, _make_job())
        c_long = self.selector._estimate_complexity(node_long, _make_job())
        assert c_long > c_short

    def test_complexity_increases_with_output_schema(self):
        node_no = _make_node()
        node_yes = _make_node(output_schema_json='{"type":"object"}')
        c_no = self.selector._estimate_complexity(node_no, _make_job())
        c_yes = self.selector._estimate_complexity(node_yes, _make_job())
        assert c_yes > c_no

    def test_complexity_increases_with_dependencies(self):
        node_no = _make_node()
        node_deps = _make_node(depends_on=["d1", "d2"])
        c_no = self.selector._estimate_complexity(node_no, _make_job())
        c_deps = self.selector._estimate_complexity(node_deps, _make_job())
        assert c_deps > c_no

    def test_complexity_capped_at_10(self):
        node = _make_node(
            instructions="x" * 5000,
            tools_allowed=["a", "b", "c", "d", "e", "f", "g"],
            output_schema_json='{"type":"object"}',
            depends_on=["d1", "d2", "d3"],
        )
        c = self.selector._estimate_complexity(node, _make_job(priority=9))
        assert c <= 10

    # --- Keyword sets exist and are frozensets ---

    def test_keyword_sets_are_frozensets(self):
        assert isinstance(_RESEARCH_KEYWORDS, frozenset)
        assert isinstance(_CODE_KEYWORDS, frozenset)
        assert isinstance(_DOCUMENT_KEYWORDS, frozenset)
        assert isinstance(_CODE_TOOLS, frozenset)
        assert isinstance(_DOCUMENT_TOOLS, frozenset)
        assert isinstance(_RESEARCH_TOOLS, frozenset)

    def test_keyword_sets_non_empty(self):
        assert len(_RESEARCH_KEYWORDS) > 0
        assert len(_CODE_KEYWORDS) > 0
        assert len(_DOCUMENT_KEYWORDS) > 0


# ===========================================================================
# 2. TestBestOfNSampler
# ===========================================================================


class TestBestOfNSampler:
    """Tests for backend.inference.best_of_n.BestOfNSampler."""

    def setup_method(self):
        self.sampler = BestOfNSampler(
            ollama_url="http://127.0.0.1:11434",
            scorer_model=None,
            max_concurrent=2,
        )

    # --- Heuristic scoring ---

    def test_heuristic_scores_good_length(self):
        c = _Candidate(response="A" * 500, tool_calls=[])
        self.sampler._score_single_heuristic(c)
        assert c.score > 5.0, "Good-length response should score above neutral"

    def test_heuristic_penalizes_empty(self):
        c = _Candidate(response="", tool_calls=[])
        self.sampler._score_single_heuristic(c)
        assert c.score < 2.0, "Empty response should be heavily penalized"

    def test_heuristic_penalizes_very_short(self):
        c = _Candidate(response="Hi", tool_calls=[])
        self.sampler._score_single_heuristic(c)
        assert c.score < 4.0

    def test_heuristic_bonus_for_json(self):
        c = _Candidate(response='{"result": "success"}', tool_calls=[])
        self.sampler._score_single_heuristic(c)
        # JSON bonus + length scoring
        assert c.score > 5.0

    def test_heuristic_bonus_for_tool_calls(self):
        c = _Candidate(response="A" * 200, tool_calls=[{"function": {"name": "web_search"}}])
        self.sampler._score_single_heuristic(c)
        assert c.score > 6.0

    def test_heuristic_penalizes_truncation(self):
        c_clean = _Candidate(response="A" * 200, tool_calls=[])
        c_trunc = _Candidate(response="A" * 200 + "...", tool_calls=[])
        self.sampler._score_single_heuristic(c_clean)
        self.sampler._score_single_heuristic(c_trunc)
        assert c_trunc.score < c_clean.score

    def test_heuristic_penalizes_error_patterns(self):
        c = _Candidate(response="I cannot help with that request", tool_calls=[])
        self.sampler._score_single_heuristic(c)
        assert c.score < 4.0

    def test_heuristic_score_clamped_0_10(self):
        c_empty = _Candidate(response="", tool_calls=[])
        self.sampler._score_single_heuristic(c_empty)
        assert 0.0 <= c_empty.score <= 10.0

        c_great = _Candidate(
            response='{"key":"val"}',
            tool_calls=[{"f": 1}] * 10,
        )
        self.sampler._score_single_heuristic(c_great)
        assert 0.0 <= c_great.score <= 10.0

    def test_score_with_heuristics_batch(self):
        candidates = [
            _Candidate(response="A" * 300, tool_calls=[]),
            _Candidate(response="B" * 50, tool_calls=[]),
            _Candidate(response="", tool_calls=[]),
        ]
        scored = self.sampler._score_with_heuristics(candidates)
        assert len(scored) == 3
        assert scored[0].score > scored[2].score

    # --- Parse judge response ---

    def test_parse_judge_valid_json(self):
        text = '{"score": 8, "reasoning": "Great job"}'
        score, reasoning = _parse_judge_response(text)
        assert score == 8.0
        assert reasoning == "Great job"

    def test_parse_judge_fenced_json(self):
        text = '```json\n{"score": 7, "reasoning": "Good"}\n```'
        score, reasoning = _parse_judge_response(text)
        assert score == 7.0
        assert reasoning == "Good"

    def test_parse_judge_regex_fallback_score_10(self):
        text = "I would rate this 8/10 because it is thorough."
        score, reasoning = _parse_judge_response(text)
        assert score == 8.0

    def test_parse_judge_regex_score_keyword(self):
        text = "Score: 6 - decent but could improve"
        score, reasoning = _parse_judge_response(text)
        assert score == 6.0

    def test_parse_judge_complete_fallback(self):
        text = "No numeric data at all, just words."
        score, reasoning = _parse_judge_response(text)
        assert score == 5.0
        assert "could not parse" in reasoning

    def test_parse_judge_clamped_to_10(self):
        text = '{"score": 15, "reasoning": "overflow"}'
        score, _ = _parse_judge_response(text)
        assert score == 10.0

    def test_parse_judge_clamped_to_0(self):
        text = '{"score": -3, "reasoning": "underflow"}'
        score, _ = _parse_judge_response(text)
        assert score == 0.0

    # --- JSON validation helper ---

    def test_is_valid_json_object(self):
        assert _is_valid_json('{"key": "val"}') is True

    def test_is_valid_json_array(self):
        assert _is_valid_json('[1, 2, 3]') is True

    def test_is_valid_json_string(self):
        assert _is_valid_json('"just a string"') is False

    def test_is_valid_json_empty(self):
        assert _is_valid_json("") is False

    def test_is_valid_json_nonsense(self):
        assert _is_valid_json("not json at all") is False

    # --- SampleResult data class ---

    def test_sample_result_to_dict(self):
        r = SampleResult(
            response="Hello",
            score=8.5,
            reasoning="good",
            tokens_in=100,
            tokens_out=50,
            n_generated=4,
            duration_ms=1234,
        )
        d = r.to_dict()
        assert d["response"] == "Hello"
        assert d["score"] == 8.5
        assert d["n_generated"] == 4

    # --- sample() with mocked Ollama ---

    @pytest.mark.asyncio
    async def test_sample_single_pass(self):
        """N=1 should produce a single candidate without overhead."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Hello from LLM", "tool_calls": None},
            "prompt_eval_count": 50,
            "eval_count": 20,
        }

        with patch("backend.inference.best_of_n.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await self.sampler.sample(
                messages=[{"role": "user", "content": "Hi"}],
                tools=None,
                model="test-model",
                n=1,
            )

        assert isinstance(result, SampleResult)
        assert result.response == "Hello from LLM"
        assert result.n_generated == 1

    @pytest.mark.asyncio
    async def test_sample_picks_best_of_multiple(self):
        """With N>1, sampler should return the best-scored candidate."""
        responses = [
            {"message": {"content": "Short", "tool_calls": None}, "prompt_eval_count": 10, "eval_count": 5},
            {"message": {"content": "A" * 500, "tool_calls": None}, "prompt_eval_count": 10, "eval_count": 50},
            {"message": {"content": "", "tool_calls": None}, "prompt_eval_count": 10, "eval_count": 0},
        ]
        call_count = [0]

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        def side_effect(*args, **kwargs):
            idx = min(call_count[0], len(responses) - 1)
            call_count[0] += 1
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = responses[idx]
            return r

        with patch("backend.inference.best_of_n.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=side_effect)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await self.sampler.sample(
                messages=[{"role": "user", "content": "Hi"}],
                tools=None,
                model="test-model",
                n=3,
                temperature=0.7,
            )

        # The medium-length response ("A" * 500) should score highest
        assert result.response == "A" * 500

    @pytest.mark.asyncio
    async def test_sample_all_fail_raises(self):
        """If all candidates fail, sample() should raise RuntimeError."""
        with patch("backend.inference.best_of_n.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RuntimeError, match="All .* candidate completions failed"):
                await self.sampler.sample(
                    messages=[{"role": "user", "content": "Hi"}],
                    tools=None,
                    model="test-model",
                    n=3,
                )

    @pytest.mark.asyncio
    async def test_sample_n_less_than_1_becomes_1(self):
        """N < 1 should be clamped to 1."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "OK", "tool_calls": None},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

        with patch("backend.inference.best_of_n.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await self.sampler.sample(
                messages=[{"role": "user", "content": "Hi"}],
                tools=None,
                model="test-model",
                n=0,
            )

        assert result.n_generated == 1

    @pytest.mark.asyncio
    async def test_sample_llm_judge_path(self):
        """When scorer_model is set, LLM-as-judge scoring should be attempted."""
        sampler = BestOfNSampler(
            ollama_url="http://127.0.0.1:11434",
            scorer_model="judge-model",
            max_concurrent=2,
        )

        call_count = [0]

        def post_side_effect(*args, **kwargs):
            call_count[0] += 1
            r = MagicMock()
            r.status_code = 200
            payload = kwargs.get("json", {})
            if payload.get("model") == "judge-model":
                r.json.return_value = {
                    "message": {"content": '{"score": 9, "reasoning": "Excellent"}'},
                }
            else:
                r.json.return_value = {
                    "message": {"content": "LLM output", "tool_calls": None},
                    "prompt_eval_count": 10,
                    "eval_count": 10,
                }
            return r

        with patch("backend.inference.best_of_n.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=post_side_effect)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await sampler.sample(
                messages=[{"role": "user", "content": "Hi"}],
                tools=None,
                model="test-model",
                n=2,
            )

        assert result.score == 9.0

    @pytest.mark.asyncio
    async def test_sample_llm_judge_fallback_on_error(self):
        """If LLM-judge fails, should fall back to heuristic scoring."""
        sampler = BestOfNSampler(
            ollama_url="http://127.0.0.1:11434",
            scorer_model="broken-judge",
            max_concurrent=2,
        )

        call_count = [0]

        def post_side_effect(*args, **kwargs):
            call_count[0] += 1
            r = MagicMock()
            payload = kwargs.get("json", {})
            if payload.get("model") == "broken-judge":
                r.status_code = 500
                r.text = "Internal Server Error"
                raise Exception("Judge exploded")
            else:
                r.status_code = 200
                r.json.return_value = {
                    "message": {"content": "A" * 300, "tool_calls": None},
                    "prompt_eval_count": 10,
                    "eval_count": 30,
                }
            return r

        with patch("backend.inference.best_of_n.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=post_side_effect)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await sampler.sample(
                messages=[{"role": "user", "content": "Hi"}],
                tools=None,
                model="test-model",
                n=1,
            )

        # Should succeed via heuristic fallback
        assert isinstance(result, SampleResult)

    @pytest.mark.asyncio
    async def test_sample_ollama_http_error(self):
        """Non-200 responses from Ollama should be treated as candidate failures."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"

        with patch("backend.inference.best_of_n.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RuntimeError, match="All .* candidate completions failed"):
                await self.sampler.sample(
                    messages=[{"role": "user", "content": "Hi"}],
                    tools=None,
                    model="test-model",
                    n=2,
                )

    @pytest.mark.asyncio
    async def test_sample_ollama_error_key_in_response(self):
        """Ollama response with 'error' key should be treated as failure."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"error": "model not found"}

        with patch("backend.inference.best_of_n.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RuntimeError, match="All .* candidate completions failed"):
                await self.sampler.sample(
                    messages=[{"role": "user", "content": "Hi"}],
                    tools=None,
                    model="test-model",
                    n=1,
                )


# ===========================================================================
# 3. TestLoRAManager
# ===========================================================================


class TestLoRAManager:
    """Tests for backend.inference.lora_manager.LoRAManager using a temp DB."""

    def setup_method(self):
        """Create a temporary database for each test."""
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._db_path = self._tmp.name
        self._patcher = patch("backend.inference.lora_manager.DB_PATH", self._db_path)
        self._patcher.start()
        # No adapters_dir validation needed for these tests
        self.mgr = LoRAManager(adapters_dir=None)

    def teardown_method(self):
        self._patcher.stop()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def _make_adapter(self, adapter_id="test-lora-1", base_model="qwen3:8b",
                      task_types=None, enabled=True):
        return LoRAAdapter(
            adapter_id=adapter_id,
            base_model=base_model,
            task_types=task_types or ["research"],
            path="/fake/path/to/adapter",
            vram_overhead_mb=128,
            enabled=enabled,
        )

    def test_register_and_list(self):
        self.mgr.register_adapter(self._make_adapter())
        adapters = self.mgr.list_adapters()
        assert len(adapters) == 1
        assert adapters[0].adapter_id == "test-lora-1"

    def test_register_multiple_adapters(self):
        self.mgr.register_adapter(self._make_adapter("lora-a"))
        self.mgr.register_adapter(self._make_adapter("lora-b"))
        assert len(self.mgr.list_adapters()) == 2

    def test_register_upsert_existing(self):
        """Re-registering same adapter_id should update, not duplicate."""
        self.mgr.register_adapter(self._make_adapter(task_types=["research"]))
        self.mgr.register_adapter(self._make_adapter(task_types=["code"]))
        adapters = self.mgr.list_adapters()
        assert len(adapters) == 1
        assert "code" in adapters[0].task_types

    def test_remove_adapter(self):
        self.mgr.register_adapter(self._make_adapter())
        removed = self.mgr.remove_adapter("test-lora-1")
        assert removed is True
        assert len(self.mgr.list_adapters()) == 0

    def test_remove_nonexistent_adapter(self):
        removed = self.mgr.remove_adapter("does-not-exist")
        assert removed is False

    def test_get_adapter_by_id(self):
        self.mgr.register_adapter(self._make_adapter())
        adapter = self.mgr.get_adapter_by_id("test-lora-1")
        assert adapter is not None
        assert adapter.adapter_id == "test-lora-1"

    def test_get_adapter_by_id_nonexistent(self):
        adapter = self.mgr.get_adapter_by_id("nope")
        assert adapter is None

    def test_get_adapter_by_task_type_and_model(self):
        self.mgr.register_adapter(self._make_adapter(
            adapter_id="research-lora",
            base_model="qwen3:8b",
            task_types=["research", "writing"],
        ))
        result = self.mgr.get_adapter(task_type="research", base_model="qwen3:8b")
        assert result is not None
        assert result.adapter_id == "research-lora"

    def test_get_adapter_no_match_wrong_model(self):
        self.mgr.register_adapter(self._make_adapter(base_model="qwen3:8b"))
        result = self.mgr.get_adapter(task_type="research", base_model="gemma4:e4b")
        assert result is None

    def test_get_adapter_no_match_wrong_task(self):
        self.mgr.register_adapter(self._make_adapter(task_types=["code"]))
        result = self.mgr.get_adapter(task_type="research", base_model="qwen3:8b")
        assert result is None

    def test_enable_disable_adapter(self):
        self.mgr.register_adapter(self._make_adapter())
        disabled = self.mgr.disable_adapter("test-lora-1")
        assert disabled is True
        adapter = self.mgr.get_adapter_by_id("test-lora-1")
        assert adapter is not None
        assert adapter.enabled is False

        enabled = self.mgr.enable_adapter("test-lora-1")
        assert enabled is True
        adapter = self.mgr.get_adapter_by_id("test-lora-1")
        assert adapter.enabled is True

    def test_disable_nonexistent(self):
        result = self.mgr.disable_adapter("nope")
        assert result is False

    def test_enable_nonexistent(self):
        result = self.mgr.enable_adapter("nope")
        assert result is False

    def test_list_enabled_only(self):
        self.mgr.register_adapter(self._make_adapter("lora-on", enabled=True))
        self.mgr.register_adapter(self._make_adapter("lora-off", enabled=False))
        # The register path always inserts enabled=1 or 0 from the adapter obj
        # but we set it via disable after register
        self.mgr.disable_adapter("lora-off")
        all_adapters = self.mgr.list_adapters(enabled_only=False)
        enabled_adapters = self.mgr.list_adapters(enabled_only=True)
        assert len(all_adapters) == 2
        assert len(enabled_adapters) == 1
        assert enabled_adapters[0].adapter_id == "lora-on"

    def test_disabled_adapter_not_matched(self):
        """get_adapter should only return enabled adapters."""
        self.mgr.register_adapter(self._make_adapter())
        self.mgr.disable_adapter("test-lora-1")
        result = self.mgr.get_adapter(task_type="research", base_model="qwen3:8b")
        assert result is None

    def test_get_adapters_for_task(self):
        self.mgr.register_adapter(self._make_adapter("lora-a", base_model="m1", task_types=["research"]))
        self.mgr.register_adapter(self._make_adapter("lora-b", base_model="m2", task_types=["research"]))
        self.mgr.register_adapter(self._make_adapter("lora-c", base_model="m3", task_types=["code"]))
        results = self.mgr.get_adapters_for_task("research")
        assert len(results) == 2
        ids = {a.adapter_id for a in results}
        assert "lora-a" in ids
        assert "lora-b" in ids

    def test_lora_adapter_to_dict(self):
        adapter = self._make_adapter()
        d = adapter.to_dict()
        assert d["adapter_id"] == "test-lora-1"
        assert d["task_types"] == ["research"]
        assert d["vram_overhead_mb"] == 128

    def test_sqlite_persistence_across_instances(self):
        """Data should survive a new LoRAManager instance on the same DB."""
        self.mgr.register_adapter(self._make_adapter())
        # Create a new manager pointing at the same DB
        mgr2 = LoRAManager(adapters_dir=None)
        adapters = mgr2.list_adapters()
        assert len(adapters) == 1
        assert adapters[0].adapter_id == "test-lora-1"


# ===========================================================================
# 4. TestExecutorInference
# ===========================================================================


class TestExecutorInference:
    """Tests for NodeExecutor model selection and best-of-N wiring."""

    def _make_mock_registry(self):
        reg = MagicMock()
        reg.get_ollama_tools.return_value = []
        reg.execute_tool = AsyncMock(return_value={"success": True})
        return reg

    def _make_mock_node(self, **overrides):
        node = MagicMock()
        node.id = overrides.get("id", "node-1")
        node.title = overrides.get("title", "Test Node")
        node.instructions = overrides.get("instructions", "Do something")
        node.tools_allowed = overrides.get("tools_allowed", [])
        node.timeout_sec = overrides.get("timeout_sec", 300)
        node.sequence = overrides.get("sequence", 1)
        node.expected_output = overrides.get("expected_output", None)
        node.output_schema_json = overrides.get("output_schema_json", None)
        return node

    def _make_mock_job(self, **overrides):
        job = MagicMock()
        job.id = overrides.get("id", "job-1")
        job.workspace_id = overrides.get("workspace_id", "ws-1")
        job.priority = overrides.get("priority", 3)
        return job

    def test_constructor_accepts_selector_and_sampler(self):
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        selector = MagicMock()
        sampler = MagicMock()

        executor = NodeExecutor(
            tool_registry=reg,
            model_selector=selector,
            best_of_n_sampler=sampler,
        )
        assert executor._model_selector is selector
        assert executor._best_of_n_sampler is sampler

    def test_constructor_none_defaults(self):
        from backend.jobs.executor import NodeExecutor

        executor = NodeExecutor(tool_registry=self._make_mock_registry())
        assert executor._model_selector is None
        assert executor._best_of_n_sampler is None

    @pytest.mark.asyncio
    async def test_model_selector_used_when_provided(self):
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        selector = MagicMock()
        selection = ModelSelection(
            model_id="selected-model",
            lora_id=None,
            vram_mb=5000,
            best_of_n=1,
            reasoning="test",
        )
        selector.select_model.return_value = selection

        executor = NodeExecutor(
            tool_registry=reg,
            model_selector=selector,
        )

        # Mock _call_ollama to return a final text response (no tool calls)
        ollama_response = {
            "message": {"content": '{"result": "done"}', "tool_calls": None},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        executor._call_ollama = AsyncMock(return_value=ollama_response)

        node = self._make_mock_node()
        job = self._make_mock_job()
        result = await executor.execute_node(node, job)

        selector.select_model.assert_called_once()
        assert result.model_used == "selected-model"

    @pytest.mark.asyncio
    async def test_fallback_when_selector_is_none(self):
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        executor = NodeExecutor(tool_registry=reg)

        ollama_response = {
            "message": {"content": '{"result": "done"}', "tool_calls": None},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        executor._call_ollama = AsyncMock(return_value=ollama_response)

        node = self._make_mock_node()
        job = self._make_mock_job()
        result = await executor.execute_node(node, job)

        # Should use MODEL_TIERS["medium"] from config
        assert result.success is True

    @pytest.mark.asyncio
    async def test_fallback_when_selector_raises(self):
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        selector = MagicMock()
        selector.select_model.side_effect = RuntimeError("Selector broken")

        executor = NodeExecutor(
            tool_registry=reg,
            model_selector=selector,
        )

        ollama_response = {
            "message": {"content": '{"result": "done"}', "tool_calls": None},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        executor._call_ollama = AsyncMock(return_value=ollama_response)

        node = self._make_mock_node()
        job = self._make_mock_job()
        result = await executor.execute_node(node, job)

        # Should still succeed using fallback model
        assert result.success is True

    @pytest.mark.asyncio
    async def test_best_of_n_triggered_on_final_iteration(self):
        """When best_of_n > 1 and BEST_OF_N_ENABLED, sampler should be called."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        selector = MagicMock()
        selection = ModelSelection(
            model_id="test-model",
            lora_id=None,
            vram_mb=5000,
            best_of_n=4,
            reasoning="test",
        )
        selector.select_model.return_value = selection

        sampler = MagicMock()
        sample_result = SampleResult(
            response='{"result": "best output"}',
            score=9.0,
            reasoning="top pick",
            tokens_in=100,
            tokens_out=50,
            n_generated=4,
            duration_ms=500,
        )
        sampler.sample = AsyncMock(return_value=sample_result)

        executor = NodeExecutor(
            tool_registry=reg,
            model_selector=selector,
            best_of_n_sampler=sampler,
        )

        # First Ollama call returns no tool calls (final output)
        ollama_response = {
            "message": {"content": "single pass output", "tool_calls": None},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        executor._call_ollama = AsyncMock(return_value=ollama_response)

        node = self._make_mock_node()
        job = self._make_mock_job()

        with patch("backend.jobs.executor.BEST_OF_N_ENABLED", True):
            result = await executor.execute_node(node, job)

        sampler.sample.assert_called_once()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_best_of_n_not_triggered_when_disabled(self):
        """When BEST_OF_N_ENABLED is False, sampler should NOT be called."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        selector = MagicMock()
        selection = ModelSelection(
            model_id="test-model", lora_id=None, vram_mb=5000,
            best_of_n=4, reasoning="test",
        )
        selector.select_model.return_value = selection

        sampler = MagicMock()
        sampler.sample = AsyncMock()

        executor = NodeExecutor(
            tool_registry=reg,
            model_selector=selector,
            best_of_n_sampler=sampler,
        )

        ollama_response = {
            "message": {"content": '{"result": "done"}', "tool_calls": None},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        executor._call_ollama = AsyncMock(return_value=ollama_response)

        node = self._make_mock_node()
        job = self._make_mock_job()

        with patch("backend.jobs.executor.BEST_OF_N_ENABLED", False):
            result = await executor.execute_node(node, job)

        sampler.sample.assert_not_called()

    @pytest.mark.asyncio
    async def test_best_of_n_not_triggered_when_sampler_none(self):
        """When sampler is None, best-of-N should not be used."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        selector = MagicMock()
        selection = ModelSelection(
            model_id="test-model", lora_id=None, vram_mb=5000,
            best_of_n=4, reasoning="test",
        )
        selector.select_model.return_value = selection

        executor = NodeExecutor(
            tool_registry=reg,
            model_selector=selector,
            best_of_n_sampler=None,
        )

        ollama_response = {
            "message": {"content": '{"result": "done"}', "tool_calls": None},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        executor._call_ollama = AsyncMock(return_value=ollama_response)

        node = self._make_mock_node()
        job = self._make_mock_job()

        with patch("backend.jobs.executor.BEST_OF_N_ENABLED", True):
            result = await executor.execute_node(node, job)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_best_of_n_graceful_fallback_on_error(self):
        """If sampler.sample() raises, executor should fall back to single-pass."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        selector = MagicMock()
        selection = ModelSelection(
            model_id="test-model", lora_id=None, vram_mb=5000,
            best_of_n=4, reasoning="test",
        )
        selector.select_model.return_value = selection

        sampler = MagicMock()
        sampler.sample = AsyncMock(side_effect=RuntimeError("Sampling exploded"))

        executor = NodeExecutor(
            tool_registry=reg,
            model_selector=selector,
            best_of_n_sampler=sampler,
        )

        ollama_response = {
            "message": {"content": '{"result": "fallback output"}', "tool_calls": None},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        executor._call_ollama = AsyncMock(return_value=ollama_response)

        node = self._make_mock_node()
        job = self._make_mock_job()

        with patch("backend.jobs.executor.BEST_OF_N_ENABLED", True):
            result = await executor.execute_node(node, job)

        assert result.success is True


# ===========================================================================
# 5. TestExecutorGoogleCreds
# ===========================================================================


class TestExecutorGoogleCreds:
    """Tests for Google credential injection in NodeExecutor (around line 598-615)."""

    def _make_mock_registry(self):
        reg = MagicMock()
        reg.get_ollama_tools.return_value = [
            {"function": {"name": "google_drive_list"}},
            {"function": {"name": "google_sheets_read"}},
            {"function": {"name": "gmail"}},
            {"function": {"name": "web_search"}},
        ]
        reg.execute_tool = AsyncMock(return_value={"success": True})
        return reg

    def _make_mock_node(self, tools_allowed=None):
        node = MagicMock()
        node.id = "node-g"
        node.title = "Google task"
        node.instructions = "Use Google tools"
        node.tools_allowed = tools_allowed or ["google_drive_list"]
        node.timeout_sec = 300
        node.sequence = 1
        node.expected_output = None
        node.output_schema_json = None
        return node

    def _make_mock_job(self):
        job = MagicMock()
        job.id = "job-g"
        job.workspace_id = "ws-g"
        job.priority = 3
        return job

    @pytest.mark.asyncio
    async def test_google_tool_gets_credentials_injected(self):
        """google_* tools should have credentials injected when available."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        executor = NodeExecutor(tool_registry=reg)

        mock_creds = MagicMock()

        # We need to simulate the agent loop processing a google tool call
        # The easiest is to test the flow via _run_agent_loop by mocking _call_ollama
        # to return a tool_call followed by a final text response

        call_count = [0]

        async def mock_ollama(messages, tools, model):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "google_drive_list", "arguments": {"path": "/"}}}
                        ],
                    },
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }
            else:
                return {
                    "message": {"content": '{"result": "done"}', "tool_calls": None},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        executor._call_ollama = mock_ollama

        with patch("backend.jobs.executor.get_credentials", return_value=mock_creds, create=True) as mock_get_creds:
            with patch("backend.routes.google_auth.get_credentials", return_value=mock_creds, create=True):
                node = self._make_mock_node(tools_allowed=["google_drive_list"])
                job = self._make_mock_job()
                result = await executor.execute_node(node, job)

        # Verify the tool was called, and args should have credentials
        reg.execute_tool.assert_called_once()
        call_args = reg.execute_tool.call_args
        tool_args_used = call_args[0][1]  # second positional arg
        assert "credentials" in tool_args_used

    @pytest.mark.asyncio
    async def test_gmail_tool_gets_credentials_injected(self):
        """The 'gmail' tool should also get credentials injected."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        executor = NodeExecutor(tool_registry=reg)

        mock_creds = MagicMock()
        call_count = [0]

        async def mock_ollama(messages, tools, model):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "gmail", "arguments": {"query": "inbox"}}}
                        ],
                    },
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }
            else:
                return {
                    "message": {"content": '{"result": "done"}', "tool_calls": None},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        executor._call_ollama = mock_ollama

        with patch("backend.routes.google_auth.get_credentials", return_value=mock_creds, create=True):
            node = self._make_mock_node(tools_allowed=["gmail"])
            job = self._make_mock_job()
            result = await executor.execute_node(node, job)

        reg.execute_tool.assert_called_once()
        call_args = reg.execute_tool.call_args
        tool_args_used = call_args[0][1]
        assert "credentials" in tool_args_used

    @pytest.mark.asyncio
    async def test_non_google_tool_not_affected(self):
        """web_search (non-google tool) should NOT get credentials injected."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        executor = NodeExecutor(tool_registry=reg)

        call_count = [0]

        async def mock_ollama(messages, tools, model):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "web_search", "arguments": {"query": "hello"}}}
                        ],
                    },
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }
            else:
                return {
                    "message": {"content": '{"result": "done"}', "tool_calls": None},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        executor._call_ollama = mock_ollama

        node = self._make_mock_node(tools_allowed=["web_search"])
        job = self._make_mock_job()
        result = await executor.execute_node(node, job)

        reg.execute_tool.assert_called_once()
        call_args = reg.execute_tool.call_args
        tool_args_used = call_args[0][1]
        assert "credentials" not in tool_args_used

    @pytest.mark.asyncio
    async def test_google_creds_graceful_on_failure(self):
        """If get_credentials() raises, execution should continue without creds."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        executor = NodeExecutor(tool_registry=reg)

        call_count = [0]

        async def mock_ollama(messages, tools, model):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "google_drive_list", "arguments": {"path": "/"}}}
                        ],
                    },
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }
            else:
                return {
                    "message": {"content": '{"result": "done"}', "tool_calls": None},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        executor._call_ollama = mock_ollama

        with patch("backend.routes.google_auth.get_credentials", side_effect=RuntimeError("No creds"), create=True):
            node = self._make_mock_node(tools_allowed=["google_drive_list"])
            job = self._make_mock_job()
            result = await executor.execute_node(node, job)

        # Should still succeed even without creds
        assert result.success is True
        reg.execute_tool.assert_called_once()
        # Credentials should NOT be in args since injection failed
        call_args = reg.execute_tool.call_args
        tool_args_used = call_args[0][1]
        assert "credentials" not in tool_args_used

    @pytest.mark.asyncio
    async def test_google_creds_not_injected_when_already_present(self):
        """If tool_args already has 'credentials', injection should be skipped."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        executor = NodeExecutor(tool_registry=reg)

        existing_creds = MagicMock()
        call_count = [0]

        async def mock_ollama(messages, tools, model):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "google_drive_list",
                                          "arguments": {"path": "/", "credentials": "existing"}}}
                        ],
                    },
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }
            else:
                return {
                    "message": {"content": '{"result": "done"}', "tool_calls": None},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        executor._call_ollama = mock_ollama
        new_creds = MagicMock()

        with patch("backend.routes.google_auth.get_credentials", return_value=new_creds, create=True):
            node = self._make_mock_node(tools_allowed=["google_drive_list"])
            job = self._make_mock_job()
            result = await executor.execute_node(node, job)

        reg.execute_tool.assert_called_once()
        call_args = reg.execute_tool.call_args
        tool_args_used = call_args[0][1]
        # Should keep the original "existing" value, not replace it
        assert tool_args_used["credentials"] == "existing"

    @pytest.mark.asyncio
    async def test_google_creds_none_returned(self):
        """If get_credentials() returns None, credentials should not be injected."""
        from backend.jobs.executor import NodeExecutor

        reg = self._make_mock_registry()
        executor = NodeExecutor(tool_registry=reg)

        call_count = [0]

        async def mock_ollama(messages, tools, model):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "google_drive_list", "arguments": {"path": "/"}}}
                        ],
                    },
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }
            else:
                return {
                    "message": {"content": '{"result": "done"}', "tool_calls": None},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        executor._call_ollama = mock_ollama

        with patch("backend.routes.google_auth.get_credentials", return_value=None, create=True):
            node = self._make_mock_node(tools_allowed=["google_drive_list"])
            job = self._make_mock_job()
            result = await executor.execute_node(node, job)

        assert result.success is True
        reg.execute_tool.assert_called_once()
        call_args = reg.execute_tool.call_args
        tool_args_used = call_args[0][1]
        # None is still truthy from the condition `if _creds is not None`
        # so when it's None, credentials should NOT be set
        assert "credentials" not in tool_args_used


# ===========================================================================
# 6. TestWorkerPassthrough
# ===========================================================================


class TestWorkerPassthrough:
    """Tests that JobWorker forwards model_selector and best_of_n_sampler."""

    def test_worker_passes_selector_to_executor(self):
        from backend.jobs.worker import JobWorker

        reg = MagicMock()
        selector = MagicMock()
        sampler = MagicMock()

        worker = JobWorker(
            tool_registry=reg,
            model_selector=selector,
            best_of_n_sampler=sampler,
        )

        assert worker.executor._model_selector is selector
        assert worker.executor._best_of_n_sampler is sampler

    def test_worker_none_defaults(self):
        from backend.jobs.worker import JobWorker

        reg = MagicMock()
        worker = JobWorker(tool_registry=reg)

        assert worker.executor._model_selector is None
        assert worker.executor._best_of_n_sampler is None

    def test_worker_selector_only(self):
        from backend.jobs.worker import JobWorker

        reg = MagicMock()
        selector = MagicMock()
        worker = JobWorker(tool_registry=reg, model_selector=selector)

        assert worker.executor._model_selector is selector
        assert worker.executor._best_of_n_sampler is None

    def test_worker_sampler_only(self):
        from backend.jobs.worker import JobWorker

        reg = MagicMock()
        sampler = MagicMock()
        worker = JobWorker(tool_registry=reg, best_of_n_sampler=sampler)

        assert worker.executor._model_selector is None
        assert worker.executor._best_of_n_sampler is sampler


# ===========================================================================
# 7. TestSchedulerModelSelection
# ===========================================================================


class TestSchedulerModelSelection:
    """Tests for JobScheduler._pick_model_for_node delegation."""

    def _make_scheduler(self, model_selector=None):
        mock_gpu = MagicMock()
        mock_gpu._loaded_models = {}
        mock_gpu._vram_total_mb = 24000
        mock_gpu._vram_allocated_mb = 0
        mock_gpu.can_fit.return_value = True
        mock_gpu.get_status.return_value = {}

        # Patch _get_conn used in _restore_state
        with patch("backend.core.scheduler._get_conn") as mock_conn:
            conn_instance = MagicMock()
            conn_instance.execute.return_value.fetchone.return_value = None
            mock_conn.return_value = conn_instance

            from backend.core.scheduler import JobScheduler
            scheduler = JobScheduler(
                gpu_manager=mock_gpu,
                model_selector=model_selector,
            )
        return scheduler

    def test_delegates_to_model_selector(self):
        selector = MagicMock()
        selection = ModelSelection(
            model_id="custom-model:7b",
            lora_id=None,
            vram_mb=4500,
            best_of_n=1,
            reasoning="delegated",
        )
        selector.select_model.return_value = selection

        scheduler = self._make_scheduler(model_selector=selector)
        node = {"title": "research task", "priority": 3}

        result = scheduler._pick_model_for_node(node)

        assert result == "custom-model:7b"
        selector.select_model.assert_called_once()

    def test_fallback_when_selector_none(self):
        scheduler = self._make_scheduler(model_selector=None)
        node = {"title": "simple task", "priority": 0}

        result = scheduler._pick_model_for_node(node)

        # Should use the simple heuristic (light tier for low priority)
        from backend.config import MODEL_TIERS
        assert result == MODEL_TIERS.get("light", "gemma4:e4b")

    def test_fallback_when_selector_raises(self):
        selector = MagicMock()
        selector.select_model.side_effect = RuntimeError("Selector error")

        scheduler = self._make_scheduler(model_selector=selector)
        node = {"title": "basic task", "priority": 0}

        result = scheduler._pick_model_for_node(node)

        # Should fall back to simple heuristic
        from backend.config import MODEL_TIERS
        assert result == MODEL_TIERS.get("light", "gemma4:e4b")

    def test_high_priority_without_selector(self):
        scheduler = self._make_scheduler(model_selector=None)
        node = {"title": "urgent task", "priority": 9}

        result = scheduler._pick_model_for_node(node)

        from backend.config import MODEL_TIERS
        assert result == MODEL_TIERS.get("heavy", "qwen2.5-coder:32b")

    def test_medium_priority_without_selector(self):
        scheduler = self._make_scheduler(model_selector=None)
        node = {"title": "normal task", "priority": 5}

        result = scheduler._pick_model_for_node(node)

        from backend.config import MODEL_TIERS
        assert result == MODEL_TIERS.get("medium", "qwen2.5-coder:14b")

    def test_review_node_gets_medium_without_selector(self):
        scheduler = self._make_scheduler(model_selector=None)
        node = {"title": "Review the output", "priority": 2}

        result = scheduler._pick_model_for_node(node)

        from backend.config import MODEL_TIERS
        assert result == MODEL_TIERS.get("medium", "qwen2.5-coder:14b")

    def test_selector_receives_correct_node_and_job_dicts(self):
        selector = MagicMock()
        selection = ModelSelection(
            model_id="test:1b", lora_id=None, vram_mb=1000,
            best_of_n=1, reasoning="test",
        )
        selector.select_model.return_value = selection

        scheduler = self._make_scheduler(model_selector=selector)
        node = {"title": "Code task", "priority": 7, "node_id": "n1"}

        scheduler._pick_model_for_node(node)

        call_args = selector.select_model.call_args
        node_arg = call_args[0][0]
        job_arg = call_args[0][1]
        assert node_arg["title"] == "Code task"
        assert job_arg["priority"] == 7

    def test_constructor_accepts_model_selector(self):
        selector = MagicMock()
        scheduler = self._make_scheduler(model_selector=selector)
        assert scheduler._model_selector is selector

    def test_constructor_none_selector(self):
        scheduler = self._make_scheduler(model_selector=None)
        assert scheduler._model_selector is None
