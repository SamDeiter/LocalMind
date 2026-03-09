"""
LocalMind — Backend Server
FastAPI server with agent loop, tool-calling, conversation management,
memory toggle, and static frontend serving.

All chat goes through a single /api/chat endpoint. The agent loop
calls Ollama, detects tool_calls in the response, executes them via
the tool registry, and streams results back to the frontend via SSE.
"""

import json
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

# Import our tool registry for the agent loop
from backend.tools.registry import ToolRegistry

# ── Constants ──────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434"
DB_PATH = Path(__file__).parent / "conversations.db"

# Default system prompt sent to the model
DEFAULT_SYSTEM_PROMPT = """You are LocalMind, a powerful local AI assistant. You have access to tools that let you:
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

# Global: learning mode toggle (controlled by the frontend)
learning_enabled = True

# Tool registry (auto-discovers tools from backend/tools/)
registry = ToolRegistry()


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


# ── Chat (Agent Loop with Tool Calling) ─────────────────────────────────
@app.post("/api/chat")
async def chat(request: Request):
    """
    Main chat endpoint. Sends user message to Ollama with tool definitions.
    If the model requests tool calls, executes them and feeds results back.
    Streams all events (tokens, tool calls, tool results) to the frontend via SSE.
    """
    body = await request.json()
    model = body.get("model", "qwen2.5-coder:32b")
    message = body.get("message", "")
    conversation_id = body.get("conversation_id")
    system_prompt = body.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
    image_base64 = body.get("image")  # Optional base64 image from webcam

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
            memories = memory_tool.execute(query=message, n_results=5)
            if "No memories" not in memories:
                memory_context = f"\n\n[REMEMBERED CONTEXT]\n{memories}\n[/REMEMBERED CONTEXT]\n"
                # Inject memory into system prompt
                ollama_messages[0]["content"] += memory_context
    except Exception:
        pass  # Memory not available, continue without it

    # Get tool definitions for Ollama (formatted for the API)
    tools = registry.get_ollama_tools()

    async def stream_response():
        """
        Agent loop: call Ollama → if tool calls, execute → feed result → repeat.
        Stream all events (tokens, tool_call, tool_result, done) as SSE.
        """
        nonlocal ollama_messages
        full_response = ""
        max_iterations = 10  # Safety: prevent infinite tool loops

        for iteration in range(max_iterations):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                    # Call Ollama with streaming
                    response = await client.post(
                        f"{OLLAMA_BASE_URL}/api/chat",
                        json={
                            "model": model,
                            "messages": ollama_messages,
                            "tools": tools if tools else None,
                            "stream": True,
                        },
                    )

                    # Accumulate the full response to detect tool calls
                    chunk_text = ""
                    tool_calls = []

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            msg = data.get("message", {})

                            # Stream text tokens to frontend
                            content = msg.get("content", "")
                            if content:
                                chunk_text += content
                                full_response += content
                                yield f"data: {json.dumps({'token': content, 'conversation_id': conversation_id})}\n\n"

                            # Detect tool calls
                            if msg.get("tool_calls"):
                                tool_calls.extend(msg["tool_calls"])

                        except json.JSONDecodeError:
                            continue

                    # If no tool calls, we're done
                    if not tool_calls:
                        break

                    # Execute each tool call
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        tool_name = func.get("name", "unknown")
                        tool_args = func.get("arguments", {})

                        # Stream tool call event to frontend
                        yield f"data: {json.dumps({'tool_call': {'name': tool_name, 'arguments': tool_args}})}\n\n"

                        # Execute the tool
                        try:
                            # Inject learning_enabled flag for memory tools
                            if tool_name == "save_memory" and not learning_enabled:
                                result = "Learning is paused. Memory not saved."
                            else:
                                result = registry.execute(tool_name, tool_args)
                        except Exception as e:
                            result = f"Error: {str(e)}"

                        # Stream tool result to frontend
                        yield f"data: {json.dumps({'tool_result': {'name': tool_name, 'result': result}})}\n\n"

                        # Add to conversation for next iteration
                        ollama_messages.append({
                            "role": "assistant",
                            "content": chunk_text,
                            "tool_calls": [tc],
                        })
                        ollama_messages.append({
                            "role": "tool",
                            "content": result,
                        })

                    # Reset for next iteration (model will respond to tool results)
                    chunk_text = ""

            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                break

        # Save full response to DB
        if full_response:
            db = get_db()
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, "assistant", full_response, time.time()),
            )
            db.commit()
            db.close()

        # Signal done
        yield f"data: {json.dumps({'done': True, 'conversation_id': conversation_id})}\n\n"

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
