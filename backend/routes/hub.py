"""
routes/hub.py — Cross-Project Hub API
======================================
Register, scan, and mine patterns across multiple local projects.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger("localmind.routes.hub")

router = APIRouter(prefix="/api/hub", tags=["hub"])


# ── Pydantic Models ────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    path: str
    description: str = ""


# ── Project Endpoints ──────────────────────────────────────────────────


@router.get("/projects")
async def list_projects(
    active_only: bool = Query(True, description="Only list active projects"),
):
    """List all registered projects."""
    try:
        from backend.core.project_registry import get_registry
        registry = get_registry()
        projects = registry.list_projects(active_only=active_only)
        return {"projects": projects, "count": len(projects)}
    except Exception as e:
        logger.exception("list_projects failed")
        return {"ok": False, "error": str(e)}


@router.post("/projects")
async def register_project(req: ProjectCreate):
    """Register a new project by name and local path."""
    if not os.path.isdir(req.path):
        return {"ok": False, "error": f"Path does not exist or is not a directory: {req.path}"}
    try:
        from backend.core.project_registry import get_registry
        registry = get_registry()
        project = registry.register(req.name, req.path, description=req.description)
        return {"ok": True, "project": project}
    except Exception as e:
        logger.exception("register_project failed")
        return {"ok": False, "error": str(e)}


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    """Get single project detail."""
    try:
        from backend.core.project_registry import get_registry
        registry = get_registry()
        project = registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
        return project
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_project(%s) failed", project_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}")
async def unregister_project(project_id: str):
    """Unregister (remove) a project from the hub."""
    try:
        from backend.core.project_registry import get_registry
        registry = get_registry()
        removed = registry.unregister(project_id)
        if not removed:
            return {"ok": False, "error": "Not found"}
        return {"ok": True}
    except Exception as e:
        logger.exception("unregister_project(%s) failed", project_id)
        return {"ok": False, "error": str(e)}


@router.post("/projects/{project_id}/scan")
async def scan_project(project_id: str):
    """Trigger a scan of a registered project."""
    try:
        from backend.core.project_registry import get_registry
        registry = get_registry()
        project = registry.scan_project(project_id)
        return {"ok": True, "project": project}
    except Exception as e:
        logger.exception("scan_project(%s) failed", project_id)
        return {"ok": False, "error": str(e)}


# ── Pattern / Mining Endpoints ─────────────────────────────────────────


@router.get("/patterns")
async def list_patterns(
    pattern_type: Optional[str] = Query(None, description="Filter by pattern type"),
    project_id: Optional[str] = Query(None, description="Filter by project ID"),
    limit: int = Query(50, ge=1, le=500, description="Max results"),
):
    """List discovered cross-project patterns."""
    try:
        from backend.research.cross_project import get_miner
        miner = get_miner()
        patterns = miner.get_patterns(
            pattern_type=pattern_type,
            project_id=project_id,
            limit=limit,
        )
        return {"patterns": patterns, "count": len(patterns)}
    except Exception as e:
        logger.exception("list_patterns failed")
        return {"ok": False, "error": str(e)}


@router.post("/mine")
async def mine_patterns():
    """Trigger pattern mining across all registered projects."""
    try:
        from backend.research.cross_project import get_miner
        miner = get_miner()
        result = miner.mine_all()
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("mine_patterns failed")
        return {"ok": False, "error": str(e)}


@router.get("/insights")
async def get_insights():
    """Get aggregated cross-project insights."""
    try:
        from backend.research.cross_project import get_miner
        miner = get_miner()
        insights = miner.get_insights()
        return {"insights": insights}
    except Exception as e:
        logger.exception("get_insights failed")
        return {"ok": False, "error": str(e)}


@router.get("/search")
async def search_patterns(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
):
    """Search across discovered patterns."""
    try:
        from backend.research.cross_project import get_miner
        miner = get_miner()
        results = miner.search_patterns(q, limit=limit)
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.exception("search_patterns failed")
        return {"ok": False, "error": str(e)}
