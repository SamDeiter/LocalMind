"""
routes/memory.py — Memory Management Router
=============================================
Handles all memory-related API endpoints:
- Toggle learning mode (save vs read-only)
- List stored memories from FTS5 store
- Delete specific memories

Memories are stored in SQLite with FTS5 full-text search.
They're used to personalize AI responses across conversations.
The "learning" toggle lets users pause memory saving while still
allowing the AI to recall existing memories.
"""

import datetime
import logging

from fastapi import APIRouter, Request

logger = logging.getLogger("localmind.routes.memory")

# Create router with /api prefix — all endpoints are memory-related
router = APIRouter(prefix="/api", tags=["memory"])

# ── Shared State ──────────────────────────────────────────────────────
# learning_enabled controls whether the AI can SAVE new memories.
# When False, the AI can still READ/recall memories but won't store new ones.
# This is a module-level flag set by the server on startup and toggled via API.
learning_enabled = True


def set_learning_enabled(value: bool):
    """Called by server.py to sync the global learning state."""
    global learning_enabled
    learning_enabled = value


def get_learning_enabled() -> bool:
    """Check if learning (memory saving) is currently enabled."""
    return learning_enabled


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/memory/status")
async def memory_status():
    """Return whether learning (memory saving) is enabled.
    
    The frontend uses this to show/hide the learning toggle indicator
    and to decide whether to show the 'Learning: ON/OFF' badge.
    """
    return {"learning_enabled": learning_enabled}


@router.post("/memory/toggle")
async def memory_toggle(request: Request):
    """Toggle learning mode on/off.
    
    When learning is OFF:
    - AI can still recall existing memories (read-only)
    - AI will NOT save new memories from conversations
    - Useful when discussing sensitive topics
    
    When learning is ON (default):
    - AI saves personal facts, preferences, and instructions
    - Both via model-initiated tool calls and auto-save heuristic
    """
    global learning_enabled
    body = await request.json()
    learning_enabled = body.get("enabled", True)
    logger.info(f"Learning mode toggled: {learning_enabled}")
    return {"learning_enabled": learning_enabled}


@router.get("/memories")
async def list_memories():
    """List all stored memories with id, content, category, and timestamp.
    
    Returns memories sorted by creation date (newest first).
    Each memory includes:
    - id: ChromaDB document ID (used for deletion)
    - content: The actual memory text (e.g., "User's name is Sam")
    - category: Classification (fact, preference, instruction, general)
    - created_at: Human-readable timestamp
    
    The frontend displays these in the Memory Viewer panel.
    """
    try:
        from backend.tools.memory import _get_fts_store
        store = _get_fts_store()
        if not store:
            return {"memories": [], "count": 0}

        total = store.count()
        if total == 0:
            return {"memories": [], "count": 0}

        recent = store.get_recent(limit=200)
        memories = []
        for m in recent:
            try:
                dt = datetime.datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                dt = "unknown"

            memories.append({
                "id": m.id,
                "content": m.content,
                "category": m.subcategory or m.category,
                "created_at": dt,
            })

        return {"memories": memories, "count": total}
    except Exception as e:
        logger.warning(f"Failed to list memories: {e}")
        return {"memories": [], "count": 0, "error": str(e)}


@router.get("/memory/session-stats")
async def session_stats():
    """Return statistics about the in-process session memory cache.

    This is the ephemeral Tier-1 cache (dual memory architecture).
    Useful for debugging and monitoring the short-term memory layer.
    """
    try:
        from backend.memory.session_cache import get_session_cache
        cache = get_session_cache()
        return cache.stats()
    except Exception as e:
        logger.warning(f"Failed to get session stats: {e}")
        return {"error": str(e)}


@router.get("/profile")
async def get_user_profile():
    """Return what the AI has learned about the user.

    Combines durable preferences (>= 0.3 confidence, post-decay) into a
    flat dict keyed by stable dotted slots (identity.name, prefs.style, etc.).
    Used by the UI to show "what I know about you" and let the user edit it.
    """
    try:
        from backend.metacognition.memory_manager import get_memory_manager
        from backend.logic.curiosity import PROFILE_SLOTS, find_unfilled_slot
        mm = get_memory_manager()
        prefs = mm.read_preferences()
        profile = {p.key: {
            "value": p.value,
            "confidence": round(p.confidence, 3),
            "source": p.source,
            "observations": p.observation_count,
        } for p in prefs}

        known_keys = set(profile.keys())
        unfilled = [s for s in PROFILE_SLOTS if s["key"] not in known_keys]
        next_question = find_unfilled_slot(known_keys)

        return {
            "profile": profile,
            "known_count": len(profile),
            "unfilled_slots": [s["key"] for s in unfilled],
            "next_curiosity": next_question["example"] if next_question else None,
        }
    except Exception as e:
        logger.warning(f"Failed to load user profile: {e}")
        return {"profile": {}, "known_count": 0, "error": str(e)}


