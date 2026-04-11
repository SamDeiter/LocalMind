"""
routes/jobs.py — Jobs REST API
================================
FastAPI routes for creating, managing, and monitoring LocalMind enterprise
task-worker jobs.

Endpoints
---------
POST   /api/jobs                             — Create a new job (+ optional file uploads)
GET    /api/jobs                             — List jobs (paginated, filterable by status)
GET    /api/jobs/activity                    — SSE stream for real-time job progress
GET    /api/jobs/templates                   — List pipeline templates
POST   /api/jobs/templates                   — Save a completed job as a reusable template
GET    /api/jobs/templates/{template_id}     — Fetch a single template
PUT    /api/jobs/templates/{template_id}     — Update a template (name, nodes, description)
DELETE /api/jobs/templates/{template_id}     — Delete a template
GET    /api/jobs/{job_id}                    — Fetch a single job with nodes, files, audit
POST   /api/jobs/{job_id}/cancel             — Cancel a running job
DELETE /api/jobs/{job_id}                    — Permanently delete a job
GET    /api/jobs/{job_id}/tree               — Get delegation tree for a job
GET    /api/jobs/{job_id}/artifacts              — List artifacts for a job
GET    /api/jobs/{job_id}/artifacts/{id}/download — Download an artifact
GET    /api/jobs/{job_id}/artifacts/{id}/lineage  — Trace artifact provenance
GET    /api/jobs/{job_id}/files/{file_id}    — Download an output file

NOTE: /activity, /templates, and /templates/{template_id} are declared BEFORE
/{job_id} so FastAPI's router does not match those literal path segments as a job_id.

Auth: workspace_id defaults to "default" — will be replaced with real auth later.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Path as FPath, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from backend.config import JOBS_DIR, MAX_UPLOAD_SIZE_MB, WORKSPACE_ROOT
from backend.jobs.models import Job, JobStatus
from backend.jobs.queue import JobQueue
from backend.security.paths import SecurityError, safe_resolve, sanitize_filename, validate_upload
from backend.swarm.delegation import DelegationEngine

logger = logging.getLogger("localmind.routes.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# ---------------------------------------------------------------------------
# Module-level config (injected by server.py via configure())
# ---------------------------------------------------------------------------

_config: dict = {}


def configure(**kwargs) -> None:
    """Called by server.py after router creation to inject dependencies."""
    _config.update(kwargs)


# ---------------------------------------------------------------------------
# SSE activity stream — global subscriber list
# ---------------------------------------------------------------------------

_subscribers: list[asyncio.Queue] = []
_shutdown_event: asyncio.Event | None = None


def _get_shutdown_event() -> asyncio.Event:
    """Lazily create the shutdown event on the current event loop."""
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event


def signal_sse_shutdown() -> None:
    """Signal all SSE generators to stop. Called during server lifespan shutdown."""
    if _shutdown_event is not None:
        _shutdown_event.set()


async def emit_activity(event_type: str, data: dict) -> None:
    """Broadcast a job progress event to all connected SSE clients.

    Called by the worker / executor to push real-time updates.  Safe to call
    from any async context; drops the event for slow subscribers rather than
    blocking.
    """
    event = {
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Subscriber is too slow — drop this event rather than blocking.
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Cache the resolved workspace UUID so we only query the DB once.
_default_workspace_id: str | None = None


def _get_default_workspace_id() -> str:
    """Return the UUID of the 'default' workspace.

    The jobs table has a FK constraint on workspaces(id) which is a UUID,
    not the slug.  We resolve the slug -> id once and cache it.
    """
    global _default_workspace_id
    if _default_workspace_id is not None:
        return _default_workspace_id

    from backend.core.identity import IdentityService
    svc = IdentityService()
    ws = svc.get_default_workspace()
    _default_workspace_id = ws.id
    return _default_workspace_id


def _queue() -> JobQueue:
    """Return a fresh JobQueue instance (stateless CRUD wrapper)."""
    return JobQueue()


async def _save_upload(job_id: str, upload: UploadFile) -> dict:
    """Write an uploaded file to JOBS_DIR/{job_id}/input/ and return metadata.

    Raises HTTPException (400/413) on security or size violations.
    """
    raw_name = upload.filename or "upload"
    try:
        safe_name = sanitize_filename(raw_name)
    except SecurityError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {exc}") from exc

    input_dir = JOBS_DIR / job_id / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    dest = input_dir / safe_name

    # Stream to disk
    content = await upload.read()

    # Size check before touching disk
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File '{safe_name}' exceeds the {MAX_UPLOAD_SIZE_MB} MB upload limit",
        )

    dest.write_bytes(content)

    # Security validation (extension, MIME, double-extension, size on disk)
    try:
        validate_upload(dest, max_size_mb=MAX_UPLOAD_SIZE_MB)
    except SecurityError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Upload rejected: {exc}") from exc

    mime = upload.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    return {
        "filename": safe_name,
        "file_path": str(dest),
        "file_type": "input",
        "mime_type": mime,
        "size_bytes": len(content),
    }


# ---------------------------------------------------------------------------
# Routes — fixed paths BEFORE parameterised /{job_id}
# ---------------------------------------------------------------------------

@router.get("/activity")
async def activity_stream(request: Request) -> StreamingResponse:
    """SSE stream — emits job lifecycle and node progress events.

    Events emitted by the worker via ``emit_activity()``:
      - job_created
      - job_status_changed
      - node_progress
      - job_completed
      - job_failed

    A keepalive comment is sent every 30 s to prevent proxy timeouts.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.append(q)
    shutdown = _get_shutdown_event()
    logger.debug("SSE client connected — %d subscriber(s) active", len(_subscribers))

    async def event_generator():
        try:
            while not shutdown.is_set():
                if await request.is_disconnected():
                    logger.debug("SSE client disconnected")
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield (
                        f"event: {event['type']}\n"
                        f"data: {json.dumps(event['data'])}\n\n"
                    )
                except asyncio.TimeoutError:
                    # Keepalive — SSE comment line
                    yield ": keepalive\n\n"
        finally:
            try:
                _subscribers.remove(q)
            except ValueError:
                pass
            logger.debug("SSE subscriber removed — %d subscriber(s) remaining", len(_subscribers))

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/templates")
async def list_templates() -> JSONResponse:
    """Return all pipeline templates for the default workspace."""
    queue = _queue()
    templates = queue.list_templates(workspace_id=_get_default_workspace_id())
    return JSONResponse({"templates": [t.to_dict() for t in templates]})


