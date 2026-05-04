"""
Tool Router — Decides what action to take based on intent and uncertainty.

Priority order:
  1. Missing critical info → ASK (one question)
  2. Need external state → TOOL_USE
  3. Factual + uncertain → VERIFY
  4. References past context → READ_MEMORY
  5. Too uncertain → ABSTAIN
  6. Default → ANSWER
"""

import logging

from backend.metacognition.models.intent import IntentState
from backend.metacognition.models.actions import Action, ActionDecision, UncertaintyScore

logger = logging.getLogger("metacognition.tool_router")


class ToolRouter:
    """Routes user requests to the appropriate action."""

    # Action verbs that signal the user wants execution, not conversation
    _ACTION_VERBS = {
        "make", "create", "build", "generate", "write", "send", "open",
        "run", "execute", "install", "search", "find", "take", "do",
        "browse", "navigate", "draft", "list", "show", "get", "download",
    }

    def route(
        self,
        intent: IntentState,
        uncertainty: UncertaintyScore,
    ) -> ActionDecision:
        """
        Decide which action to take. Returns ActionDecision with reason.

        Bias: LocalMind is a task worker. When in doubt, act — don't ask.
        Only ask when genuinely missing critical info that would cause
        the wrong action (e.g., "send an email" but no recipient).
        """

        # Check if the user's message is action-oriented (starts with a verb
        # like "make", "create", "send", etc.).  If so, strongly prefer
        # executing over asking.
        is_action_request = self._is_action_oriented(intent)

        # Priority 1: Needs external state → use a tool
        # (Moved ABOVE "ask" — if we know a tool is needed, just call it)
        if (uncertainty.needs_tool or intent.needs_tool) and not getattr(intent, "is_verified", False):
            tool_name = self._pick_tool(intent)
            return ActionDecision(
                action=Action.TOOL_USE,
                reason="Requires external data or file access",
                confidence=0.7,
                tool_name=tool_name,
            )

        # Priority 2: Missing critical information → ask ONE question
        # BUT only if this is NOT a clear action request with enough context
        if uncertainty.missing_critical_info and uncertainty.questions and not is_action_request:
            return ActionDecision(
                action=Action.ASK,
                reason="Missing critical information",
                confidence=0.8,
                clarification_question=uncertainty.questions[0],
            )

        # Priority 3: Factual domain + uncertain → verify first
        if uncertainty.needs_verification and uncertainty.score > 0.4:
            return ActionDecision(
                action=Action.VERIFY,
                reason="Factual claim needs verification before answering",
                confidence=0.6,
            )

        # Priority 4: References past context → check memory
        if intent.references_past_context:
            return ActionDecision(
                action=Action.READ_MEMORY,
                reason="User references prior context",
                confidence=0.7,
            )

        # Priority 5: Too uncertain → abstain
        if uncertainty.should_abstain():
            return ActionDecision(
                action=Action.ABSTAIN,
                reason=uncertainty.top_concern or "Confidence too low to answer reliably",
                confidence=1.0 - uncertainty.score,
                abstain_explanation=self._build_abstain_message(uncertainty),
            )

        # Priority 6: Should ask (uncertainty above threshold but not critical)
        # Skip for action-oriented requests — just do the thing
        if uncertainty.should_ask() and uncertainty.questions and not is_action_request:
            return ActionDecision(
                action=Action.ASK,
                reason=uncertainty.top_concern or "Request needs clarification",
                confidence=0.5,
                clarification_question=uncertainty.questions[0],
            )

        # Default: answer directly
        confidence = max(0.0, 1.0 - uncertainty.score)
        return ActionDecision(
            action=Action.ANSWER,
            reason="Request is clear enough to answer directly",
            confidence=confidence,
        )

    def _is_action_oriented(self, intent: IntentState) -> bool:
        """Check if the user's request starts with or contains action verbs."""
        words = intent.explicit_request.lower().split()
        if not words:
            return False
        # First word is an action verb, or "please/can you/i need" + action verb
        if words[0] in self._ACTION_VERBS:
            return True
        # "I need a ...", "can you ...", "please ..."
        skip_prefixes = {"i", "can", "could", "please", "you", "need", "want", "a", "an", "the", "to", "me"}
        for w in words[:5]:
            if w in self._ACTION_VERBS:
                return True
            if w not in skip_prefixes:
                break
        return False

    def _pick_tool(self, intent: IntentState) -> str:
        """Heuristic tool selection based on domain and request."""
        lower = intent.explicit_request.lower()

        if any(w in lower for w in ["file", "read", "open", "contents"]):
            return "read_file"
        if any(w in lower for w in ["search", "find", "grep", "look for"]):
            return "search_files"
        if any(w in lower for w in ["run", "execute", "test", "python"]):
            return "run_command"
        if any(w in lower for w in ["list", "directory", "folder"]):
            return "list_directory"

        return "general_tool"

    def _build_abstain_message(self, uncertainty: UncertaintyScore) -> str:
        """Build a helpful abstain message."""
        msg = "I don't have enough confidence to answer this reliably."
        if uncertainty.top_concern:
            msg += f" Main concern: {uncertainty.top_concern}."
        if uncertainty.questions:
            msg += f" It might help if you could clarify: {uncertainty.questions[0]}"
        return msg
