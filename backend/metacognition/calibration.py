"""
Calibration Tracker — Logs predicted confidence vs actual outcome.

Appends to a JSONL file. Provides analysis functions to compute
accuracy per confidence bucket so thresholds can be tuned over time.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from backend.metacognition.models.memory import CalibrationEntry

logger = logging.getLogger("metacognition.calibration")

WORKSPACE = Path.home() / "LocalMind_Workspace"
CALIBRATION_PATH = WORKSPACE / "metacognition_calibration.jsonl"


class CalibrationTracker:
    """Logs and analyzes prediction calibration data."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or CALIBRATION_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, entry: CalibrationEntry) -> None:
        """Append a calibration entry to the log."""
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
        except OSError as e:
            logger.warning(f"Failed to log calibration: {e}")

    def read_all(self) -> list:
        """Read all calibration entries."""
        entries = []
        if not self.path.exists():
            return entries
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(CalibrationEntry.from_dict(json.loads(line)))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to read calibration log: {e}")
        return entries

    def accuracy_by_bucket(self) -> dict:
        """
        Compute actual success rate per confidence bucket.

        Returns: {"0.0-0.2": 0.33, "0.2-0.4": 0.55, ...}
        A well-calibrated system would have bucket rates match bucket centers.
        """
        buckets = {
            "0.0-0.2": {"total": 0, "success": 0},
            "0.2-0.4": {"total": 0, "success": 0},
            "0.4-0.6": {"total": 0, "success": 0},
            "0.6-0.8": {"total": 0, "success": 0},
            "0.8-1.0": {"total": 0, "success": 0},
        }

        for entry in self.read_all():
            if not entry.actual_outcome:
                continue
            conf = entry.predicted_confidence
            if conf < 0.2:
                bucket = "0.0-0.2"
            elif conf < 0.4:
                bucket = "0.2-0.4"
            elif conf < 0.6:
                bucket = "0.4-0.6"
            elif conf < 0.8:
                bucket = "0.6-0.8"
            else:
                bucket = "0.8-1.0"

            buckets[bucket]["total"] += 1
            if entry.actual_outcome == "success":
                buckets[bucket]["success"] += 1

        result = {}
        for bucket, data in buckets.items():
            if data["total"] > 0:
                result[bucket] = round(data["success"] / data["total"], 3)
            else:
                result[bucket] = None  # No data
        return result

    def overall_stats(self) -> dict:
        """High-level calibration statistics."""
        entries = self.read_all()
        if not entries:
            return {"total": 0}

        completed = [e for e in entries if e.actual_outcome]
        successes = [e for e in completed if e.actual_outcome == "success"]

        return {
            "total_logged": len(entries),
            "total_completed": len(completed),
            "success_rate": round(len(successes) / len(completed), 3) if completed else 0,
            "avg_confidence": round(
                sum(e.predicted_confidence for e in completed) / len(completed), 3
            ) if completed else 0,
            "avg_revisions": round(
                sum(e.revision_count for e in completed) / len(completed), 2
            ) if completed else 0,
        }

    # ── Self-Tuning Feedback Loop ────────────────────────────────────

    def log_action_outcome(
        self,
        action: str,
        uncertainty_score: float,
        was_helpful: bool,
        task_type: str = "",
    ) -> None:
        """Log whether a metacog action was actually helpful.

        Called after the turn completes:
        - ASK actions: was the follow-up meaningful (user answered) vs annoying (user ignored)?
        - ANSWER actions: did the response need revision?
        - ABSTAIN actions: did the user rephrase (appropriate) or get frustrated?
        """
        try:
            record = {
                "type": "action_outcome",
                "action": action,
                "uncertainty_score": round(uncertainty_score, 3),
                "was_helpful": was_helpful,
                "task_type": task_type,
                "timestamp": __import__("time").time(),
            }
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as e:
            logger.warning(f"Failed to log action outcome: {e}")

    def auto_adjust_thresholds(self, min_samples: int = 20) -> dict:
        """Compute recommended threshold adjustments from logged outcomes.

        Reads the JSONL log and detects:
        1. ASK actions that were unhelpful → raise the ASK threshold (ask less)
        2. ANSWER actions that needed revision → lower the ASK threshold (ask more)
        3. ABSTAIN actions that frustrated users → raise the ABSTAIN threshold

        Returns a dict of recommended thresholds (only if ≥ min_samples).
        The controller can apply these to its UncertaintyGate.

        This is the feedback loop that makes the system self-tuning.
        """
        entries = []
        if not self.path.exists():
            return {"status": "no_data"}

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            record = json.loads(line)
                            if record.get("type") == "action_outcome":
                                entries.append(record)
                        except json.JSONDecodeError:
                            continue
        except OSError:
            return {"status": "read_error"}

        if len(entries) < min_samples:
            return {
                "status": "insufficient_data",
                "samples": len(entries),
                "needed": min_samples,
            }

        # Analyze ASK outcomes
        ask_entries = [e for e in entries if e["action"] == "ask"]
        ask_helpful_rate = (
            sum(1 for e in ask_entries if e["was_helpful"]) / len(ask_entries)
            if ask_entries else 0.5
        )

        # Analyze ANSWER outcomes (those that needed revision)
        answer_entries = [e for e in entries if e["action"] == "answer"]
        answer_success_rate = (
            sum(1 for e in answer_entries if e["was_helpful"]) / len(answer_entries)
            if answer_entries else 0.5
        )

        # Analyze ABSTAIN outcomes
        abstain_entries = [e for e in entries if e["action"] == "abstain"]
        abstain_helpful_rate = (
            sum(1 for e in abstain_entries if e["was_helpful"]) / len(abstain_entries)
            if abstain_entries else 0.5
        )

        # Current defaults from UncertaintyScore
        current_ask = 0.6
        current_abstain = 0.85

        # Adjust ASK threshold
        # If ASK actions are rarely helpful → raise threshold (be less trigger-happy)
        # If ANSWER actions often need revision → lower threshold (ask more often)
        recommended_ask = current_ask
        if ask_helpful_rate < 0.4:
            # More than 60% of ASK actions were annoying → raise threshold
            recommended_ask = min(0.85, current_ask + 0.1)
            logger.info(
                f"SELF-TUNE: ASK helpful rate {ask_helpful_rate:.0%} is low "
                f"→ raising ASK threshold from {current_ask} to {recommended_ask}"
            )
        elif answer_success_rate < 0.5 and ask_helpful_rate > 0.7:
            # Answers often fail + ASK is usually helpful → lower threshold
            recommended_ask = max(0.3, current_ask - 0.1)
            logger.info(
                f"SELF-TUNE: Answer success {answer_success_rate:.0%} low, "
                f"ASK helpful {ask_helpful_rate:.0%} → lowering ASK to {recommended_ask}"
            )

        # Adjust ABSTAIN threshold
        recommended_abstain = current_abstain
        if abstain_helpful_rate < 0.3:
            # Abstaining too aggressively → raise threshold
            recommended_abstain = min(0.95, current_abstain + 0.05)
            logger.info(
                f"SELF-TUNE: ABSTAIN helpful rate {abstain_helpful_rate:.0%} low "
                f"→ raising ABSTAIN threshold to {recommended_abstain}"
            )

        return {
            "status": "computed",
            "samples": len(entries),
            "ask": {
                "current": current_ask,
                "recommended": round(recommended_ask, 2),
                "helpful_rate": round(ask_helpful_rate, 3),
                "sample_count": len(ask_entries),
            },
            "abstain": {
                "current": current_abstain,
                "recommended": round(recommended_abstain, 2),
                "helpful_rate": round(abstain_helpful_rate, 3),
                "sample_count": len(abstain_entries),
            },
            "answer": {
                "success_rate": round(answer_success_rate, 3),
                "sample_count": len(answer_entries),
            },
        }
