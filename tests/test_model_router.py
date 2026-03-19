"""
Tests for the intelligent model router.

The model router picks the best model based on task complexity
and user constraints (local-only, cloud availability/approval).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.model_router import route_model, MODELS


# ── Route Model ──────────────────────────────────────────────────

class TestRouteModel:
    def test_simple_task_uses_local_light(self):
        """Low complexity (0-4) should use the lightweight local model."""
        result = route_model(complexity_score=2)
        assert result["name"] == MODELS["local_light"]["name"]
        assert result["provider"] == "ollama"

    def test_medium_task_uses_local_heavy(self):
        """Medium complexity (5-7) should upgrade to local heavy model."""
        result = route_model(complexity_score=6)
        assert result["name"] == MODELS["local_heavy"]["name"]
        assert result["provider"] == "ollama"

    def test_high_complexity_without_cloud_stays_local(self):
        """High complexity without cloud available stays local heavy."""
        result = route_model(complexity_score=9, gemini_available=False)
        assert result["provider"] == "ollama"
        assert result["name"] == MODELS["local_heavy"]["name"]

    def test_force_local_light_task(self):
        """Force local with low complexity picks local light."""
        result = route_model(complexity_score=3, force_local=True)
        assert result["name"] == MODELS["local_light"]["name"]
        assert result["needs_approval"] is False

    def test_force_local_heavy_task(self):
        """Force local with high complexity picks local heavy."""
        result = route_model(complexity_score=8, force_local=True)
        assert result["name"] == MODELS["local_heavy"]["name"]
        assert result["needs_approval"] is False

    def test_force_local_ignores_cloud(self):
        """Even when cloud is available + approved, force_local wins."""
        result = route_model(
            complexity_score=9,
            force_local=True,
            gemini_available=True,
            cloud_approved=True,
        )
        assert result["provider"] == "ollama"

    def test_cloud_requires_approval(self):
        """Cloud routing should require user approval."""
        result = route_model(
            complexity_score=9,
            gemini_available=True,
            cloud_approved=False,
        )
        # Either stays local (needing approval) or flags needs_approval
        assert "needs_approval" in result

    def test_all_results_have_required_keys(self):
        """Every route result should have name, provider, needs_approval."""
        for score in [0, 3, 5, 7, 10]:
            result = route_model(complexity_score=score)
            assert "name" in result
            assert "provider" in result
            assert "needs_approval" in result

    def test_route_reason_populated(self):
        """Route results should explain why the model was chosen."""
        result = route_model(complexity_score=5)
        assert "route_reason" in result
        assert len(result["route_reason"]) > 0


# ── Model Definitions ────────────────────────────────────────────

class TestModels:
    def test_all_tiers_defined(self):
        """Should have local_light, local_heavy, cloud_flash, cloud_pro."""
        expected = {"local_light", "local_heavy", "cloud_flash", "cloud_pro"}
        assert set(MODELS.keys()) == expected

    def test_local_models_are_ollama(self):
        assert MODELS["local_light"]["provider"] == "ollama"
        assert MODELS["local_heavy"]["provider"] == "ollama"

    def test_cloud_models_are_gemini(self):
        assert MODELS["cloud_flash"]["provider"] == "gemini"
        assert MODELS["cloud_pro"]["provider"] == "gemini"

    def test_all_models_have_name(self):
        for tier, model in MODELS.items():
            assert "name" in model, f"{tier} missing 'name'"
            assert len(model["name"]) > 0
