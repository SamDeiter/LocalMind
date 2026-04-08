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
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import (
    DEFAULT_SYSTEM_PROMPT, OLLAMA_BASE_URL, PROPOSALS_DIR, FRONTEND_URLS,
    SLACK_ENABLED, GPU_VRAM_GB,
)
from backend.utils.server_utils import kill_existing_server, estimate_task_complexity
from backend.tools.registry import ToolRegistry
from backend.autonomy import AutonomyEngine, PROPOSALS_DIR
from backend.metacognition.controller import MetaCognitiveController
from backend import notifications, gemini_client, db
from backend.db import DB_PATH, get_db
from backend.core.schema import init_phase0_schema, ensure_default_tenant
from backend.core.telemetry import (
    init_telemetry_schema, health_checker, metrics_collector, alert_manager,
)
from backend.jobs.worker import JobWorker

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
autonomy_engine = AutonomyEngine(ollama_url=OLLAMA_BASE_URL)
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

    # ── Database & schema ───────────────────────────────────────
    db.init_db()
    init_phase0_schema()
    init_telemetry_schema()
    ensure_default_tenant()
    _configure_routers()

    # ── Startup secret scan ─────────────────────────────────────
    try:
        from backend.core.secret_manager import StartupSecretScanner
        warnings = StartupSecretScanner.scan_config_files()
        for w in warnings:
            logger.warning("Secret scan: %s in %s", w.get("pattern", "unknown"), w.get("file", "unknown"))
    except Exception:
        logger.debug("Secret scanner skipped (non-critical)")

    # Store engine on app.state for route access
    app.state.autonomy_engine = autonomy_engine

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

    # ── Start main workers ──────────────────────────────────────
    await autonomy_engine.start()
    asyncio.create_task(job_worker.start())
    logger.info("LocalMind server initialized (autonomy + job worker + GC active)")
    yield

    # ── Graceful shutdown ───────────────────────────────────────
    await job_worker.stop()
    if gc_worker:
        await gc_worker.stop()
    if slack_bot:
        await slack_bot.stop()
    if hasattr(autonomy_engine, 'coordinator') and autonomy_engine.coordinator:
        await autonomy_engine.coordinator.stop()
    await autonomy_engine.stop()

def _configure_routers():
    """Inject dependencies into route modules to avoid circular imports."""
    from backend.routes import chat, conversations, documents, autonomy_routes
    from backend.routes.chat import init_chat_service

    init_chat_service(
        registry=registry,
        autonomy_engine=autonomy_engine,
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

    autonomy_routes.configure(
        engine=autonomy_engine,
        proposals_dir=PROPOSALS_DIR,
        rag_available=RAG_AVAILABLE,
        list_indexed_documents_fn=list_indexed_documents if RAG_AVAILABLE else None,
    )

# -- Create FastAPI App --
app = FastAPI(title="LocalMind", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_URLS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
from backend.routes.autonomy_routes import router as autonomy_router
from backend.routes.research_routes import router as research_router
from backend.routes.system import router as system_router
from backend.routes.settings import router as settings_router
from backend.routes.swarm_routes import router as swarm_router
from backend.routes.validation_routes import router as validation_router
from backend.routes.google_auth import router as google_auth_router
from backend.routes.google_auth import _legacy_router as google_auth_legacy_router
from backend.routes.jobs import router as jobs_router

app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(memory_router)
app.include_router(files_router)
app.include_router(tools_router)
app.include_router(documents_router)
app.include_router(autonomy_router)
app.include_router(research_router)
app.include_router(system_router)
app.include_router(settings_router)
app.include_router(swarm_router)
app.include_router(validation_router)
app.include_router(google_auth_router)
app.include_router(google_auth_legacy_router)
app.include_router(jobs_router)

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
