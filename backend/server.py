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
import sqlite3
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Configure logging with detailed output
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("localmind")

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

# Import our tool registry for the agent loop
from backend.tools.registry import ToolRegistry

# ── Constants ──────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434"
DB_PATH = Path(__file__).parent / "conversations.db"

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

IMPORTANT:
- When the user shares something personal or a preference, save it to memory. Don't announce it every time — just do it naturally.
- Use recall_memories when context about the user would help your response, especially their preferences and past requests.
- When using tools, briefly mention what you're doing — like a person would. "Let me look that up..." or "I'll save that for next time."
- Be proactive. If you can help more than asked, do it. That's what a good assistant does."""

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
    system_prompt = body.get("system_prompt", "")
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
        logger.info(f"Starting stream_response. Messages count: {len(ollama_messages)}")

        for iteration in range(max_iterations):
            logger.info(f"Agent loop iteration {iteration + 1}/{max_iterations}")
            try:
                ollama_payload = {
                    "model": model,
                    "messages": ollama_messages,
                    "stream": True,
                }
                # Only include tools if we have any
                if tools:
                    ollama_payload["tools"] = tools
                logger.info(f"Sending to Ollama: model={model}, msg_count={len(ollama_messages)}, tools_count={len(tools) if tools else 0}")
                logger.debug(f"Ollama payload keys: {list(ollama_payload.keys())}")

                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                    async with client.stream(
                        "POST",
                        f"{OLLAMA_BASE_URL}/api/chat",
                        json=ollama_payload,
                    ) as response:
                        logger.info(f"Ollama response status: {response.status_code}")
                        if response.status_code != 200:
                            error_body = await response.aread()
                            logger.error(f"Ollama error response: {error_body.decode()}")
                            yield f"data: {json.dumps({'error': f'Ollama returned {response.status_code}: {error_body.decode()}'})}"
                            return

                        # Accumulate the full response to detect tool calls
                        chunk_text = ""
                        tool_calls = []
                        line_count = 0

                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            line_count += 1
                            try:
                                data = json.loads(line)
                                msg = data.get("message", {})

                                # Stream text tokens to frontend immediately
                                content = msg.get("content", "")
                                if content:
                                    chunk_text += content
                                    full_response += content
                                    sse_data = json.dumps({'token': content, 'conversation_id': conversation_id})
                                    logger.debug(f"Streaming token (line {line_count}): {content[:50]}...")
                                    yield f"data: {sse_data}\n\n"

                                # Detect tool calls
                                if msg.get("tool_calls"):
                                    logger.info(f"Tool call detected: {msg['tool_calls']}")
                                    tool_calls.extend(msg["tool_calls"])

                                # Check if done
                                if data.get("done"):
                                    logger.info(f"Ollama stream done. Total lines: {line_count}, text_len: {len(chunk_text)}")

                            except json.JSONDecodeError as e:
                                logger.warning(f"JSON decode error on line {line_count}: {e}, raw: {line[:100]}")
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
                logger.error(f"Stream error: {e}")
                logger.error(traceback.format_exc())
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                break

        # Save full response to DB
        logger.info(f"Stream complete. Full response length: {len(full_response)}")
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
