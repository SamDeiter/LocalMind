"""
Self-Checker — Post-generation 5-point verification against IntentState.

Asks specific questions, not "is this good?":
  1. Does the output address the inferred_goal?
  2. Does it violate any constraints?
  3. Does it violate any forbidden_actions?
  4. Does it contain unsupported factual claims?
  5. Does it match the preferred output style?
"""

import json
import logging
from typing import Optional

import httpx

from backend.metacognition.models.intent import IntentState
from backend.metacognition.models.actions import CheckResult

logger = logging.getLogger("metacognition.self_checker")

SELF_CHECK_PROMPT = """You are a quality reviewer. Check this AI-generated response against the user's actual requirements.

USER'S ACTUAL GOAL: {inferred_goal}
EXPLICIT REQUEST: {explicit_request}
CONSTRAINTS: {constraints}
FORBIDDEN ACTIONS: {forbidden_actions}
PREFERRED STYLE: {preferred_style}

AI's RESPONSE:
{draft}

Check each criterion and output a JSON object:
- addresses_goal: boolean — does the response address the user's actual goal?
- violates_constraints: boolean — does it violate any stated constraint?
- violates_forbidden: boolean — does it do something explicitly forbidden?
- unsupported_claims: boolean — does it contain factual claims without evidence?
- matches_style: boolean — does it match the preferred output style?
- issues: list of strings — specific problems found (empty if none)
- overall_pass: boolean — should this response be sent to the user as-is?

Output ONLY valid JSON."""


class SelfChecker:
    """
    Post-generation verification. Checks draft against IntentState.

    Can operate in two modes:
      - LLM mode: uses an LLM call for nuanced checking
      - Heuristic mode: fast pattern matching (no LLM call needed)
    """

    def __init__(self, ollama_url: str = "http://localhost:11434", model: str = ""):
        self.ollama_url = ollama_url
        self.model = model or "qwen2.5-coder:7b"

    async def check(
        self,
        draft: str,
        intent: IntentState,
        use_llm: bool = True,
    ) -> CheckResult:
        """
        Run the 5-point check on a draft response.

        Args:
            draft: The generated response text
            intent: The user's parsed intent
            use_llm: If True, use LLM for nuanced checking. If False, heuristic only.
        """
        # Always run heuristic checks (fast, free)
        heuristic_result = self._heuristic_check(draft, intent)

        # If heuristic already found issues, no need for LLM
        if not heuristic_result.passed:
            return heuristic_result

        # If LLM check requested and draft is non-trivial
        if use_llm and len(draft) > 100:
            llm_result = await self._llm_check(draft, intent)
            if llm_result:
                return self._merge_results(heuristic_result, llm_result)

        return heuristic_result

    def _heuristic_check(self, draft: str, intent: IntentState) -> CheckResult:
        """Fast pattern-matching checks that don't require an LLM call."""
        result = CheckResult()
        draft_lower = draft.lower()

        # Check 1: Does it address the goal? (basic length check)
        if len(draft.strip()) < 10:
            result.issues.append("Response is too short to be useful")
            result.passed = False
            return result

        # Check 2: Constraint violations
        for constraint in intent.constraints:
            # Check for obvious negations of constraints
            constraint_lower = constraint.lower()
            negations = [f"don't {constraint_lower}", f"not {constraint_lower}",
                         f"avoid {constraint_lower}"]
            if any(neg in draft_lower for neg in negations):
                result.issues.append(f"May violate constraint: '{constraint}'")
                result.violates_constraint = True

        # Check 3: Forbidden action violations
        for forbidden in intent.forbidden_actions:
            if forbidden.lower() in draft_lower:
                result.issues.append(f"Contains forbidden action: '{forbidden}'")
                result.violates_constraint = True
                result.passed = False

        # Check 4: Hallucination signals (heuristic)
        hallucination_phrases = [
            "as of my knowledge", "i believe", "i think",
            "probably", "most likely", "generally speaking",
        ]
        uncertain_count = sum(1 for p in hallucination_phrases if p in draft_lower)
        if uncertain_count >= 3:
            result.issues.append("Multiple uncertainty markers — possible hedging/bluffing")
            result.hallucination_detected = True

        # Check 5: Style match (basic)
        if intent.preferred_output_style == "concise" and len(draft) > 2000:
            result.issues.append("Response is very long but user prefers concise output")
            result.wrong_style = True

        if intent.preferred_output_style == "code-first" and "```" not in draft:
            result.issues.append("User prefers code-first but no code block found")
            result.wrong_style = True

        if result.issues:
            result.passed = not any([
                result.violates_constraint,
                result.hallucination_detected,
                result.contradicts_intent,
            ])

        return result

    async def _llm_check(self, draft: str, intent: IntentState) -> Optional[CheckResult]:
        """LLM-powered self-check for nuanced issues."""
        prompt = SELF_CHECK_PROMPT.format(
            inferred_goal=intent.inferred_goal or intent.explicit_request,
            explicit_request=intent.explicit_request,
            constraints=", ".join(intent.constraints) or "(none)",
            forbidden_actions=", ".join(intent.forbidden_actions) or "(none)",
            preferred_style=intent.preferred_output_style or "(not specified)",
            draft=draft[:2000],  # Cap to avoid huge prompts
        )

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 300, "temperature": 0.1},
                    },
                )

                if resp.status_code != 200:
                    return None

                text = resp.json().get("response", "").strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()

                data = json.loads(text)

                result = CheckResult(
                    passed=data.get("overall_pass", True),
                    issues=data.get("issues", []),
                    contradicts_intent=not data.get("addresses_goal", True),
                    violates_constraint=data.get("violates_constraints", False),
                    hallucination_detected=data.get("unsupported_claims", False),
                    wrong_style=not data.get("matches_style", True),
                )
                return result

        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"LLM self-check failed: {e}")
            return None

    def _merge_results(self, heuristic: CheckResult, llm: CheckResult) -> CheckResult:
        """Combine heuristic and LLM check results. Any failure = failure."""
        merged = CheckResult(
            passed=heuristic.passed and llm.passed,
            issues=heuristic.issues + llm.issues,
            contradicts_intent=heuristic.contradicts_intent or llm.contradicts_intent,
            violates_constraint=heuristic.violates_constraint or llm.violates_constraint,
            hallucination_detected=heuristic.hallucination_detected or llm.hallucination_detected,
            missing_information=heuristic.missing_information or llm.missing_information,
            wrong_style=heuristic.wrong_style or llm.wrong_style,
        )
        return merged
