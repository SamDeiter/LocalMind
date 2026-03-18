"""
LocalMind — Backend Server
FastAPI server with agent loop, tool-calling, conversation management,
memory toggle, and static frontend serving.

All chat goes through a single /api/chat endpoint. The agent loop
calls Ollama, detects tool_calls in the response, executes them via
the tool registry, and streams results back to the frontend via SSE.
"""

import json
import logging
import os
import sqlite3
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Configure logging with detailed output
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("localmind")


def kill_existing_server(port: int = 8000):
    """Kill any existing process on the target port to prevent duplicate servers."""
    import subprocess
    import signal
    try:
        # Find PIDs using the port (Windows: netstat, Unix: lsof)
        if os.name == "nt":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True
            )
            current_pid = os.getpid()
            pids_killed = set()
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    if pid != current_pid and pid not in pids_killed:
                        try:
                            os.kill(pid, signal.SIGTERM)
                            pids_killed.add(pid)
                            logger.info(f"🔪 Killed existing server (PID {pid}) on port {port}")
                        except (ProcessLookupError, PermissionError):
                            pass
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True
            )
            if result.stdout.strip():
                for pid_str in result.stdout.strip().split("\n"):
                    pid = int(pid_str)
                    if pid != os.getpid():
                        try:
                            os.kill(pid, signal.SIGTERM)
                            logger.info(f"🔪 Killed existing server (PID {pid}) on port {port}")
                        except (ProcessLookupError, PermissionError):
                            pass
        if pids_killed if os.name == "nt" else result.stdout.strip():
            time.sleep(1)  # Give processes time to release the port
    except Exception as e:
        logger.warning(f"Port guard check failed (non-fatal): {e}")


# Kill any existing server on port 8000 before we start
kill_existing_server(8000)

import httpx
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

# Import our tool registry for the agent loop
from backend.tools.registry import ToolRegistry
from backend.tools.propose_action import resolve_approval, get_pending_requests

# ── Constants ──────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434"
DB_PATH = Path(__file__).parent / "conversations.db"

# ── Multi-Model Routing ────────────────────────────────────────────────
MODEL_TIERS = {
    "light":  "qwen2.5-coder:7b",    # Fast — fits in VRAM, ~3-5x faster
    "medium": "qwen2.5-coder:7b",    # Still fast for moderate tasks
    "heavy":  "qwen2.5-coder:32b",   # Deep analysis — slower but smarter
}

import re

def estimate_task_complexity(message: str, history_len: int = 0) -> dict:
    """Score message complexity 0–10 and pick the right model tier."""
    msg = message.lower().strip()
    score = 5  # Start neutral

    # ── Signals that push LIGHTER ──
    # Greetings / politeness
    if re.match(r'^(hi|hey|hello|yo|sup|thanks|thank you|ok|cool|got it|bye|gm|gn)\b', msg):
        score -= 4
    # Very short messages (< 20 chars)
    if len(msg) < 20:
        score -= 2
    # Simple questions
    if re.match(r'^(what is|who is|when did|where is|how do i|can you)\b', msg) and len(msg) < 80:
        score -= 1

    # ── Signals that push HEAVIER ──
    # Code generation keywords
    heavy_code = ["write a", "implement", "build a", "create a", "generate", "design a"]
    if any(k in msg for k in heavy_code) and len(msg) > 40:
        score += 2
    # Deep analysis keywords
    deep_analysis = ["refactor", "debug", "optimize", "review", "analyze", "architecture",
                     "performance", "security audit", "explain the codebase", "full"]
    if any(k in msg for k in deep_analysis):
        score += 3
    # Multi-file / project-level work
    project_signals = ["project", "codebase", "repository", "multiple files", "entire"]
    if any(k in msg for k in project_signals):
        score += 2
    # Contains code block (triple backticks)
    if "```" in message:
        score += 2
    # Long messages suggest complex requests
    if len(msg) > 200:
        score += 1
    if len(msg) > 500:
        score += 1
    # Long conversation context = likely complex ongoing task
    if history_len > 10:
        score += 1

    # Clamp to 0-10
    score = max(0, min(10, score))

    # Map score to tier
    if score <= 3:
        tier = "light"
    elif score <= 6:
        tier = "medium"
    else:
        tier = "heavy"

    model = MODEL_TIERS[tier]

    # Build reason string
    reasons = []
    if score <= 3:
        reasons.append("quick response")
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

