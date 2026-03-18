"""
routes/conversations.py — Conversation CRUD Router
====================================================
Handles all conversation management endpoints:
- List all conversations
- Get conversation details and messages
- Create, update, and delete conversations
- Update system prompts per conversation
- Export conversations as Markdown or JSON

Conversations are stored in SQLite (conversations.db) with two tables:
- conversations: metadata (id, title, model, system_prompt, timestamps)
- messages: individual message records (role, content, timestamp)

Each conversation can have its own system prompt, allowing users
to customize AI behavior per conversation (e.g., "Act as a Python tutor").
"""

import json
import logging
import time

from fastapi import APIRouter, Request, Response

logger = logging.getLogger("localmind.routes.conversations")

# Create router — all endpoints are conversation-related
router = APIRouter(prefix="/api", tags=["conversations"])

# These will be injected by server.py at startup via configure()
_get_db = None
_DEFAULT_SYSTEM_PROMPT = ""


def configure(get_db_func, default_prompt: str):
    """Called by server.py to inject database access and default prompt.
    
    We use dependency injection rather than importing from server.py
    to avoid circular imports. The server owns the DB connection factory
    and the default system prompt.
    """
    global _get_db, _DEFAULT_SYSTEM_PROMPT
    _get_db = get_db_func
    _DEFAULT_SYSTEM_PROMPT = default_prompt


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/conversations")
async def list_conversations():
    """List all conversations, newest first.
    
    Returns conversation metadata (not messages) for the sidebar.
    Sorted by updated_at so recently active conversations appear first.
    """
    db = _get_db()
    rows = db.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    db.close()
    return {"conversations": [dict(r) for r in rows]}


@router.get("/conversations/{conv_id}/messages")
async def get_conversation_messages(conv_id: str):
    """Get all messages for a conversation, ordered chronologically.
    
    Used when the user clicks on a conversation in the sidebar to load
    the full chat history. Messages include role (user/assistant) and content.
    """
    db = _get_db()
    rows = db.execute(
        "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    db.close()
    return {"messages": [dict(r) for r in rows]}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation and all its messages.
    
    Deletes from both tables. Messages are deleted first to respect
    the foreign key constraint, then the conversation record itself.
    """
    db = _get_db()
    db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    db.commit()
    db.close()
    logger.info(f"Deleted conversation: {conv_id}")
    return {"ok": True}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Get conversation metadata including system prompt.
    
    Used by the frontend to load conversation settings and display
    the model name and custom system prompt in the UI.
    """
    db = _get_db()
    row = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    db.close()
    if not row:
        return {"error": "Conversation not found"}
    return dict(row)


@router.put("/conversations/{conv_id}/system-prompt")
async def update_system_prompt(conv_id: str, request: Request):
    """Update the system prompt for a specific conversation.
    
    Allows users to customize AI behavior per conversation.
    Example: "Act as a Python tutor" or "Respond only in Spanish".
    The updated prompt takes effect on the next message in this conversation.
    """
    body = await request.json()
    prompt = body.get("system_prompt", "")
    db = _get_db()
    db.execute(
        "UPDATE conversations SET system_prompt = ?, updated_at = ? WHERE id = ?",
        (prompt, time.time(), conv_id),
    )
    db.commit()
    db.close()
    logger.info(f"Updated system prompt for conversation: {conv_id}")
    return {"ok": True, "system_prompt": prompt}


@router.get("/default-system-prompt")
async def get_default_system_prompt():
    """Return the server's default system prompt.
    
    Used by the frontend to populate the system prompt editor
    when creating a new conversation or resetting to defaults.
    """
    return {"system_prompt": _DEFAULT_SYSTEM_PROMPT}


@router.get("/conversations/{conv_id}/export")
async def export_conversation(conv_id: str, format: str = "md"):
    """Export a conversation as Markdown or JSON.
    
    Supported formats:
    - 'md' (default): Human-readable Markdown with role labels
    - 'json': Machine-readable JSON with all metadata
    
    Both formats include a Content-Disposition header so the browser
    downloads the file rather than displaying it inline.
    """
    db = _get_db()
    conv = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    msgs = db.execute(
        "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    db.close()

    if not conv:
        return {"error": "Conversation not found"}

    if format == "json":
        # JSON export: structured data for import/analysis
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
        # Markdown export: human-readable format for sharing/archiving
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
