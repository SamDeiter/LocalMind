"""
server.py — LocalMind Application Shell (Modular Version)
==========================================================
This is the entry point for the LocalMind backend. 
It wires together the configuration, database, and route modules.

The actual endpoint logic lives in backend/routes/.
Utility functions live in backend/utils/.
Constants live in backend/config.py.
"""

import logging
from pathlib import Path, PurePosixPath
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import (
    DEFAULT_SYSTEM_PROMPT, OLLAMA_BASE_URL, FRONTEND_URLS,
    SLACK_ENABLED, GPU_VRAM_GB, VACUUM_INTERVAL_HOURS, JOB_RETENTION_DAYS,
    WORKSPACE_ROOT, BEST_OF_N_ENABLED, PRM_MODEL, LORA_ADAPTERS_DIR,
    MODEL_TIERS,
)
from backend.utils.server_utils import kill_existing_server, estimate_task_complexity
from backend.tools.registry import ToolRegistry
from backend.metacognition.controller import MetaCognitiveController
from backend import notifications, gemini_client, db
from backend.db import DB_PATH, get_db
from backend.core.schema import init_phase0_schema, ensure_default_tenant
from backend.core.telemetry import (
    init_telemetry_schema, health_checker, metrics_collector, alert_manager,
)
from backend.jobs.worker import JobWorker
from backend.core.audit import audit_event as _audit_event, get_audit_logger, get_alert_manager
from backend.security.redact import install_redacting_filter
from backend.security.data_protection import (
    harden_db_permissions, schedule_vacuum, daily_purge_loop,
)

# -- Logging --
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("localmind")

# -- RAG Availability Check --
RAG_AVAILABLE = False
try:
    from backend.tools.rag import index_document, list_indexed_documents, delete_document
    RAG_AVAILABLE = True
    logger.info("RAG module loaded successfully")
except ImportError:
    logger.info("RAG not available — chromadb not installed")

# -- Global Components --
metacog_controller = MetaCognitiveController(
    emit_activity=lambda *a, **kw: logger.debug(f"metacog: {a} {kw}"),
)
registry = ToolRegistry()
job_worker = None  # Initialized in lifespan after schema setup
gc_worker = None   # GC background worker
slack_bot = None   # Slack bot (if enabled)

