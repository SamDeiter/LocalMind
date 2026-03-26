"""
backend/routes/autonomy_routes.py — Autonomy Engine API endpoints
=====================================================================
Extracted from server.py to keep that file lean.

Provides endpoints for:
- Engine status, toggle, mode switching
- Proposal CRUD (list, approve, deny, retry)
- Activity stream (SSE)
- Reflection/execution triggers
"""

import asyncio
import json
import time
import logging

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

logger = logging.getLogger("localmind.routes.autonomy")

router = APIRouter(prefix="/api", tags=["autonomy"])

# ── Dependency Injection ─────────────────────────────────────────────
# These are set by configure() called from server.py at startup.
_engine = None
_proposals_dir = None
_rag_available = False
_list_indexed_documents = None


def configure(
    *,
    engine,
    proposals_dir,
    rag_available=False,
    list_indexed_documents_fn=None,
):
    """Inject dependencies from server.py."""
    global _engine, _proposals_dir, _rag_available, _list_indexed_documents
    _engine = engine
    _proposals_dir = proposals_dir
    _rag_available = rag_available
    _list_indexed_documents = list_indexed_documents_fn


# ── Status & Control ─────────────────────────────────────────────────

@router.get("/autonomy/status")
async def autonomy_status():
    """Get the current autonomy engine status.

    Returns loop timing, proposal counts, test results, and uptime.
    Also includes global counts for memories, documents, and proposals.
    """
    status = _engine.get_status()
    status["mode"] = _engine.mode
    status["start_time"] = _engine._start_time
    status["recent_events"] = _engine._recent_events[-20:]

    # Add global counts for sidebar synchronization
    memories_count = 0
    try:
        from backend.tools.memory import _get_collection
        memories_count = _get_collection().count()
    except Exception:
        pass

    documents_count = 0
    try:
        if _rag_available and _list_indexed_documents:
            docs = _list_indexed_documents()
            documents_count = len(docs.get("documents", []))
    except Exception:
        pass

    proposals_count = 0
    try:
        if _proposals_dir and _proposals_dir.exists():
            proposals_count = len(list(_proposals_dir.glob("*.json")))
    except Exception:
        pass

    status["memories_count"] = memories_count
    status["documents_count"] = documents_count
    status["proposals_count"] = proposals_count

    return status


@router.post("/autonomy/reflect")
async def autonomy_reflect():
    """Manually trigger the AI reflection cycle."""
    _engine.trigger_reflection()
    return {"ok": True, "message": "Reflection cycle triggered"}


@router.post("/autonomy/execute")
async def autonomy_execute():
    """Manually trigger the AI execution cycle."""
    _engine.trigger_execution()
    return {"ok": True, "message": "Execution cycle triggered"}


@router.post("/autonomy/toggle")
async def toggle_autonomy():
    """Pause or resume the autonomy engine."""
    new_state = _engine.toggle()
    return {"enabled": new_state}


@router.post("/autonomy/mode")
async def set_autonomy_mode(request: Request, mode: str = None):
    """Switch between 'supervised' and 'autonomous' mode."""
    # Accept mode from query param OR JSON body
    if not mode:
        try:
            body = await request.json()
            mode = body.get("mode", "supervised")
        except Exception:
            mode = "supervised"
    try:
        new_mode = _engine.set_mode(mode)
        return {"ok": True, "mode": new_mode}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.post("/autonomy/reset")
async def reset_engine():
    """Full engine reset: archive stale proposals, retry failed, reset state.

    Archives all denied/skipped/completed proposals, retries all failed ones,
    resets circuit breaker/backoff/futility counters, and switches to autonomous mode.
    """
    try:
        summary = _engine.reset_engine()
        return {"ok": True, **summary}
    except Exception as e:
        logger.exception("Engine reset failed")
        return {"ok": False, "error": str(e)}


# ── Activity Stream (SSE) ────────────────────────────────────────────

