"""
Uncertainty Gate — Scores uncertainty using observable proxy signals.

Does NOT trust self-reported model confidence.
Instead uses heuristic signals: domain, specificity, contradiction detection,
known failure patterns, and whether external state is needed.
"""

import logging

from backend.metacognition.models.intent import IntentState, ConfidenceLevel
from backend.metacognition.models.actions import UncertaintyScore

logger = logging.getLogger("metacognition.uncertainty_gate")

# Known high-failure-rate task patterns
KNOWN_FAILURE_PATTERNS = [
    "current price", "stock price", "live data",
    "tomorrow", "next week", "predict",
    "how much does", "what year did",
]

# Domains where the model is typically unreliable
HIGH_RISK_DOMAINS = ["medical", "legal", "financial", "security"]


class UncertaintyGate:
    """
    Scores uncertainty using observable signals, not self-reported confidence.

    Returns an UncertaintyScore that the router uses to decide the action.

    Bias: LocalMind is an action-oriented task worker.  When the user asks
    us to *do* something (create, make, send, search, …) we should act,
    not interrogate.  Uncertainty penalties are dampened for tool-backed
    requests because the tool itself provides guardrails.
    """

    # Verbs that signal "just do it" intent
    _ACTION_VERBS = {
        "make", "create", "build", "generate", "write", "send", "open",
        "run", "execute", "install", "search", "find", "take", "do",
        "browse", "navigate", "draft", "list", "show", "get", "download",
    }

    def _is_action_request(self, text: str) -> bool:
        words = text.lower().split()
        skip = {"i", "can", "could", "please", "you", "need", "want", "a", "an", "the", "to", "me"}
        for w in words[:6]:
            if w in self._ACTION_VERBS:
                return True
            if w not in skip:
                break
        return False

    def score(self, intent: IntentState) -> UncertaintyScore:
        """
        Compute uncertainty score from intent signals.

        Score: 0.0 = fully certain, 1.0 = completely uncertain.
        """
        uncertainty = UncertaintyScore()
        reasons = []
        score_components = []

        is_action = self._is_action_request(intent.explicit_request)

        # 1. Ambiguity check — heavily dampened for action requests
        if intent.has_ambiguity():
            penalty = min(0.3, len(intent.unresolved_ambiguities) * 0.1)
            if is_action:
                penalty *= 0.3  # Action requests: just pick a reasonable default
            score_components.append(penalty)
            reasons.append(
                f"Ambiguous: {intent.unresolved_ambiguities[0]}"
            )
            if not is_action:
                uncertainty.questions = [
                    f"Could you clarify: {a}?" for a in intent.unresolved_ambiguities[:2]
                ]

        # 2. Risky assumptions — dampened for action requests
        risky = intent.risky_assumptions()
        if risky:
            penalty = min(0.3, len(risky) * 0.15)
            if is_action:
                penalty *= 0.3
            score_components.append(penalty)
            reasons.append(
                f"Low-confidence assumption: {risky[0].statement}"
            )
            # Only flag missing_critical_info for non-action requests
            if not is_action:
                uncertainty.missing_critical_info = any(
                    a.confidence == ConfidenceLevel.NONE for a in risky
                )

        # 3. Needs external state
        if intent.needs_tool:
            score_components.append(0.05)  # Tiny penalty — tools handle it
            uncertainty.needs_tool = True
            reasons.append("Requires external data (tool use needed)")

        # 4. Known failure patterns
        lower = intent.explicit_request.lower()
        for pattern in KNOWN_FAILURE_PATTERNS:
            if pattern in lower:
                score_components.append(0.4)
                reasons.append(f"Known failure pattern: '{pattern}'")
                break

        # 5. Domain risk
        for domain in HIGH_RISK_DOMAINS:
            if domain in intent.domain_context.lower():
                score_components.append(0.2)
                reasons.append(f"High-risk domain: {domain}")
                break

        # 6. Vagueness — only for truly vague requests (< 3 words, no tool match)
        if intent.needs_clarification and not is_action and not intent.needs_tool:
            score_components.append(0.25)
            uncertainty.missing_critical_info = True
            reasons.append("Request is too vague to act on")
            if not uncertainty.questions:
                uncertainty.questions = [
                    "Could you provide more detail about what you'd like?"
                ]

        # 7. Contradicting constraints
        if len(intent.constraints) > 2:
            constraint_text = " ".join(intent.constraints).lower()
            contradictions = [
                ("fast", "thorough"), ("simple", "comprehensive"),
                ("minimal", "complete"), ("quick", "perfect"),
            ]
            for a, b in contradictions:
                if a in constraint_text and b in constraint_text:
                    score_components.append(0.2)
                    reasons.append(f"Potentially contradicting: '{a}' vs '{b}'")
                    uncertainty.questions.append(
                        f"Your constraints mention both '{a}' and '{b}'. Which should I prioritize?"
                    )
                    break

        # 8. Factual claims need verification
        if intent.domain_context == "factual":
            score_components.append(0.15)
            uncertainty.needs_verification = True
            reasons.append("Factual domain — claims should be verified")

        # Compute final score (capped at 1.0)
        if score_components:
            final = max(score_components) + sum(s * 0.3 for s in score_components[1:])
            uncertainty.score = min(1.0, final)
        else:
            uncertainty.score = 0.1  # Baseline low uncertainty

        if reasons:
            uncertainty.top_concern = reasons[0]

        return uncertainty