# -- App Lifecycle --
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database, configure routers, and start all background workers."""
    global job_worker, gc_worker, slack_bot

    import asyncio

    # ── Log redaction (install BEFORE any logging) ─────────────
    install_redacting_filter()

    # ── Database & schema ───────────────────────────────────────
    db.init_db()
    init_phase0_schema()
    init_telemetry_schema()
    ensure_default_tenant()
    _configure_routers()

    # ── Audit & monitoring (Section 26) ──────────────────────────
    _al = get_audit_logger()
    _am = get_alert_manager()
    try:
        import json as _json
        _version_file = Path(__file__).parent.parent / "version.json"
        _version_info = _json.loads(_version_file.read_text()) if _version_file.exists() else {}
        await _audit_event("system", "server_started", {
            "version": _version_info.get("version", "unknown"),
            "build": _version_info.get("build", 0),
        })
    except Exception:
        logger.debug("Startup audit event failed (non-critical)")

    # ── Data-at-rest hardening ─────────────────────────────────
    harden_db_permissions(DB_PATH)

    # ── Startup secret scan ─────────────────────────────────────
    try:
        from backend.core.secret_manager import StartupSecretScanner
        warnings = StartupSecretScanner.scan_config_files()
        for w in warnings:
            logger.warning("Secret scan: %s in %s", w.get("pattern", "unknown"), w.get("file", "unknown"))
    except Exception:
        logger.debug("Secret scanner skipped (non-critical)")

    # ── Inference dependencies (ModelSelector, LoRA, BestOfN) ───
    model_selector = None
    best_of_n_sampler = None
    try:
        from backend.inference.lora_manager import LoRAManager
        from backend.inference.model_selector import ModelSelector
        lora_manager = LoRAManager(adapters_dir=str(LORA_ADAPTERS_DIR))
        model_selector = ModelSelector(
            model_tiers=MODEL_TIERS,
            lora_manager=lora_manager,
        )
        logger.info("ModelSelector initialised (tiers=%s)", list(MODEL_TIERS.keys()))
    except Exception as e:
        logger.warning("ModelSelector init failed (falling back to defaults): %s", e)

    if BEST_OF_N_ENABLED:
        try:
            from backend.inference.best_of_n import BestOfNSampler
            best_of_n_sampler = BestOfNSampler(
                ollama_url=OLLAMA_BASE_URL,
                scorer_model=PRM_MODEL or None,
            )
            logger.info(
                "BestOfNSampler initialised (scorer=%s)",
                PRM_MODEL or "heuristic",
            )
        except Exception as e:
            logger.warning("BestOfNSampler init failed (best-of-N disabled): %s", e)

    # ── Job worker ──────────────────────────────────────────────
    from backend.routes.jobs import emit_activity

    async def _activity_multiplex(event_type: str, data: dict):
        """Fan-out worker events to SSE subscribers and Slack."""
        await emit_activity(event_type, data)
        from backend.integrations.slack_bot import get_slack_bot
        bot = get_slack_bot()
        if bot:
            try:
                await bot.handle_worker_event(event_type, data)
            except Exception as e:
                logger.debug("Slack notification failed (non-fatal): %s", e)

    job_worker = JobWorker(
        tool_registry=registry,
        ollama_url=OLLAMA_BASE_URL,
        activity_callback=_activity_multiplex,
        model_selector=model_selector,
        best_of_n_sampler=best_of_n_sampler,
    )
    app.state.job_worker = job_worker

    # ── GC worker ───────────────────────────────────────────────
    try:
        from backend.core.gc import GCWorker
        gc_worker = GCWorker()
        asyncio.create_task(gc_worker.start())
        logger.info("GC worker started")
    except Exception as e:
        logger.warning("GC worker failed to start: %s", e)

    # ── Slack bot ───────────────────────────────────────────────
    if SLACK_ENABLED:
        try:
            from backend.integrations.slack_bot import get_slack_bot
            slack_bot = get_slack_bot()
            if slack_bot:
                await slack_bot.start()
                logger.info("Slack bot started (Socket Mode)")
        except Exception as e:
            logger.warning("Slack bot failed to start: %s", e)

    # ── Memory encryption migration ────────────────────────────
    try:
        from backend.security.memory_encryption import ensure_memory_columns
        ensure_memory_columns()
    except Exception:
        logger.debug("Memory encryption migration skipped (non-critical)")

    # ── MemPalace (Tier-3 long-term memory) ────────────────────
    try:
        from backend.memory.palace_manager import initialize_palace, get_status
        palace_ready = initialize_palace()
        if palace_ready:
            status = get_status()
            logger.info(
                "MemPalace Tier-3 ready — %s drawers at %s",
                status.get("drawer_count", "?"),
                status.get("palace_path", "?"),
            )
        else:
            logger.info(
                "MemPalace not configured — Tier-3 memory disabled. "
                "Run 'mempalace init' to enable palace memory."
            )
    except Exception as _mp_exc:
        logger.debug("MemPalace init skipped (non-critical): %s", _mp_exc)

    # ── Data protection background tasks ─────────────────────────
    asyncio.create_task(schedule_vacuum(DB_PATH, interval_hours=VACUUM_INTERVAL_HOURS))
    asyncio.create_task(daily_purge_loop(WORKSPACE_ROOT, retention_days=JOB_RETENTION_DAYS))
    logger.info("Data protection tasks started (VACUUM every %dh, purge retention %dd)",
                VACUUM_INTERVAL_HOURS, JOB_RETENTION_DAYS)

    # ── Start main workers ──────────────────────────────────────
    asyncio.create_task(job_worker.start())

    # ── Background learning loop (self-discovery + skill learning) ──
    from backend.autonomy.loops.research import run_learning_loop
    asyncio.create_task(run_learning_loop())

    logger.info("LocalMind server initialized (job worker + learning loop + GC active)")
    yield

    # ── Graceful shutdown ───────────────────────────────────────
    await job_worker.stop()
    if gc_worker:
        await gc_worker.stop()
    if slack_bot:
        await slack_bot.stop()

def _configure_routers():
    """Inject dependencies into route modules to avoid circular imports."""
    from backend.routes import chat, conversations, documents
    from backend.routes.chat import init_chat_service

    init_chat_service(
        registry=registry,
        metacog_controller=metacog_controller
    )

    conversations.configure(
        get_db_func=db.get_db,
        default_prompt=DEFAULT_SYSTEM_PROMPT,
    )

    if RAG_AVAILABLE:
        documents.configure(
            rag_available=True,
            index_fn=index_document,
            list_fn=list_indexed_documents,
            delete_fn=delete_document,
        )
    else:
        documents.configure(rag_available=False)


# -- Create FastAPI App --
app = FastAPI(title="LocalMind", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_URLS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Auth & RBAC Middleware --
# Paths that skip authentication entirely (health, docs, static, frontend).
_AUTH_SKIP_PREFIXES = (
    "/health",
    "/docs",
    "/openapi.json",
    "/static/",
)
_AUTH_SKIP_EXACT = {"/", "/health", "/health/ready", "/health/deep", "/docs", "/openapi.json", "/sw.js"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Authenticate API requests and enforce RBAC permissions.

    Skips auth for health checks, OpenAPI docs, static assets, and the
    frontend root.  All ``/api/`` routes require a valid API key (unless
    the system is in bootstrap mode with no keys).
    """
    from fastapi.responses import JSONResponse as _JSONResponse
    path = request.url.path

    # Skip auth for non-API routes
    target_path = PurePosixPath(path)
    if path in _AUTH_SKIP_EXACT or any(target_path.is_relative_to(PurePosixPath(p)) for p in _AUTH_SKIP_PREFIXES):
        return await call_next(request)

    # Only enforce auth on /api/ routes
    if target_path.is_relative_to(PurePosixPath("/api")):
        # Explicitly allow /api/health if it exists and should be public,
        # but for now, we follow the previous logic which was any /api/
        try:
            from backend.security.auth import authenticate_request
            from backend.security.rbac import check_permission

            user = authenticate_request(request)
            check_permission(user["role"], request.method, path)

            # Stash user context on request.state for route handlers
            request.state.user = user
        except HTTPException as exc:
            return _JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )

    return await call_next(request)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path

    is_local = request.url.hostname in ["localhost", "127.0.0.1"]
    is_static = path.endswith((".js", ".css", ".html")) or path == "/"

    # We want Service Worker to handle caching for stability,
    # but still allow browser to check for updates during dev.
    if is_static and is_local:
        response.headers["Cache-Control"] = "no-cache"

    return response

