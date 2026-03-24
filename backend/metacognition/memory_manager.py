"""
Memory Manager — Session + Long-term memory with strict write policies.

Session memory: lives for one conversation, tracks working context.
Long-term memory: persists across sessions, stores stable user preferences.

Write policy enforced here:
  - Inferred preferences need 3+ observations across 2+ sessions
  - Explicit statements write immediately
  - 30-day decay on unseen preferences
  - Never store secrets, paths, or transient data
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from backend.metacognition.models.memory import UserPreference

logger = logging.getLogger("metacognition.memory")

WORKSPACE = Path.home() / "LocalMind_Workspace"
PREFERENCES_PATH = WORKSPACE / "user_preferences.json"

# Keys that should NEVER be stored (security policy)
FORBIDDEN_KEYS = {
    "api_key", "password", "secret", "token", "credential",
    "ssn", "credit_card", "private_key",
}


class MemoryManager:
    """Manages session and long-term memory with strict write policies."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or PREFERENCES_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._preferences: dict = {}  # key -> UserPreference
        self._load()

    def _load(self) -> None:
        """Load preferences from disk."""
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, val in data.items():
                self._preferences[key] = UserPreference.from_dict(val)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load preferences: {e}")

    def _save(self) -> None:
        """Persist durable preferences to disk."""
        durable = {
            k: v.to_dict() for k, v in self._preferences.items()
            if v.is_durable()
        }
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(durable, f, indent=2)
        except OSError as e:
            logger.warning(f"Failed to save preferences: {e}")

    # ── Read ──────────────────────────────────────────────────────────

    def read_preferences(self, domain: str = "") -> list:
        """
        Read relevant preferences. Apply decay to stale ones.

        Returns preferences with confidence > 0.3 (below that = too uncertain).
        """
        self._apply_decay()
        results = []
        for pref in self._preferences.values():
            if pref.confidence < 0.3:
                continue  # Too uncertain to use
            if domain and pref.key.startswith(f"{domain}."):
                results.append(pref)
            elif not domain:
                results.append(pref)
        return results

    def get_preference(self, key: str) -> Optional[UserPreference]:
        """Get a specific preference by key."""
        pref = self._preferences.get(key)
        if pref and pref.confidence >= 0.3:
            return pref
        return None

    # ── Write ─────────────────────────────────────────────────────────

    def propose_preference(
        self,
        key: str,
        value: str,
        source: str = "inferred",
        session_id: str = "",
    ) -> bool:
        """
        Propose a new preference observation.

        Does NOT immediately persist inferred preferences.
        Requires 3+ observations across 2+ sessions before it becomes durable.
        Explicit preferences write immediately.
        """
        # Security check
        if any(forbidden in key.lower() for forbidden in FORBIDDEN_KEYS):
            logger.warning(f"Blocked forbidden preference key: {key}")
            return False

        existing = self._preferences.get(key)

        if existing:
            # Update existing preference
            is_new_session = (session_id and
                              session_id != getattr(existing, '_last_session', ''))
            existing.observe(session_id=session_id, is_new_session=is_new_session)
            existing.value = value  # Update to latest observed value
            existing._last_session = session_id
        else:
            # Create new preference
            confidence = 0.9 if source == "explicit" else 0.4
            pref = UserPreference(
                key=key,
                value=value,
                confidence=confidence,
                source=source,
            )
            pref._last_session = session_id
            self._preferences[key] = pref

        self._save()
        return True

    def forget(self, key: str) -> bool:
        """Explicitly remove a preference."""
        if key in self._preferences:
            del self._preferences[key]
            self._save()
            return True
        return False

    # ── Maintenance ───────────────────────────────────────────────────

    def _apply_decay(self) -> None:
        """Apply 30-day decay to all preferences."""
        for pref in self._preferences.values():
            pref.apply_decay()

    def stats(self) -> dict:
        """Summary stats for debugging."""
        total = len(self._preferences)
        durable = sum(1 for p in self._preferences.values() if p.is_durable())
        explicit = sum(1 for p in self._preferences.values() if p.source == "explicit")
        return {
            "total_preferences": total,
            "durable_preferences": durable,
            "explicit": explicit,
            "inferred": total - explicit,
        }

    def all_preferences(self) -> list:
        """Return all preferences as dicts (for debugging/display)."""
        return [p.to_dict() for p in self._preferences.values()]
