import logging
from fastapi import APIRouter, Request, Response
from backend import notifications, gemini_client

router = APIRouter(prefix="/api")
logger = logging.getLogger("localmind.routes.settings")

@router.get("/settings/notifications")
async def get_notification_settings():
    """Return current SMS/Text notification settings."""
    return notifications.get_settings()

@router.post("/settings/notifications")
async def update_notification_settings(settings: dict):
    """Update phone, carrier, and enable/disable status."""
    notifications.save_settings(settings)
    return {"status": "ok", "settings": settings}

@router.get("/settings/cloud")
async def get_cloud_settings():
    """Return current cloud configuration (Gemini)."""
    settings = gemini_client.get_settings()
    if settings.get("api_key"):
        key = settings["api_key"]
        settings["api_key"] = "*" * (len(key) - 4) + key[-4:] if len(key) > 4 else "****"
    return settings

@router.post("/settings/cloud")
async def update_cloud_settings(settings: dict):
    """Update Gemini API key."""
    incoming_key = settings.get("api_key", "")
    if incoming_key.startswith("****"):
        current = gemini_client.get_settings()
        if current.get("api_key"):
            settings["api_key"] = current["api_key"]
    gemini_client.save_settings(settings)
    return {"status": "ok"}

@router.post("/cloud/test")
async def test_gemini():
    """Test Gemini connection."""
    try:
        response = await gemini_client.generate("Hello, are you there? Reply with exactly 'GEMINI_ONLINE'", scrub=False)
        return {"success": "GEMINI_ONLINE" in response, "message": response}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/notifications/test")
async def test_notification():
    """Send a test SMS to verify configuration."""
    success = await notifications.send_sms("LocalMind: Test alert for SMS notifications")
    return {"success": success}

@router.post("/notifications/sms/webhook")
async def sms_webhook(request: Request):
    """Handle incoming SMS from Twilio and route to LocalMind chat."""
    form_data = await request.form()
    sender = form_data.get("From", "")
    body = form_data.get("Body", "").strip()
    if not sender or not body:
        return Response(content="Invalid message", status_code=400)
    logger.info(f"Incoming SMS from {sender}: {body}")
    try:
        from backend.routes.chat import handle_sms_chat
        response_text = await handle_sms_chat(sender, body)
        from twilio.twiml.messaging_response import MessagingResponse
        twiml = MessagingResponse()
        twiml.message(response_text)
        return Response(content=str(twiml), media_type="application/xml")
    except Exception as e:
        logger.error(f"Failed to process SMS chat: {e}")
        return Response(content="Internal Error", status_code=500)