# Default system prompt sent to the model
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

IMPORTANT:
- When the user shares something personal or a preference, save it to memory. Don't announce it every time — just do it naturally.
- Use recall_memories when context about the user would help your response, especially their preferences and past requests.
- When using tools, briefly mention what you're doing — like a person would. "Let me look that up..." or "I'll save that for next time."
- Be proactive. If you can help more than asked, do it. That's what a good assistant does."""

# Global: learning mode toggle (controlled by the frontend)
learning_enabled = True

# Tool registry (auto-discovers tools from backend/tools/)
registry = ToolRegistry()

# RAG imports
try:
    from backend.tools.rag import index_document, query_documents, list_indexed_documents, delete_document
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False


# ── Database Setup ──────────────────────────────────────────────────────
def init_db():
    """Create conversations and messages tables if they don't exist."""
    conn = sqlite3.connect(str(DB_PATH))
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
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── App Lifecycle ───────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    init_db()
    yield


app = FastAPI(title="LocalMind", version="1.0.0", lifespan=lifespan)

# Allow CORS from any origin (for local dev and Tailscale access)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health & Models ─────────────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    """Check server and Ollama status."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
            ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False
    return {"server": True, "ollama": ollama_ok}


@app.get("/api/models")
async def list_models():
    """List all Ollama models available locally."""
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


# ── Memory Toggle ───────────────────────────────────────────────────────
@app.get("/api/memory/status")
async def memory_status():
    """Return whether learning (memory saving) is enabled."""
    return {"learning_enabled": learning_enabled}


@app.post("/api/memory/toggle")
async def memory_toggle(request: Request):
    """Toggle learning mode on/off. When off, AI reads but doesn't save memories."""
    global learning_enabled
    body = await request.json()
    learning_enabled = body.get("enabled", True)
    return {"learning_enabled": learning_enabled}


# ── Memory List & Delete ────────────────────────────────────────────────
@app.get("/api/memories")
async def list_memories():
    """List all stored memories with id, content, category, and timestamp."""
    try:
        from backend.tools.memory import _get_collection
        import datetime
        collection = _get_collection()
        if collection.count() == 0:
            return {"memories": [], "count": 0}
        results = collection.get(include=["documents", "metadatas"])
        memories = []
        for doc_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"]):
            ts = meta.get("created_at", "0")
            try:
                dt = datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                dt = "unknown"
            memories.append({
                "id": doc_id,
                "content": doc,
                "category": meta.get("category", "general"),
                "created_at": dt,
            })
        memories.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return {"memories": memories, "count": len(memories)}
    except Exception as e:
        return {"memories": [], "count": 0, "error": str(e)}


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a specific memory by its ID."""
    try:
        from backend.tools.memory import _get_collection
        collection = _get_collection()
        collection.delete(ids=[memory_id])
        return {"success": True, "deleted": memory_id}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ── File Browser ────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

@app.get("/api/files/list")
async def list_files_api(path: str = "."):
    """List files in a directory relative to project root."""
    target = os.path.normpath(os.path.join(PROJECT_ROOT, path))
    # Security: prevent directory traversal
    if not target.startswith(PROJECT_ROOT):
        return {"error": "Access denied", "files": []}
    if not os.path.isdir(target):
        return {"error": "Not a directory", "files": []}
    try:
        entries = []
        for entry in sorted(os.listdir(target)):
            if entry.startswith('.') or entry in ('__pycache__', 'node_modules', 'venv', '.git'):
                continue
            full = os.path.join(target, entry)
            rel = os.path.relpath(full, PROJECT_ROOT).replace("\\", "/")
            entries.append({
                "name": entry,
                "path": rel,
                "type": "directory" if os.path.isdir(full) else "file",
                "size": os.path.getsize(full) if os.path.isfile(full) else None,
            })
        return {"files": entries, "path": os.path.relpath(target, PROJECT_ROOT).replace("\\", "/")}
    except Exception as e:
        return {"error": str(e), "files": []}


@app.get("/api/files/read")
async def read_file_api(path: str):
    """Read a file's content relative to project root."""
    target = os.path.normpath(os.path.join(PROJECT_ROOT, path))
    # Security: prevent directory traversal
    if not target.startswith(PROJECT_ROOT):
        return {"error": "Access denied"}
    if not os.path.isfile(target):
        return {"error": "File not found"}
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(100_000)  # Cap at 100KB
        return {
            "content": content,
            "path": os.path.relpath(target, PROJECT_ROOT).replace("\\", "/"),
            "size": os.path.getsize(target),
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/files/write")
async def write_file_api(request: Request):
    """Write content to a file relative to project root."""
    body = await request.json()
    path = body.get("path", "")
    content = body.get("content", "")
    target = os.path.normpath(os.path.join(PROJECT_ROOT, path))
    # Security: prevent directory traversal
    if not target.startswith(PROJECT_ROOT):
        return {"error": "Access denied"}
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "path": os.path.relpath(target, PROJECT_ROOT).replace("\\", "/")}
    except Exception as e:
        return {"error": str(e)}


