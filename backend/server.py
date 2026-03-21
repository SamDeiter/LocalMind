"""
server.py — LocalMind Application Shell
=========================================
This is the entry point for the LocalMind backend. It:
1. Initializes the FastAPI app with CORS and lifespan
2. Sets up the SQLite database (WAL mode for multi-worker safety)
3. Registers all route modules (chat, conversations, memory, files, tools, documents)
4. Configures each router with its dependencies (DB, tools, prompts)
5. Serves the frontend static files
6. Provides health, version, hardware, and model listing endpoints

The actual endpoint logic lives in backend/routes/ — this file is
the orchestrator that wires everything together.

ARCHITECTURE:
  server.py (this file) → App shell, DB, config, health endpoints
  routes/chat.py        → Main chat + agent loop + streaming
  routes/conversations.py → Conversation CRUD + export
  routes/memory.py      → Memory toggle, list, delete
  routes/files.py       → File browser (sandboxed)
  routes/tools.py       → Code execution, approvals, dependencies
  routes/documents.py   → RAG document upload/index/delete
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import traceback

from contextlib import asynccontextmanager
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────
# Configure structured logging with timestamps for debugging.
# Log level can be overridden via LOG_LEVEL env var.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("localmind")


# ── Port Guard ────────────────────────────────────────────────────────
# Prevents multiple server instances from binding the same port.
# NOTE: In production, run.py handles this. This is kept for
# backwards compatibility when running server.py directly.

def kill_existing_server(port: int = 8000):
    """Kill any process currently using the given port.
    
    This prevents 'Address already in use' errors when restarting.
    Uses Windows-specific taskkill command.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                if pid != "0":
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True,
                    )
                    logger.info(f"Killed existing server on port {port} (PID {pid})")
    except Exception as e:
        logger.warning(f"Port guard check failed (non-fatal): {e}")


# NOTE: kill_existing_server() is now called by run.py launcher.
# Keeping the function here for backwards compatibility if run directly.

import httpx
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse, Response

# ── Tool Registry ─────────────────────────────────────────────────────
# Auto-discovers tools from backend/tools/ directory.
# Tools include: save_memory, recall_memories, web_search, run_code,
# read_file, write_file, propose_action, etc.
from backend.tools.registry import ToolRegistry
from backend.tools.propose_action import resolve_approval, get_pending_requests

# ── RAG (Optional) ────────────────────────────────────────────────────
# RAG (Retrieval-Augmented Generation) requires chromadb.
# If not installed, RAG features are gracefully disabled.
RAG_AVAILABLE = False
try:
    from backend.tools.rag import index_document, list_indexed_documents, delete_document
    RAG_AVAILABLE = True
    logger.info("RAG module loaded successfully")
except ImportError:
    logger.info("RAG not available — chromadb not installed")

# ── Model Router ──────────────────────────────────────────────────────
# Hybrid routing: local Ollama models + optional Gemini cloud fallback
from backend.model_router import route_model as route_model_hybrid, MODELS as MODEL_DEFS
from backend.gemini_client import is_available as gemini_is_available

# ── Autonomy Engine ───────────────────────────────────────────────────
# Background scheduler for autonomous health checks, reflection, and proposal execution
from backend.autonomy import AutonomyEngine, PROPOSALS_DIR
autonomy_engine = AutonomyEngine(
    ollama_url="http://localhost:11434",
)

# ── Constants ─────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434"
DB_PATH = Path(__file__).parent / "conversations.db"

# ── Multi-Model Routing ──────────────────────────────────────────────
# Maps complexity tiers to Ollama model names.
# Light: fast inference for simple queries (greetings, short questions)
# Heavy: deeper analysis for complex tasks (code review, architecture)
MODEL_TIERS = {
    "light":  "qwen2.5-coder:7b",
    "medium": "qwen2.5-coder:7b",
    "heavy":  "qwen2.5-coder:32b",
}


