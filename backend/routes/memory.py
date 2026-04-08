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
