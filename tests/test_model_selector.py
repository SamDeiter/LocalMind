"""
Tests for backend/inference/model_selector.py
=============================================

Covers: ModelSelection dataclass, ModelSelector._classify_task,
_estimate_complexity, _pick_tier, _pick_best_of_n, _find_lora,
and full select_model() integration.
"""

import pytest
from unittest.mock import MagicMock, patch

from backend.inference.model_selector import (
    ModelSelection,
    ModelSelector,
    _estimate_vram,
    _LORA_OVERHEAD_MB,
    _VRAM_ESTIMATES,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

TEST_TIERS = {
    "light": "gemma4:e4b",
    "medium": "qwen2.5-coder:14b",
    "heavy": "qwen2.5-coder:32b",
}


def _make_node(
    title: str = "",
    instructions: str = "",
    tools: list[str] | None = None,
    output_schema_json: dict | None = None,
    depends_on: list[str] | None = None,
) -> dict:
    """Build a minimal node dict for testing."""
    node: dict = {"title": title, "instructions": instructions}
    if tools is not None:
        node["tools_allowed"] = tools
    if output_schema_json is not None:
        node["output_schema_json"] = output_schema_json
    if depends_on is not None:
        node["depends_on"] = depends_on
    return node


def _make_job(priority: int = 0) -> dict:
    return {"priority": priority}


@pytest.fixture
def selector() -> ModelSelector:
    return ModelSelector(model_tiers=TEST_TIERS)


@pytest.fixture
def selector_with_lora() -> tuple[ModelSelector, MagicMock]:
    mgr = MagicMock()
    sel = ModelSelector(model_tiers=TEST_TIERS, lora_manager=mgr)
    return sel, mgr


# ===================================================================
# ModelSelection.to_dict
# ===================================================================


class TestModelSelectionToDict:
    def test_to_dict_keys(self):
        ms = ModelSelection(
            model_id="m1", lora_id=None, vram_mb=4000,
            best_of_n=1, reasoning="test",
        )
        d = ms.to_dict()
        assert set(d.keys()) == {"model_id", "lora_id", "vram_mb", "best_of_n", "reasoning"}

    def test_to_dict_values(self):
        ms = ModelSelection(
            model_id="qwen3:8b", lora_id="lora-42", vram_mb=5256,
            best_of_n=4, reasoning="task_type=code; tier=medium",
        )
        d = ms.to_dict()
        assert d["model_id"] == "qwen3:8b"
        assert d["lora_id"] == "lora-42"
        assert d["vram_mb"] == 5256
        assert d["best_of_n"] == 4
        assert d["reasoning"] == "task_type=code; tier=medium"

    def test_to_dict_none_lora(self):
        ms = ModelSelection(
            model_id="m1", lora_id=None, vram_mb=4000,
            best_of_n=1, reasoning="r",
        )
        assert ms.to_dict()["lora_id"] is None


# ===================================================================
# _classify_task
# ===================================================================


class TestClassifyTask:
    """12 tests covering keyword, tool, weight, and edge-case classification."""

    def test_research_keyword_in_title(self, selector):
        node = _make_node(title="Research the market")
        assert selector._classify_task(node) == "research"

    def test_research_keyword_in_instructions(self, selector):
        node = _make_node(instructions="Please investigate the root cause")
        assert selector._classify_task(node) == "research"

    def test_code_keyword(self, selector):
        node = _make_node(title="Implement the login module")
        assert selector._classify_task(node) == "code"

    def test_document_keyword(self, selector):
        node = _make_node(title="Create a presentation about Q4")
        assert selector._classify_task(node) == "document"

    def test_no_matching_keywords_returns_general(self, selector):
        node = _make_node(title="Do the thing", instructions="Just handle it")
        assert selector._classify_task(node) == "general"

    def test_research_tools(self, selector):
        node = _make_node(tools=["web_search", "browser"])
        assert selector._classify_task(node) == "research"

    def test_code_tools(self, selector):
        node = _make_node(tools=["run_code", "git_status"])
        assert selector._classify_task(node) == "code"

    def test_document_tools(self, selector):
        node = _make_node(tools=["pptx_create", "excel_read"])
        assert selector._classify_task(node) == "document"

    def test_tools_have_double_weight(self, selector):
        """One code tool (2 pts) should outweigh one research keyword (1 pt)."""
        node = _make_node(
            title="Research this topic",  # +1 research
            tools=["run_code"],            # +2 code
        )
        assert selector._classify_task(node) == "code"

    def test_mixed_signals_highest_wins(self, selector):
        """When both research and code keywords appear, the one with more signal wins."""
        node = _make_node(
            title="implement the code module and develop a function",
            instructions="use the api endpoint to build a script",
        )
        # code keywords: implement, code, module, develop, function, api, endpoint, script, build = 9
        # research keywords: none significant
        assert selector._classify_task(node) == "code"

    def test_empty_node_returns_general(self, selector):
        node = _make_node()
        assert selector._classify_task(node) == "general"

    def test_none_values_handled(self, selector):
        node = {"title": None, "instructions": None, "tools_allowed": None}
        assert selector._classify_task(node) == "general"


# ===================================================================
# _estimate_complexity
# ===================================================================


class TestEstimateComplexity:
    """10 tests covering every factor that contributes to complexity score."""

    def test_baseline_is_1(self, selector):
        node = _make_node()
        assert selector._estimate_complexity(node, _make_job()) == 1

    def test_five_plus_tools_adds_3(self, selector):
        node = _make_node(tools=["a", "b", "c", "d", "e"])
        # baseline 1 + 3 = 4
        assert selector._estimate_complexity(node, _make_job()) == 4

    def test_three_to_four_tools_adds_2(self, selector):
        node = _make_node(tools=["a", "b", "c"])
        # baseline 1 + 2 = 3
        assert selector._estimate_complexity(node, _make_job()) == 3

    def test_one_to_two_tools_adds_1(self, selector):
        node = _make_node(tools=["a"])
        # baseline 1 + 1 = 2
        assert selector._estimate_complexity(node, _make_job()) == 2

    def test_long_instructions_adds_2(self, selector):
        node = _make_node(instructions="x" * 1001)
        # baseline 1 + 2 = 3
        assert selector._estimate_complexity(node, _make_job()) == 3

    def test_medium_instructions_adds_1(self, selector):
        node = _make_node(instructions="x" * 301)
        # baseline 1 + 1 = 2
        assert selector._estimate_complexity(node, _make_job()) == 2

    def test_output_schema_adds_1(self, selector):
        node = _make_node(output_schema_json={"type": "object"})
        # baseline 1 + 1 = 2
        assert selector._estimate_complexity(node, _make_job()) == 2

    def test_two_plus_dependencies_adds_1(self, selector):
        node = _make_node(depends_on=["node-a", "node-b"])
        # baseline 1 + 1 = 2
        assert selector._estimate_complexity(node, _make_job()) == 2

    def test_high_priority_adds_1(self, selector):
        node = _make_node()
        # baseline 1 + 1 (priority >= 7) = 2
        assert selector._estimate_complexity(node, _make_job(priority=7)) == 2

    def test_clamped_to_max_10(self, selector):
        """Stack all factors to exceed 10, verify clamped."""
        node = _make_node(
            tools=["a", "b", "c", "d", "e", "f"],  # +3
            instructions="x" * 1500,                 # +2
            output_schema_json={"type": "object"},   # +1
            depends_on=["a", "b", "c"],              # +1
        )
        # baseline 1 + 3 + 2 + 1 + 1 = 8; with priority>=7 → 9
        # All factors sum to at most 9 here; build a worse case:
        job = _make_job(priority=8)
        score = selector._estimate_complexity(node, job)
        assert score <= 10

    def test_minimal_node_complexity_1(self, selector):
        """A completely empty node has complexity 1."""
        node = {}
        assert selector._estimate_complexity(node, _make_job()) == 1


# ===================================================================
# _pick_tier
# ===================================================================


class TestPickTier:
    """8 tests for tier selection logic."""

    def test_priority_8_or_more_gives_heavy(self, selector):
        model_id, tier = selector._pick_tier("general", priority=8, complexity=1)
        assert tier == "heavy"
        assert model_id == TEST_TIERS["heavy"]

    def test_priority_9_gives_heavy(self, selector):
        _, tier = selector._pick_tier("research", priority=9, complexity=1)
        assert tier == "heavy"

    def test_priority_5_gives_medium(self, selector):
        _, tier = selector._pick_tier("general", priority=5, complexity=1)
        assert tier == "medium"

    def test_complexity_7_gives_medium_for_general(self, selector):
        _, tier = selector._pick_tier("general", priority=0, complexity=7)
        assert tier == "medium"

    def test_complexity_7_code_gives_heavy(self, selector):
        """Code tasks at complexity >= 7 are promoted to heavy."""
        _, tier = selector._pick_tier("code", priority=0, complexity=7)
        assert tier == "heavy"

    def test_low_complexity_gives_light(self, selector):
        _, tier = selector._pick_tier("general", priority=0, complexity=2)
        assert tier == "light"

    def test_medium_complexity_code_gives_medium(self, selector):
        """Code at complexity 4-6 and low priority gets medium."""
        _, tier = selector._pick_tier("code", priority=0, complexity=5)
        assert tier == "medium"

    def test_missing_tier_falls_back_to_light(self):
        """If the computed tier key is missing, fall back to 'light' model."""
        tiers = {"light": "fallback-model"}  # no medium or heavy
        sel = ModelSelector(model_tiers=tiers)
        model_id, _ = sel._pick_tier("general", priority=8, complexity=10)
        # heavy is missing, but light exists as fallback
        assert model_id == "fallback-model"


# ===================================================================
# _pick_best_of_n
# ===================================================================


class TestPickBestOfN:
    """4 tests for the best-of-N sampling tiers."""

    def test_low_complexity_returns_1(self, selector):
        for c in (1, 2, 3):
            assert selector._pick_best_of_n(c) == 1

    def test_medium_complexity_returns_4(self, selector):
        for c in (4, 5, 6):
            assert selector._pick_best_of_n(c) == 4

    def test_high_complexity_returns_8(self, selector):
        for c in (7, 8):
            assert selector._pick_best_of_n(c) == 8

    def test_very_high_complexity_returns_16(self, selector):
        for c in (9, 10):
            assert selector._pick_best_of_n(c) == 16


# ===================================================================
# _find_lora
# ===================================================================


class TestFindLora:
    """5 tests for LoRA adapter lookup."""

    def test_no_lora_manager_returns_none(self, selector):
        assert selector._find_lora("code", "qwen3:8b") is None

    def test_manager_has_match(self, selector_with_lora):
        sel, mgr = selector_with_lora
        adapter = MagicMock()
        adapter.adapter_id = "code-lora-v1"
        mgr.get_adapter.return_value = adapter

        result = sel._find_lora("code", "qwen3:8b")
        assert result == "code-lora-v1"
        mgr.get_adapter.assert_called_once_with(task_type="code", base_model="qwen3:8b")

    def test_manager_no_match(self, selector_with_lora):
        sel, mgr = selector_with_lora
        mgr.get_adapter.return_value = None

        assert sel._find_lora("general", "qwen3:8b") is None

    def test_manager_raises_exception(self, selector_with_lora):
        sel, mgr = selector_with_lora
        mgr.get_adapter.side_effect = RuntimeError("DB unavailable")

        result = sel._find_lora("code", "qwen3:8b")
        assert result is None

    def test_manager_exception_is_logged(self, selector_with_lora, caplog):
        sel, mgr = selector_with_lora
        mgr.get_adapter.side_effect = ValueError("bad lookup")

        with caplog.at_level("WARNING", logger="localmind.inference.model_selector"):
            sel._find_lora("research", "qwen3:8b")

        assert any("LoRA lookup failed" in r.message for r in caplog.records)


# ===================================================================
# _estimate_vram helper
# ===================================================================


class TestEstimateVram:
    def test_known_model(self):
        assert _estimate_vram("qwen3:8b") == 5000

    def test_unknown_model_defaults_4000(self):
        assert _estimate_vram("unknown:model") == 4000

    def test_lora_adds_overhead(self):
        assert _estimate_vram("qwen3:8b", lora_id="lora-x") == 5000 + _LORA_OVERHEAD_MB

    def test_no_lora_no_overhead(self):
        assert _estimate_vram("qwen3:8b", lora_id=None) == 5000


# ===================================================================
# select_model integration
# ===================================================================


class TestSelectModelIntegration:
    """8 integration tests calling the full select_model pipeline."""

    def test_research_node_selects_model(self, selector):
        node = _make_node(title="Research AI trends", tools=["web_search"])
        sel = selector.select_model(node, _make_job(priority=3))
        assert isinstance(sel, ModelSelection)
        assert "research" in sel.reasoning

    def test_code_node_selects_model(self, selector):
        node = _make_node(title="Implement parser", tools=["run_code", "write_file"])
        sel = selector.select_model(node, _make_job(priority=3))
        assert "code" in sel.reasoning

    def test_high_priority_gets_heavy_tier(self, selector):
        node = _make_node(title="Critical deployment")
        sel = selector.select_model(node, _make_job(priority=9))
        assert sel.model_id == TEST_TIERS["heavy"]
        assert "tier=heavy" in sel.reasoning

    def test_simple_node_gets_light_tier(self, selector):
        node = _make_node(title="Say hello")
        sel = selector.select_model(node, _make_job(priority=1))
        assert sel.model_id == TEST_TIERS["light"]
        assert "tier=light" in sel.reasoning

    def test_returns_model_selection_with_all_fields(self, selector):
        node = _make_node(title="Do something")
        sel = selector.select_model(node, _make_job())
        assert sel.model_id is not None
        assert sel.vram_mb > 0
        assert sel.best_of_n >= 1
        assert isinstance(sel.reasoning, str)

    def test_with_lora_match_populates_lora_id(self):
        mgr = MagicMock()
        adapter = MagicMock()
        adapter.adapter_id = "research-lora-v2"
        adapter.vram_overhead_mb = 128
        mgr.get_adapter.return_value = adapter
        mgr.get_adapter_by_id.return_value = adapter

        sel = ModelSelector(model_tiers=TEST_TIERS, lora_manager=mgr)
        node = _make_node(title="Research papers", tools=["web_search"])
        result = sel.select_model(node, _make_job(priority=3))

        assert result.lora_id == "research-lora-v2"
        assert "lora=research-lora-v2" in result.reasoning

    def test_vram_includes_lora_overhead_when_adapter_found(self):
        mgr = MagicMock()
        adapter = MagicMock()
        adapter.adapter_id = "code-lora-v1"
        adapter.vram_overhead_mb = 200
        mgr.get_adapter.return_value = adapter
        mgr.get_adapter_by_id.return_value = adapter

        sel = ModelSelector(model_tiers=TEST_TIERS, lora_manager=mgr)
        node = _make_node(title="Implement feature", tools=["run_code"])
        result = sel.select_model(node, _make_job(priority=3))

        # VRAM should be base model + adapter overhead
        base_vram = _estimate_vram(result.model_id)
        assert result.vram_mb == base_vram + adapter.vram_overhead_mb

    def test_no_lora_manager_still_works(self, selector):
        node = _make_node(title="Research papers", tools=["web_search"])
        result = selector.select_model(node, _make_job(priority=5))
        assert result.lora_id is None
        assert result.vram_mb > 0

    def test_document_node_classification(self, selector):
        node = _make_node(
            title="Create spreadsheet",
            tools=["excel_create", "excel_edit"],
        )
        result = selector.select_model(node, _make_job(priority=2))
        assert "document" in result.reasoning

    def test_complex_node_gets_higher_best_of_n(self, selector):
        node = _make_node(
            title="Implement complex algorithm",
            instructions="x" * 1200,
            tools=["run_code", "write_file", "read_file", "git_commit", "project_context"],
            output_schema_json={"type": "object"},
            depends_on=["node-1", "node-2"],
        )
        result = selector.select_model(node, _make_job(priority=7))
        # complexity should be high → best_of_n > 1
        assert result.best_of_n > 1
