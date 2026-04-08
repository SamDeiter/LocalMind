"""
Swarm Routes — API endpoints for the Hive Mind dashboard.
"""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.swarm.delegation import DelegationEngine
from backend.swarm.shared_memory import SharedMemoryStore
from backend.swarm.resource_lock import ResourceLockManager
from backend.swarm.messaging import AgentMessageBus

logger = logging.getLogger("localmind.routes.swarm")

router = APIRouter(prefix="/api/swarm", tags=["swarm"])


@router.get("/status")
async def swarm_status(request: Request):
    """Full swarm status: agents, queue, metrics."""
    engine = getattr(request.app.state, "autonomy_engine", None)
    coordinator = getattr(engine, "coordinator", None) if engine else None
    if not coordinator:
        return JSONResponse({"error": "Swarm not initialized", "running": False}, status_code=200)
    return JSONResponse(coordinator.get_status())


@router.get("/agents")
async def swarm_agents(request: Request):
    """Detailed status for every agent in the swarm."""
    engine = getattr(request.app.state, "autonomy_engine", None)
    coordinator = getattr(engine, "coordinator", None) if engine else None
    if not coordinator:
        return JSONResponse({"agents": []})
    return JSONResponse({"agents": coordinator.get_agent_details()})


@router.post("/scan")
async def trigger_scan(request: Request):
    """Submit a full parallel codebase scan."""
    engine = getattr(request.app.state, "autonomy_engine", None)
    coordinator = getattr(engine, "coordinator", None) if engine else None
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
    body = await request.json()
    query = body.get("query", "")
    source = body.get("source", "web")

    engine = getattr(request.app.state, "autonomy_engine", None)
    coordinator = getattr(engine, "coordinator", None) if engine else None
    if not coordinator:
        return JSONResponse({"error": "Swarm not initialized"}, status_code=503)

    task_id = coordinator.submit_research(query, source)
    return JSONResponse({"task_id": task_id, "query": query, "source": source})


@router.post("/test")
async def trigger_test(request: Request):
    """Submit a test/validation task."""
    body = await request.json()
    mode = body.get("mode", "syntax")
    files = body.get("files", [])

    engine = getattr(request.app.state, "autonomy_engine", None)
    coordinator = getattr(engine, "coordinator", None) if engine else None
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


@router.get("/tree/{job_id}")
async def delegation_tree(job_id: str):
    """Return the full delegation tree for a job."""
    try:
        engine = DelegationEngine()
        root_id = engine.get_tree_root(job_id)
        tree = engine.get_full_tree(root_id)
        return JSONResponse({"tree": tree, "root_job_id": root_id})
    except KeyError:
        return JSONResponse({"error": f"Job '{job_id}' not found"}, status_code=404)
    except Exception as exc:
        logger.exception("Failed to get delegation tree for job %s", job_id)
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/memory/{job_id}")
async def shared_memory(job_id: str):
    """Return shared memory entries for a job's delegation tree."""
    try:
        engine = DelegationEngine()
        root_id = engine.get_tree_root(job_id)
        entries = SharedMemoryStore().get_all(root_id)
        return JSONResponse({
            "entries": [e.to_dict() for e in entries],
            "tree_root_id": root_id,
        })
    except KeyError:
        return JSONResponse({"error": f"Job '{job_id}' not found"}, status_code=404)
    except Exception as exc:
        logger.exception("Failed to get shared memory for job %s", job_id)
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/delegate")
async def manual_delegate(request: Request):
    """Manually trigger a delegation (spawn a child job)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    parent_job_id = body.get("parent_job_id")
    title = body.get("title")
    description = body.get("description", "")
    delegation_type = body.get("delegation_type", "sub_task")
    priority = body.get("priority", 5)

    if not parent_job_id or not title:
        return JSONResponse(
            {"error": "'parent_job_id' and 'title' are required"},
            status_code=400,
        )

    try:
        engine = DelegationEngine()
        delegation = engine.spawn_child_job(
            parent_job_id=parent_job_id,
            title=title,
            description=description,
            delegation_type=delegation_type,
            priority=priority,
        )
        return JSONResponse(delegation.to_dict(), status_code=201)
    except KeyError:
        return JSONResponse(
            {"error": f"Parent job '{parent_job_id}' not found"},
            status_code=404,
        )
    except Exception as exc:
        logger.exception("Failed to delegate from job %s", parent_job_id)
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/messages/{job_id}")
async def message_log(job_id: str):
    """Return the message log for a job's delegation tree."""
    try:
        engine = DelegationEngine()
        tree_root_id = engine.get_tree_root(job_id)
        messages = AgentMessageBus().get_messages_for_tree(tree_root_id)
        return JSONResponse({"messages": [m.to_dict() for m in messages]})
    except KeyError:
        return JSONResponse({"error": f"Job '{job_id}' not found"}, status_code=404)
    except Exception as exc:
        logger.exception("Failed to get messages for job %s", job_id)
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/locks")
async def list_locks():
    """Return all active resource locks."""
    try:
        locks = ResourceLockManager().list_active()
        return JSONResponse({"locks": [l.to_dict() for l in locks]})
    except Exception as exc:
        logger.exception("Failed to list resource locks")
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/locks/{lock_id}/release")
async def force_release_lock(lock_id: str):
    """Force-release a resource lock by ID."""
    try:
        released = ResourceLockManager().force_release(lock_id)
        return JSONResponse({"released": released})
    except KeyError:
        return JSONResponse({"error": f"Lock '{lock_id}' not found"}, status_code=404)
    except Exception as exc:
        logger.exception("Failed to release lock %s", lock_id)
        return JSONResponse({"error": str(exc)}, status_code=400)
