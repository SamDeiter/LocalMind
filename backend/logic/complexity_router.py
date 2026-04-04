"""
Complexity Router — Heuristic-based model selection for user-facing chat.

Classifies incoming messages by complexity (simple / moderate / complex)
and maps them to the best available Ollama model, keeping overhead <1ms.

This module does NOT affect autonomy-engine model selection, which has
its own routing via backend.model_router.get_autonomy_models().
"""

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import httpx

from backend import config

logger = logging.getLogger("localmind.logic.complexity_router")

# ── Cached model list ─────────────────────────────────────────────────
_model_cache: Dict[str, object] = {"models": [], "expires": 0.0}
_CACHE_TTL = 300  # 5 minutes


# ── Keyword / pattern sets (compiled once at import time) ─────────────

_GREETING_PATTERNS = re.compile(
    r"^(hi|hey|hello|howdy|yo|sup|good\s+(morning|afternoon|evening)|what'?s?\s+up)\b",
    re.IGNORECASE,
)

_SIMPLE_PATTERNS = re.compile(
    r"^(thanks|thank\s+you|ok|okay|yes|no|sure|got\s+it|cool|nice)\b",
    re.IGNORECASE,
)

_COMPLEX_KEYWORDS = frozenset({
    "refactor", "architecture", "design pattern", "implement", "build",
    "optimize", "performance", "benchmark", "migrate", "convert",
    "debug", "stack trace", "traceback", "exception", "segfault",
    "review", "code review", "pull request", "diff",
    "algorithm", "data structure", "complexity",
    "deploy", "ci/cd", "docker", "kubernetes",
    "database", "schema", "migration", "sql",
    "security", "authentication", "authorization", "encrypt",
    "api design", "system design", "microservice",
    "machine learning", "neural network", "training",
    "concurrency", "async", "threading", "multiprocess",
})

_MODERATE_KEYWORDS = frozenset({
    "code", "function", "class", "method", "variable",
    "error", "bug", "fix", "issue", "problem",
    "explain", "how does", "how do", "what is", "why does",
    "test", "unit test", "pytest",
    "file", "read", "write", "parse", "json", "csv",
    "install", "setup", "configure", "config",
    "git", "commit", "branch", "merge",
    "loop", "condition", "if statement",
    "import", "module", "package", "library",
    "type", "typing", "annotation",
})

_CODE_FENCE = re.compile(r"```")
_INLINE_CODE = re.compile(r"`[^`]+`")


# ── Classification ────────────────────────────────────────────────────

def classify_complexity(
    messages: List[Dict[str, str]],
    task_type: Optional[str] = None,
) -> str:
    """Classify a conversation's complexity as 'simple', 'moderate', or 'complex'.

    Heuristic-only — no LLM call, designed for <1ms overhead.

    Args:
        messages: The conversation messages (at minimum the latest user message).
        task_type: Optional hint like 'code_generation', 'chat', 'review'.

    Returns:
        One of: 'simple', 'moderate', 'complex'.
    """
    # Fast-path: if caller already knows it's complex
    if task_type in ("code_generation", "review", "refactor", "architecture"):
        return "complex"
    if task_type in ("greeting", "thanks"):
        return "simple"

    # Extract the latest user message
    user_text = ""
    user_turn_count = 0
    for msg in messages:
        if msg.get("role") == "user":
            user_text = msg.get("content", "")
            user_turn_count += 1

    if not user_text:
        return "simple"

    score = 0  # accumulate; thresholds: <3 simple, 3-6 moderate, >6 complex

    text_lower = user_text.lower()
    char_len = len(user_text)

    # ── Length signals ────────────────────────────────────────────
    # Rough token estimate: chars / 4
    approx_tokens = char_len / 4

    if approx_tokens < 25:
        score -= 1  # very short
    elif approx_tokens < 100:
        pass  # neutral
    elif approx_tokens < 400:
        score += 2
    else:
        score += 4  # long context

    # ── Greeting / trivial check ──────────────────────────────────
    if _GREETING_PATTERNS.search(user_text) and char_len < 80:
        return "simple"  # fast exit

    if _SIMPLE_PATTERNS.match(user_text) and char_len < 40:
        return "simple"

    # ── Code presence ─────────────────────────────────────────────
    code_fences = len(_CODE_FENCE.findall(user_text))
    inline_code = len(_INLINE_CODE.findall(user_text))

    if code_fences >= 2:
        score += 3  # at least one fenced code block
    elif inline_code >= 3:
        score += 1

    # ── Keyword matching ──────────────────────────────────────────
    complex_hits = sum(1 for kw in _COMPLEX_KEYWORDS if kw in text_lower)
    moderate_hits = sum(1 for kw in _MODERATE_KEYWORDS if kw in text_lower)

    score += min(complex_hits * 2, 6)   # cap contribution at 6
    score += min(moderate_hits, 3)       # cap at 3

    # ── Multi-turn signal ─────────────────────────────────────────
    if user_turn_count >= 4:
        score += 2
    elif user_turn_count >= 2:
        score += 1

    # ── Multi-step reasoning indicators ───────────────────────────
    step_indicators = sum(1 for p in [
        r"\bstep\s*\d",
        r"\bfirst\b.*\bthen\b",
        r"\b(1\.|2\.|3\.)",
        r"\bcompare\b.*\b(and|vs|versus)\b",
    ] if re.search(p, text_lower))
    score += min(step_indicators * 2, 4)

    # ── Classify ──────────────────────────────────────────────────
    if score <= 2:
        return "simple"
    elif score <= 6:
        return "moderate"
    else:
        return "complex"


