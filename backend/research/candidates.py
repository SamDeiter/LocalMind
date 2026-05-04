"""
Change-candidate parser + status store.

Lane jobs save memories shaped like:
    "LocalMind change candidate (<Lane>): <change>. Hook: <hook>.
     Why: <why>. (source: <url>)"

This module surfaces those memories as structured records so the frontend
can list/filter/triage them. Status (new | accepted | rejected | implemented)
is persisted in a small JSON sidecar so we don't have to touch the schema.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.research.candidates")

CONFIG_DIR = Path.home() / ".localmind"
STATUS_FILE = CONFIG_DIR / "candidate_status.json"

VALID_STATUSES = ("new", "accepted", "rejected", "implemented")
DEFAULT_STATUS = "new"

# Captures (lane, change, hook, why, source_url) from the canonical format.
_RE_CANDIDATE = re.compile(
    r"LocalMind change candidate\s*\(([^)]+)\):\s*(.*?)\."
    r"\s*Hook:\s*(.*?)\."
    r"\s*Why:\s*(.*?)(?:\.\s*)?\(source:\s*(\S+?)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _load_status_map() -> dict[str, str]:
    """Read the sidecar status map. Returns {} if missing/corrupt."""
    if not STATUS_FILE.exists():
        return {}
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read candidate status file: %s", exc)
        return {}


def _save_status_map(data: dict[str, str]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.error("Could not write candidate status file: %s", exc)


def _parse_candidate(content: str) -> Optional[dict]:
    """Parse a candidate-shaped memory body. Returns None if it doesn't match."""
    if not content or "LocalMind change candidate" not in content:
        return None
    match = _RE_CANDIDATE.search(content.strip())
    if not match:
        # Fall back: capture lane only, leave the rest as raw text.
        lane_match = re.search(r"LocalMind change candidate\s*\(([^)]+)\)", content)
        return {
            "lane": (lane_match.group(1).strip() if lane_match else "Unknown"),
            "change": content,
            "hook": None,
            "why": None,
            "source_url": None,
            "parsed": False,
        }
    lane, change, hook, why, source_url = match.groups()
    return {
        "lane": lane.strip(),
        "change": change.strip(),
        "hook": hook.strip(),
        "why": why.strip(),
        "source_url": source_url.strip().rstrip(".,;)"),
        "parsed": True,
    }


def status_for(memory_id) -> str:
    """Look up the status for a memory id; default to 'new'."""
    return _load_status_map().get(str(memory_id), DEFAULT_STATUS)


def set_status(memory_id, status: str) -> str:
    """Set the status for a memory id. Returns the persisted status."""
    s = (status or "").strip().lower()
    if s not in VALID_STATUSES:
        raise ValueError(
            f"status must be one of {VALID_STATUSES}, got {status!r}"
        )
    data = _load_status_map()
    data[str(memory_id)] = s
    data[f"{memory_id}.updated_at"] = int(time.time())
    _save_status_map(data)
    return s


def list_candidates(
    status_filter: Optional[str] = None,
    lane_filter: Optional[str] = None,
) -> list[dict]:
    """Return all memories shaped as change candidates, oldest-first.

    Each entry: {memory_id, lane, change, hook, why, source_url, status,
    created_at, raw_content, parsed}.
    """
    try:
        from backend.memory.retriever import MemoryRetriever  # type: ignore
    except Exception:
        try:
            # Older path — adapt if the retriever lives elsewhere.
            from backend.memory import MemoryRetriever  # type: ignore
        except Exception:
            MemoryRetriever = None  # type: ignore[assignment]

    memories: list[dict] = []
    if MemoryRetriever is not None:
        try:
            retr = MemoryRetriever()
            # Same call shape /api/memories uses.
            mems = retr.list_all() if hasattr(retr, "list_all") else retr.all()
            for m in mems:
                memories.append({
                    "id": getattr(m, "id", None) if not isinstance(m, dict) else m.get("id"),
                    "content": getattr(m, "content", None) if not isinstance(m, dict) else m.get("content"),
                    "category": getattr(m, "category", None) if not isinstance(m, dict) else m.get("category"),
                    "created_at": str(
                        getattr(m, "created_at", None) if not isinstance(m, dict) else m.get("created_at")
                    ),
                })
        except Exception as exc:
            logger.debug("Direct retriever access failed, falling back: %s", exc)
            memories = []

    # Fallback: hit our own /api/memories via in-process import — avoids
    # having to know the retriever's exact interface.
    if not memories:
        try:
            from backend.routes.memory import list_memories  # type: ignore
            data = list_memories()  # may be sync or coroutine
            if hasattr(data, "__await__"):
                # Don't run an event loop here; let the route layer call us instead.
                memories = []
            else:
                memories = list(data.get("memories") or [])
        except Exception as exc:
            logger.debug("Could not enumerate memories: %s", exc)
            memories = []

    status_map = _load_status_map()
    out: list[dict] = []
    for m in memories:
        parsed = _parse_candidate(m.get("content") or "")
        if not parsed:
            continue
        sid = str(m.get("id"))
        st = status_map.get(sid, DEFAULT_STATUS)
        if status_filter and st != status_filter:
            continue
        if lane_filter and parsed["lane"].lower() != lane_filter.lower():
            continue
        out.append({
            "memory_id": m.get("id"),
            "lane": parsed["lane"],
            "change": parsed["change"],
            "hook": parsed["hook"],
            "why": parsed["why"],
            "source_url": parsed["source_url"],
            "parsed": parsed["parsed"],
            "status": st,
            "created_at": m.get("created_at"),
            "raw_content": m.get("content"),
        })

    # Newest first — easier to triage.
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def list_candidates_from_memory_list(memories: list[dict]) -> list[dict]:
    """Variant that takes the memory list directly (used by route handlers)."""
    status_map = _load_status_map()
    out: list[dict] = []
    for m in memories or []:
        parsed = _parse_candidate(m.get("content") or "")
        if not parsed:
            continue
        sid = str(m.get("id"))
        out.append({
            "memory_id": m.get("id"),
            "lane": parsed["lane"],
            "change": parsed["change"],
            "hook": parsed["hook"],
            "why": parsed["why"],
            "source_url": parsed["source_url"],
            "parsed": parsed["parsed"],
            "status": status_map.get(sid, DEFAULT_STATUS),
            "created_at": m.get("created_at"),
            "raw_content": m.get("content"),
        })
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out