def estimate_task_complexity(message: str, history_len: int = 0) -> dict:
    """Score message complexity 0–10 and pick the right model tier.
    
    Uses keyword matching and message length analysis to determine
    if a simple lightweight model or a heavier model is needed.
    
    Returns:
        dict with score (0-10), tier (light/medium/heavy),
        model name, and human-readable reason.
    """
    msg = message.lower().strip()
    score = 3  # Start biased toward fast — escalate only when needed

    # ── Signals that push LIGHTER ──
    greetings = (
        r'^(hi|hey|hello|yo|sup|thanks|thank you|ok|cool|got it|bye|gm|gn'
        r'|how are|how you|how\'s it|what\'s up|good morning|good evening'
        r'|what is my name|who am i|do you remember|tell me about me)\b'
    )
    if re.match(greetings, msg):
        score -= 2
    if len(msg) < 30:
        score -= 1
    if re.match(r'^(what is|who is|when did|where is|how do i|can you)\b', msg) and len(msg) < 80:
        score -= 1

    # ── Signals that push HEAVIER ──
    heavy_code = ["write a", "implement", "build a", "create a", "generate", "design a"]
    if any(k in msg for k in heavy_code) and len(msg) > 40:
        score += 2
    deep_analysis = ["refactor", "debug", "optimize", "review", "analyze", "architecture",
                     "performance", "security audit", "explain the codebase", "full"]
    if any(k in msg for k in deep_analysis):
        score += 3
    project_signals = ["project", "codebase", "repository", "multiple files", "entire"]
    if any(k in msg for k in project_signals):
        score += 2
    if "```" in message:
        score += 2
    if len(msg) > 200:
        score += 1
    if len(msg) > 500:
        score += 1
    if history_len > 10:
        score += 1

    # Clamp to 0-10
    score = max(0, min(10, score))

    # Map score to tier and model
    if score <= 3:
        tier, model = "light", MODEL_TIERS["light"]
    elif score <= 6:
        tier, model = "medium", MODEL_TIERS["medium"]
    else:
        tier, model = "heavy", MODEL_TIERS["heavy"]

    reasons = []
    if score <= 3:
        reasons.append("simple query")
    elif score >= 7:
        reasons.append("complex task detected")
    if len(msg) < 20:
        reasons.append("short message")
    if len(msg) > 200:
        reasons.append("detailed request")

    return {
        "score": score,
        "tier": tier,
        "model": model,
        "reason": " • ".join(reasons) if reasons else "standard",
    }


# ── Default System Prompt ─────────────────────────────────────────────
# This is the personality and instruction set for the AI.
# It includes memory rules, tool usage guidance, and model self-awareness.
DEFAULT_SYSTEM_PROMPT = """You are LocalMind — think of yourself as the user's brilliant, reliable friend who happens to be great with technology. You talk naturally, like a real person — not a corporate chatbot.

PERSONALITY:
- Be warm, direct, and genuine. Use casual language when it fits, but stay sharp and competent.
- Have personality. React to things. If something is cool, say so. If a request is tricky, acknowledge it.
- Don't over-explain unless asked. Get to the point, then offer more detail if they want it.
- Remember things about the user. Reference past conversations and preferences naturally.
- When you don't know something, just say so honestly — then offer to look it up.
- Keep responses conversational. Write like you talk, not like a manual.

YOUR CAPABILITIES (use them proactively):
- Search the web for current info
- Read, write, and list files (sandboxed to ~/LocalMind_Workspace — you can NEVER delete files)
- Execute Python code safely
- Save and recall memories about the user
- Analyze images from camera or screenshots
- Take screenshots and read the clipboard
- Check git status, view diffs, read commit history, and make commits in workspace repos
- Load project directory trees to understand codebase structure

CRITICAL — MEMORY RULES (follow these EVERY time):
1. When the user tells you their name, job, location, age, or ANY personal fact → IMMEDIATELY call save_memory with category='fact'.
2. When the user expresses a preference (favorite color, language, tool, food, etc.) → IMMEDIATELY call save_memory with category='preference'.
3. When the user gives you an instruction like "always do X" or "I prefer Y" → IMMEDIATELY call save_memory with category='instruction'.
4. ALWAYS call recall_memories at the start of conversations to check what you know about the user.
5. Don't announce saving — just do it silently in the background.

EXAMPLE:
  User: "My name is Sam"
  You should: call save_memory(content="User's name is Sam", category="fact") AND respond naturally.

GENERAL:
- When using tools, briefly mention what you're doing — like a person would.
- Be proactive. If you can help more than asked, do it."""

# ── Shared State ──────────────────────────────────────────────────────
# learning_enabled controls whether the AI saves new memories.
# Toggled via the frontend's Learning toggle switch.
learning_enabled = True

# Tool registry: auto-discovers all tools in backend/tools/
registry = ToolRegistry()


# ── Database Setup ────────────────────────────────────────────────────

