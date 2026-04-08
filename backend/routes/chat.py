import logging
import json
import traceback
from typing import Optional
from fastapi import APIRouter, Request, Body, HTTPException
from fastapi.responses import StreamingResponse
from backend.logic.chat_service import ChatService
from backend import db

router = APIRouter(prefix="/api")
logger = logging.getLogger("localmind.routes.chat")

# These will be initialized by the server on startup or via dependency injection
_chat_service: Optional[ChatService] = None

def init_chat_service(registry, metacog_controller):
    global _chat_service
    _chat_service = ChatService(
        db_factory=db.get_db_connection,
        registry=registry,
        metacog_controller=metacog_controller
    )

@router.post("/chat")
async def chat(request: Request, body: dict = Body(...)):
    """Unified chat endpoint delegating to ChatService."""
    if not _chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")
    
    try:
        stream = await _chat_service.handle_chat(body)
        return StreamingResponse(stream, media_type="text/event-stream")
    except Exception as e:
        logger.error(f"Chat error: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat/sms")
async def sms_chat(request: Request, body: dict = Body(...)):
    """SMS chat endpoint — accepts a sender number + text, returns a plain-text reply.

    Expected body: {"from": "+1234567890", "text": "hello"}
    Returns: {"reply": "...", "conversation_id": "..."}
    """
    if not _chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")

    sender = body.get("from", "sms_user")
    message = body.get("text", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Missing 'text' field")

    try:
        # Build a chat request compatible with ChatService
        chat_body = {
            "model": "auto",
            "message": message,
            "conversation_id": body.get("conversation_id"),
            "sms_sender": sender,
        }
        stream = await _chat_service.handle_chat(chat_body)

        # Collect the full streamed response into a single plain-text reply
        full_reply = ""
        conv_id = None
        async for chunk in stream:
            if not chunk.startswith("data: "):
                continue
            raw = chunk[6:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if evt.get("token"):
                full_reply += evt["token"]
            if evt.get("conversation_id"):
                conv_id = evt["conversation_id"]

        return {"reply": full_reply.strip(), "conversation_id": conv_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SMS chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