@router.get("/autonomy/activity")
async def autonomy_activity_stream(request: Request):
    """SSE endpoint streaming real-time autonomy engine events.

    The frontend connects to this for the live activity feed.
    Each event is a JSON object with action, detail, and timestamp.
    """
    queue = _engine.subscribe_activity()

    async def event_generator():
        try:
            # Send current status as first event
            status = _engine.get_status()
            current = status.get("current_activity")
            if current:
                yield f"data: {json.dumps(current)}\n\n"
            else:
                yield f"data: {json.dumps({'action': 'idle', 'detail': 'Waiting...', 'time': time.strftime('%H:%M:%S')})}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive\n\n"
        finally:
            _engine.unsubscribe_activity(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Proposal Endpoints ───────────────────────────────────────────────

@router.get("/autonomy/proposals")
async def list_autonomy_proposals(status: str = "all"):
    """List all autonomy proposals, optionally filtered by status.

    Query params:
        status: 'all', 'proposed', 'approved', 'in_progress', 'completed', 'failed', 'denied'
    """
    proposals = _engine.list_proposals(status_filter=status)
    return {"proposals": proposals, "count": len(proposals)}


@router.post("/autonomy/proposals/{proposal_id}/approve")
async def approve_autonomy_proposal(proposal_id: str):
    """Approve a proposal so the execution loop will pick it up."""
    result = _engine.approve_proposal(proposal_id)
    if result is None:
        return {"ok": False, "error": f"Proposal not found: {proposal_id}"}
    return {"ok": True, "proposal": result}


@router.post("/autonomy/proposals/{proposal_id}/deny")
async def deny_autonomy_proposal(proposal_id: str):
    """Deny a proposal to prevent it from being executed."""
    result = _engine.deny_proposal(proposal_id)
    if result is None:
        return {"ok": False, "error": f"Proposal not found: {proposal_id}"}
    return {"ok": True, "proposal": result}


@router.post("/autonomy/proposals/{proposal_id}/retry")
async def retry_proposal(proposal_id: str):
    """Reset a failed proposal to approved status for re-execution."""
    updated = _engine.retry_proposal(proposal_id)
    if updated:
        return {"ok": True, "proposal": updated}
    return {"ok": False, "message": "Proposal not found or max retries reached"}


# ── Priority Queue ───────────────────────────────────────────────

@router.get("/autonomy/priorities")
async def list_priorities():
    """List all user-defined priorities."""
    return {"priorities": _engine.priority_queue.list_all()}


@router.post("/autonomy/priorities")
async def add_priority(request: Request):
    """Add a user priority to steer the autonomy engine."""
    body = await request.json()
    description = body.get("description", "").strip()
    priority = body.get("priority", "high")
    if not description:
        return {"ok": False, "error": "Description is required"}
    item = _engine.priority_queue.add(description, priority)
    return {"ok": True, "priority": item}


@router.delete("/autonomy/priorities/{priority_id}")
async def remove_priority(priority_id: str):
    """Remove a priority."""
    removed = _engine.priority_queue.remove(priority_id)
    return {"ok": removed}


# ── Daily Digest ─────────────────────────────────────────────────

@router.get("/autonomy/digest")
async def get_digest():
    """Get the latest daily digest."""
    from backend.digest import get_latest_digest
    return {"digest": get_latest_digest()}


@router.get("/autonomy/digest/{date}")
async def get_digest_by_date(date: str):
    """Get a digest for a specific date (YYYY-MM-DD)."""
    from backend.digest import get_digest_by_date
    result = get_digest_by_date(date)
    if result:
        return {"digest": result}
    return {"ok": False, "error": "No digest for that date"}


# ── Rollback ─────────────────────────────────────────────────────

@router.post("/autonomy/proposals/{proposal_id}/rollback")
async def rollback_proposal(proposal_id: str):
    """Revert a completed proposal's merge."""
    from backend.git_ops import get_merge_commit, revert_merge
    from backend.proposals import PROPOSALS_DIR
    import json

    # Find the proposal
    for f in PROPOSALS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id") == proposal_id:
                branch = data.get("branch")
                if not branch:
                    return {"ok": False, "error": "No branch info on this proposal"}

                merge_sha = get_merge_commit(branch)
                if not merge_sha:
                    return {"ok": False, "error": "Could not find merge commit"}

                success = revert_merge(merge_sha)
                if success:
                    data["status"] = "rolled_back"
                    data["rolled_back_at"] = __import__("time").time()
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    return {"ok": True, "reverted_sha": merge_sha}
                return {"ok": False, "error": "Git revert failed"}
        except (json.JSONDecodeError, OSError):
            continue

    return {"ok": False, "error": "Proposal not found"}


@router.get("/autonomy/category-stats")
async def category_stats():
    """Return per-category success/fail/total counts for dashboard charts."""
    from backend.proposals import PROPOSALS_DIR
    import json
    from collections import defaultdict

    stats = defaultdict(lambda: {"completed": 0, "failed": 0, "total": 0, "denied": 0})

    if PROPOSALS_DIR.exists():
        for f in PROPOSALS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                cat = data.get("category", "other")
                status = data.get("status", "unknown")
                stats[cat]["total"] += 1
                if status == "completed":
                    stats[cat]["completed"] += 1
                elif status == "failed":
                    stats[cat]["failed"] += 1
                elif status == "denied":
                    stats[cat]["denied"] += 1
            except (json.JSONDecodeError, OSError):
                continue

    # Calculate success rates
    result = {}
    for cat, counts in stats.items():
        decided = counts["completed"] + counts["failed"]
        rate = round((counts["completed"] / decided) * 100) if decided > 0 else 0
        result[cat] = {**counts, "success_rate": rate}

    return {"categories": result}