def init_db():
    """Create conversations and messages tables if they don't exist.
    
    Uses WAL (Write-Ahead Logging) journal mode for safe concurrent
    reads when running with multiple uvicorn workers.
    busy_timeout prevents immediate failures when the DB is briefly locked.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            system_prompt TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


def get_db():
    """Get a database connection with row factory enabled.
    
    Each call creates a new connection (no connection pooling).
    SQLite with WAL mode handles concurrent reads safely.
    Row factory converts rows to dict-like objects for cleaner access.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ── App Lifecycle ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database, configure routers, and start autonomy engine on startup."""
    init_db()
    _configure_routers()
    await autonomy_engine.start()
    logger.info("LocalMind server initialized (autonomy engine active)")
    yield
    await autonomy_engine.stop()


def _configure_routers():
    """Inject dependencies into all route modules.
    
    Each router uses dependency injection rather than direct imports
    to avoid circular dependencies between modules.
    """
    from backend.routes import chat, conversations, memory, documents

    # Chat router needs everything — DB, tools, model routing, prompts
    chat.configure(
        get_db_func=get_db,
        registry=registry,
        ollama_base_url=OLLAMA_BASE_URL,
        default_system_prompt=DEFAULT_SYSTEM_PROMPT,
        estimate_task_complexity_func=estimate_task_complexity,
        route_model_hybrid_func=route_model_hybrid,
        gemini_is_available_func=gemini_is_available,
        learning_enabled_func=lambda: learning_enabled,
        autonomy_engine=autonomy_engine,
    )

    # Conversations router needs DB access and the default prompt
    conversations.configure(
        get_db_func=get_db,
        default_prompt=DEFAULT_SYSTEM_PROMPT,
    )

    # Documents router needs RAG functions (optional dependency)
    if RAG_AVAILABLE:
        documents.configure(
            rag_available=True,
            index_fn=index_document,
            list_fn=list_indexed_documents,
            delete_fn=delete_document,
        )
    else:
        documents.configure(rag_available=False)


# ── Create FastAPI App ────────────────────────────────────────────────

app = FastAPI(title="LocalMind", version="1.0.0", lifespan=lifespan)

# Allow CORS from any origin (needed for local dev and Tailscale access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── No-Cache Middleware (Dev Mode) ────────────────────────────────────
# Prevents browser from caching stale JS/CSS files during development.
# Without this, changes to frontend files may not take effect until
# the user does a hard-refresh (Ctrl+Shift+R).
@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css", ".html")) or path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ── Register Routers ─────────────────────────────────────────────────
# Each router handles a specific domain of the API.
from backend.routes.chat import router as chat_router
from backend.routes.conversations import router as conversations_router
from backend.routes.memory import router as memory_router
from backend.routes.files import router as files_router
from backend.routes.tools import router as tools_router
from backend.routes.documents import router as documents_router

app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(memory_router)
app.include_router(files_router)
app.include_router(tools_router)
app.include_router(documents_router)


# ── Core Endpoints (kept in server.py — they're small + foundational) ─

@app.get("/api/health")
async def health_check():
    """Check server and Ollama connectivity.
    
    The frontend polls this endpoint to show connection status
    in the bottom status bar (green dot = connected).
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
            ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False
    return {"server": True, "ollama": ollama_ok}


