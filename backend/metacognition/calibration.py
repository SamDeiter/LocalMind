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
