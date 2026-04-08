"""
routes/time_machine.py -- AI Time Machine REST API
====================================================
Browse, inspect, and restore historical action versions.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.time_machine.replay import get_replay_engine

logger = logging.getLogger("localmind.routes.time_machine")

router = APIRouter(prefix="/api/time-machine", tags=["time-machine"])


# ── GET /actions ─────────────────────────────────────────────────────
@router.get("/actions")
async def list_actions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    entity_type: Optional[str] = Query(None),
    since: Optional[float] = Query(None),
):
    """List action summaries (no before/after state for the list view).

    Query params:
        limit       -- max results (default 50, max 500)
        offset      -- pagination offset
        entity_type -- filter by entity type (optional)
        since       -- only actions after this unix timestamp (optional)
    """
    try:
        from backend.time_machine.recorder import get_recorder
        recorder = get_recorder()
        actions = recorder.list_actions(
            limit=limit,
            offset=offset,
            entity_type=entity_type,
            since=since,
        )
        # Strip heavy blobs from the list view
        summaries = []
        for a in actions:
            summary = {
                "id": a.get("id"),
                "action_type": a.get("action_type"),
                "entity_type": a.get("entity_type"),
                "entity_id": a.get("entity_id"),
                "timestamp": a.get("timestamp"),
                "reverted": a.get("reverted", 0),
                "conversation_id": a.get("conversation_id"),
                "user_id": a.get("user_id"),
            }
            summaries.append(summary)
        return {"actions": summaries, "count": len(summaries)}
    except Exception as e:
        logger.warning("list_actions failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /actions/{action_id} ────────────────────────────────────────
@router.get("/actions/{action_id}")
async def get_action(action_id: int):
    """Return full action detail including before/after state."""
    try:
        from backend.time_machine.recorder import get_recorder
        recorder = get_recorder()
        action = recorder.get_action(action_id)
        if not action:
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")
        return action
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("get_action(%s) failed: %s", action_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /restore/{action_id} ──────────────────────────────────────
@router.post("/restore/{action_id}")
async def restore_action(action_id: int):
    """Restore an entity to the state *before* the given action."""
    engine = get_replay_engine()
    result = engine.restore_action(action_id)
    if not result.get("ok"):
        status = 404 if "not found" in result.get("error", "").lower() else 400
        raise HTTPException(status_code=status, detail=result.get("error"))
    return result


# ── GET /timeline ───────────────────────────────────────────────────
@router.get("/timeline")
async def get_timeline(
    since: float = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """Lightweight timeline data for the UI slider (no state blobs)."""
    engine = get_replay_engine()
    events = engine.get_timeline(since=since, limit=limit)
    return {"timeline": events, "count": len(events)}


# ── GET /entity/{entity_type}/{entity_id}/history ───────────────────
@router.get("/entity/{entity_type}/{entity_id}/history")
async def get_entity_history(
    entity_type: str,
    entity_id: str,
    limit: int = Query(20, ge=1, le=200),
):
    """Full history of actions affecting a specific entity."""
    engine = get_replay_engine()
    history = engine.get_entity_history(
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
    )
    return {"entity_type": entity_type, "entity_id": entity_id, "history": history, "count": len(history)}
