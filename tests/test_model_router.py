"""
Tests for the intelligent model router.
"""
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.model_router import classify_task, _match_model_family, route_model


# ── Task Classification ──────────────────────────────────────────

class TestClassifyTask:
    def test_code_task(self):
        assert classify_task("Write a Python function to sort a list") == "code"

    def test_debug_task(self):
        assert classify_task("Fix this bug in my code, it's not working") == "debug"

    def test_explain_task(self):
        assert classify_task("Explain how memory allocation works") == "explain"

    def test_plan_task(self):
        assert classify_task("Design the architecture for a microservices project") == "plan"

    def test_data_task(self):
        assert classify_task("Parse this CSV and analyze the data with pandas") == "data"

    def test_creative_task(self):
        assert classify_task("Write a story about a robot brainstorm ideas") == "creative"

    def test_general_fallback(self):
        assert classify_task("Hello, how are you today?") == "general"

    def test_mixed_signals(self):
        """When multiple keywords match, the highest-scoring type wins."""
        result = classify_task("Write code to fix the bug and debug the error")
        assert result in ("code", "debug")  # Both valid


# ── Model Family Matching ────────────────────────────────────────

class TestMatchModelFamily:
    def test_exact_match(self):
        assert _match_model_family("qwen2.5-coder:32b") == "qwen2.5-coder"

    def test_versioned_match(self):
        assert _match_model_family("llama3.1:70b") == "llama3.1"

    def test_gemma_match(self):
        assert _match_model_family("gemma3:4b") == "gemma3"

    def test_unknown_model(self):
        assert _match_model_family("totally-unknown-model:1b") is None

    def test_longest_match_wins(self):
        """qwen2.5-coder should match over qwen2.5."""
        assert _match_model_family("qwen2.5-coder:7b") == "qwen2.5-coder"


# ── Model Routing ────────────────────────────────────────────────

class TestRouteModel:
    @pytest.mark.asyncio
    async def test_no_models_installed(self):
        """When no models are installed, returns default."""
        with patch("backend.model_router.get_installed_models", new_callable=AsyncMock, return_value=[]):
            result = await route_model("Write some code")
            assert result["selected_model"] == "qwen2.5-coder:32b"
            assert "No models" in result["reason"]

    @pytest.mark.asyncio
    async def test_single_model(self):
        """When only one model is installed, always returns it."""
        with patch("backend.model_router.get_installed_models", new_callable=AsyncMock, return_value=["gemma3:4b"]):
            result = await route_model("Fix this bug")
            assert result["selected_model"] == "gemma3:4b"
            assert "Only installed" in result["reason"]

    @pytest.mark.asyncio
    async def test_coding_task_prefers_coder(self):
        """For code tasks, qwen2.5-coder should score highest."""
        models = ["qwen2.5-coder:7b", "gemma3:4b", "llama3.1:8b"]
        with patch("backend.model_router.get_installed_models", new_callable=AsyncMock, return_value=models):
            result = await route_model("Write a Python class")
            assert result["selected_model"] == "qwen2.5-coder:7b"
            assert result["task_type"] == "code"

    @pytest.mark.asyncio
    async def test_larger_model_gets_bonus(self):
        """Larger models should get a size bonus."""
        models = ["qwen2.5-coder:7b", "qwen2.5-coder:32b"]
        with patch("backend.model_router.get_installed_models", new_callable=AsyncMock, return_value=models):
            result = await route_model("Write a function")
            assert result["selected_model"] == "qwen2.5-coder:32b"

    @pytest.mark.asyncio
    async def test_alternatives_populated(self):
        """Alternatives should list other available models."""
        models = ["qwen2.5-coder:7b", "gemma3:4b", "llama3.1:8b"]
        with patch("backend.model_router.get_installed_models", new_callable=AsyncMock, return_value=models):
            result = await route_model("Write code")
            assert len(result["alternatives"]) > 0