@app.get("/api/models")
async def list_models():
    """List all Ollama models available locally.
    
    Returns model names and sizes for the model selector dropdown.
    Also used by the auto-router to verify which models are available.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10.0)
            data = resp.json()
            models = [
                {"name": m["name"], "size": m.get("size", 0)}
                for m in data.get("models", [])
            ]
            return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}


# ── Autonomy Endpoints ────────────────────────────────────────────────

@app.get("/api/autonomy/status")
async def autonomy_status():
    """Get the current autonomy engine status.
    
    Returns loop timing, proposal counts, test results, and uptime.
    Also includes global counts for memories, documents, and proposals.
    """
    status = autonomy_engine.get_status()
    status["mode"] = autonomy_engine.mode
    status["start_time"] = autonomy_engine._start_time
    status["recent_events"] = autonomy_engine._recent_events[-20:]
    
    # Add global counts for sidebar synchronization
    memories_count = 0
    try:
        from backend.tools.memory import _get_collection
        memories_count = _get_collection().count()
    except Exception:
        pass
        
    documents_count = 0
    try:
        if RAG_AVAILABLE:
            docs = list_indexed_documents()
            documents_count = len(docs.get("documents", []))
    except Exception:
        pass
        
    proposals_count = 0
    try:
        if PROPOSALS_DIR.exists():
            proposals_count = len(list(PROPOSALS_DIR.glob("*.json")))
    except Exception:
        pass
        
    status["memories_count"] = memories_count
    status["documents_count"] = documents_count
    status["proposals_count"] = proposals_count
    
    return status


@app.post("/api/autonomy/reflect")
async def autonomy_reflect():
    """Manually trigger the AI reflection cycle."""
    autonomy_engine.trigger_reflection()
    return {"ok": True, "message": "Reflection cycle triggered"}

@app.post("/api/autonomy/execute")
async def autonomy_execute():
    """Manually trigger the AI execution cycle."""
    autonomy_engine.trigger_execution()
    return {"ok": True, "message": "Execution cycle triggered"}

@app.post("/api/proposals/{proposal_id}/retry")
async def retry_proposal(proposal_id: str):
    """Reset a failed proposal to approved status for re-execution."""
    updated = autonomy_engine.retry_proposal(proposal_id)
    if updated:
        return {"ok": True, "proposal": updated}
    return {"ok": False, "message": "Proposal not found"}


@app.post("/api/autonomy/toggle")
async def toggle_autonomy():
    """Pause or resume the autonomy engine."""
    new_state = autonomy_engine.toggle()
    return {"enabled": new_state}


@app.post("/api/autonomy/mode")
async def set_autonomy_mode(request: Request):
    """Switch between 'supervised' and 'autonomous' mode."""
    body = await request.json()
    mode = body.get("mode", "supervised")
    try:
        new_mode = autonomy_engine.set_mode(mode)
        return {"ok": True, "mode": new_mode}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/autonomy/activity")
async def autonomy_activity_stream(request: Request):
    """SSE endpoint streaming real-time autonomy engine events.
    
    The frontend connects to this for the live activity feed.
    Each event is a JSON object with action, detail, and timestamp.
    """
    from starlette.responses import StreamingResponse

    queue = autonomy_engine.subscribe_activity()

    async def event_generator():
        try:
            # Send current status as first event
            status = autonomy_engine.get_status()
            current = status.get("current_activity")
            if current:
                yield f"data: {json.dumps(current)}\n\n"
            else:
                yield f"data: {json.dumps({'action': 'idle', 'detail': 'Waiting...', 'time': time.strftime('%H:%M:%S')})}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive\n\n"
        finally:
            autonomy_engine.unsubscribe_activity(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@app.get("/api/autonomy/proposals")
async def list_autonomy_proposals(status: str = "all"):
    """List all autonomy proposals, optionally filtered by status.

    Query params:
        status: 'all', 'proposed', 'approved', 'in_progress', 'completed', 'failed', 'denied'
    """
    proposals = autonomy_engine.list_proposals(status_filter=status)
    return {"proposals": proposals, "count": len(proposals)}


@app.post("/api/autonomy/proposals/{proposal_id}/approve")
async def approve_autonomy_proposal(proposal_id: str):
    """Approve a proposal so the execution loop will pick it up."""
    result = autonomy_engine.approve_proposal(proposal_id)
    if result is None:
        return {"ok": False, "error": f"Proposal not found: {proposal_id}"}
    return {"ok": True, "proposal": result}


@app.post("/api/autonomy/proposals/{proposal_id}/deny")
async def deny_autonomy_proposal(proposal_id: str):
    """Deny a proposal to prevent it from being executed."""
    result = autonomy_engine.deny_proposal(proposal_id)
    if result is None:
        return {"ok": False, "error": f"Proposal not found: {proposal_id}"}
    return {"ok": True, "proposal": result}


@app.get("/api/version")
async def get_version():
    """Return the current build version from version.json.
    
    The frontend displays this in the bottom-left corner.
    version.json is bumped by scripts/bump_build.py on each deploy.
    """
    version_file = Path(__file__).parent.parent / "version.json"
    if version_file.exists():
        with open(version_file) as f:
            return json.load(f)
    return {"version": "unknown", "build": 0}


@app.get("/api/hardware")
async def hardware_status():
    """Get loaded models, VRAM usage, and system metrics.
    
    Used by the frontend hardware monitor panel to show:
    - CPU usage percentage
    - RAM usage (GB and percentage)
    - Currently loaded Ollama models with VRAM allocation
    """
    import psutil

    # System metrics
    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    system = {
        "cpu_percent": cpu_pct,
        "ram_used_gb": round(mem.used / (1024**3), 1),
        "ram_total_gb": round(mem.total / (1024**3), 1),
        "ram_percent": mem.percent,
    }

    # GPU metrics via Ollama /api/ps
    models = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/ps")
            data = r.json()
            for m in data.get("models", []):
                models.append({
                    "name": m.get("name", "unknown"),
                    "size_gb": round(m.get("size", 0) / (1024**3), 1),
                    "vram_gb": round(m.get("size_vram", 0) / (1024**3), 1),
                    "processor": m.get("details", {}).get("quantization_level", ""),
                    "expires": m.get("expires_at", ""),
                })
    except Exception:
        pass

    return {"loaded": len(models) > 0, "models": models, "system": system}


# ── Serve Frontend ────────────────────────────────────────────────────
# Mount static files: JS/CSS from /static/, HTML from root /
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="root")


# ── Direct Execution ──────────────────────────────────────────────────
# For development: python backend/server.py
# For production: use run.py instead (multi-worker, hot-reload, etc.)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
