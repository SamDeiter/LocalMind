"""
routes/memory.py — Memory Management Router
=============================================
Handles all memory-related API endpoints:
- Toggle learning mode (save vs read-only)
- List stored memories from ChromaDB
- Delete specific memories

Memories are vector-embedded facts about the user stored in ChromaDB.
They're used to personalize AI responses across conversations.
The "learning" toggle lets users pause memory saving while still
allowing the AI to recall existing memories.
"""

import datetime
import logging

from fastapi import APIRouter

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
async def memory_toggle(request):
    """Toggle learning mode on/off.
    
    When learning is OFF:
    - AI can still recall existing memories (read-only)
    - AI will NOT save new memories from conversations
    - Useful when discussing sensitive topics
    
    When learning is ON (default):
    - AI saves personal facts, preferences, and instructions
    - Both via model-initiated tool calls and auto-save heuristic
    """
    from fastapi import Request
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
        from backend.tools.memory import _get_collection
        collection = _get_collection()
        if collection.count() == 0:
            return {"memories": [], "count": 0}

        results = collection.get(include=["documents", "metadatas"])
        memories = []
        for doc_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"]):
            # Convert Unix timestamp to human-readable format
            ts = meta.get("created_at", "0")
            try:
                dt = datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                dt = "unknown"

            memories.append({
                "id": doc_id,
                "content": doc,
                "category": meta.get("category", "general"),
                "created_at": dt,
            })

        # Sort newest first so the most recent memories appear at top
        memories.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return {"memories": memories, "count": len(memories)}
    except Exception as e:
        logger.warning(f"Failed to list memories: {e}")
        return {"memories": [], "count": 0, "error": str(e)}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a specific memory by its ChromaDB document ID.
    
    Users can delete memories from the Memory Viewer panel.
    This is important for privacy — users should always be able
    to remove any information the AI has stored about them.
    """
    try:
        from backend.tools.memory import _get_collection
        collection = _get_collection()
        collection.delete(ids=[memory_id])
        logger.info(f"Deleted memory: {memory_id}")
        return {"success": True, "deleted": memory_id}
    except Exception as e:
        logger.warning(f"Failed to delete memory {memory_id}: {e}")
        return {"success": False, "error": str(e)}
