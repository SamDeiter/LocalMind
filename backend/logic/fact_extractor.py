"""Fact Extractor — pulls structured user-facts from conversation turns.

After each user message, a small/fast LLM is asked to extract any durable
facts about the user (name, role, preferences, goals, tools, etc.) as a
JSON list. Results are persisted to:

  1. The FTS5 memory store (existing semantic memory)
  2. The MemoryManager preference store (with confidence + decay)

This is what lets LocalMind "get to know" the user across conversations.
The AI is the learner here — it absorbs facts about the user, it does not
teach them.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend import config
from backend.logic.llm_client import LLMClient

logger = logging.getLogger("localmind.logic.fact_extractor")

EXTRACTOR_SYSTEM_PROMPT = """You extract durable facts about a user from a single message.

Return a JSON array. Each item: {"key": str, "value": str, "source": "explicit"|"inferred", "confidence": 0.0-1.0}.

Rules:
- Only durable facts (name, role, location, tools, preferences, goals, hobbies). Skip task requests, one-off questions, transient state.
- "explicit" = user directly stated it ("I'm a data scientist"). "inferred" = strongly implied.
- Skip secrets, passwords, tokens, full addresses, phone numbers, SSNs.
- Use stable dotted keys: identity.name, identity.role, identity.location, work.tools, prefs.style, prefs.tone, goals.current, hobbies.
- Lowercase values, concise (under 60 chars).
- If nothing durable, return [].
- Output ONLY the JSON array. No prose, no markdown fences.

Examples:
"I'm Sam, a backend engineer at Epic" -> [{"key":"identity.name","value":"Sam","source":"explicit","confidence":0.95},{"key":"identity.role","value":"backend engineer at epic","source":"explicit","confidence":0.9}]
"can you fix this bug" -> []
"I prefer terse responses" -> [{"key":"prefs.style","value":"terse","source":"explicit","confidence":0.9}]
"""

FORBIDDEN_VALUE_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),       # SSN
    re.compile(r"\b\d{16}\b"),                   # credit card-ish
    re.compile(r"(?i)password|secret|token|api[_-]?key"),
]

ALLOWED_KEY_PREFIXES = (
    "identity.", "work.", "prefs.", "goals.", "hobbies.", "comms.",
)


def _is_safe(item: Dict[str, Any]) -> bool:
    key = (item.get("key") or "").strip().lower()
    val = (item.get("value") or "").strip()
    if not key or not val:
        return False
    if not any(key.startswith(p) for p in ALLOWED_KEY_PREFIXES):
        return False
    if len(val) > 120:
        return False
    if any(p.search(val) for p in FORBIDDEN_VALUE_PATTERNS):
        return False
    return True


def _parse_json_array(raw: str) -> List[Dict[str, Any]]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(raw[start : end + 1])
        if isinstance(parsed, list):
            return [p for p in parsed if isinstance(p, dict)]
    except json.JSONDecodeError:
        return []
    return []


class FactExtractor:
    def __init__(self, llm: Optional[LLMClient] = None, model: Optional[str] = None):
        self.llm = llm or LLMClient()
        # Use the lightest configured model for extraction — speed > nuance.
        self.model = model or config.MODEL_TIERS.get("light", "gemma4:e2b")

    async def extract(self, user_message: str) -> List[Dict[str, Any]]:
        msg = (user_message or "").strip()
        if len(msg) < 8 or len(msg) > 4000:
            return []

        messages = [
            {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
            {"role": "user", "content": msg},
        ]

        try:
            result = await self.llm.generate(self.model, messages, provider="ollama")
        except Exception as exc:
            logger.debug("Fact extractor LLM call failed: %s", exc)
            return []

        if not isinstance(result, dict) or result.get("error"):
            return []

        items = _parse_json_array(result.get("content", ""))
        safe = [i for i in items if _is_safe(i)]
        if safe:
            logger.info("Fact extractor: %d fact(s) from user message", len(safe))
        return safe