# -- Register Routers --
from backend.routes.chat import router as chat_router
from backend.routes.conversations import router as conversations_router
from backend.routes.memory import router as memory_router
from backend.routes.files import router as files_router
from backend.routes.tools import router as tools_router
from backend.routes.documents import router as documents_router
from backend.routes.research_routes import router as research_router
from backend.routes.system import router as system_router
from backend.routes.settings import router as settings_router
from backend.routes.swarm_routes import router as swarm_router
from backend.routes.validation_routes import router as validation_router
from backend.routes.google_auth import router as google_auth_router
from backend.routes.google_auth import _legacy_router as google_auth_legacy_router
from backend.routes.jobs import router as jobs_router
from backend.routes.admin import router as admin_router
from backend.routes.knowledge_graph import router as knowledge_graph_router
from backend.routes.time_machine import router as time_machine_router
from backend.routes.hub import router as hub_router
from backend.routes.tts import router as tts_router
from backend.routes.eval_routes import router as eval_router
from backend.routes.push import router as push_router
from backend.routes.tools_generated import router as tools_generated_router
from backend.routes.self_discovery import router as self_discovery_router
from backend.routes.skill_learning import router as skill_learning_router

app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(memory_router)
app.include_router(files_router)
app.include_router(tools_router)
app.include_router(documents_router)
app.include_router(research_router)
app.include_router(system_router)
app.include_router(settings_router)
app.include_router(swarm_router)
app.include_router(validation_router)
app.include_router(google_auth_router)
app.include_router(google_auth_legacy_router)
app.include_router(jobs_router)
app.include_router(admin_router)
app.include_router(knowledge_graph_router)
app.include_router(time_machine_router)
app.include_router(hub_router)
app.include_router(tts_router)
app.include_router(eval_router)
app.include_router(push_router)
app.include_router(tools_generated_router)
app.include_router(self_discovery_router)
app.include_router(skill_learning_router)

# -- Health Check Endpoints --
@app.get("/health")
async def health_liveness():
    """Basic liveness check."""
    result = await health_checker.check_liveness()
    return result.to_dict() if hasattr(result, 'to_dict') else {"healthy": result.healthy}

@app.get("/health/ready")
async def health_readiness():
    """Readiness check — Ollama reachable, models loaded."""
    result = await health_checker.check_readiness()
    return result.to_dict() if hasattr(result, 'to_dict') else {"healthy": result.healthy}

@app.get("/health/deep")
async def health_deep():
    """Deep health check — VRAM, queue depth, disk space."""
    result = await health_checker.check_deep()
    return result.to_dict() if hasattr(result, 'to_dict') else {"healthy": result.healthy}

# -- Static Files --
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="root")

if __name__ == "__main__":
    import uvicorn
    # kill_existing_server(8000) # Optional, run.py usually handles this
    uvicorn.run(app, host="0.0.0.0", port=8000)
