"""Activity log — process-wide ring buffer of bot actions.

Thread-safe: a single Lock guards the deque. Bounded to MAX_ENTRIES so
memory stays flat. Entries are dicts (cheap, JSON-friendly).

Each entry carries:
  - id: monotonic counter (clients use this to do delta-fetches)
  - at: epoch seconds
  - kind: ActivityKind
  - actor: "bot" | "user" | "system"
  - summary: one-line human description
  - detail: structured payload (args/result preview)
  - duration_ms: optional, for tool calls
  - success: optional bool
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

MAX_ENTRIES = 200


class ActivityKind:
    TOOL_CALL = "tool_call"
    IDENTITY_CHANGE = "identity_change"
    FACT_LEARNED = "fact_learned"
    FACT_FORGOTTEN = "fact_forgotten"
    THEME_CHANGE = "theme_change"
    MEMORY_SAVE = "memory_save"


@dataclass
class _ActivityState:
    counter: int = 0
    entries: deque = field(default_factory=lambda: deque(maxlen=MAX_ENTRIES))
    lock: threading.Lock = field(default_factory=threading.Lock)


_state = _ActivityState()


def record(
    kind: str,
    summary: str,
    *,
    actor: str = "bot",
    detail: Optional[dict] = None,
    duration_ms: Optional[float] = None,
    success: Optional[bool] = None,
) -> dict:
    """Append a single activity entry. Returns the entry."""
    with _state.lock:
        _state.counter += 1
        entry = {
            "id": _state.counter,
            "at": time.time(),
            "kind": kind,
            "actor": actor,
            "summary": summary[:240],
            "detail": _trim_detail(detail or {}),
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
            "success": success,
        }
        _state.entries.append(entry)
        return entry


def get_activity_log(since_id: int = 0, limit: int = 200) -> list[dict]:
    """Return entries with id > since_id, newest first, capped at limit."""
    with _state.lock:
        snapshot = list(_state.entries)
    if since_id > 0:
        snapshot = [e for e in snapshot if e["id"] > since_id]
    snapshot.reverse()
    return snapshot[:limit]


def clear() -> int:
    """Empty the buffer. Returns how many entries were dropped."""
    with _state.lock:
        n = len(_state.entries)
        _state.entries.clear()
        return n


def _trim_detail(detail: dict) -> dict:
    """Cap each value at 400 chars so a giant tool result doesn't bloat memory."""
    out = {}
    for k, v in list(detail.items())[:12]:
        if isinstance(v, (dict, list)):
            try:
                import json
                s = json.dumps(v, default=str)
                if len(s) > 400:
                    s = s[:400] + "…"
                out[k] = s
            except Exception:
                out[k] = str(v)[:400]
        else:
            s = str(v)
            out[k] = s[:400] + ("…" if len(s) > 400 else "")
    return out
