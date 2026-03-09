"""
LocalMind Backend — FastAPI server with agent loop.
Proxies to Ollama with tool calling, streams responses via SSE.
Manages conversations (SQLite) and memory (ChromaDB).
"""

import json
import logging
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from tools.registry import ToolRegistry
from tools.memory import set_learning_enabled, get_learning_enabled, get_recent_memories

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5-coder:32b"
MAX_TOOL_ITERATIONS = 10
DB_PATH = Path(__file__).parent / "conversations.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("localmind")

SYSTEM_PROMPT_DEFAULT = """You are LocalMind, a powerful local AI assistant. You have access to tools that let you:
- Search the web for current information
- Read, write, and list files in the user's workspace
- Execute Python code
- Save and recall memories about the user
- Analyze images from the camera or screenshots
- Take screenshots of the user's screen
- Read the clipboard

IMPORTANT BEHAVIORS:
- When the user shares preferences, facts about themselves, or important context, use save_memory to remember it.
- When you need to recall something about the user, use recall_memories.
- You can NEVER delete files. You have no delete capability.
- All file operations are sandboxed to ~/LocalMind_Workspace.
- Be proactive about using your tools when they would be helpful.
- When using tools, explain what you're doing and show the results clearly."""

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'New Chat',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
    """)
    conn.close()

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    app.state.tool_registry = ToolRegistry()
    tools = app.state.tool_registry.tools
    logger.info(f"LocalMind started with {len(tools)} tools: {[t.name for t in tools]}")
    yield


app = FastAPI(title="LocalMind", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

# ---------------------------------------------------------------------------
# Routes — Frontend
# ---------------------------------------------------------------------------

from fastapi.responses import FileResponse

@app.get("/")
async def serve_index():
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "LocalMind API is running. Frontend not found."}


@app.get("/manifest.json")
async def serve_manifest():
    manifest_path = frontend_dir / "manifest.json"
    if manifest_path.exists():
        return FileResponse(str(manifest_path), media_type="application/json")
    raise HTTPException(404)


@app.get("/sw.js")
async def serve_sw():
    sw_path = frontend_dir / "sw.js"
    if sw_path.exists():
        return FileResponse(str(sw_path), media_type="application/javascript")
    raise HTTPException(404)

# ---------------------------------------------------------------------------
# Routes — Models
# ---------------------------------------------------------------------------

@app.get("/api/models")
async def list_models():
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [
                {"name": m["name"], "size": m.get("size", 0), "modified_at": m.get("modified_at", "")}
                for m in data.get("models", [])
            ]
            return {"models": models}
        except httpx.ConnectError:
            raise HTTPException(503, "Ollama is not running. Start it with: ollama serve")
        except Exception as exc:
            raise HTTPException(500, str(exc))

# ---------------------------------------------------------------------------
# Routes — Memory
# ---------------------------------------------------------------------------

@app.get("/api/memory/status")
async def memory_status():
    return {"learning_enabled": get_learning_enabled()}


@app.post("/api/memory/toggle")
async def toggle_memory(request: Request):
    body = await request.json()
    enabled = body.get("enabled", not get_learning_enabled())
    set_learning_enabled(enabled)
    return {"learning_enabled": get_learning_enabled()}

# ---------------------------------------------------------------------------
# Routes — Conversations
# ---------------------------------------------------------------------------

@app.get("/api/conversations")
async def list_conversations():
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return {"conversations": [dict(r) for r in rows]}


@app.post("/api/conversations")
async def create_conversation():
    conv_id = str(uuid.uuid4())
    now = time.time()
    conn = _get_db()
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conv_id, "New Chat", now, now),
    )
    conn.commit()
    conn.close()
    return {"id": conv_id, "title": "New Chat", "created_at": now, "updated_at": now}


@app.get("/api/conversations/{conv_id}/messages")
async def get_messages(conv_id: str):
    conn = _get_db()
    rows = conn.execute(
        "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    conn.close()
    return {"messages": [dict(r) for r in rows]}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    conn = _get_db()
    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ---------------------------------------------------------------------------
# Routes — Chat (agent loop with tool calling)
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    conv_id = body.get("conversation_id")
    user_msg = body.get("message", "").strip()
    model = body.get("model", DEFAULT_MODEL)
    system_prompt = body.get("system_prompt", SYSTEM_PROMPT_DEFAULT)
    image_base64 = body.get("image")  # Optional webcam/screenshot image

    if not user_msg:
        raise HTTPException(400, "message is required")

    conn = _get_db()
    now = time.time()

    # Auto-create conversation if needed
    if not conv_id:
        conv_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conv_id, user_msg[:80], now, now),
        )
    else:
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))

    # Save user message
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conv_id, "user", user_msg, now),
    )
    conn.commit()

    # Build message history
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    conn.close()

    # Inject memory context into system prompt
    memories = get_recent_memories(10)
    memory_context = ""
    if memories:
        memory_lines = "\n".join(f"- [{m['category']}]: {m['content']}" for m in memories)
        memory_context = f"\n\nYour memories about the user:\n{memory_lines}"

    messages = [{"role": "system", "content": system_prompt + memory_context}]
    for r in rows:
        msg = {"role": r["role"], "content": r["content"]}
        messages.append(msg)

    # If image attached, add it to the last user message
    if image_base64:
        messages[-1]["images"] = [image_base64]

    # Auto-title
    user_msgs = [m for m in messages if m["role"] == "user"]
    if len(user_msgs) == 1:
        conn2 = _get_db()
        conn2.execute("UPDATE conversations SET title = ? WHERE id = ?", (user_msg[:80], conv_id))
        conn2.commit()
        conn2.close()

    registry: ToolRegistry = request.app.state.tool_registry

    async def _agent_stream():
        """Agent loop: send to Ollama, handle tool calls, stream final response."""
        nonlocal messages

        tool_defs = registry.get_ollama_tools()
        iteration = 0

        while iteration < MAX_TOOL_ITERATIONS:
            iteration += 1

            # Call Ollama (non-streaming for tool detection)
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                try:
                    resp = await client.post(
                        f"{OLLAMA_BASE}/api/chat",
                        json={
                            "model": model,
                            "messages": messages,
                            "tools": tool_defs if tool_defs else None,
                            "stream": False,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.ConnectError:
                    yield f"data: {json.dumps({'error': 'Ollama is not running. Start with: ollama serve'})}\n\n"
                    return
                except Exception as exc:
                    yield f"data: {json.dumps({'error': str(exc)})}\n\n"
                    return

            response_msg = data.get("message", {})
            tool_calls = response_msg.get("tool_calls", [])

            if not tool_calls:
                # No tool calls — stream the text response
                content = response_msg.get("content", "")
                if content:
                    # Save assistant response
                    conn3 = _get_db()
                    conn3.execute(
                        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                        (conv_id, "assistant", content, time.time()),
                    )
                    conn3.commit()
                    conn3.close()

                    # Stream in chunks for typing effect
                    chunk_size = 8
                    for i in range(0, len(content), chunk_size):
                        chunk = content[i : i + chunk_size]
                        yield f"data: {json.dumps({'token': chunk, 'conversation_id': conv_id})}\n\n"

                yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id})}\n\n"
                return

            # Handle tool calls
            messages.append(response_msg)

            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args = tc.get("function", {}).get("arguments", {})

                # Stream tool call notification to UI
                yield f"data: {json.dumps({'tool_call': {'name': fn_name, 'arguments': fn_args}})}\n\n"

                logger.info(f"Agent calling tool: {fn_name}({json.dumps(fn_args)[:200]})")

                # Execute the tool
                result = await registry.execute_tool(fn_name, fn_args)

                # If screenshot returned image, auto-analyze it
                if fn_name == "take_screenshot" and result.get("success") and result.get("image_base64"):
                    vision_result = await registry.execute_tool(
                        "analyze_image",
                        {"image_base64": result["image_base64"], "question": "Describe what you see on the screen."},
                    )
                    result["vision_analysis"] = vision_result.get("result", "")
                    result_text = f"Screenshot captured and analyzed:\n{vision_result.get('result', 'No analysis available.')}"
                else:
                    result_text = result.get("result", result.get("error", "No result"))

                # Stream tool result notification to UI
                yield f"data: {json.dumps({'tool_result': {'name': fn_name, 'result': result_text[:500]}})}\n\n"

                # Add tool result to conversation
                messages.append({
                    "role": "tool",
                    "content": result_text,
                })

        # Safety: max iterations reached
        yield f"data: {json.dumps({'token': '[Agent reached max iterations]', 'conversation_id': conv_id})}\n\n"
        yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id})}\n\n"

    return StreamingResponse(_agent_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
