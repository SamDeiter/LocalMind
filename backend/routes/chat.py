import logging
import json
import traceback
from typing import Optional
from fastapi import APIRouter, Request, Body, HTTPException
from fastapi.responses import StreamingResponse
from backend.logic.chat_service import ChatService
from backend import db

router = APIRouter()
logger = logging.getLogger("localmind.routes.chat")

# These will be initialized by the server on startup or via dependency injection
_chat_service: Optional[ChatService] = None

def init_chat_service(registry, autonomy_engine, metacog_controller):
    global _chat_service
    _chat_service = ChatService(
        db_factory=db.get_db_connection,
        registry=registry,
        autonomy_engine=autonomy_engine,
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
        return {"error": str(e)}

@router.post("/chat/sms")
async def sms_chat(request: Request, body: dict = Body(...)):
    """SMS specific endpoint (can be further refactored into ChatService)."""
    # For now, keeping simple or delegating to a future ChatService method
    # sender = body.get("from")
    # message = body.get("text")
    # ... logic ...
    return {"status": "unimplemented_in_modular_arch"}