@router.post("/profile/reload")
async def reload_user_profile():
    """Re-read user_preferences.json from disk (refreshes the in-memory singleton).

    Use after migrating the JSON file by hand or after restoring a backup.
    """
    try:
        from backend.metacognition import memory_manager as mm_module
        mm_module._singleton = None  # force re-init on next access
        fresh = mm_module.get_memory_manager()
        prefs = fresh.read_preferences()
        return {"reloaded": True, "count": len(prefs), "keys": [p.key for p in prefs]}
    except Exception as e:
        logger.warning(f"Profile reload failed: {e}")
        return {"reloaded": False, "error": str(e)}


@router.delete("/profile/{key:path}")
async def forget_profile_key(key: str):
    """Remove one learned fact from the user profile."""
    try:
        from backend.metacognition.memory_manager import get_memory_manager
        mm = get_memory_manager()
        removed = mm.forget(key)
        return {"success": removed, "key": key}
    except Exception as e:
        logger.warning(f"Failed to forget profile key {key}: {e}")
        return {"success": False, "error": str(e)}


# ── AI identity ───────────────────────────────────────────────────────


@router.get("/identity")
async def get_ai_identity():
    """Return the AI's current name, persona, and change history."""
    try:
        from backend.identity.store import get_identity_store
        ident = get_identity_store().get()
        return {"identity": ident.to_dict()}
    except Exception as e:
        logger.warning(f"Failed to load identity: {e}")
        return {"identity": None, "error": str(e)}


@router.post("/identity")
async def update_ai_identity(request: Request):
    """User-initiated identity update (rename, persona, voice)."""
    try:
        body = await request.json()
        from backend.identity.store import get_identity_store
        store = get_identity_store()
        result = store.update(
            name=body.get("name"),
            persona=body.get("persona"),
            voice=body.get("voice"),
            reason=body.get("reason", "user edit"),
            actor="user",
        )
        return result
    except Exception as e:
        logger.warning(f"Identity update failed: {e}")
        return {"ok": False, "errors": [str(e)]}


@router.post("/identity/reset")
async def reset_ai_identity():
    """Restore the default LocalMind identity."""
    try:
        from backend.identity.store import get_identity_store
        result = get_identity_store().reset_to_default()
        return result
    except Exception as e:
        return {"ok": False, "errors": [str(e)]}


# ── Agent activity log ────────────────────────────────────────────────


@router.get("/agent/activity")
async def get_agent_activity(since_id: int = 0, limit: int = 200):
    """Return what the bot has done recently (newest first).

    Sources merged into the feed:
      - Tool calls (auto-recorded by ToolRegistry)
      - Identity self-changes (rename, persona, voice)
      - Facts learned about the user
      - Theme rewrites

    Use `since_id` for delta polling: only entries with id > since_id are
    returned. The UI polls every 2s and tracks the highest id seen.
    """
    try:
        from backend.observability.activity_log import get_activity_log
        entries = get_activity_log(since_id=max(0, int(since_id)),
                                   limit=max(1, min(int(limit), 500)))
        return {"entries": entries, "count": len(entries)}
    except Exception as e:
        logger.warning(f"Activity log read failed: {e}")
        return {"entries": [], "count": 0, "error": str(e)}


@router.delete("/agent/activity")
async def clear_agent_activity():
    """Wipe the activity buffer. Local-only, no external effect."""
    try:
        from backend.observability.activity_log import clear
        return {"cleared": clear()}
    except Exception as e:
        return {"cleared": 0, "error": str(e)}


# ── Theme lock ────────────────────────────────────────────────────────


@router.get("/theme/lock")
async def theme_lock_status():
    from pathlib import Path
    lock = Path.home() / "LocalMind_Workspace" / "theme_lock"
    return {"locked": lock.exists()}


@router.post("/theme/lock")
async def theme_lock_set(request: Request):
    from pathlib import Path
    body = await request.json()
    locked = bool(body.get("locked", False))
    lock = Path.home() / "LocalMind_Workspace" / "theme_lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if locked:
        lock.touch()
    elif lock.exists():
        try:
            lock.unlink()
        except OSError:
            pass
    return {"locked": lock.exists()}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a specific memory by its ID.

    Users can delete memories from the Memory Viewer panel.
    This is important for privacy — users should always be able
    to remove any information the AI has stored about them.
    """
    try:
        from backend.tools.memory import _get_fts_store
        store = _get_fts_store()
        if not store:
            return {"success": False, "error": "Memory store unavailable"}
        store.delete(int(memory_id))
        logger.info(f"Deleted memory: {memory_id}")
        return {"success": True, "deleted": memory_id}
    except Exception as e:
        logger.warning(f"Failed to delete memory {memory_id}: {e}")
        return {"success": False, "error": str(e)}
