"""
Intent Parser — Extract structured IntentState from user input via LLM.

Uses a single LLM call with a structured output prompt to populate
all fields of the IntentState dataclass. Falls back to a heuristic
parser if the LLM call fails or exceeds the call budget.
"""

import json
import logging
from typing import Optional

import httpx

from backend.metacognition.models.intent import IntentState, Assumption, ConfidenceLevel

logger = logging.getLogger("metacognition.intent_parser")

INTENT_EXTRACTION_PROMPT = """Analyze this user message and extract structured intent.

USER MESSAGE: {user_input}

RECENT CONVERSATION (last 3 turns):
{recent_history}

Output a JSON object with exactly these fields:
- explicit_request: string — what they literally asked for
- inferred_goal: string — what they actually want to accomplish
- subgoals: list of strings — decomposed sub-tasks (if any)
- constraints: list of strings — hard requirements mentioned or implied
- forbidden_actions: list of strings — things NOT to do
- quality_bar: "draft" | "production" | "critical"
- preferred_output_style: "concise" | "detailed" | "code-first" | ""
- domain_context: "code" | "factual" | "creative" | "system" | "general"
- unresolved_ambiguities: list of strings — things that are unclear
- assumptions: list of objects with {{statement, confidence, source}} where
    confidence is "high" | "medium" | "low" | "none"
    source is "explicit" | "inferred" | "default"
- needs_tool: boolean — does this need file access, search, or code execution?
- needs_clarification: boolean — is the request too vague to act on?
- references_past_context: boolean — does this refer to earlier conversation?

Output ONLY valid JSON, nothing else."""


class IntentParser:
    """Extracts structured IntentState from user input."""

    def __init__(self, ollama_url: str = "http://localhost:11434", model: str = ""):
        self.ollama_url = ollama_url
        self.model = model or "qwen2.5-coder:7b"

    async def parse(
        self,
        user_input: str,
        history: list = None,
        previous_intent: Optional[IntentState] = None,
    ) -> IntentState:
        """
        Parse user input into structured IntentState.

        If LLM call fails, falls back to heuristic parsing.
        """
        try:
            intent = await self._llm_parse(user_input, history or [])
            if intent:
                # Merge with previous intent (carry forward constraints)
                if previous_intent:
                    intent = self._merge_with_previous(intent, previous_intent)
                return intent
        except Exception as e:
            logger.warning(f"LLM intent parse failed: {e}")

        # Fallback: heuristic parsing
        return self._heuristic_parse(user_input, previous_intent)

    async def _llm_parse(self, user_input: str, history: list) -> Optional[IntentState]:
        """Use LLM to extract structured intent."""
        recent = ""
        if history:
            last_3 = history[-6:]  # last 3 turns (user+assistant pairs)
            recent = "\n".join(
                f"  {m.get('role', '?')}: {m.get('content', '')[:150]}"
                for m in last_3
            )

        prompt = INTENT_EXTRACTION_PROMPT.format(
            user_input=user_input,
            recent_history=recent or "(no previous turns)",
        )

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 500, "temperature": 0.1},
                },
            )

            if resp.status_code != 200:
                return None

            text = resp.json().get("response", "").strip()

            # Extract JSON from response (handles markdown code blocks)
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            data = json.loads(text)
            return IntentState.from_dict(data)

    def _heuristic_parse(
        self,
        user_input: str,
        previous_intent: Optional[IntentState] = None,
    ) -> IntentState:
        """
        Fast, no-LLM fallback for intent parsing.
        Uses keyword matching to approximate intent fields.
        """
        lower = user_input.lower()

        # Domain detection
        domain = "general"
        if any(w in lower for w in ["code", "function", "class", "bug", "error", "fix"]):
            domain = "code"
        elif any(w in lower for w in ["what is", "how does", "explain", "define"]):
            domain = "factual"
        elif any(w in lower for w in ["write", "story", "poem", "creative"]):
            domain = "creative"
        elif any(w in lower for w in ["file", "system", "install", "run", "deploy"]):
            domain = "system"

        # Needs tool?
        needs_tool = any(w in lower for w in [
            "file", "read", "search", "run", "execute", "current", "open",
        ])

        # Needs clarification?
        needs_clarification = (
            len(user_input.split()) < 3 and "?" not in user_input
        )

        # Quality bar
        quality_bar = "production"
        if any(w in lower for w in ["quick", "draft", "rough", "just"]):
            quality_bar = "draft"
        elif any(w in lower for w in ["critical", "production", "important", "careful"]):
            quality_bar = "critical"

        # References past context?
        references_past = any(w in lower for w in [
            "before", "earlier", "last time", "we discussed", "remember",
            "you said", "that thing", "the same",
        ])

        intent = IntentState(
            explicit_request=user_input,
            inferred_goal=user_input,  # Heuristic: same as explicit
            domain_context=domain,
            needs_tool=needs_tool,
            needs_clarification=needs_clarification,
            quality_bar=quality_bar,
            references_past_context=references_past,
        )

        if previous_intent:
            intent = self._merge_with_previous(intent, previous_intent)

        return intent

    def _merge_with_previous(
        self,
        current: IntentState,
        previous: IntentState,
    ) -> IntentState:
        """
        Carry forward stable context from previous turn.

        Rules:
          - constraints: accumulated (never dropped)
          - forbidden_actions: accumulated (never dropped)
          - preferred_output_style: inherited if not set
          - subgoals from previous turn are NOT carried (each turn re-evaluates)
        """
        # Carry constraints forward
        prev_constraints = set(previous.constraints)
        for c in current.constraints:
            prev_constraints.add(c)
        current.constraints = list(prev_constraints)

        # Carry forbidden actions forward
        prev_forbidden = set(previous.forbidden_actions)
        for f in current.forbidden_actions:
            prev_forbidden.add(f)
        current.forbidden_actions = list(prev_forbidden)

        # Inherit style if not specified this turn
        if not current.preferred_output_style and previous.preferred_output_style:
            current.preferred_output_style = previous.preferred_output_style

        return current
