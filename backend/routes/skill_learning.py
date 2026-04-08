"""
backend/routes/skill_learning.py --- AI Internet Learning API
===============================================================
Exposes the SkillLearner as HTTP endpoints for triggering learning cycles,
browsing the learning journal, and getting topic suggestions.
"""

import logging

from fastapi import APIRouter, Query

logger = logging.getLogger("localmind.routes.skill_learning")

router = APIRouter(prefix="/api/learning", tags=["learning"])


def _get_learner():
    """Lazy-init to avoid import-time side effects."""
    from backend.autonomy.skill_learner import get_skill_learner
    return get_skill_learner()


@router.get("/journal")
async def get_journal(limit: int = Query(50, ge=1, le=500, description="Max entries")):
    """Return the learning journal (most recent entries)."""
    learner = _get_learner()
    entries = learner.get_journal(limit=limit)
    return {"entries": entries, "count": len(entries)}


@router.post("/learn")
async def learn(topic: str = ""):
    """Trigger a learning cycle.

    If *topic* is provided the learner researches that specific topic.
    Otherwise it auto-detects the most valuable gap and learns about it.
    """
    learner = _get_learner()
    try:
        result = await learner.learn(topic=topic or None)
        return {
            "ok": True,
            "topic": result.get("topic", ""),
            "findings_count": len(result.get("findings", [])),
            "summary": result.get("summary", ""),
            "applied": result.get("applied", False),
            "tool_name": result.get("tool_name"),
        }
    except Exception as exc:
        logger.error("Learning cycle failed: %s", exc)
        return {"ok": False, "error": str(exc)}


@router.get("/suggestions")
async def get_suggestions():
    """Return suggested learning topics based on gap analysis."""
    learner = _get_learner()
    try:
        suggestions = await learner.suggest_topics()
        return {"suggestions": suggestions}
    except Exception as exc:
        logger.error("Suggestion generation failed: %s", exc)
        return {"suggestions": [], "error": str(exc)}


@router.get("/stats")
async def get_stats():
    """Return learning statistics."""
    learner = _get_learner()
    return learner.get_stats()
