"""
Self-Improver: teaches the autonomy engine to adapt its own behavior.

Instead of editing its own Python code (dangerous), the engine maintains a
learnable brain_config.json that it reads each cycle. This module analyzes
past performance data and proposes safe config changes.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("autonomy.self_improver")

WORKSPACE = Path.home() / "LocalMind_Workspace"
BRAIN_CONFIG_PATH = WORKSPACE / "brain_config.json"
STATS_PATH = WORKSPACE / "execution_stats.json"
LESSONS_PATH = WORKSPACE / "lessons_learned.json"

DEFAULT_CONFIG = {
    "version": 1,
    "last_updated": None,
    "last_updated_reason": None,
    "learned_rules": [],
    "banned_patterns": [
        "error handling", "exception handling", "try/catch", "try-catch",
        "improve error", "ssl encryption", "ssl/tls", "tls encryption",
        "https redirection", "https redirect", "secure sockets",
        "implement ssl", "implement tls", "implement https",
        "multi-factor authentication", "mfa", "two-factor",
        "input validation", "validate user input", "secure user input",
        "sanitize input", "code quality improvement", "improve code quality",
        "refactor for readability", "add type hints", "add docstrings",
        "improve logging", "add logging", "logging improvements",
        "implement rate limiting", "api rate limit",
        "image recognition", "visual content analysis",
        "asynchronous task handling", "implement async",
    ],
    "category_weights": {
        "performance": 1.0, "feature": 1.0, "ux": 1.0,
        "security": 1.0, "code_quality": 1.0, "bugfix": 1.0,
    },
    "confidence_threshold": 0.5,
    "prompt_hints": [],
    "preferred_file_targets": [],
    "avoided_file_targets": [],
    "max_edit_lines": 30,
    "max_files_per_proposal": 3,
    "reflection_cooldown_minutes": 10,
    "improvement_history": [],
}


class SelfImprover:
    """Analyzes engine performance and tunes brain_config.json."""

    def __init__(self, emit_activity=None):
        self._emit = emit_activity or (lambda *a, **kw: None)
        self.config = self._load_config()

    # ── Config I/O ────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """Load brain config, creating defaults if missing."""
        if BRAIN_CONFIG_PATH.exists():
            try:
                return json.loads(BRAIN_CONFIG_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load brain_config.json: {e}")
        return dict(DEFAULT_CONFIG)

    def _save_config(self, reason: str):
        """Persist config with metadata."""
        self.config["last_updated"] = time.time()
        self.config["last_updated_reason"] = reason
        self.config["version"] = self.config.get("version", 1) + 1
        BRAIN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        BRAIN_CONFIG_PATH.write_text(
            json.dumps(self.config, indent=2), encoding="utf-8"
        )

    # ── Data Loading ──────────────────────────────────────────────

    def _load_stats(self) -> dict:
        """Load execution_stats.json."""
        if STATS_PATH.exists():
            try:
                return json.loads(STATS_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _load_lessons(self) -> list:
        """Load lessons_learned.json."""
        if LESSONS_PATH.exists():
            try:
                data = json.loads(LESSONS_PATH.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
                return data.get("lessons", [])
            except (json.JSONDecodeError, OSError):
                pass
        return []

    # ── Core Optimization ─────────────────────────────────────────

    def optimize(self) -> list[str]:
        """
        Analyze performance data and propose config improvements.
        Returns a list of changes made (for thinking events).
        """
        changes = []
        stats = self._load_stats()
        lessons = self._load_lessons()

        if not stats and not lessons:
            return changes  # No data yet, nothing to optimize

        # Strategy 1: Adjust category weights based on success rates
        changes.extend(self._optimize_category_weights(stats))

        # Strategy 2: Learn banned patterns from repeated failures
        changes.extend(self._learn_banned_patterns(lessons))

        # Strategy 3: Adjust confidence threshold
        changes.extend(self._optimize_confidence(stats))

        # Strategy 4: Learn prompt hints from successful patterns
        changes.extend(self._learn_prompt_hints(lessons, stats))

        # Strategy 5: Adjust edit scope based on failure patterns
        changes.extend(self._optimize_edit_scope(lessons))

        # Strategy 6: Learn file preferences
        changes.extend(self._learn_file_preferences(lessons, stats))

        if changes:
            # Record improvement history
            entry = {
                "timestamp": time.time(),
                "changes": changes,
                "stats_snapshot": {
                    cat: data.get("success_rate", 0)
                    for cat, data in stats.items()
                    if isinstance(data, dict)
                },
            }
            history = self.config.get("improvement_history", [])
            history.append(entry)
            # Keep last 50 entries
            self.config["improvement_history"] = history[-50:]

            reason = f"Auto-tuned {len(changes)} setting(s): {'; '.join(changes[:3])}"
            self._save_config(reason)

            self._emit(
                "thinking",
                f"🧬 Self-improvement: {reason}",
                thinking_type="self_improvement",
                changes=changes,
            )

        return changes

    # ── Strategy 1: Category Weights ──────────────────────────────

    def _optimize_category_weights(self, stats: dict) -> list[str]:
        """Boost weight for categories with high success, lower for low success."""
        changes = []
        weights = self.config.get("category_weights", {})

        for category, data in stats.items():
            if not isinstance(data, dict):
                continue
            success_rate = data.get("success_rate", 0)
            total = data.get("total", 0)

            if total < 3:
                continue  # Not enough data

            old_weight = weights.get(category, 1.0)

            if success_rate >= 0.7:
                # Good at this — boost weight (prefer it)
                new_weight = min(old_weight + 0.2, 2.0)
            elif success_rate <= 0.3:
                # Bad at this — lower weight (avoid it)
                new_weight = max(old_weight - 0.2, 0.2)
            else:
                continue

            if abs(new_weight - old_weight) > 0.01:
                weights[category] = round(new_weight, 2)
                direction = "↑" if new_weight > old_weight else "↓"
                changes.append(
                    f"{category} weight {direction} {old_weight:.1f}→{new_weight:.1f} "
                    f"(success: {success_rate:.0%})"
                )

        self.config["category_weights"] = weights
        return changes

    # ── Strategy 2: Banned Patterns ───────────────────────────────

    def _learn_banned_patterns(self, lessons: list) -> list[str]:
        """If a keyword appears in 3+ failure lessons, ban it."""
        changes = []
        banned = set(self.config.get("banned_patterns", []))

        # Count failure keywords
        failure_titles = []
        for lesson in lessons:
            if isinstance(lesson, dict):
                title = lesson.get("title", "").lower()
                if lesson.get("type") in ("failure", "error"):
                    failure_titles.append(title)
            elif isinstance(lesson, str):
                failure_titles.append(lesson.lower())

        # Find repeated 2-word phrases
        phrase_counts = {}
        for title in failure_titles:
            words = title.split()
            for i in range(len(words) - 1):
                phrase = f"{words[i]} {words[i + 1]}"
                if len(phrase) > 5 and phrase not in banned:
                    phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

        for phrase, count in phrase_counts.items():
            if count >= 3:
                banned.add(phrase)
                changes.append(f"Banned '{phrase}' (failed {count} times)")

        self.config["banned_patterns"] = list(banned)
        return changes

    # ── Strategy 3: Confidence Threshold ──────────────────────────

    def _optimize_confidence(self, stats: dict) -> list[str]:
        """Raise confidence threshold when overall failure rate is high."""
        changes = []
        total_success = 0
        total_count = 0

        for cat, data in stats.items():
            if not isinstance(data, dict):
                continue
            total_success += data.get("success", 0)
            total_count += data.get("total", 0)

        if total_count < 5:
            return changes  # Not enough data

        overall_rate = total_success / total_count if total_count else 0
        old_threshold = self.config.get("confidence_threshold", 0.5)

        if overall_rate < 0.4 and old_threshold < 0.8:
            # Failing a lot — be more cautious
            new_threshold = min(old_threshold + 0.1, 0.9)
            self.config["confidence_threshold"] = round(new_threshold, 2)
            changes.append(
                f"Confidence threshold ↑ {old_threshold:.1f}→{new_threshold:.1f} "
                f"(overall success: {overall_rate:.0%})"
            )
        elif overall_rate > 0.7 and old_threshold > 0.4:
            # Doing well — can be more confident
            new_threshold = max(old_threshold - 0.05, 0.3)
            self.config["confidence_threshold"] = round(new_threshold, 2)
            changes.append(
                f"Confidence threshold ↓ {old_threshold:.1f}→{new_threshold:.1f} "
                f"(overall success: {overall_rate:.0%})"
            )

        return changes

    # ── Strategy 4: Prompt Hints ──────────────────────────────────

    def _learn_prompt_hints(self, lessons: list, stats: dict) -> list[str]:
        """Extract actionable hints from failure patterns."""
        changes = []
        existing_hints = set(self.config.get("prompt_hints", []))

        # Lesson-based hints
        hint_map = {
            "import_missing": "Always verify that all import statements exist in the target file before adding code that uses them.",
            "syntax_error": "After generating code, mentally trace the indentation and bracket matching before applying.",
            "test_assertion": "Before editing, read the existing test expectations to avoid breaking assertions.",
            "file_not_found": "Only propose edits to files that exist in the project file listing.",
            "search_text_not_found": "Read the exact current content of the file before proposing search-and-replace edits.",
        }

        for lesson in lessons:
            if isinstance(lesson, dict):
                lesson_type = lesson.get("type", "")
                if lesson_type in hint_map:
                    hint = hint_map[lesson_type]
                    if hint not in existing_hints:
                        existing_hints.add(hint)
                        changes.append(f"New prompt hint: '{hint[:60]}...'")

        self.config["prompt_hints"] = list(existing_hints)
        return changes

    # ── Strategy 5: Edit Scope ────────────────────────────────────

    def _optimize_edit_scope(self, lessons: list) -> list[str]:
        """Reduce max edit lines when edits frequently fail."""
        changes = []
        edit_failures = sum(
            1 for l in lessons
            if isinstance(l, dict) and l.get("type") in (
                "search_text_not_found", "syntax_error", "edit_failed"
            )
        )

        old_max = self.config.get("max_edit_lines", 30)

        if edit_failures >= 5 and old_max > 15:
            new_max = max(old_max - 5, 10)
            self.config["max_edit_lines"] = new_max
            changes.append(
                f"Max edit lines ↓ {old_max}→{new_max} "
                f"({edit_failures} edit failures detected)"
            )
        elif edit_failures == 0 and old_max < 30:
            new_max = min(old_max + 5, 40)
            self.config["max_edit_lines"] = new_max
            changes.append(
                f"Max edit lines ↑ {old_max}→{new_max} (no recent edit failures)"
            )

        return changes

    # ── Strategy 6: File Preferences ──────────────────────────────

    def _learn_file_preferences(self, lessons: list, stats: dict) -> list[str]:
        """Track which files succeed/fail to build preferences."""
        changes = []
        file_outcomes = {}

        for lesson in lessons:
            if not isinstance(lesson, dict):
                continue
            files = lesson.get("files_affected", [])
            outcome = lesson.get("type", "")

            for f in files:
                if f not in file_outcomes:
                    file_outcomes[f] = {"success": 0, "failure": 0}
                if outcome in ("success", "completed"):
                    file_outcomes[f]["success"] += 1
                else:
                    file_outcomes[f]["failure"] += 1

        avoided = set(self.config.get("avoided_file_targets", []))
        preferred = set(self.config.get("preferred_file_targets", []))

        for filepath, outcomes in file_outcomes.items():
            total = outcomes["success"] + outcomes["failure"]
            if total < 3:
                continue

            fail_rate = outcomes["failure"] / total
            if fail_rate >= 0.8 and filepath not in avoided:
                avoided.add(filepath)
                changes.append(f"Avoiding '{filepath}' (fail rate: {fail_rate:.0%})")
            elif fail_rate <= 0.2 and filepath not in preferred:
                preferred.add(filepath)
                changes.append(f"Preferring '{filepath}' (success rate: {1 - fail_rate:.0%})")

        self.config["avoided_file_targets"] = list(avoided)
        self.config["preferred_file_targets"] = list(preferred)
        return changes

    # ── Prompt Injection ──────────────────────────────────────────

    def get_config_for_prompt(self) -> str:
        """Return config data formatted for injection into the reflection prompt."""
        parts = []

        # Learned rules
        rules = self.config.get("learned_rules", [])
        if rules:
            parts.append("LEARNED RULES (follow these):")
            for rule in rules[:10]:
                parts.append(f"  ✅ {rule}")

        # Prompt hints
        hints = self.config.get("prompt_hints", [])
        if hints:
            parts.append("SELF-TAUGHT LESSONS:")
            for hint in hints[:10]:
                parts.append(f"  💡 {hint}")

        # Banned patterns
        banned = self.config.get("banned_patterns", [])
        if banned:
            parts.append("BANNED TOPICS (never propose these):")
            for b in banned:
                parts.append(f"  🚫 {b}")

        # Category weights
        weights = self.config.get("category_weights", {})
        non_default = {k: v for k, v in weights.items() if abs(v - 1.0) > 0.01}
        if non_default:
            parts.append("CATEGORY PREFERENCES:")
            for cat, w in sorted(non_default.items(), key=lambda x: -x[1]):
                label = "preferred" if w > 1.0 else "less confident"
                parts.append(f"  {'🟢' if w > 1 else '🟡'} {cat}: {label} (weight: {w:.1f})")

        # Avoided files
        avoided = self.config.get("avoided_file_targets", [])
        if avoided:
            parts.append("AVOID THESE FILES (high failure rate):")
            for f in avoided[:5]:
                parts.append(f"  ⚠️ {f}")

        if not parts:
            return ""

        return "\n".join(["BRAIN CONFIG (self-taught intelligence):"] + parts) + "\n"
