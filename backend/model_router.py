"""
Model Router — Smart routing between local Ollama and cloud Gemini.

Routing logic:
  - Simple tasks → Local Ollama (7B) — free, fully private
  - Medium tasks → Local Ollama (32B if available) — free, private
  - Complex tasks → Gemini 2.0 Flash (free tier) — needs user approval
  - Very hard tasks → Gemini 2.5 Pro (free tier) — needs user approval

The router checks if Gemini is available and whether the user has approved
cloud usage for the current session. Cloud models require approval via
the propose_action tool before each use.
"""

import logging
from typing import Optional

logger = logging.getLogger("localmind.model_router")


# ── Model Definitions ──────────────────────────────────────────────────
MODELS = {
    "local_micro": {
        "name": "gemma3:4b",
        "provider": "ollama",
        "privacy": "fully_private",
        "description": "Tiny local model for instant startup and simple chat",
    },
    "local_light": {
        "name": "qwen2.5-coder:32b",
        "provider": "ollama",
        "privacy": "fully_private",
        "description": "Powerful local model for robust code generation (Sam's daily driver)",
    },
    "local_heavy": {
        "name": "qwen2.5-coder:32b",
        "provider": "ollama",
        "privacy": "fully_private",
        "description": "Powerful local model for robust code generation and editing",
    },
    "local_ultra": {
        "name": "qwen2.5-coder:70b",
        "provider": "ollama",
        "privacy": "fully_private",
        "description": "Elite local model for complex architectural reasoning (requires 256GB+ RAM)",
    },
    "cloud_flash": {
        "name": "gemini-2.0-flash",
        "provider": "gemini",
        "privacy": "cloud",
        "description": "Google Gemini Flash — fast cloud model with game complexity simulation (free tier)",
    },
    "cloud_pro": {
        "name": "gemini-2.5-pro",
        "provider": "gemini",
        "privacy": "cloud",
        "description": "Google Gemini Pro — strongest reasoning (free tier)",
    },
}


def route_model(
    complexity_score: int,
    force_local: bool = False,
    gemini_available: bool = False,
    cloud_approved: bool = False,
) -> dict:
    """Pick the best model based on task complexity and constraints.
    
    Args:
        complexity_score: 0-10 complexity rating from estimate_task_complexity()
        force_local: User preference to stay local regardless
        gemini_available: Whether GEMINI_API_KEY is configured
        cloud_approved: Whether user approved cloud usage for this request
    
    Returns:
        Model config dict with name, provider, privacy, needs_approval
    """
    # Always respect user's force_local preference
    if force_local:
        if complexity_score >= 8:
            model = MODELS["local_ultra"].copy()
        elif complexity_score >= 6:
            model = MODELS["local_heavy"].copy()
        else:
            model = MODELS["local_light"].copy()
        model["needs_approval"] = False
        model["route_reason"] = "User prefers local (Ultra/Heavy tier)"
        return model

    # Trivial tasks → micro model (instant response)
    if complexity_score <= 2:
        model = MODELS["local_micro"].copy()
        model["needs_approval"] = False
        model["route_reason"] = "Simple chat — using micro model"
        return model

    # Simple chat — using micro model
    if complexity_score <= 2:
        model = MODELS["local_micro"].copy()
        model["needs_approval"] = False
        model["route_reason"] = "Simple chat — using micro model"
        return model

    # Simple tasks → local light
    if complexity_score <= 4:
        model = MODELS["local_light"].copy()
        model["needs_approval"] = False
        model["route_reason"] = "Simple task — handled locally"
        return model

    # Medium tasks → local heavy
    if complexity_score <= 6:
        model = MODELS["local_heavy"].copy()
        model["needs_approval"] = False
        model["route_reason"] = "Medium complexity — using larger local model"
        return model
    
    # High complexity → local ultra (if forced/configured) or cloud
    if complexity_score <= 8:
        if gemini_available and cloud_approved:
            model = MODELS["cloud_flash"].copy()
            model["needs_approval"] = False
            model["route_reason"] = "Complex task — using Gemini Flash (approved)"
            return model
        else:
            # Fallback to the strongest local model
            model = MODELS["local_ultra"].copy()
            model["needs_approval"] = False
            model["route_reason"] = "Complex task — using local Ultra (Gemini not configured/approved)"
            return model

    # Very hard tasks → Ultra Local or Gemini Pro
    if gemini_available and cloud_approved:
        model = MODELS["cloud_pro"].copy()
        model["needs_approval"] = False
        model["route_reason"] = "Very complex task — using Gemini Pro (approved)"
        return model
    else:
        model = MODELS["local_ultra"].copy()
        model["needs_approval"] = False
        model["route_reason"] = "High complexity — using local Ultra"
        return model


def get_available_models() -> list[dict]:
    """Return all models with their availability status."""
    from backend.gemini_client import is_available as gemini_check
    
    models = []
    for key, config in MODELS.items():
        entry = config.copy()
        entry["key"] = key
        if config["provider"] == "gemini":
            entry["available"] = gemini_check()
        else:
            entry["available"] = True  # Assume Ollama is running
        models.append(entry)
    return models


def get_autonomy_models() -> dict:
    """Return model names for autonomy engine tasks.
    
    Reflection/proposals → 14B or 32B (good for reasoning)
    Code editing → 70B (Ultra precision for 256GB systems)
    """
    return {
        "reflection": MODELS["local_heavy"]["name"],
        "editing": MODELS["local_ultra"]["name"],
        "file_targeting": MODELS["local_heavy"]["name"],
    }


def get_startup_model() -> str:
    """Return the model to pre-warm on boot (smallest available)."""
    return MODELS["local_micro"]["name"]
