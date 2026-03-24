"""
Revision Controller — Critique-revise loop with stopping criteria.

Rules:
  - Max 2 revision passes (hard cap)
  - Skip revision for draft-quality or short factual answers
  - Stop early if revision doesn't improve (diminishing returns)
  - After 2 failed revisions, ship with caveats instead of spinning
"""

import json
import logging
from typing import Optional

import httpx

from backend.metacognition.models.intent import IntentState
from backend.metacognition.models.actions import CheckResult
from backend.metacognition.models.session import SessionState

logger = logging.getLogger("metacognition.revision")

REVISION_PROMPT = """You generated a response that has some issues.

USER'S GOAL: {inferred_goal}
EXPLICIT REQUEST: {explicit_request}
CONSTRAINTS: {constraints}

YOUR ORIGINAL RESPONSE:
{draft}

ISSUES FOUND:
{issues}

Fix these specific issues and write an improved response.
Keep everything that was correct. Only change what needs fixing.
Do NOT add a preamble like "Here's the revised version" — just output the improved response directly."""


class RevisionController:
    """
    Manages the critique-revise loop with hard stopping criteria.

    When to reflect (should_revise):
      ✓ Complex multi-step answers
      ✓ Code generation
      ✓ Critical quality bar
      ✗ Draft quality (skip)
      ✗ Short factual answers (skip)
      ✗ Already revised twice (stop)
    """

    MAX_REVISIONS = 2

    def __init__(self, ollama_url: str = "http://localhost:11434", model: str = ""):
        self.ollama_url = ollama_url
        self.model = model or "qwen2.5-coder:7b"

    def should_revise(
        self,
        draft: str,
        intent: IntentState,
        check: CheckResult,
        session: SessionState,
    ) -> bool:
        """
        Decide whether to attempt a revision.

        Returns False (skip revision) when:
          - Check passed (no issues found)
          - Already at max revisions
          - Draft quality (user doesn't need perfection)
          - Short factual answer (revision rarely helps)
        """
        # Already passed — no revision needed
        if check.passed and not check.issues:
            return False

        # Hit revision limit
        if not session.can_revise():
            logger.info("Skipping revision: max passes reached")
            return False

        # Draft quality — don't over-polish
        if intent.quality_bar == "draft":
            return False

        # Short factual answers — reflection often makes worse
        if len(draft) < 200 and intent.domain_context == "factual":
            return False

        # Has actual issues to fix
        return True

    async def revise(
        self,
        draft: str,
        issues: list,
        intent: IntentState,
        session: SessionState,
    ) -> Optional[str]:
        """
        Attempt to revise a draft to fix specific issues.

        Returns the improved draft, or None if revision fails.
        Records the revision attempt in session state.
        """
        session.record_revision()

        if not session.can_call_llm():
            logger.info("Skipping LLM revision: call budget exhausted")
            return None

        prompt = REVISION_PROMPT.format(
            inferred_goal=intent.inferred_goal or intent.explicit_request,
            explicit_request=intent.explicit_request,
            constraints=", ".join(intent.constraints) or "(none)",
            draft=draft[:3000],
            issues="\n".join(f"- {i}" for i in issues),
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 2000},
                    },
                )

                session.record_llm_call()

                if resp.status_code != 200:
                    return None

                revised = resp.json().get("response", "").strip()

                # Basic quality check: revision should be at least 50% of original
                if len(revised) < len(draft) * 0.3:
                    logger.warning("Revision too short — likely a failure")
                    return None

                return revised

        except (httpx.HTTPError, Exception) as e:
            logger.warning(f"Revision failed: {e}")
            return None

    def add_caveats(self, draft: str, issues: list) -> str:
        """
        When revision fails or is skipped, add caveats instead.
        This is the fallback: ship with transparency, don't spin.
        """
        if not issues:
            return draft

        caveat_block = "\n\n---\n⚠️ **Note:** "
        if len(issues) == 1:
            caveat_block += issues[0]
        else:
            caveat_block += "I noticed some potential issues with this response:\n"
            for issue in issues[:3]:  # Max 3 caveats
                caveat_block += f"- {issue}\n"

        return draft + caveat_block
