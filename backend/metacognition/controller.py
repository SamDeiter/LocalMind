"""
Meta-Cognitive Controller — The main control loop.

Orchestrates the full cycle for every user turn:
  1. Parse intent
  2. Read preferences
  3. Score uncertainty
  4. Route action
  5. Execute
  6. Self-check
  7. Revise or finalize
  8. Log calibration data

Budget: max 3 LLM calls per turn.
Max: 2 revision passes.
"""

import logging
import time
from typing import Optional, Callable

from backend.metacognition.models.intent import IntentState
from backend.metacognition.models.session import SessionState
from backend.metacognition.models.actions import (
    Action, ActionDecision, Response, CheckResult, UncertaintyScore,
)
from backend.metacognition.models.memory import CalibrationEntry
from backend.metacognition.intent_parser import IntentParser
from backend.metacognition.uncertainty_gate import UncertaintyGate
from backend.metacognition.tool_router import ToolRouter
from backend.metacognition.self_checker import SelfChecker
from backend.metacognition.revision_controller import RevisionController
from backend.metacognition.memory_manager import MemoryManager
from backend.metacognition.calibration import CalibrationTracker

logger = logging.getLogger("metacognition.controller")


class MetaCognitiveController:
    """
    Main control loop for meta-cognitive processing.

    Injection point: sits between user input and the LLM response generation.
    Does NOT generate the actual response — it wraps the existing agent pipeline
    with intent parsing, uncertainty gating, self-checking, and revision.
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "",
        emit_activity: Optional[Callable] = None,
    ):
        self.ollama_url = ollama_url
        self.model = model or "qwen2.5-coder:7b"
        self._emit = emit_activity or (lambda *a, **kw: None)

        # Initialize subsystems
        self.intent_parser = IntentParser(ollama_url, self.model)
        self.uncertainty_gate = UncertaintyGate()
        self.tool_router = ToolRouter()
        self.self_checker = SelfChecker(ollama_url, self.model)
        self.revision_controller = RevisionController(ollama_url, self.model)
        self.memory = MemoryManager()
        self.calibration = CalibrationTracker()

        # Active session (created per conversation)
        self.session: Optional[SessionState] = None

    def new_session(self, conversation_id: str = "") -> SessionState:
        """Create a new session for a conversation."""
        self.session = SessionState(conversation_id=conversation_id)
        return self.session

    def get_or_create_session(self, conversation_id: str = "") -> SessionState:
        """Get existing session or create new one."""
        if self.session is None or self.session.conversation_id != conversation_id:
            return self.new_session(conversation_id)
        return self.session

    # ── Main Control Loop ─────────────────────────────────────────────

    async def pre_process(
        self,
        user_input: str,
        conversation_id: str = "",
    ) -> ActionDecision:
        """
        Pre-process a user turn: parse intent, score uncertainty, route action.

        Called BEFORE the main LLM generates a response.
        Returns an ActionDecision telling the caller what to do.
        """
        session = self.get_or_create_session(conversation_id)
        session.new_turn()
        session.add_message("user", user_input)

        self._emit("thinking",
                    f"Parsing intent from: '{user_input[:80]}...'",
                    thinking_type="intent_parse")

        # Step 1: Parse intent
        previous_intent = session.active_intent
        intent = await self.intent_parser.parse(
            user_input,
            history=session.history,
            previous_intent=previous_intent,
        )
        session.active_intent = intent
        session.record_llm_call()

        self._emit("thinking",
                    f"Intent: {intent.inferred_goal or intent.explicit_request}",
                    thinking_type="intent_result",
                    intent=intent.to_dict())

        # Step 2: Read preferences
        prefs = self.memory.read_preferences(intent.domain_context)
        if prefs:
            # Apply known preferences (e.g., code style)
            for pref in prefs:
                if pref.key == "output_style" and not intent.preferred_output_style:
                    intent.preferred_output_style = pref.value
                elif pref.key.startswith("constraint."):
                    constraint = pref.value
                    if constraint not in intent.constraints:
                        intent.constraints.append(constraint)

        # Step 3: Score uncertainty
        uncertainty = self.uncertainty_gate.score(intent)

        self._emit("thinking",
                    f"Uncertainty: {uncertainty.score:.0%} — {uncertainty.top_concern or 'no concerns'}",
                    thinking_type="uncertainty_score",
                    uncertainty=uncertainty.to_dict())

        # Step 4: Route action
        decision = self.tool_router.route(intent, uncertainty)

        self._emit("thinking",
                    f"Action: {decision.action.value} — {decision.reason}",
                    thinking_type="action_route",
                    decision=decision.to_dict())

        return decision

    async def post_process(
        self,
        draft: str,
        conversation_id: str = "",
    ) -> Response:
        """
        Post-process a generated response: self-check, revise if needed.

        Called AFTER the main LLM generates a response.
        Returns a Response with potential caveats and metadata.
        """
        session = self.get_or_create_session(conversation_id)
        intent = session.active_intent

        if not intent:
            # No intent parsed — pass through unchanged
            return Response(
                content=draft,
                action_taken=Action.ANSWER,
                confidence=0.5,
            )

        # Step 5: Self-check
        use_llm = session.can_call_llm() and intent.quality_bar != "draft"
        check = await self.self_checker.check(draft, intent, use_llm=use_llm)
        if use_llm:
            session.record_llm_call()

        self._emit("thinking",
                    f"Self-check: {'PASS' if check.passed else 'FAIL'} — {len(check.issues)} issues",
                    thinking_type="self_check",
                    check=check.to_dict())

        # Step 6: Revise or finalize
        final_draft = draft
        revision_count = 0

        if not check.passed and self.revision_controller.should_revise(
            draft, intent, check, session
        ):
            self._emit("thinking",
                        f"Revising: {check.issues[0]}",
                        thinking_type="revision_start")

            revised = await self.revision_controller.revise(
                draft, check.issues, intent, session,
            )

            if revised:
                final_draft = revised
                revision_count = session.revision_count

                # Re-check the revision (heuristic only to save budget)
                recheck = await self.self_checker.check(revised, intent, use_llm=False)
                if not recheck.passed:
                    # Revision didn't fully fix it — add caveats
                    final_draft = self.revision_controller.add_caveats(
                        revised, recheck.issues,
                    )
            else:
                # Revision failed — add caveats to original
                final_draft = self.revision_controller.add_caveats(draft, check.issues)
        elif not check.passed:
            # Can't revise (budget or policy) — add caveats
            final_draft = self.revision_controller.add_caveats(draft, check.issues)

        # Step 7: Check for preference revelation
        if intent.reveals_preference and intent.preference_candidate:
            self.memory.propose_preference(
                key=intent.preference_candidate.get("key", ""),
                value=intent.preference_candidate.get("value", ""),
                source=intent.preference_candidate.get("source", "inferred"),
                session_id=conversation_id,
            )

        # Step 8: Log calibration
        self.calibration.log(CalibrationEntry(
            task_type=intent.domain_context,
            predicted_confidence=1.0 - self.uncertainty_gate.score(intent).score,
            turn_number=session.turn_number,
            revision_count=revision_count,
            # actual_outcome filled in later (via feedback or correction detection)
        ))

        session.add_message("assistant", final_draft)

        # Build response
        confidence = max(0.0, 1.0 - self.uncertainty_gate.score(intent).score)
        caveats = check.issues if not check.passed else []
        assumptions = [
            a.statement for a in intent.assumptions
            if hasattr(a, 'is_risky') and a.is_risky()
        ]

        return Response(
            content=final_draft,
            action_taken=Action.ANSWER,
            confidence=confidence,
            caveats=caveats,
            assumptions_made=assumptions,
            revision_count=revision_count,
            metadata={
                "intent": intent.to_dict(),
                "turn": session.turn_number,
            },
        )

    # ── Convenience: Full Pipeline ────────────────────────────────────

    async def process_turn(
        self,
        user_input: str,
        generate_fn,
        conversation_id: str = "",
    ) -> Response:
        """
        Full pipeline: pre-process → generate → post-process.

        Args:
            user_input: The user's message
            generate_fn: Async function that generates a response string
            conversation_id: For session tracking
        """
        # Pre-process
        decision = await self.pre_process(user_input, conversation_id)

        # Handle non-ANSWER actions
        if decision.action == Action.ASK:
            return Response(
                content=decision.clarification_question,
                action_taken=Action.ASK,
                confidence=decision.confidence,
            )

        if decision.action == Action.ABSTAIN:
            return Response(
                content=decision.abstain_explanation,
                action_taken=Action.ABSTAIN,
                confidence=decision.confidence,
            )

        # Generate response using the provided function
        draft = await generate_fn(user_input)

        # Post-process
        return await self.post_process(draft, conversation_id)

    # ── Feedback ──────────────────────────────────────────────────────

    def record_feedback(self, outcome: str, failure_type: str = ""):
        """
        Record user feedback on the last response.
        Called when user corrects or approves a response.

        outcome: "success" | "partial" | "failure"
        """
        entries = self.calibration.read_all()
        if entries:
            last = entries[-1]
            last.actual_outcome = outcome
            last.failure_type = failure_type
            # Re-log with the outcome filled in
            self.calibration.log(last)

    def get_stats(self) -> dict:
        """Return combined stats for debugging/display."""
        return {
            "calibration": self.calibration.overall_stats(),
            "memory": self.memory.stats(),
            "session": self.session.to_dict() if self.session else None,
        }
