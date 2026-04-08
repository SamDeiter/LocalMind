"""Self-Discovery API routes -- AI profile introspection and discovery."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("localmind.routes.self_discovery")

router = APIRouter(tags=["self-discovery"])


@router.get("/api/ai-profile")
async def get_ai_profile():
    """Return the AI's self-profile, or 404 if not yet discovered."""
    from backend.autonomy.self_discovery import get_self_discovery

    service = get_self_discovery()
    profile = service.get_profile()
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="AI profile not yet discovered. POST /api/ai-profile/discover to generate one.",
        )
    return profile


@router.post("/api/ai-profile/discover")
async def run_discovery(force: bool = False):
    """Trigger a self-discovery cycle.

    Query params:
        force: if true, re-run discovery even if a profile already exists.
    """
    from backend.autonomy.self_discovery import get_self_discovery

    service = get_self_discovery()
    try:
        profile = await service.discover(force=force)
        return {"ok": True, "profile": profile}
    except Exception as exc:
        logger.error("Self-discovery failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Discovery failed: {exc}")


@router.get("/api/ai-profile/capabilities")
async def get_capabilities():
    """Return just the capabilities section of the AI profile.

    If no profile exists yet, runs a lightweight capability introspection
    without triggering a full discovery cycle.
    """
    from backend.autonomy.self_discovery import get_self_discovery

    service = get_self_discovery()
    profile = service.get_profile()

    if profile and "capabilities" in profile:
        return {"capabilities": profile["capabilities"]}

    # No profile yet -- run just the capability scan
    try:
        capabilities = await service._introspect_capabilities()
        return {"capabilities": capabilities}
    except Exception as exc:
        logger.error("Capability introspection failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Introspection failed: {exc}")
