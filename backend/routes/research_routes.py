"""
backend/routes/research_routes.py — ArXiv Research Search API
================================================================
Exposes the existing AcademicResearcher.search_arxiv() as a user-facing
API endpoint so the frontend dashboard can search for academic papers.
"""

import logging
from fastapi import APIRouter, Query

logger = logging.getLogger("localmind.routes.research")

router = APIRouter(prefix="/api", tags=["research"])

# Lazy-initialized singleton (created on first request)
_researcher = None


def _get_researcher():
    """Lazy-init the AcademicResearcher to avoid import-time side effects."""
    global _researcher
    if _researcher is None:
        from backend.research_engine import AcademicResearcher
        _researcher = AcademicResearcher()
    return _researcher


@router.get("/research/arxiv")
async def search_arxiv(
    q: str = Query(..., min_length=2, description="Search query"),
    max: int = Query(5, ge=1, le=20, description="Max results"),
):
    """Search arXiv for academic papers matching the query.

    Returns a list of papers with title, authors, abstract, URL, and
    publication date. Uses the arXiv Atom API under the hood.
    """
    researcher = _get_researcher()
    try:
        papers = await researcher.search_arxiv(q, max_results=max)
        return {"papers": papers, "count": len(papers), "query": q}
    except Exception as e:
        logger.warning(f"arXiv search failed for '{q}': {e}")
        return {"papers": [], "count": 0, "query": q, "error": str(e)}
