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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import DEFAULT_SYSTEM_PROMPT, OLLAMA_BASE_URL
from backend.utils.server_utils import kill_existing_server, estimate_task_complexity
from backend.tools.registry import ToolRegistry
from backend.autonomy import AutonomyEngine, PROPOSALS_DIR
from backend.metacognition.controller import MetaCognitiveController
from backend import notifications, gemini_client, db

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

# -- App Lifecycle --
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database, configure routers, and start autonomy engine."""
    db.init_db()
    _configure_routers()
    await autonomy_engine.start()
    logger.info("LocalMind server initialized (autonomy engine active)")
    yield
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css", ".html")) or path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
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

# -- Static Files --
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="root")

if __name__ == "__main__":
    import uvicorn
    # kill_existing_server(8000) # Optional, run.py usually handles this
    uvicorn.run(app, host="0.0.0.0", port=8000)