@router.post("/templates")
async def create_template(request: Request) -> JSONResponse:
    """Save a successfully completed job as a reusable pipeline template.

    Body: { "job_id": "...", "name": "My Template" }
    """
    try:
        body: dict = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    job_id: str | None = body.get("job_id")
    name: str | None = body.get("name")

    if not job_id:
        raise HTTPException(status_code=400, detail="'job_id' is required")
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="'name' is required and must not be blank")

    queue = _queue()
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job.status != JobStatus.DONE.value:
        raise HTTPException(
            status_code=422,
            detail=f"Only completed jobs can be saved as templates (status is '{job.status}')",
        )

    try:
        template = queue.save_as_template(
            job_id=job_id,
            name=name.strip(),
            workspace_id=_get_default_workspace_id(),
            created_by=None,
        )
    except Exception as exc:
        logger.exception("Failed to create template from job %s", job_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("Template '%s' (%s) created from job %s", template.name, template.id, job_id)
    return JSONResponse({"template": template.to_dict()}, status_code=201)


@router.get("/templates/{template_id}")
async def get_template(template_id: str) -> JSONResponse:
    """Return a single template by ID."""
    queue = _queue()
    tmpl = queue.get_template(template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return JSONResponse({"template": tmpl.to_dict()})


@router.put("/templates/{template_id}")
async def update_template(template_id: str, request: Request) -> JSONResponse:
    """Update a template's name, description, and/or node configuration.

    Body: { "name": "...", "description": "...", "nodes": [...] }
    All fields are optional; only provided fields are updated.
    """
    try:
        body: dict = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    queue = _queue()
    tmpl = queue.update_template(
        template_id=template_id,
        name=body.get("name"),
        description=body.get("description"),
        nodes=body.get("nodes"),
    )
    if tmpl is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    logger.info("Template '%s' (%s) updated", tmpl.name, tmpl.id)
    return JSONResponse({"template": tmpl.to_dict()})


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str) -> JSONResponse:
    """Delete a template by ID."""
    queue = _queue()
    deleted = queue.delete_template(template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    logger.info("Template %s deleted", template_id)
    return JSONResponse({"deleted": True, "template_id": template_id})


@router.get("/stats")
async def job_stats() -> JSONResponse:
    """Aggregate job statistics: totals, cost, and cost-by-period.

    Returns total_jobs, completed_jobs, failed_jobs, total_cost_cents,
    avg_cost_cents, and a daily cost breakdown for the last 30 days.
    """
    import sqlite3 as _sqlite3
    from backend.config import DB_PATH

    conn = _sqlite3.connect(str(DB_PATH))
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        # Aggregate job-level stats
        rows = conn.execute(
            "SELECT status, cost_cents, cloud_cost_cents, tokens_in_total, tokens_out_total FROM jobs"
        ).fetchall()
        total_jobs = len(rows)
        completed_jobs = 0
        failed_jobs = 0
        total_cost_cents = 0.0
        total_cloud_cost_cents = 0.0
        total_tokens_in = 0
        total_tokens_out = 0
        for r in rows:
            s = r["status"]
            if s == "done":
                completed_jobs += 1
            elif s == "failed":
                failed_jobs += 1
            total_cost_cents += r["cost_cents"] or 0.0
            total_cloud_cost_cents += r["cloud_cost_cents"] or 0.0
            total_tokens_in += r["tokens_in_total"] or 0
            total_tokens_out += r["tokens_out_total"] or 0

        avg_cost_cents = round(total_cost_cents / total_jobs, 4) if total_jobs > 0 else 0.0

        # Most expensive recent job
        most_expensive = conn.execute(
            """
            SELECT id, title, cost_cents, status, created_at
            FROM jobs
            WHERE cost_cents > 0
            ORDER BY cost_cents DESC
            LIMIT 1
            """,
        ).fetchone()
        most_expensive_job = None
        if most_expensive:
            most_expensive_job = {
                "id": most_expensive["id"],
                "title": most_expensive["title"],
                "cost_cents": most_expensive["cost_cents"],
                "status": most_expensive["status"],
                "created_at": most_expensive["created_at"],
            }

        # Cost by day (last 30 days)
        daily_rows = conn.execute(
            """
            SELECT DATE(created_at) AS day,
                   COUNT(*) AS job_count,
                   SUM(COALESCE(cost_cents, 0)) AS cost_cents
            FROM jobs
            WHERE created_at >= DATE('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            """,
        ).fetchall()
        cost_by_day = [
            {
                "date": r["day"],
                "job_count": r["job_count"],
                "cost_cents": round(r["cost_cents"] or 0, 4),
            }
            for r in daily_rows
        ]

        # Cost this week and this month (actual + cloud equivalent)
        week_row = conn.execute(
            """
            SELECT SUM(COALESCE(cost_cents, 0)) AS cost,
                   SUM(COALESCE(cloud_cost_cents, 0)) AS cloud_cost,
                   SUM(COALESCE(tokens_in_total, 0)) AS tokens_in,
                   SUM(COALESCE(tokens_out_total, 0)) AS tokens_out
            FROM jobs
            WHERE created_at >= DATE('now', '-7 days')
            """,
        ).fetchone()
        cost_this_week = round((week_row["cost"] or 0), 4) if week_row else 0.0
        cloud_cost_this_week = round((week_row["cloud_cost"] or 0), 4) if week_row else 0.0

        month_row = conn.execute(
            """
            SELECT SUM(COALESCE(cost_cents, 0)) AS cost,
                   SUM(COALESCE(cloud_cost_cents, 0)) AS cloud_cost,
                   SUM(COALESCE(tokens_in_total, 0)) AS tokens_in,
                   SUM(COALESCE(tokens_out_total, 0)) AS tokens_out
            FROM jobs
            WHERE created_at >= DATE('now', '-30 days')
            """,
        ).fetchone()
        cost_this_month = round((month_row["cost"] or 0), 4) if month_row else 0.0
        cloud_cost_this_month = round((month_row["cloud_cost"] or 0), 4) if month_row else 0.0

    finally:
        conn.close()

    return JSONResponse({
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "total_cost_cents": round(total_cost_cents, 4),
        "avg_cost_cents": avg_cost_cents,
        "cost_this_week": cost_this_week,
        "cost_this_month": cost_this_month,
        "cloud_cost_this_week": cloud_cost_this_week,
        "cloud_cost_this_month": cloud_cost_this_month,
        "total_cloud_cost_cents": round(total_cloud_cost_cents, 4),
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "most_expensive_job": most_expensive_job,
        "cost_by_day": cost_by_day,
    })


@router.post("/worker/reset")
async def reset_worker(request: Request) -> JSONResponse:
    """Reset the job worker circuit breaker so pending jobs can run again."""
    worker = getattr(request.app.state, "job_worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Job worker not initialized")
    worker._consecutive_failures = 0
    worker._circuit_open_until = 0.0
    logger.info("Circuit breaker reset by user.")
    return JSONResponse({"reset": True})


# ---------------------------------------------------------------------------
# Approval API — PolicyEngine DB-backed approvals
# ---------------------------------------------------------------------------


@router.get("/approvals/pending")
async def list_pending_approvals() -> JSONResponse:
    """List all pending approval requests from the PolicyEngine DB.

    Returns approvals with status='pending' that haven't expired.
    Used by the frontend approval queue to show items needing human decision.
    """
    from backend.core.policy import PolicyEngine

    engine = PolicyEngine()
    # Expire stale approvals first
    engine.expire_stale_approvals()

    import sqlite3 as _sqlite3
    from backend.config import DB_PATH

    conn = _sqlite3.connect(str(DB_PATH))
    conn.row_factory = _sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.policy_id, a.job_id, a.node_id, a.tool_call_json,
                   a.status, a.requested_at, a.expires_at,
                   p.name AS policy_name, p.description AS policy_description
            FROM approvals a
            LEFT JOIN approval_policies p ON a.policy_id = p.id
            WHERE a.status = 'pending'
            ORDER BY a.requested_at ASC
            """,
        ).fetchall()
        pending = []
        for r in rows:
            pending.append({
                "id": r["id"],
                "policy_id": r["policy_id"],
                "policy_name": r["policy_name"],
                "policy_description": r["policy_description"],
                "job_id": r["job_id"],
                "node_id": r["node_id"],
                "tool_call": json.loads(r["tool_call_json"]),
                "status": r["status"],
                "requested_at": r["requested_at"],
                "expires_at": r["expires_at"],
            })
    finally:
        conn.close()

    return JSONResponse({"pending": pending, "count": len(pending)})


@router.get("/approvals/all")
async def list_all_approvals(
    limit: int = Query(default=50, ge=1, le=500),
) -> JSONResponse:
    """List all approval requests (pending, approved, denied, expired).

    Used by the audit trail panel in the frontend.
    """
    import sqlite3 as _sqlite3
    from backend.config import DB_PATH

    conn = _sqlite3.connect(str(DB_PATH))
    conn.row_factory = _sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.policy_id, a.job_id, a.node_id, a.tool_call_json,
                   a.status, a.requested_at, a.expires_at,
                   a.decided_by, a.decided_at, a.reason,
                   p.name AS policy_name
            FROM approvals a
            LEFT JOIN approval_policies p ON a.policy_id = p.id
            ORDER BY a.requested_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        approvals = []
        for r in rows:
            approvals.append({
                "id": r["id"],
                "policy_id": r["policy_id"],
                "policy_name": r["policy_name"],
                "job_id": r["job_id"],
                "node_id": r["node_id"],
                "tool_call": json.loads(r["tool_call_json"]),
                "status": r["status"],
                "requested_at": r["requested_at"],
                "expires_at": r["expires_at"],
                "decided_by": r["decided_by"],
                "decided_at": r["decided_at"],
                "reason": r["reason"],
            })
    finally:
        conn.close()

    return JSONResponse({"approvals": approvals, "count": len(approvals)})


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(
    approval_id: str,
    request: Request,
) -> JSONResponse:
    """Approve or deny a pending approval request.

    Body: { "approved": true/false, "reason": "optional reason", "decided_by": "user" }

    Emits an SSE event so the executor/worker can be notified.
    """
    from backend.core.policy import PolicyEngine

    try:
        body: dict = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    approved = bool(body.get("approved", False))
    reason = str(body.get("reason", ""))
    decided_by = str(body.get("decided_by", "unknown"))

    engine = PolicyEngine()
    try:
        engine.decide_approval(
            approval_id=approval_id,
            decided_by=decided_by,
            approved=approved,
            reason=reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    status_str = "approved" if approved else "denied"
    logger.info("Approval '%s' %s by '%s'.", approval_id, status_str, decided_by)

    # Log to the job's audit trail for accountability
    import sqlite3 as _sqlite3
    from backend.config import DB_PATH as _DB_PATH

    try:
        _conn = _sqlite3.connect(str(_DB_PATH))
        _conn.row_factory = _sqlite3.Row
        _row = _conn.execute(
            "SELECT job_id FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        _conn.close()
        if _row:
            queue = _queue()
            queue.add_audit(
                _row["job_id"],
                action=f"approval_{status_str}",
                detail=f"approval_id={approval_id} decided_by={decided_by} reason={reason}",
                actor=decided_by,
            )
    except Exception as _audit_exc:
        logger.warning("Failed to log approval audit: %s", _audit_exc)

    await emit_activity("approval_decided", {
        "approval_id": approval_id,
        "status": status_str,
        "decided_by": decided_by,
        "reason": reason,
    })

    return JSONResponse({
        "approval_id": approval_id,
        "status": status_str,
        "decided_by": decided_by,
    })


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str) -> JSONResponse:
    """Get the current status of a single approval request."""
    from backend.core.policy import PolicyEngine

    engine = PolicyEngine()
    try:
        status = engine.check_approval(approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return JSONResponse({"approval_id": approval_id, "status": status})


# ---------------------------------------------------------------------------
# Routes — parameterised by job_id
# ---------------------------------------------------------------------------

@router.post("")
async def create_job(
    request: Request,
    files: list[UploadFile] = File(default=[]),
) -> JSONResponse:
    """Create a new job, optionally with file attachments.

    JSON fields (in the request body *or* as a multipart ``metadata`` part):
      - title        (required)
      - description  (optional)
      - mode         "quick" | "pipeline"  (default: "quick")
      - template_id  (optional)
      - priority     integer               (default: 0)

    Uploaded files are saved to JOBS_DIR/{job_id}/input/ and registered in the
    database via JobQueue.add_file().
    """
    # FastAPI doesn't auto-parse JSON when files are present; handle both.
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        raw_meta = form.get("metadata", "{}")
        try:
            body: dict = json.loads(raw_meta) if isinstance(raw_meta, str) else {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in 'metadata' form field")
    else:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    title: str | None = body.get("title")
    if not title or not str(title).strip():
        raise HTTPException(status_code=400, detail="'title' is required and must not be blank")

    description: str | None = body.get("description")
    mode: str = str(body.get("mode", "quick")).lower()
    if mode not in ("quick", "pipeline"):
        raise HTTPException(status_code=400, detail="'mode' must be 'quick' or 'pipeline'")
    template_id: str | None = body.get("template_id")
    try:
        priority = int(body.get("priority", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="'priority' must be an integer")

    queue = _queue()

    # Create the job row first to get the job_id
    try:
        job = queue.create_job(
            workspace_id=_get_default_workspace_id(),
            title=str(title).strip(),
            description=description,
            source="api",
            requester=None,
            mode=mode,
            template_id=template_id,
            priority=priority,
        )
    except Exception as exc:
        logger.exception("Failed to create job")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Create job directory structure
    job_input_dir = JOBS_DIR / job.id / "input"
    job_output_dir = JOBS_DIR / job.id / "output"
    job_input_dir.mkdir(parents=True, exist_ok=True)
    job_output_dir.mkdir(parents=True, exist_ok=True)

    # Handle uploaded files
    saved_files: list[dict] = []
    for upload in files:
        if not upload.filename:
            continue
        try:
            meta = await _save_upload(job.id, upload)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Skipping upload '%s': %s", upload.filename, exc)
            continue

        try:
            queue.add_file(
                job_id=job.id,
                filename=meta["filename"],
                file_path=meta["file_path"],
                file_type=meta["file_type"],
                mime_type=meta["mime_type"],
                size_bytes=meta["size_bytes"],
            )
            saved_files.append(meta)
        except Exception as exc:
            logger.exception("DB add_file failed for '%s'", meta["filename"])
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Audit
    queue.add_audit(
        job.id,
        action="job_created",
        detail=f"mode={mode}, files={len(saved_files)}",
        actor=None,
    )

    logger.info("Job %s created (mode=%s, uploads=%d)", job.id, mode, len(saved_files))

    # Notify SSE subscribers
    await emit_activity("job_created", {**job.to_api_dict(), "file_count": len(saved_files)})

    result = job.to_api_dict()
    result["files"] = saved_files
    return JSONResponse(result, status_code=201)


@router.get("")
async def list_jobs(
    status: Optional[str] = Query(default=None, description="Filter by job status"),
    limit: int = Query(default=50, ge=1, le=500, description="Max results to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> JSONResponse:
    """List jobs for the default workspace.

    Query params:
      - status  — one of the JobStatus values (pending, executing, done, …)
      - limit   — default 50, max 500
      - offset  — default 0
    """
    # Validate status value(s) — supports comma-separated list
    statuses: list[str] | None = None
    if status is not None:
        valid_statuses = {s.value for s in JobStatus}
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        invalid = [s for s in statuses if s not in valid_statuses]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status(es): {invalid}. Valid values: {sorted(valid_statuses)}",
            )

    queue = _queue()
    jobs = queue.list_jobs(
        workspace_id=_get_default_workspace_id(),
        statuses=statuses,
        limit=limit,
        offset=offset,
    )
    return JSONResponse({
        "jobs": [j.to_api_dict() for j in jobs],
        "limit": limit,
        "offset": offset,
        "count": len(jobs),
    })


@router.get("/{job_id}")
async def get_job(
    job_id: str = FPath(..., description="Job UUID"),
) -> JSONResponse:
    """Fetch a single job with its nodes, files, and recent audit log.

    Returns 404 if the job does not exist.
    """
    queue = _queue()
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    nodes = queue.get_nodes(job_id)
    files = queue.get_files(job_id)
    audit = queue.get_audit_log(job_id, limit=50)

    result = job.to_dict()  # Full detail including error / cost for operator view
    result["nodes"] = [n.to_dict() for n in nodes]
    result["files"] = [f.to_dict() for f in files]
    result["audit"] = [a.to_dict() for a in audit]

    # Expose delegation tree fields if present on the job
    if getattr(job, "parent_job_id", None):
        result["parent_job_id"] = job.parent_job_id
    if getattr(job, "tree_root_id", None):
        result["tree_root_id"] = job.tree_root_id

    # Include artifacts with current version metadata
    try:
        from backend.core.artifacts import ArtifactManager
        mgr = ArtifactManager()
        artifacts = mgr.get_artifacts_by_job(job_id)
        artifact_list = []
        for art in artifacts:
            entry = {
                "id": art["id"],
                "name": art["name"],
                "artifact_type": art["artifact_type"],
                "created_at": art["created_at"],
                "current_version": None,
            }
            ver = mgr.get_current_version(art["id"])
            if ver:
                entry["current_version"] = {
                    "id": ver["id"],
                    "version_number": ver["version_number"],
                    "file_size_bytes": ver["file_size_bytes"],
                    "sha256": ver["sha256"],
                    "mime_type": ver["mime_type"],
                    "node_attempt_id": ver["node_attempt_id"],
                    "parent_version_id": ver["parent_version_id"],
                    "created_at": ver["created_at"],
                }
            artifact_list.append(entry)
        result["artifacts"] = artifact_list
    except Exception as exc:
        logger.warning("Failed to load artifacts for job %s: %s", job_id, exc)
        result["artifacts"] = []

    return JSONResponse(result)


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str = FPath(..., description="Job UUID"),
) -> JSONResponse:
    """Request cancellation of a running job.

    Transitions the job to 'cancelling'; the executor picks this up and
    performs a graceful stop.  Returns the updated job dict.
    """
    queue = _queue()
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    terminal_statuses = {JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}
    if job.status in terminal_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"Job '{job_id}' is already in a terminal state ('{job.status}')",
        )

    # If the job isn't actively being processed by a worker (executing),
    # skip the 'cancelling' intermediate state and go straight to 'cancelled'
    # — no worker will pick it up to complete the transition.
    active_statuses = {JobStatus.EXECUTING.value, JobStatus.PLANNING.value}
    immediate_cancel = job.status not in active_statuses

    try:
        if immediate_cancel:
            queue.update_job_status(job_id, JobStatus.CANCELLED.value)
        else:
            queue.cancel_job(job_id)
    except Exception as exc:
        logger.exception("Failed to cancel job %s", job_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    queue.add_audit(job_id, action="job_cancel_requested", actor=None)

    # Fetch the refreshed job to return current state
    updated = queue.get_job(job_id)
    final_status = updated.status if updated else JobStatus.CANCELLED.value
    logger.info("Cancellation requested for job %s (immediate=%s)", job_id, immediate_cancel)

    await emit_activity("job_status_changed", {
        "job_id": job_id,
        "status": final_status,
    })

    return JSONResponse((updated or job).to_api_dict())


@router.delete("/{job_id}")
async def delete_job(
    job_id: str = FPath(..., description="Job UUID"),
) -> JSONResponse:
    """Permanently delete a job and all its related data.

    If the job is still active it will be force-cancelled first, then deleted.
    Returns 404 if not found.
    """
    queue = _queue()
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    terminal_statuses = {JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value, JobStatus.CANCELLING.value}
    if job.status not in terminal_statuses:
        try:
            queue.cancel_job(job_id)
        except Exception:
            pass
    # Ensure status is fully terminal before delete
    if job.status not in {JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}:
        queue.update_job_status(job_id, JobStatus.CANCELLED.value)
        queue.add_audit(job_id, action="force_cancelled_for_delete", actor="user")

    try:
        queue.delete_job(job_id)
    except Exception as exc:
        logger.exception("Failed to delete job %s", job_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("Deleted job %s", job_id)
    await emit_activity("job_deleted", {"job_id": job_id})
    return JSONResponse({"deleted": True, "job_id": job_id})


@router.get("/{job_id}/tree")
async def get_job_tree(
    job_id: str = FPath(..., description="Job UUID"),
) -> JSONResponse:
    """Return the delegation tree for a job.

    Uses DelegationEngine to build the full tree starting from this job.
    Returns 404 if the job is not found.
    """
    queue = _queue()
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    try:
        engine = DelegationEngine()
        tree = engine.get_full_tree(job_id)
        return JSONResponse({"tree": tree})
    except Exception as exc:
        logger.exception("Failed to get delegation tree for job %s", job_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{job_id}/artifacts")
async def list_artifacts(
    job_id: str = FPath(..., description="Job UUID"),
) -> JSONResponse:
    """List all artifacts and their current versions for a job.

    Returns artifact metadata including name, type, size, producing node,
    SHA-256 hash, and lineage (parent_version_id).
    """
    queue = _queue()
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    artifacts = mgr.get_artifacts_by_job(job_id)

    result = []
    for art in artifacts:
        entry = {
            "id": art["id"],
            "name": art["name"],
            "artifact_type": art["artifact_type"],
            "created_at": art["created_at"],
            "current_version": None,
        }
        ver = mgr.get_current_version(art["id"])
        if ver:
            entry["current_version"] = {
                "id": ver["id"],
                "version_number": ver["version_number"],
                "file_path": ver["file_path"],
                "file_size_bytes": ver["file_size_bytes"],
                "sha256": ver["sha256"],
                "mime_type": ver["mime_type"],
                "created_by": ver["created_by"],
                "node_attempt_id": ver["node_attempt_id"],
                "parent_version_id": ver["parent_version_id"],
                "metadata_json": ver["metadata_json"],
                "created_at": ver["created_at"],
            }
        result.append(entry)

    return JSONResponse({"artifacts": result, "count": len(result)})


@router.get("/{job_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    job_id: str = FPath(..., description="Job UUID"),
    artifact_id: str = FPath(..., description="Artifact UUID"),
) -> FileResponse:
    """Download the current version of an artifact.

    Validates ownership and serves the file from disk.
    """
    queue = _queue()
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    artifact = mgr.get_artifact(artifact_id)
    if artifact is None or artifact["job_id"] != job_id:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found for job '{job_id}'",
        )

    version = mgr.get_current_version(artifact_id)
    if version is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_id}' has no versions",
        )

    file_path = Path(version["file_path"])

    # Path-jail check
    try:
        safe_resolve(JOBS_DIR, file_path)
    except SecurityError as exc:
        logger.error("Artifact path jail violation for %s: %s", artifact_id, exc)
        raise HTTPException(
            status_code=403,
            detail="File path is outside the permitted directory",
        ) from exc

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Artifact file exists in DB but not on disk",
        )

    media_type = version["mime_type"] or "application/octet-stream"
    filename = file_path.name
    logger.info(
        "Serving artifact %s (%s) for job %s",
        artifact_id[:12], filename, job_id[:12],
    )
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
    )


@router.get("/{job_id}/artifacts/{artifact_id}/lineage")
async def get_artifact_lineage(
    job_id: str = FPath(..., description="Job UUID"),
    artifact_id: str = FPath(..., description="Artifact UUID"),
) -> JSONResponse:
    """Trace the production lineage of an artifact back to its source.

    Follows parent_version_id links to build the full provenance chain.
    """
    queue = _queue()
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    from backend.core.artifacts import ArtifactManager

    mgr = ArtifactManager()
    artifact = mgr.get_artifact(artifact_id)
    if artifact is None or artifact["job_id"] != job_id:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found for job '{job_id}'",
        )

    version = mgr.get_current_version(artifact_id)
    if version is None:
        return JSONResponse({"lineage": [], "count": 0})

    chain = []
    current_vid = version["id"]
    seen = set()
    while current_vid and current_vid not in seen:
        seen.add(current_vid)
        ver = mgr.get_version(current_vid)
        if ver is None:
            break
        chain.append({
            "version_id": ver["id"],
            "version_number": ver["version_number"],
            "artifact_id": ver["artifact_id"],
            "node_attempt_id": ver["node_attempt_id"],
            "created_by": ver["created_by"],
            "sha256": ver["sha256"],
            "file_size_bytes": ver["file_size_bytes"],
            "mime_type": ver["mime_type"],
            "created_at": ver["created_at"],
        })
        current_vid = ver["parent_version_id"]

    return JSONResponse({"lineage": chain, "count": len(chain)})


@router.get("/{job_id}/files/{file_id}")
async def download_file(
    job_id: str = FPath(..., description="Job UUID"),
    file_id: str = FPath(..., description="File UUID"),
) -> FileResponse:
    """Download an output file belonging to a job.

    Validates that the requested file actually belongs to *job_id* before
    serving it.  Returns 404 if the job or file is not found, 403 if the
    file belongs to a different job.
    """
    queue = _queue()

    # Confirm job exists
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    # Look up file record
    all_files = queue.get_files(job_id)
    matched = next((f for f in all_files if f.id == file_id), None)
    if matched is None:
        raise HTTPException(
            status_code=404,
            detail=f"File '{file_id}' not found for job '{job_id}'",
        )

    # Ownership check (belt-and-suspenders against DB inconsistency)
    if matched.job_id != job_id:
        logger.warning(
            "File ownership mismatch: file %s belongs to job %s, requested under %s",
            file_id, matched.job_id, job_id,
        )
        raise HTTPException(status_code=403, detail="File does not belong to this job")

    file_path = Path(matched.file_path)

    # Path-jail check: file must live under JOBS_DIR
    try:
        safe_resolve(JOBS_DIR, file_path)
    except SecurityError as exc:
        logger.error("File path jail violation for file %s: %s", file_id, exc)
        raise HTTPException(status_code=403, detail="File path is outside the permitted directory") from exc

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File '{matched.filename}' exists in DB but not on disk",
        )

    media_type = matched.mime_type or mimetypes.guess_type(matched.filename)[0] or "application/octet-stream"
    logger.info("Serving file %s (%s) for job %s", file_id, matched.filename, job_id)
    return FileResponse(
        path=str(file_path),
        filename=matched.filename,
        media_type=media_type,
    )