# ── Chat (Agent Loop with Tool Calling) ─────────────────────────────────
@app.post("/api/chat")
async def chat(request: Request):
    """
    Main chat endpoint. Sends user message to Ollama with tool definitions.
    If the model requests tool calls, executes them and feeds results back.
    Streams all events (tokens, tool calls, tool results) to the frontend via SSE.
    """
    body = await request.json()
    model = body.get("model", "auto")
    message = body.get("message", "")
    conversation_id = body.get("conversation_id")
    system_prompt = body.get("system_prompt", "")

    # Auto model routing: estimate task complexity and pick the right model
    task_estimate = None
    if model == "auto":
        task_estimate = estimate_task_complexity(message, len(body.get("messages", [])))
        model = task_estimate["model"]
        logger.info(f"AUTO-ROUTE: score={task_estimate['score']} tier={task_estimate['tier']} → {model} ({task_estimate['reason']})")
    logger.info(f"CHAT REQUEST: model={model}, msg_len={len(message)}, conv_id={conversation_id}")

    # If no prompt in request, load from conversation or use default
    if not system_prompt and conversation_id:
        db_tmp = get_db()
        row_tmp = db_tmp.execute(
            "SELECT system_prompt FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        db_tmp.close()
        if row_tmp and row_tmp["system_prompt"]:
            system_prompt = row_tmp["system_prompt"]
    if not system_prompt:
        system_prompt = DEFAULT_SYSTEM_PROMPT
        # Inject RAG context if documents are indexed
        if RAG_AVAILABLE:
            try:
                from backend.tools.rag import query_documents as _rag_query
                rag_results = _rag_query(user_message, n_results=3)
                if rag_results.get("results"):
                    rag_context = "\n\nRelevant document context:\n"
                    for r in rag_results["results"]:
                        rag_context += f"[From {r['source']}]: {r['content'][:500]}\n"
                    system_prompt += rag_context
            except Exception:
                pass  # RAG query failed, continue without context

    image_base64 = body.get("image")  # Optional base64 image from webcam
    editor_context = body.get("editor_context")  # Optional: file open in editor

    # Inject editor context so AI knows what file the user is working on
    if editor_context:
        system_prompt += f"\n\n[EDITOR CONTEXT — The user currently has this file open in their editor]\n{editor_context}\n[/EDITOR CONTEXT]"

    # Create conversation if needed
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        title = message[:50] + ("..." if len(message) > 50 else "")
        db = get_db()
        now = time.time()
        db.execute(
            "INSERT INTO conversations (id, title, model, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, title, model, system_prompt, now, now),
        )
        db.commit()
        db.close()

    # Load conversation history
    db = get_db()
    rows = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conversation_id,),
    ).fetchall()
    db.close()

    # Build messages list for Ollama
    ollama_messages = [{"role": "system", "content": system_prompt}]
    for row in rows:
        ollama_messages.append({"role": row["role"], "content": row["content"]})

    # Add current user message (with optional image for vision models)
    user_msg = {"role": "user", "content": message}
    if image_base64:
        user_msg["images"] = [image_base64]
    ollama_messages.append(user_msg)

    # Save user message to DB
    db = get_db()
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, "user", message, time.time()),
    )
    db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time(), conversation_id))
    db.commit()
    db.close()

    # Load memory context (most recent memories) if learning is enabled
    memory_context = ""
    try:
        memory_tool = registry.get_tool("recall_memories")
        if memory_tool:
            memories = await memory_tool.execute(query=message, limit=5)
            # memories is a dict: {"success": True, "result": "...", "memories": [...]}
            mem_text = memories.get("result", "") if isinstance(memories, dict) else str(memories)
            if mem_text and "No memories" not in mem_text and "No relevant" not in mem_text:
                memory_context = f"\n\n[REMEMBERED CONTEXT]\n{mem_text}\n[/REMEMBERED CONTEXT]\n"
                # Inject memory into system prompt
                ollama_messages[0]["content"] += memory_context
                logger.info(f"Injected memory context: {mem_text[:200]}")
    except Exception as e:
        logger.warning(f"Memory recall failed (non-fatal): {e}")

    # Grab tool defs once (read-only from here on)
    tool_defs = registry.get_ollama_tools()
    known_tool_names = {t.name for t in registry.tools}

    def _extract_text_tool_call(text: str) -> list[dict]:
        """Fallback: parse tool call JSON from model text output."""
        import re as _re
        calls = []
        # Match JSON objects with "name" and "arguments" keys
        pattern = _re.compile(
            r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{[^}]*\})\s*\}',
            _re.DOTALL,
        )
        for match in pattern.finditer(text):
            name = match.group(1)
            if name in known_tool_names:
                try:
                    args = json.loads(match.group(2))
                    calls.append({"function": {"name": name, "arguments": args}})
                except json.JSONDecodeError:
                    pass
        return calls


    async def stream_response():
        nonlocal ollama_messages
        full_response = ""
        include_tools = bool(tool_defs)  # Flag: can this model use tools?
        stream_start = time.time()
        total_tokens = 0
        total_tool_calls = 0
        logger.info(f"Starting stream_response. Messages: {len(ollama_messages)}, include_tools: {include_tools}")

        # Send thinking event at start
        context_chars = sum(len(m.get('content', '')) for m in ollama_messages)
        yield f"data: {json.dumps({'thinking': {'model': model, 'messages': len(ollama_messages), 'context_chars': context_chars, 'tools_enabled': include_tools}})}\n\n"

        # Send task estimation if auto-routing was used
        if task_estimate:
            yield f"data: {json.dumps({'task_estimate': task_estimate})}\n\n"

        for iteration in range(10):
            logger.info(f"Agent loop iteration {iteration + 1}")

            # Build payload
            payload = {"model": model, "messages": ollama_messages, "stream": True}
            if include_tools:
                payload["tools"] = tool_defs

            chunk_text = ""
            tool_calls_found = []

            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                    async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat", json=payload) as response:
                        logger.info(f"Ollama status: {response.status_code}")

                        if response.status_code != 200:
                            error_body = (await response.aread()).decode()
                            logger.error(f"Ollama error: {error_body}")

                            # If model doesn't support tools, disable and retry
                            if "does not support tools" in error_body and include_tools:
                                logger.info("Model doesn't support tools — disabling and retrying")
                                include_tools = False
                                continue  # Re-enter the for loop without tools

                            yield f"data: {json.dumps({'error': error_body})}\n\n"
                            return

                        # Stream tokens line by line
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                                msg = data.get("message", {})
                                content = msg.get("content", "")
                                if content:
                                    chunk_text += content
                                    total_tokens += 1  # Approximate token count
                                    yield f"data: {json.dumps({'token': content, 'conversation_id': conversation_id})}\n\n"
                                if msg.get("tool_calls"):
                                    tool_calls_found.extend(msg["tool_calls"])
                                if data.get("done"):
                                    logger.info(f"Stream segment done. text_len={len(chunk_text)}, tool_calls={len(tool_calls_found)}")
                            except json.JSONDecodeError:
                                continue

                full_response += chunk_text

                # Fallback: detect tool calls embedded as text
                if not tool_calls_found and chunk_text.strip():
                    parsed = _extract_text_tool_call(chunk_text)
                    if parsed:
                        tool_calls_found.extend(parsed)
                        logger.info(f"Parsed {len(parsed)} tool call(s) from text output")

                # No tool calls → we're done
                if not tool_calls_found:
                    break

                # Execute tool calls and feed results back
                for tc in tool_calls_found:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "unknown")
                    tool_args = func.get("arguments", {})

                    yield f"data: {json.dumps({'tool_call': {'name': tool_name, 'arguments': tool_args}})}\n\n"

                    # If this is a propose_action call, emit an approval_request
                    # SSE event so the frontend can render an approval card.
                    if tool_name == "propose_action":
                        yield f"data: {json.dumps({'approval_request': tool_args})}\n\n"

                    try:
                        if tool_name == "save_memory" and not learning_enabled:
                            result = "Learning is paused. Memory not saved."
                        else:
                            result = await registry.execute_tool(tool_name, tool_args)
                            # Serialize dict result to string for model context
                            if isinstance(result, dict):
                                result = result.get("result", json.dumps(result))
                            result = str(result)
                    except Exception as e:
                        result = f"Error: {str(e)}"

                    yield f"data: {json.dumps({'tool_result': {'name': tool_name, 'result': result}})}\n\n"

                    ollama_messages.append({
                        "role": "assistant",
                        "content": chunk_text,
                        "tool_calls": [tc],
                    })
                    ollama_messages.append({
                        "role": "tool",
                        "content": result,
                    })

            except Exception as e:
                logger.error(f"Stream error: {e}")
                logger.error(traceback.format_exc())
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                break

        # Save response to DB
        logger.info(f"Stream complete. Response length: {len(full_response)}")
        if full_response:
            db = get_db()
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, "assistant", full_response, time.time()),
            )
            db.commit()
            db.close()

        # Signal done with analytics
        elapsed = round(time.time() - stream_start, 2)
        tps = round(total_tokens / elapsed, 1) if elapsed > 0 else 0
        yield f"data: {json.dumps({'done': True, 'conversation_id': conversation_id, 'analytics': {'elapsed_sec': elapsed, 'total_tokens': total_tokens, 'tokens_per_sec': tps, 'tool_calls': total_tool_calls, 'model': model}})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")