# ── Model routing ─────────────────────────────────────────────────────

# Size tiers for known model families.  The number is extracted from the
# model tag (e.g. "llama3.1:8b" → 8).  This lets us sort any Ollama model
# into small / medium / large buckets without maintaining a full catalog.
_SIZE_RE = re.compile(r":(\d+\.?\d*)b", re.IGNORECASE)


def _extract_param_size(model_name: str) -> float:
    """Extract the parameter size in billions from a model tag, or 0."""
    m = _SIZE_RE.search(model_name)
    return float(m.group(1)) if m else 0.0


def route_model(
    complexity: str,
    available_models: List[str],
) -> Tuple[str, str]:
    """Pick the best Ollama model for the given complexity tier.

    Args:
        complexity: 'simple', 'moderate', or 'complex'.
        available_models: List of model name strings from Ollama.

    Returns:
        (model_name, reason) tuple.
    """
    if not available_models:
        fallback = config.MODEL_TIERS.get("light", "qwen2.5-coder:7b")
        return fallback, "no models from Ollama; using config fallback"

    # Check user-configured tier preferences first
    prefs = getattr(config, "COMPLEXITY_MODEL_PREFS", {})
    if complexity in prefs:
        preferred = prefs[complexity]
        # Walk the preference list; pick the first that is available
        for pref in preferred:
            if pref in available_models:
                return pref, f"matched preference for {complexity}"

    # Fallback: sort available models by parameter size and bucket
    sized = [(name, _extract_param_size(name)) for name in available_models]
    # Filter to models whose size we can determine
    known = [(n, s) for n, s in sized if s > 0]
    if not known:
        # Cannot determine sizes — fall back to config tiers
        tier_map = {"simple": "light", "moderate": "medium", "complex": "heavy"}
        cfg_model = config.MODEL_TIERS.get(tier_map.get(complexity, "light"), "qwen2.5-coder:7b")
        return cfg_model, f"could not size models; using config tier '{tier_map.get(complexity)}'"

    known.sort(key=lambda x: x[1])

    if complexity == "simple":
        # Smallest available model
        model = known[0][0]
        return model, f"simple task -> smallest model ({known[0][1]}B)"

    if complexity == "moderate":
        # Middle-of-the-road: pick the median model
        idx = len(known) // 2
        model = known[idx][0]
        return model, f"moderate task -> mid-tier model ({known[idx][1]}B)"

    # complex
    model = known[-1][0]
    return model, f"complex task -> largest model ({known[-1][1]}B)"


# ── Ollama model discovery (async, cached) ────────────────────────────

async def get_available_models(ollama_url: Optional[str] = None) -> List[str]:
    """Query Ollama /api/tags and return a list of model name strings.

    Results are cached for 5 minutes to avoid repeated HTTP round-trips.
    """
    now = time.monotonic()
    if _model_cache["models"] and now < _model_cache["expires"]:
        return _model_cache["models"]

    url = (ollama_url or config.OLLAMA_BASE_URL).rstrip("/") + "/api/tags"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        models = [m["name"] for m in data.get("models", [])]
        _model_cache["models"] = models
        _model_cache["expires"] = now + _CACHE_TTL
        logger.debug("Refreshed Ollama model list: %s", models)
        return models

    except Exception as exc:
        logger.warning("Failed to query Ollama models: %s", exc)
        # Return stale cache if available, else empty
        return _model_cache.get("models", [])


# ── Convenience: one-call classification + routing ────────────────────

async def auto_route(
    messages: List[Dict[str, str]],
    task_type: Optional[str] = None,
    ollama_url: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Classify and route in one call.

    Returns:
        (model_name, complexity, reason)
    """
    complexity = classify_complexity(messages, task_type)
    available = await get_available_models(ollama_url)
    model, reason = route_model(complexity, available)
    logger.info(
        "complexity_router: complexity=%s  model=%s  reason=%s",
        complexity, model, reason,
    )
    return model, complexity, reason
