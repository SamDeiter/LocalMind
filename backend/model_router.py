"""
LocalMind — Intelligent Model Router
Automatically selects the best installed model for each task.

Concept: Classify the task type, then route to the best available model.
Models are scored by capability for each task type. If a preferred model
isn't installed, falls back to the next best available option.
"""

import httpx

OLLAMA_BASE_URL = "http://localhost:11434"

# ── Model Capability Profiles ───────────────────────────────────────────────
# Each model family gets scored on different task types (0-10).
# When multiple models are installed, the router picks the highest-scoring one.

MODEL_PROFILES = {
    # Coding specialists
    "qwen2.5-coder": {"code": 10, "debug": 9, "explain": 8, "plan": 7, "general": 6, "creative": 5, "data": 7},
    "deepseek-coder": {"code": 9,  "debug": 9, "explain": 7, "plan": 6, "general": 5, "creative": 4, "data": 7},
    "codellama":      {"code": 8,  "debug": 7, "explain": 6, "plan": 5, "general": 4, "creative": 3, "data": 5},

    # General purpose
    "llama3":         {"code": 6, "debug": 6, "explain": 9, "plan": 9, "general": 9, "creative": 8, "data": 7},
    "llama3.1":       {"code": 7, "debug": 7, "explain": 9, "plan": 9, "general": 9, "creative": 8, "data": 8},
    "llama3.2":       {"code": 7, "debug": 7, "explain": 9, "plan": 9, "general": 9, "creative": 8, "data": 8},
    "gemma2":         {"code": 7, "debug": 7, "explain": 8, "plan": 8, "general": 8, "creative": 7, "data": 7},
    "gemma3":         {"code": 7, "debug": 7, "explain": 9, "plan": 9, "general": 9, "creative": 8, "data": 8},
    "phi3":           {"code": 7, "debug": 6, "explain": 8, "plan": 7, "general": 8, "creative": 7, "data": 7},
    "phi4":           {"code": 8, "debug": 7, "explain": 9, "plan": 8, "general": 9, "creative": 8, "data": 8},
    "qwen2.5":        {"code": 8, "debug": 7, "explain": 8, "plan": 8, "general": 8, "creative": 7, "data": 8},
    "qwen3":          {"code": 8, "debug": 8, "explain": 9, "plan": 9, "general": 9, "creative": 8, "data": 8},

    # Reasoning / planning
    "mistral":        {"code": 7, "debug": 7, "explain": 8, "plan": 8, "general": 8, "creative": 7, "data": 7},
    "mixtral":        {"code": 7, "debug": 7, "explain": 9, "plan": 9, "general": 9, "creative": 8, "data": 8},
    "command-r":      {"code": 6, "debug": 6, "explain": 8, "plan": 8, "general": 8, "creative": 7, "data": 8},

    # Creative / writing
    "neural-chat":    {"code": 4, "debug": 4, "explain": 7, "plan": 6, "general": 7, "creative": 8, "data": 5},
}

# ── Task Classification Keywords ───────────────────────────────────────────
TASK_KEYWORDS = {
    "code": [
        "write", "create", "build", "implement", "function", "class", "script",
        "program", "code", "develop", "api", "endpoint", "component", "module",
        "html", "css", "javascript", "python", "react", "flask", "django",
    ],
    "debug": [
        "fix", "bug", "error", "broken", "crash", "issue", "failing", "wrong",
        "debug", "traceback", "exception", "not working", "doesn't work",
    ],
    "explain": [
        "explain", "how does", "what is", "what does", "why", "understand",
        "describe", "tell me about", "walk me through", "how to",
    ],
    "plan": [
        "plan", "design", "architect", "strategy", "approach", "structure",
        "organize", "roadmap", "steps", "break down", "project",
    ],
    "data": [
        "data", "csv", "json", "database", "sql", "scrape", "parse", "analyze",
        "extract", "transform", "pandas", "spreadsheet", "table",
    ],
    "creative": [
        "write a story", "creative", "blog", "article", "content", "email",
        "marketing", "copy", "name", "brainstorm", "ideas",
    ],
}


def classify_task(message: str) -> str:
    """Classify a user message into a task type."""
    msg_lower = message.lower()
    scores = {task_type: 0 for task_type in TASK_KEYWORDS}

    for task_type, keywords in TASK_KEYWORDS.items():
        for keyword in keywords:
            if keyword in msg_lower:
                scores[task_type] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def _match_model_family(model_name: str) -> str | None:
    """Match an installed model name to a known family profile."""
    name_lower = model_name.lower()
    # Try exact prefix match first, longest match wins
    matches = []
    for family in MODEL_PROFILES:
        if name_lower.startswith(family):
            matches.append(family)
    if matches:
        return max(matches, key=len)  # Longest match
    return None


async def get_installed_models() -> list[str]:
    """Fetch the list of installed Ollama models."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


async def route_model(message: str, preferred_model: str = None) -> dict:
    """
    Intelligently select the best model for a given message.

    Returns:
        {
            "selected_model": "model-name",
            "task_type": "code",
            "reason": "Why this model was selected",
            "alternatives": ["other-model-1", "other-model-2"],
        }
    """
    task_type = classify_task(message)
    installed = await get_installed_models()

    if not installed:
        return {
            "selected_model": preferred_model or "qwen2.5-coder:32b",
            "task_type": task_type,
            "reason": "No models detected — using default",
            "alternatives": [],
        }

    # If only one model is installed, use it
    if len(installed) == 1:
        return {
            "selected_model": installed[0],
            "task_type": task_type,
            "reason": f"Only installed model (task: {task_type})",
            "alternatives": [],
        }

    # Score each installed model for this task type
    scored = []
    for model_name in installed:
        family = _match_model_family(model_name)
        if family:
            profile = MODEL_PROFILES[family]
            score = profile.get(task_type, profile.get("general", 5))
            # Bonus for larger models (more parameters = generally better)
            size_bonus = 0
            for size in ["70b", "32b", "14b"]:
                if size in model_name.lower():
                    size_bonus = 2
                    break
            scored.append({"model": model_name, "score": score + size_bonus, "family": family})
        else:
            # Unknown model — give it a middle score
            scored.append({"model": model_name, "score": 5, "family": "unknown"})

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    best = scored[0]
    alternatives = [s["model"] for s in scored[1:3]]

    return {
        "selected_model": best["model"],
        "task_type": task_type,
        "reason": f"Best for {task_type} tasks (score: {best['score']}/12, family: {best['family']})",
        "alternatives": alternatives,
    }
