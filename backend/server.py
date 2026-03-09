"""
LocalMind — Backend Server
A FastAPI server that proxies chat requests to Ollama and manages conversations.
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

OLLAMA_BASE_URL = "http://localhost:11434"
DB_PATH = Path(__file__).parent / "conversations.db"


# ── Database Setup ──────────────────────────────────────────────────────────
def init_db():
    """Initialize the SQLite database for conversation storage."""
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
    """Get a database connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── App Lifecycle ───────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="LocalMind", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API Routes ──────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    """Check if the server and Ollama are running."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
            ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False
    return {"server": True, "ollama": ollama_ok}


@app.get("/api/models")
async def list_models():
    """List all available Ollama models."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10.0)
            data = resp.json()
            models = []
            for m in data.get("models", []):
                models.append({
                    "name": m["name"],
                    "size": m.get("size", 0),
                    "modified_at": m.get("modified_at", ""),
                })
            return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/api/chat")
async def chat(request: Request):
    """Stream a chat response from Ollama."""
    body = await request.json()
    model = body.get("model", "qwen2.5-coder:32b")
    messages = body.get("messages", [])
    conversation_id = body.get("conversation_id")
    system_prompt = body.get("system_prompt", "")

    # Build the messages payload for Ollama
    ollama_messages = []
    if system_prompt:
        ollama_messages.append({"role": "system", "content": system_prompt})
    ollama_messages.extend(messages)

    # Save user message to DB
    if conversation_id and messages:
        user_msg = messages[-1]
        if user_msg.get("role") == "user":
            db = get_db()
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, "user", user_msg["content"], time.time()),
            )
            db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (time.time(), conversation_id),
            )
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

        # Save assistant response to DB
        if conversation_id and full_response:
            db = get_db()
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, "assistant", full_response, time.time()),
            )
            db.commit()
            db.close()

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Conversation CRUD ───────────────────────────────────────────────────────
@app.get("/api/conversations")
async def list_conversations():
    """List all conversations, newest first."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM conversations ORDER BY updated_at DESC"
    ).fetchall()
    db.close()
    return {"conversations": [dict(r) for r in rows]}


@app.post("/api/conversations")
async def create_conversation(request: Request):
    """Create a new conversation."""
    body = await request.json()
    conv_id = str(uuid.uuid4())
    now = time.time()
    db = get_db()
    db.execute(
        "INSERT INTO conversations (id, title, model, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            conv_id,
            body.get("title", "New Conversation"),
            body.get("model", "qwen2.5-coder:32b"),
            body.get("system_prompt", ""),
            now,
            now,
        ),
    )
    db.commit()
    db.close()
    return {"id": conv_id, "title": body.get("title", "New Conversation")}


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Get a conversation with its messages."""
    db = get_db()
    conv = db.execute(
        "SELECT * FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if not conv:
        return {"error": "Conversation not found"}, 404
    messages = db.execute(
        "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    db.close()
    return {
        "conversation": dict(conv),
        "messages": [dict(m) for m in messages],
    }


@app.put("/api/conversations/{conv_id}")
async def update_conversation(conv_id: str, request: Request):
    """Update a conversation title or system prompt."""
    body = await request.json()
    db = get_db()
    if "title" in body:
        db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (body["title"], time.time(), conv_id),
        )
    if "system_prompt" in body:
        db.execute(
            "UPDATE conversations SET system_prompt = ?, updated_at = ? WHERE id = ?",
            (body["system_prompt"], time.time(), conv_id),
        )
    db.commit()
    db.close()
    return {"ok": True}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation and its messages."""
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
