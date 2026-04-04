"""
Swarm Routes — API endpoints for the Hive Mind dashboard.
"""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("localmind.routes.swarm")

router = APIRouter(prefix="/api/swarm", tags=["swarm"])


@router.get("/status")
async def swarm_status(request: Request):
    """Full swarm status: agents, queue, metrics."""
    engine = request.app.state.autonomy_engine
    coordinator = getattr(engine, "coordinator", None)
    if not coordinator:
        return JSONResponse({"error": "Swarm not initialized", "running": False}, status_code=200)
    return JSONResponse(coordinator.get_status())


@router.get("/history")
async def swarm_history(request: Request):
    """Return recent activity events for hydration."""
    engine = request.app.state.autonomy_engine
    if not engine:
        return JSONResponse({"history": []})
    return JSONResponse({"history": engine.get_recent_events()})


@router.get("/agents")
async def swarm_agents(request: Request):
    """Detailed status for every agent in the swarm."""
    engine = request.app.state.autonomy_engine
    coordinator = getattr(engine, "coordinator", None)
    if not coordinator:
        return JSONResponse({"agents": []})
    return JSONResponse({"agents": coordinator.get_agent_details()})


@router.post("/scan")
async def trigger_scan(request: Request):
    """Submit a full parallel codebase scan."""
    engine = request.app.state.autonomy_engine
    coordinator = getattr(engine, "coordinator", None)
    if not coordinator:
        return JSONResponse({"error": "Swarm not initialized"}, status_code=503)

    task_ids = coordinator.submit_parallel_scan(chunk_size=20)
    return JSONResponse({
        "message": f"Submitted {len(task_ids)} parallel scan tasks",
        "task_ids": task_ids,
    })


@router.post("/research")
async def trigger_research(request: Request):
    """Submit a research query to the swarm."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    query = body.get("query", "")
    source = body.get("source", "web")

    engine = request.app.state.autonomy_engine
    coordinator = getattr(engine, "coordinator", None)
    if not coordinator:
        return JSONResponse({"error": "Swarm not initialized"}, status_code=503)

    task_id = coordinator.submit_research(query, source)
    return JSONResponse({"task_id": task_id, "query": query, "source": source})


@router.post("/test")
async def trigger_test(request: Request):
    """Submit a test/validation task."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid or missing JSON body"}, status_code=400)
    mode = body.get("mode", "syntax")
    files = body.get("files", [])

    engine = request.app.state.autonomy_engine
    coordinator = getattr(engine, "coordinator", None)
    if not coordinator:
        return JSONResponse({"error": "Swarm not initialized"}, status_code=503)

    task_id = coordinator.submit_test(files, mode)
    return JSONResponse({"task_id": task_id, "mode": mode})


@router.post("/scale")
async def scale_workers(request: Request):
    """Adjust worker counts at runtime (for future use)."""
    body = await request.json()
    # Placeholder — future implementation will dynamically add/remove workers
    return JSONResponse({
        "message": "Scaling not yet implemented. Use startup config.",
        "requested": body,
    })
