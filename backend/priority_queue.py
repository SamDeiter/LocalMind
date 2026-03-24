"""
priority_queue.py — User-defined priority queue for autonomy engine
====================================================================
Lets the user steer the engine by saying "focus on X" instead of
relying on random category rotation.

Priorities are stored in data/priorities.json and injected into
the reflection prompt, overriding the default category selection.
"""

import json
import logging
import time
import uuid
from pathlib import Path

logger = logging.getLogger("localmind.autonomy.priorities")

PRIORITIES_FILE = Path(__file__).resolve().parent.parent / "data" / "priorities.json"


class PriorityQueue:
    """JSON-backed priority queue for user-directed autonomy."""

    def __init__(self):
        PRIORITIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not PRIORITIES_FILE.exists():
            PRIORITIES_FILE.write_text("[]", encoding="utf-8")

    def _load(self) -> list[dict]:
        try:
            return json.loads(PRIORITIES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, priorities: list[dict]):
        PRIORITIES_FILE.write_text(json.dumps(priorities, indent=2), encoding="utf-8")

    def add(self, description: str, priority: str = "high") -> dict:
        """Add a user priority. Returns the created priority."""
        priorities = self._load()
        item = {
            "id": str(uuid.uuid4())[:8],
            "description": description,
            "priority": priority,
            "created_at": time.time(),
            "status": "active",
        }
        priorities.append(item)
        self._save(priorities)
        logger.info(f"Priority added: {description}")
        return item

    def list_all(self) -> list[dict]:
        """List all priorities."""
        return self._load()

    def list_active(self) -> list[dict]:
        """List only active priorities."""
        return [p for p in self._load() if p.get("status") == "active"]

    def get_top(self) -> dict | None:
        """Get the highest-priority active item."""
        active = self.list_active()
        if not active:
            return None
        # Sort: critical > high > medium > low, then oldest first
        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        active.sort(key=lambda p: (rank.get(p.get("priority", "low"), 9), p.get("created_at", 0)))
        return active[0]

    def complete(self, priority_id: str) -> dict | None:
        """Mark a priority as completed."""
        priorities = self._load()
        for p in priorities:
            if p["id"] == priority_id:
                p["status"] = "completed"
                p["completed_at"] = time.time()
                self._save(priorities)
                return p
        return None

    def remove(self, priority_id: str) -> bool:
        """Remove a priority entirely."""
        priorities = self._load()
        original = len(priorities)
        priorities = [p for p in priorities if p["id"] != priority_id]
        if len(priorities) < original:
            self._save(priorities)
            return True
        return False

    def get_prompt_injection(self) -> str:
        """Get priority context for the reflection prompt."""
        active = self.list_active()
        if not active:
            return ""

        lines = ["\\nUSER PRIORITIES (address these FIRST, they override category selection):"]
        for p in active[:5]:
            lines.append(f"  ⭐ [{p['priority'].upper()}] {p['description']}")
        lines.append("\\nIMPORTANT: Your proposal SHOULD address one of the user's priorities above.\\n")
        return "\\n".join(lines)
