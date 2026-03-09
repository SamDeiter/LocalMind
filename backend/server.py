"""
LocalMind — Backend Server v2
FastAPI server with chat, agent loop, conversation management, and tool calling.
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

from agent import agent_chat_streaming
from model_router import route_model

OLLAMA_BASE_URL = "http://localhost:11434"
DB_PATH = Path(__file__).parent / "conversations.db"


# ── Database Setup ──────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            system_prompt TEXT DEFAULT '',
            working_dir TEXT DEFAULT '',
            is_agent BOOLEAN DEFAULT 0,
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
            tool_data TEXT DEFAULT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── App Lifecycle ───────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="LocalMind", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health & Models ─────────────────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
            ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False
    return {"server": True, "ollama": ollama_ok}


@app.get("/api/models")
async def list_models():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10.0)
            data = resp.json()
            models = [
                {"name": m["name"], "size": m.get("size", 0), "modified_at": m.get("modified_at", "")}
                for m in data.get("models", [])
            ]
            return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/api/route-model")
async def route_model_endpoint(request: Request):
    """Intelligently select the best model for a given message."""
    body = await request.json()
    message = body.get("message", "")
    preferred = body.get("preferred_model", None)
    result = await route_model(message, preferred)
    return result


# ── Standard Chat (No Tools) ───────────────────────────────────────────────
@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model", "qwen2.5-coder:32b")
    messages = body.get("messages", [])
    conversation_id = body.get("conversation_id")
    system_prompt = body.get("system_prompt", "")

    ollama_messages = []
    if system_prompt:
        ollama_messages.append({"role": "system", "content": system_prompt})
    ollama_messages.extend(messages)

    # Save user message
    if conversation_id and messages:
        user_msg = messages[-1]
        if user_msg.get("role") == "user":
            db = get_db()
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, "user", user_msg["content"], time.time()),
            )
            db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time(), conversation_id))
            db.commit()
            db.close()

    async def generate():
        full_response = ""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json={"model": model, "messages": ollama_messages, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            try:
                                chunk = json.loads(line)
                                content = chunk.get("message", {}).get("content", "")
                                if content:
                                    full_response += content
                                    yield f"data: {json.dumps({'content': content})}\n\n"
                                if chunk.get("done", False):
                                    yield f"data: {json.dumps({'done': True})}\n\n"
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

        if conversation_id and full_response:
            db = get_db()
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, "assistant", full_response, time.time()),
            )
            db.commit()
            db.close()

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Agent Chat (With Tools) ────────────────────────────────────────────────
@app.post("/api/agent/chat")
async def agent_chat_endpoint(request: Request):
    """Agentic chat — model can use tools (file ops, terminal, search)."""
    body = await request.json()
    model = body.get("model", "qwen2.5-coder:32b")
    messages = body.get("messages", [])
    conversation_id = body.get("conversation_id")
    system_prompt = body.get("system_prompt", "")
    working_dir = body.get("working_dir", "")
    auto_execute = body.get("auto_execute", True)

    if not working_dir:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': 'No working directory set. Please set a project directory in settings.'})}\n\n"]),
            media_type="text/event-stream",
        )

    # Enhance system prompt for agent mode
    agent_system_prompt = f"""{system_prompt}

You are LocalMind, an agentic AI assistant with access to tools. You can read and write files, run terminal commands, search codebases, and search the web.

WORKING DIRECTORY: {working_dir}

INSTRUCTIONS:
- When asked to build something, first explore the project structure, then create a step-by-step plan, then execute each step.
- Always read existing files before modifying them.
- When running commands, check the output for errors and fix them.
- Be proactive: if you see issues, fix them without being asked.
- For multi-step tasks, explain what you're doing at each step.
- When creating files, always include proper comments and documentation.
"""

    # Save user message
    if conversation_id and messages:
        user_msg = messages[-1]
        if user_msg.get("role") == "user":
            db = get_db()
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, "user", user_msg["content"], time.time()),
            )
            db.execute("UPDATE conversations SET updated_at = ?, is_agent = 1 WHERE id = ?", (time.time(), conversation_id))
            db.commit()
            db.close()

    async def generate():
        full_text = ""
        tool_events = []

        async for event in agent_chat_streaming(
            messages=messages,
            model=model,
            system_prompt=agent_system_prompt,
            working_dir=working_dir,
            auto_execute=auto_execute,
        ):
            # Forward all events to the frontend
            yield f"data: {json.dumps(event)}\n\n"

            if event.get("type") == "content":
                full_text += event.get("content", "")
            elif event.get("type") in ("tool_call", "tool_result"):
                tool_events.append(event)

        # Save assistant response with tool data
        if conversation_id and (full_text or tool_events):
            db = get_db()
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_data, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    "assistant",
                    full_text,
                    json.dumps(tool_events) if tool_events else None,
                    time.time(),
                ),
            )
            db.commit()
            db.close()

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Conversation CRUD ───────────────────────────────────────────────────────
@app.get("/api/conversations")
async def list_conversations():
    db = get_db()
    rows = db.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    db.close()
    return {"conversations": [dict(r) for r in rows]}


@app.post("/api/conversations")
async def create_conversation(request: Request):
    body = await request.json()
    conv_id = str(uuid.uuid4())
    now = time.time()
    db = get_db()
    db.execute(
        "INSERT INTO conversations (id, title, model, system_prompt, working_dir, is_agent, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            conv_id,
            body.get("title", "New Conversation"),
            body.get("model", "qwen2.5-coder:32b"),
            body.get("system_prompt", ""),
            body.get("working_dir", ""),
            body.get("is_agent", False),
            now,
            now,
        ),
    )
    db.commit()
    db.close()
    return {"id": conv_id, "title": body.get("title", "New Conversation")}


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    db = get_db()
    conv = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if not conv:
        return {"error": "Conversation not found"}, 404
    messages = db.execute(
        "SELECT role, content, tool_data, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    db.close()
    return {
        "conversation": dict(conv),
        "messages": [dict(m) for m in messages],
    }


@app.put("/api/conversations/{conv_id}")
async def update_conversation(conv_id: str, request: Request):
    body = await request.json()
    db = get_db()
    updates = []
    params = []
    for field in ("title", "system_prompt", "working_dir", "is_agent"):
        if field in body:
            updates.append(f"{field} = ?")
            params.append(body[field])
    if updates:
        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(conv_id)
        db.execute(f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
    db.close()
    return {"ok": True}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    db = get_db()
    db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    db.commit()
    db.close()
    return {"ok": True}


# ── Serve Frontend ──────────────────────────────────────────────────────────
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
