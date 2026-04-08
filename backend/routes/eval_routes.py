"""
Eval Routes — API endpoints for the evaluation harness.

Endpoints
---------
POST   /api/evals/run              — Trigger an eval run (optional case_ids filter)
GET    /api/evals/runs             — List recent eval runs (paginated)
GET    /api/evals/runs/{run_id}    — Get detailed results for a single run
GET    /api/evals/cases            — List all eval cases
GET    /api/evals/trend            — Pass rates over last N runs (for sparkline chart)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("localmind.routes.evals")

router = APIRouter(prefix="/api/evals", tags=["evals"])


# ---------------------------------------------------------------------------
# Routes — fixed paths BEFORE parameterised /{run_id}
# ---------------------------------------------------------------------------


@router.get("/cases")
async def list_cases() -> JSONResponse:
    """Return all seed eval cases plus any user-defined cases."""
    try:
        from backend.config import DB_PATH
        from backend.eval.cases import get_seed_cases, load_cases_to_db

        # Ensure seed cases are in the DB (idempotent)
        load_cases_to_db(str(DB_PATH))
        cases = get_seed_cases()
        return JSONResponse({"cases": cases, "count": len(cases)})
    except Exception as exc:
        logger.exception("Failed to list eval cases")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/run")
async def trigger_run(request: Request) -> JSONResponse:
    """Trigger an evaluation run.

    Body (optional):
        { "case_ids": ["id1", "id2"] }

    If case_ids is omitted or empty, all seed cases are run.
    Returns a summary with pass_count, fail_count, and individual results.
    """
    try:
        body: dict = await request.json()
    except Exception:
        body = {}

    case_ids: list[str] | None = body.get("case_ids") or None

    try:
        from backend.config import DB_PATH
        from backend.eval.cases import get_seed_cases
        from backend.eval.harness import EvalHarness

        harness = EvalHarness(db_path=str(DB_PATH))

        # Filter seed cases if case_ids were provided
        cases = get_seed_cases()
        if case_ids:
            cases = [c for c in cases if c["id"] in case_ids]

        summary = harness.run_all(cases=cases)
        return JSONResponse(summary, status_code=201)
    except Exception as exc:
        logger.exception("Eval run failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/trend")
async def eval_trend(
    n: int = Query(default=10, ge=1, le=100, description="Number of recent runs"),
) -> JSONResponse:
    """Return pass rates over the last N eval runs for sparkline charts.

    Returns a list of { run_id, ran_at, pass_rate, total_cases } dicts
    ordered chronologically (oldest first).
    """
    try:
        from backend.core.eval import EvalRunTracker

        tracker = EvalRunTracker()
        all_runs = tracker.list_runs()

        # Take the most recent N runs
        recent = all_runs[:n]

        trend = []
        for run in reversed(recent):  # oldest first for chart
            score = run.get("score") or 0.0
            trend.append({
                "run_id": run["id"],
                "ran_at": run.get("ran_at"),
                "pass_rate": score,
                "status": run.get("status", "unknown"),
            })

        return JSONResponse({"trend": trend, "count": len(trend)})
    except Exception as exc:
        logger.exception("Failed to compute eval trend")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Routes — listing runs (before parameterised path)
# ---------------------------------------------------------------------------


@router.get("/runs")
async def list_runs(
    limit: int = Query(default=50, ge=1, le=500, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> JSONResponse:
    """List recent eval runs with pagination."""
    try:
        from backend.core.eval import EvalRunTracker

        tracker = EvalRunTracker()
        all_runs = tracker.list_runs()

        # Manual pagination since the tracker returns all
        page = all_runs[offset : offset + limit]
        return JSONResponse({
            "runs": page,
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "total": len(all_runs),
        })
    except Exception as exc:
        logger.exception("Failed to list eval runs")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Routes — parameterised by run_id
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> JSONResponse:
    """Get detailed results for a single eval run."""
    try:
        from backend.core.eval import EvalRunTracker

        tracker = EvalRunTracker()
        run = tracker.get_run(run_id)

        if run is None:
            raise HTTPException(
                status_code=404, detail=f"Eval run '{run_id}' not found"
            )

        return JSONResponse({"run": run})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get eval run %s", run_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
