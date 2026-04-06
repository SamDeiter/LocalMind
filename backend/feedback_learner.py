"""
feedback_learner.py — Learns user preferences from proposal approval/denial patterns.
=======================================================================================
Tracks which proposals users approve vs deny and builds a lightweight preference
profile. This profile is used to:
  1. Score new proposals (predict likelihood of approval)
  2. Generate guidance injected into the reflection prompt to steer the LLM

No ML models or embeddings — just keyword/category statistics and heuristics.
"""

import json
import logging
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.feedback_learner")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
FEEDBACK_PROFILE_PATH = DATA_DIR / "feedback_profile.json"

# Minimum data points before we start making non-neutral predictions
COLD_START_THRESHOLD = 10

# Stop-words excluded from keyword extraction
_STOP_WORDS = frozenset({
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "shall", "should", "may", "might",
    "can", "could", "with", "from", "by", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "out", "off", "over",
    "under", "again", "further", "then", "once", "all", "each", "every",
    "both", "few", "more", "most", "other", "some", "such", "no", "not",
    "only", "own", "same", "so", "than", "too", "very", "just", "because",
    "but", "if", "while", "about", "up", "its", "it", "this", "that",
    "these", "those", "he", "she", "they", "we", "you", "i", "me", "my",
    "your", "his", "her", "our", "their", "what", "which", "who", "whom",
    "when", "where", "why", "how", "add", "implement", "improve", "update",
    "create", "make", "use", "new",
})


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from proposal text."""
    words = re.findall(r"[a-z][a-z_]+", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


def _estimate_complexity(proposal: dict) -> str:
    """Classify a proposal as 'simple', 'medium', or 'complex'."""
    files = proposal.get("files_affected", [])
    effort = proposal.get("effort", "medium").lower()
    desc_len = len(proposal.get("description", ""))

    if effort == "small" or (len(files) <= 1 and desc_len < 200):
        return "simple"
    elif effort == "large" or len(files) >= 4 or desc_len > 800:
        return "complex"
    return "medium"


class FeedbackLearner:
    """Learns user preferences from approved/denied proposals."""

    def __init__(self):
        self.profile = self._load_profile()

    # ── Persistence ──────────────────────────────────────────────────

    def _default_profile(self) -> dict:
        """Return a fresh, empty preference profile."""
        return {
            "version": 1,
            "created_at": time.time(),
            "last_updated": None,
            "total_approved": 0,
            "total_denied": 0,
            "category_approved": {},   # category -> count
            "category_denied": {},     # category -> count
            "keyword_approved": {},    # keyword -> count
            "keyword_denied": {},      # keyword -> count
            "complexity_approved": {}, # simple/medium/complex -> count
            "complexity_denied": {},   # simple/medium/complex -> count
            "effort_approved": {},     # small/medium/large -> count
            "effort_denied": {},       # small/medium/large -> count
            "recent_feedback": [],     # last 50 entries for recency weighting
        }

    def _load_profile(self) -> dict:
        """Load feedback profile from disk, or create a default."""
        if FEEDBACK_PROFILE_PATH.exists():
            try:
                data = json.loads(FEEDBACK_PROFILE_PATH.read_text(encoding="utf-8"))
                # Ensure all expected keys exist (forward compat)
                default = self._default_profile()
                for key, val in default.items():
                    data.setdefault(key, val)
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load feedback profile: {e}")
        return self._default_profile()

    def _save_profile(self):
        """Persist the preference profile to disk."""
        self.profile["last_updated"] = time.time()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            FEEDBACK_PROFILE_PATH.write_text(
                json.dumps(self.profile, indent=2), encoding="utf-8"
            )
        except OSError as e:
            logger.error(f"Failed to save feedback profile: {e}")

    # ── Data Collection ──────────────────────────────────────────────

    def record_feedback(self, proposal: dict, outcome: str):
        """Record an approved or denied proposal for learning.

        Args:
            proposal: The full proposal dict (title, category, description, etc.)
            outcome: 'approved' or 'denied'
        """
        if outcome not in ("approved", "denied"):
            logger.warning(f"Ignoring unknown feedback outcome: {outcome}")
            return

        category = proposal.get("category", "unknown")
        title = proposal.get("title", "")
        description = proposal.get("description", "")
        effort = proposal.get("effort", "medium").lower()
        complexity = _estimate_complexity(proposal)

        # Full text for keyword extraction
        full_text = f"{title} {description}"
        keywords = _extract_keywords(full_text)

        # Update counters
        if outcome == "approved":
            self.profile["total_approved"] += 1
            cat_dict = self.profile["category_approved"]
            kw_dict = self.profile["keyword_approved"]
            cx_dict = self.profile["complexity_approved"]
            eff_dict = self.profile["effort_approved"]
        else:
            self.profile["total_denied"] += 1
            cat_dict = self.profile["category_denied"]
            kw_dict = self.profile["keyword_denied"]
            cx_dict = self.profile["complexity_denied"]
            eff_dict = self.profile["effort_denied"]

        cat_dict[category] = cat_dict.get(category, 0) + 1

        for kw in keywords:
            kw_dict[kw] = kw_dict.get(kw, 0) + 1

        cx_dict[complexity] = cx_dict.get(complexity, 0) + 1
        eff_dict[effort] = eff_dict.get(effort, 0) + 1

        # Track recent feedback for recency weighting
        entry = {
            "ts": time.time(),
            "outcome": outcome,
            "title": title,
            "category": category,
            "effort": effort,
            "complexity": complexity,
        }
        recent = self.profile["recent_feedback"]
        recent.append(entry)
        # Keep last 50
        if len(recent) > 50:
            self.profile["recent_feedback"] = recent[-50:]

        self._save_profile()
        logger.info(
            f"Feedback recorded: {outcome} — '{title}' (category={category}, "
            f"complexity={complexity}, effort={effort})"
        )

    # ── Cold Start Check ─────────────────────────────────────────────

    def _is_cold_start(self) -> bool:
        """Return True if we don't have enough data for meaningful predictions."""
        total = self.profile["total_approved"] + self.profile["total_denied"]
        return total < COLD_START_THRESHOLD

    # ── Preference Scoring ───────────────────────────────────────────

    def score_proposal(self, proposal_text: str, proposal: dict = None) -> float:
        """Score a proposal 0.0-1.0 indicating predicted likelihood of user approval.

        Args:
            proposal_text: Full text of the proposal (title + description).
            proposal: Optional full proposal dict for category/effort scoring.

        Returns:
            Float 0.0-1.0. Returns 0.5 during cold start.
        """
        if self._is_cold_start():
            return 0.5

        scores = []
        weights = []

        # 1. Category score (weight: 3)
        if proposal:
            cat_score = self._score_category(proposal.get("category", "unknown"))
            if cat_score is not None:
                scores.append(cat_score)
                weights.append(3.0)

        # 2. Keyword score (weight: 2)
        kw_score = self._score_keywords(proposal_text)
        if kw_score is not None:
            scores.append(kw_score)
            weights.append(2.0)

        # 3. Complexity score (weight: 1.5)
        if proposal:
            cx_score = self._score_complexity(proposal)
            if cx_score is not None:
                scores.append(cx_score)
                weights.append(1.5)

        # 4. Effort score (weight: 1)
        if proposal:
            eff_score = self._score_effort(proposal.get("effort", "medium"))
            if eff_score is not None:
                scores.append(eff_score)
                weights.append(1.0)

        if not scores:
            return 0.5

        # Weighted average
        total_weight = sum(weights)
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        return round(min(1.0, max(0.0, weighted_sum / total_weight)), 3)

    def _score_category(self, category: str) -> Optional[float]:
        """Score based on category approval rate."""
        approved = self.profile["category_approved"].get(category, 0)
        denied = self.profile["category_denied"].get(category, 0)
        total = approved + denied
        if total == 0:
            return None
        return approved / total

    def _score_keywords(self, text: str) -> Optional[float]:
        """Score based on keyword overlap with approved vs denied patterns."""
        keywords = _extract_keywords(text)
        if not keywords:
            return None

        kw_approved = self.profile["keyword_approved"]
        kw_denied = self.profile["keyword_denied"]

        approved_signal = 0.0
        denied_signal = 0.0

        for kw in keywords:
            a = kw_approved.get(kw, 0)
            d = kw_denied.get(kw, 0)
            approved_signal += a
            denied_signal += d

        total_signal = approved_signal + denied_signal
        if total_signal == 0:
            return None

        return approved_signal / total_signal

    def _score_complexity(self, proposal: dict) -> Optional[float]:
        """Score based on preferred complexity level."""
        complexity = _estimate_complexity(proposal)
        approved = self.profile["complexity_approved"].get(complexity, 0)
        denied = self.profile["complexity_denied"].get(complexity, 0)
        total = approved + denied
        if total == 0:
            return None
        return approved / total

    def _score_effort(self, effort: str) -> Optional[float]:
        """Score based on preferred effort level."""
        effort = effort.lower()
        approved = self.profile["effort_approved"].get(effort, 0)
        denied = self.profile["effort_denied"].get(effort, 0)
        total = approved + denied
        if total == 0:
            return None
        return approved / total

    # ── Guidance Generation ──────────────────────────────────────────

    def get_reflection_guidance(self) -> str:
        """Generate a prompt snippet to steer proposal generation toward user preferences.

        Returns empty string during cold start.
        """
        if self._is_cold_start():
            return ""

        parts = []
        parts.append("USER PREFERENCE LEARNING (based on past approvals/denials):")

        # Category preferences
        cat_prefs = self._get_category_preferences()
        if cat_prefs["preferred"]:
            parts.append("  Preferred categories (user tends to APPROVE these):")
            for cat, rate in cat_prefs["preferred"]:
                parts.append(f"    + {cat} ({rate:.0%} approval rate)")
        if cat_prefs["avoided"]:
            parts.append("  Avoided categories (user tends to DENY these):")
            for cat, rate in cat_prefs["avoided"]:
                parts.append(f"    - {cat} ({rate:.0%} approval rate)")

        # Complexity preference
        cx_pref = self._get_complexity_preference()
        if cx_pref:
            parts.append(f"  Preferred complexity: {cx_pref}")

        # Effort preference
        eff_pref = self._get_effort_preference()
        if eff_pref:
            parts.append(f"  Preferred effort level: {eff_pref}")

        # Top approved keywords (topics the user likes)
        top_kw = self._get_top_keywords("approved", limit=8)
        if top_kw:
            parts.append("  Topics the user approves: " + ", ".join(top_kw))

        # Top denied keywords (topics the user rejects)
        denied_kw = self._get_top_keywords("denied", limit=5)
        if denied_kw:
            parts.append("  Topics the user rejects: " + ", ".join(denied_kw))

        if len(parts) <= 1:
            return ""

        parts.append("  -> Bias proposals toward preferred categories/topics/complexity.\n")
        return "\n".join(parts)

    def _get_category_preferences(self) -> dict:
        """Compute preferred and avoided categories."""
        all_cats = set(self.profile["category_approved"].keys()) | set(self.profile["category_denied"].keys())
        preferred = []
        avoided = []

        for cat in all_cats:
            approved = self.profile["category_approved"].get(cat, 0)
            denied = self.profile["category_denied"].get(cat, 0)
            total = approved + denied
            if total < 2:
                continue
            rate = approved / total
            if rate >= 0.65:
                preferred.append((cat, rate))
            elif rate <= 0.35:
                avoided.append((cat, rate))

        preferred.sort(key=lambda x: -x[1])
        avoided.sort(key=lambda x: x[1])
        return {"preferred": preferred, "avoided": avoided}

    def _get_complexity_preference(self) -> Optional[str]:
        """Return the preferred complexity level, or None."""
        cx_approved = self.profile["complexity_approved"]
        if not cx_approved:
            return None
        best = max(cx_approved, key=cx_approved.get)
        total_approved = sum(cx_approved.values())
        if total_approved < 3:
            return None
        pct = cx_approved[best] / total_approved
        if pct >= 0.5:
            return f"{best} ({pct:.0%} of approvals)"
        return None

    def _get_effort_preference(self) -> Optional[str]:
        """Return the preferred effort level, or None."""
        eff_approved = self.profile["effort_approved"]
        if not eff_approved:
            return None
        best = max(eff_approved, key=eff_approved.get)
        total_approved = sum(eff_approved.values())
        if total_approved < 3:
            return None
        pct = eff_approved[best] / total_approved
        if pct >= 0.5:
            return f"{best} ({pct:.0%} of approvals)"
        return None

    def _get_top_keywords(self, outcome: str, limit: int = 8) -> list[str]:
        """Get the most frequent keywords for an outcome, excluding shared ones."""
        if outcome == "approved":
            primary = self.profile["keyword_approved"]
            secondary = self.profile["keyword_denied"]
        else:
            primary = self.profile["keyword_denied"]
            secondary = self.profile["keyword_approved"]

        if not primary:
            return []

        # Score each keyword by how distinguishing it is:
        # primary_count / (primary_count + secondary_count)
        scored = []
        for kw, count in primary.items():
            other = secondary.get(kw, 0)
            total = count + other
            if total < 2:
                continue
            distinctiveness = count / total
            if distinctiveness >= 0.65:
                scored.append((kw, count * distinctiveness))

        scored.sort(key=lambda x: -x[1])
        return [kw for kw, _ in scored[:limit]]

    # ── Profile Summary (for API) ────────────────────────────────────

    def get_profile_summary(self) -> dict:
        """Return a summary of the preference profile for the API endpoint."""
        total = self.profile["total_approved"] + self.profile["total_denied"]
        cat_prefs = self._get_category_preferences() if not self._is_cold_start() else {"preferred": [], "avoided": []}

        return {
            "total_feedback": total,
            "total_approved": self.profile["total_approved"],
            "total_denied": self.profile["total_denied"],
            "approval_rate": round(self.profile["total_approved"] / total, 3) if total > 0 else 0,
            "is_cold_start": self._is_cold_start(),
            "cold_start_threshold": COLD_START_THRESHOLD,
            "preferred_categories": [
                {"category": cat, "approval_rate": round(rate, 3)}
                for cat, rate in cat_prefs["preferred"]
            ],
            "avoided_categories": [
                {"category": cat, "approval_rate": round(rate, 3)}
                for cat, rate in cat_prefs["avoided"]
            ],
            "complexity_preference": self._get_complexity_preference() if not self._is_cold_start() else None,
            "effort_preference": self._get_effort_preference() if not self._is_cold_start() else None,
            "top_approved_keywords": self._get_top_keywords("approved") if not self._is_cold_start() else [],
            "top_denied_keywords": self._get_top_keywords("denied") if not self._is_cold_start() else [],
            "recent_feedback_count": len(self.profile.get("recent_feedback", [])),
            "last_updated": self.profile.get("last_updated"),
        }
