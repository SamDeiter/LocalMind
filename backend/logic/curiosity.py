"""Curiosity — the AI's drive to learn about the user.

Defines the profile slots LocalMind would like to know about its user.
At chat time, picks ONE unfilled slot and produces a short hint that tells
the model: "you're curious about X — if it fits naturally, ask."

Important framing: the AI is the LEARNER. It is not teaching the user
anything; it is curious about them and trying to build a mental model of
who they are. Questions should feel natural, not like an interrogation.

Gating rules (so the AI doesn't pester):
- Only suggest a question on conversational/casual turns, never on task
  requests (file ops, code edits, web search, etc.).
- At most one question per turn.
- Skip if the most recent user message already answered the slot
  (handled implicitly because by next turn the fact will be in the profile).
- Skip if the user is mid-task or sounds frustrated (heuristic).
"""

import logging
import random
from typing import Dict, List, Optional

logger = logging.getLogger("localmind.logic.curiosity")


# ── Slot definitions ─────────────────────────────────────────────────
# Ordered roughly by social priority — name first, deeper goals later.
PROFILE_SLOTS: List[Dict[str, str]] = [
    {"key": "identity.name",     "topic": "what to call you",                  "example": "Hey, what should I call you?"},
    {"key": "identity.role",     "topic": "what you do",                       "example": "Out of curiosity, what do you do for work?"},
    {"key": "work.tools",        "topic": "the stack/tools you work with",     "example": "What stack are you usually working in?"},
    {"key": "prefs.style",       "topic": "how you like responses (terse/detailed)", "example": "Do you tend to want short answers or more detail?"},
    {"key": "prefs.tone",        "topic": "preferred tone (formal/casual)",    "example": "Should I keep things casual or more buttoned-up?"},
    {"key": "identity.location", "topic": "where you're based (timezone hint)","example": "Where are you based, roughly?"},
    {"key": "goals.current",     "topic": "what you're working on right now",  "example": "What are you focused on this week?"},
    {"key": "hobbies",           "topic": "what you do outside work",          "example": "What do you get into outside work?"},
]


# Heuristic: user message looks like a task → don't interrupt with curiosity.
_TASK_KEYWORDS = (
    "fix", "bug", "error", "refactor", "implement", "build", "write",
    "create file", "edit", "run", "execute", "deploy", "commit", "push",
    "search", "look up", "find", "open", "install", "update", "delete",
    "send email", "draft", "screenshot",
)

_FRUSTRATION_MARKERS = (
    "stop", "no ", "not that", "wrong", "ugh", "frustrat", "annoying",
)


def _looks_like_task(message: str) -> bool:
    msg = (message or "").lower()
    return any(k in msg for k in _TASK_KEYWORDS)


def _sounds_frustrated(message: str) -> bool:
    msg = (message or "").lower()
    return any(k in msg for k in _FRUSTRATION_MARKERS)


def find_unfilled_slot(known_keys: set) -> Optional[Dict[str, str]]:
    """Return the highest-priority slot the AI doesn't yet know about."""
    for slot in PROFILE_SLOTS:
        if slot["key"] not in known_keys:
            return slot
    return None


def build_curiosity_hint(
    known_keys: set,
    user_message: str,
    turn_count: int,
    ask_probability: float = 0.6,
) -> Optional[str]:
    """Produce a short instruction to weave a curiosity question into the reply.

    Returns None if the AI shouldn't ask anything this turn.

    Args:
        known_keys: profile keys already known about the user.
        user_message: the current user turn (used for gating).
        turn_count: total turns in this conversation so far.
        ask_probability: chance of actually asking even when conditions hold.
    """
    if turn_count < 1:
        return None
    if _looks_like_task(user_message) or _sounds_frustrated(user_message):
        return None
    if random.random() > ask_probability:
        return None

    slot = find_unfilled_slot(known_keys)
    if not slot:
        return None

    return (
        f"You don't yet know {slot['topic']}. If, and only if, it fits naturally "
        f"at the end of your reply, ask one short question to learn this. "
        f"Example phrasing: \"{slot['example']}\". "
        f"Do not force it. Do not ask if the user is mid-task. Never ask more than one question."
    )


def render_profile_block(profile: Dict[str, str]) -> Optional[str]:
    """Render the known-user-facts block for the system prompt."""
    if not profile:
        return None
    lines = [f"- {k}: {v}" for k, v in profile.items()]
    return (
        "[USER_PROFILE] (what you've learned about the user across conversations)\n"
        + "\n".join(lines)
        + "\n[/USER_PROFILE]"
    )