# ── Conversation CRUD ───────────────────────────────────────────────────
@app.get("/api/conversations")
async def list_conversations():
    """List all conversations, newest first."""
    db = get_db()
    rows = db.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    db.close()
    return {"conversations": [dict(r) for r in rows]}


@app.get("/api/conversations/{conv_id}/messages")
async def get_conversation_messages(conv_id: str):
    """Get all messages for a conversation."""
    db = get_db()
    rows = db.execute(
        "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    db.close()
    return {"messages": [dict(r) for r in rows]}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation and its messages."""
    db = get_db()
    db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    db.commit()
    db.close()
    return {"ok": True}




@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Get conversation metadata including system prompt."""
    db = get_db()
    row = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    db.close()
    if not row:
        return {"error": "Conversation not found"}
    return dict(row)


@app.put("/api/conversations/{conv_id}/system-prompt")
async def update_system_prompt(conv_id: str, request: Request):
    """Update the system prompt for a conversation."""
    body = await request.json()
    prompt = body.get("system_prompt", "")
    db = get_db()
    db.execute(
        "UPDATE conversations SET system_prompt = ?, updated_at = ? WHERE id = ?",
        (prompt, __import__('time').time(), conv_id),
    )
    db.commit()
    db.close()
    return {"ok": True, "system_prompt": prompt}


@app.get("/api/default-system-prompt")
async def get_default_system_prompt():
    """Return the server's default system prompt."""
    return {"system_prompt": DEFAULT_SYSTEM_PROMPT}


@app.get("/api/conversations/{conv_id}/export")
async def export_conversation(conv_id: str, format: str = "md"):
    """Export a conversation as Markdown or JSON."""
    db = get_db()
    conv = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    msgs = db.execute(
        "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    db.close()

    if not conv:
        return {"error": "Conversation not found"}

    if format == "json":
        export_data = {
            "title": conv["title"],
            "model": conv["model"],
            "created_at": conv["created_at"],
            "messages": [dict(m) for m in msgs],
        }
        return Response(
            content=json.dumps(export_data, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="localmind_{conv_id[:8]}.json"'},
        )
    else:
        # Markdown format
        lines = [f"# {conv['title']}\n"]
        lines.append(f"*Model: {conv['model']}*\n")
        lines.append("---\n")
        for m in msgs:
            role_label = "**You**" if m["role"] == "user" else "**LocalMind**"
            lines.append(f"{role_label}:\n")
            lines.append(f"{m['content']}\n")
            lines.append("")
        md_content = "\n".join(lines)
        return Response(
            content=md_content,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="localmind_{conv_id[:8]}.md"'},
        )



@app.get("/api/version")
async def get_version():
    """Return the current build version."""
    import json as _json
    version_file = Path(__file__).parent.parent / "version.json"
    if version_file.exists():
        with open(version_file) as f:
            return _json.load(f)
    return {"version": "unknown", "build": 0}



@app.get("/api/hardware")
async def hardware_status():
    """Get loaded models, VRAM usage, and system metrics."""
    import httpx
    import psutil

    # System metrics
    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    ram_used = round(mem.used / (1024**3), 1)
    ram_total = round(mem.total / (1024**3), 1)

    system = {
        "cpu_percent": cpu_pct,
        "ram_used_gb": ram_used,
        "ram_total_gb": ram_total,
        "ram_percent": mem.percent,
    }

    # GPU metrics via Ollama /api/ps
    models = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/ps")
            data = r.json()
            for m in data.get("models", []):
                size_gb = m.get("size", 0) / (1024**3)
                vram_gb = m.get("size_vram", 0) / (1024**3)
                models.append({
                    "name": m.get("name", "unknown"),
                    "size_gb": round(size_gb, 1),
                    "vram_gb": round(vram_gb, 1),
                    "processor": m.get("details", {}).get("quantization_level", ""),
                    "expires": m.get("expires_at", ""),
                })
    except Exception:
        pass

    return {
        "loaded": len(models) > 0,
        "models": models,
        "system": system,
    }

# ── Approval Flow Endpoints ──────────────────────────────────────────────
@app.post("/api/approve/{request_id}")
async def approve_action(request_id: str, request: Request):
    """Approve or deny a pending action request from the AI."""
    body = await request.json()
    approved = body.get("approved", False)
    success = resolve_approval(request_id, approved)
    if not success:
        return {"success": False, "error": "Request not found or already resolved"}
    return {"success": True, "approved": approved}


@app.get("/api/approvals")
async def list_approvals():
    """List all approval requests (pending and resolved) for the audit trail."""
    from backend.tools.propose_action import _load_approval_log
    return {"approvals": _load_approval_log()}


@app.get("/api/approvals/pending")
async def list_pending_approvals():
    """List only pending approval requests."""
    return {"pending": get_pending_requests()}


# ── Code Execution (Editor Run Button) ──────────────────────────────────
@app.post("/api/tools/run")
async def run_code_api(request: Request):
    """Execute Python code from the editor's Run button."""
    body = await request.json()
    code = body.get("code", "")
    if not code.strip():
        return {"success": False, "error": "No code provided"}
    try:
        from backend.tools.run_code import RunCodeTool
        tool = RunCodeTool()
        result = await tool.execute(code=code)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Document RAG ────────────────────────────────────────────────────────

@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    """Upload and index a document for RAG queries."""
    if not RAG_AVAILABLE:
        return {"error": "RAG not available — install chromadb"}

    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    if not text.strip():
        return {"error": "Empty file"}

    result = index_document(file.filename, text)
    return result


@app.get("/api/documents")
async def list_documents():
    """List all indexed documents."""
    if not RAG_AVAILABLE:
        return {"documents": []}
    return list_indexed_documents()


@app.delete("/api/documents/{filename:path}")
async def remove_document(filename: str):
    """Remove a document from the RAG index."""
    if not RAG_AVAILABLE:
        return {"error": "RAG not available"}
    return delete_document(filename)

# ── Serve Frontend ──────────────────────────────────────────────────────
# Mount static files: JS/CSS served from /static/, HTML from /
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    # Serve JS, CSS, manifest, sw.js as static files
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")
    # Serve index.html and manifest.json at root
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="root")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
