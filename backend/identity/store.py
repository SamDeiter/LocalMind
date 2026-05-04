"""Identity store — persistent name + persona for the AI.

Lives at ~/LocalMind_Workspace/ai_identity.json. Editable by:
- The user (via /api/identity endpoints)
- The bot itself (via the `update_identity` tool)

Default identity is the original LocalMind persona; the bot is free to
adopt a new name or refine its persona over time.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("localmind.identity")

WORKSPACE = Path.home() / "LocalMind_Workspace"
IDENTITY_PATH = WORKSPACE / "ai_identity.json"

DEFAULT_NAME = "LocalMind"
DEFAULT_PERSONA = (
    "An autonomous task worker. Acts first, asks clarifying questions only "
    "when literally unable to proceed. Curious about the user, learns over time."
)
DEFAULT_VOICE = "direct, action-oriented, warm but concise"

# Forbidden name patterns — the bot must not impersonate trademarks or people.
_FORBIDDEN_NAME_FRAGMENTS = (
    "openai", "anthropic", "claude", "gpt", "chatgpt", "google",
    "microsoft", "siri", "alexa", "cortana",
)


@dataclass
class Identity:
    name: str = DEFAULT_NAME
    persona: str = DEFAULT_PERSONA
    voice: str = DEFAULT_VOICE
    history: List[dict] = field(default_factory=list)  # [{at, field, old, new, reason}]
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Identity":
        return cls(
            name=d.get("name", DEFAULT_NAME),
            persona=d.get("persona", DEFAULT_PERSONA),
            voice=d.get("voice", DEFAULT_VOICE),
            history=d.get("history", []) or [],
            updated_at=d.get("updated_at", 0.0),
        )


def _name_is_safe(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n or len(n) > 40:
        return False
    if any(frag in n for frag in _FORBIDDEN_NAME_FRAGMENTS):
        return False
    # Letters, digits, spaces, basic punctuation only.
    return all(c.isalnum() or c in " -_'." for c in n)


class IdentityStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or IDENTITY_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._identity = Identity()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._identity = Identity.from_dict(data)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Identity load failed, using defaults: {e}")

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._identity.to_dict(), f, indent=2)
        except OSError as e:
            logger.warning(f"Identity save failed: {e}")

    def get(self) -> Identity:
        return self._identity

    def update(
        self,
        name: Optional[str] = None,
        persona: Optional[str] = None,
        voice: Optional[str] = None,
        reason: str = "",
        actor: str = "system",
    ) -> dict:
        """Update one or more identity fields. Returns {ok, errors, identity}."""
        errors = []
        changed = []

        if name is not None:
            name = name.strip()
            if not _name_is_safe(name):
                errors.append(f"Name '{name}' rejected (length, characters, or trademark conflict).")
            elif name != self._identity.name:
                self._identity.history.append({
                    "at": time.time(),
                    "field": "name",
                    "old": self._identity.name,
                    "new": name,
                    "reason": reason,
                    "actor": actor,
                })
                self._identity.name = name
                changed.append("name")

        if persona is not None:
            persona = persona.strip()
            if len(persona) > 1500:
                errors.append("Persona too long (>1500 chars).")
            elif persona and persona != self._identity.persona:
                self._identity.history.append({
                    "at": time.time(),
                    "field": "persona",
                    "old": self._identity.persona[:200],
                    "new": persona[:200],
                    "reason": reason,
                    "actor": actor,
                })
                self._identity.persona = persona
                changed.append("persona")

        if voice is not None:
            voice = voice.strip()
            if len(voice) > 200:
                errors.append("Voice descriptor too long (>200 chars).")
            elif voice and voice != self._identity.voice:
                self._identity.history.append({
                    "at": time.time(),
                    "field": "voice",
                    "old": self._identity.voice,
                    "new": voice,
                    "reason": reason,
                    "actor": actor,
                })
                self._identity.voice = voice
                changed.append("voice")

        if changed:
            # Cap history to last 50 entries so the file stays small.
            self._identity.history = self._identity.history[-50:]
            self._identity.updated_at = time.time()
            self._save()
            logger.info(f"Identity updated by {actor}: {changed} (reason: {reason or 'n/a'})")
            try:
                from backend.observability.activity_log import ActivityKind, record
                summary_parts = []
                if "name" in changed:
                    summary_parts.append(f"name -> '{self._identity.name}'")
                if "persona" in changed:
                    summary_parts.append("persona updated")
                if "voice" in changed:
                    summary_parts.append(f"voice -> '{self._identity.voice}'")
                record(
                    ActivityKind.IDENTITY_CHANGE,
                    f"Identity: {', '.join(summary_parts)}",
                    actor=actor,
                    detail={"changed": changed, "reason": reason or ""},
                    success=True,
                )
            except Exception:
                pass

        return {
            "ok": not errors,
            "errors": errors,
            "changed": changed,
            "identity": self._identity.to_dict(),
        }

    def reset_to_default(self) -> dict:
        return self.update(
            name=DEFAULT_NAME,
            persona=DEFAULT_PERSONA,
            voice=DEFAULT_VOICE,
            reason="reset to default",
            actor="user",
        )


_singleton: Optional[IdentityStore] = None


def get_identity_store() -> IdentityStore:
    global _singleton
    if _singleton is None:
        _singleton = IdentityStore()
    return _singleton


def get_identity() -> Identity:
    return get_identity_store().get()
