"""
routes/jobs.py — Jobs REST API
================================
FastAPI routes for creating, managing, and monitoring LocalMind enterprise
task-worker jobs.

Endpoints
---------
POST   /api/jobs                        — Create a new job (+ optional file uploads)
GET    /api/jobs                        — List jobs (paginated, filterable by status)
GET    /api/jobs/activity               — SSE stream for real-time job progress
GET    /api/jobs/templates              — List pipeline templates
POST   /api/jobs/templates              — Save a completed job as a reusable template
GET    /api/jobs/{job_id}               — Fetch a single job with nodes, files, audit
POST   /api/jobs/{job_id}/cancel        — Cancel a running job
GET    /api/jobs/{job_id}/files/{file_id} — Download an output file

NOTE: /activity and /templates are declared BEFORE /{job_id} so FastAPI's router
does not match those literal path segments as a job_id.

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

_DEFAULT_WORKSPACE = "default"


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
    logger.debug("SSE client connected — %d subscriber(s) active", len(_subscribers))

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    logger.debug("SSE client disconnected")
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
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
    templates = queue.list_templates(workspace_id=_DEFAULT_WORKSPACE)
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
            workspace_id=_DEFAULT_WORKSPACE,
            created_by=None,
        )
    except Exception as exc:
        logger.exception("Failed to create template from job %s", job_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("Template '%s' (%s) created from job %s", template.name, template.id, job_id)
    return JSONResponse({"template": template.to_dict()}, status_code=201)


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
            workspace_id=_DEFAULT_WORKSPACE,
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
    # Validate status value if provided
    if status is not None:
        valid_statuses = {s.value for s in JobStatus}
        if status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid values: {sorted(valid_statuses)}",
            )

    queue = _queue()
    jobs = queue.list_jobs(
        workspace_id=_DEFAULT_WORKSPACE,
        status=status,
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

    try:
        queue.cancel_job(job_id)
    except Exception as exc:
        logger.exception("Failed to cancel job %s", job_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    queue.add_audit(job_id, action="job_cancel_requested", actor=None)

    # Fetch the refreshed job to return current state
    updated = queue.get_job(job_id)
    logger.info("Cancellation requested for job %s", job_id)

    await emit_activity("job_status_changed", {
        "job_id": job_id,
        "status": updated.status if updated else JobStatus.CANCELLING.value,
    })

    return JSONResponse((updated or job).to_api_dict())


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
