import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Request, Response
from backend import notifications, gemini_client

router = APIRouter(prefix="/api")
logger = logging.getLogger("localmind.routes.settings")

# ── Security: Sensitive fields that should not be exposed in plain text ──
SENSITIVE_FIELDS = ["smtp_pass", "api_key", "twilio_auth_token"]


def _mask_settings(settings: dict) -> dict:
    """Replace sensitive fields with mask characters."""
    masked = settings.copy()
    for field in SENSITIVE_FIELDS:
        if masked.get(field):
            masked[field] = "********"
    return masked


def _preserve_secrets(incoming: dict, current: dict) -> dict:
    """Ensure masked fields in incoming data don't overwrite real values."""
    for field in SENSITIVE_FIELDS:
        # Check for our mask (8 stars) or the common 4-star variant
        val = incoming.get(field)
        if isinstance(val, str) and val.startswith("****"):
            if current.get(field):
                incoming[field] = current[field]
            else:
                incoming.pop(field, None)
    return incoming


# ── User-profile storage paths ───────────────────────────────────────
_WORKSPACE = Path.home() / "LocalMind_Workspace"
_PROFILE_ENC_PATH = _WORKSPACE / "user_profile.enc"
_PROFILE_JSON_PATH = _WORKSPACE / "user_profile.json"


def _get_memory_encryption():
    """Lazy import to avoid circular deps at module load time."""
    try:
        from backend.security.memory_encryption import get_memory_encryption
        return get_memory_encryption()
    except Exception:
        return None


def _save_encrypted_profile(user_id: str, encrypted_blob: str) -> None:
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    data = {"user_id": user_id, "blob": encrypted_blob}
    _PROFILE_ENC_PATH.write_text(json.dumps(data), encoding="utf-8")


def _save_plaintext_profile(user_id: str, profile: dict) -> None:
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    data = {"user_id": user_id, "profile": profile}
    _PROFILE_JSON_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_profile(user_id: str, enc) -> dict | None:
    """Load a user profile, decrypting if necessary."""
    # Try encrypted first
    if _PROFILE_ENC_PATH.exists():
        try:
            data = json.loads(_PROFILE_ENC_PATH.read_text(encoding="utf-8"))
            if data.get("user_id") == user_id and enc:
                decrypted = enc.decrypt(user_id, data["blob"])
                return json.loads(decrypted)
        except Exception as exc:
            logger.warning("Failed to load encrypted profile: %s", exc)

    # Fallback to plaintext
    if _PROFILE_JSON_PATH.exists():
        try:
            data = json.loads(_PROFILE_JSON_PATH.read_text(encoding="utf-8"))
            if data.get("user_id") == user_id:
                return data.get("profile")
        except Exception as exc:
            logger.warning("Failed to load plaintext profile: %s", exc)

    return None


# ── User-profile endpoints ───────────────────────────────────────────

@router.post("/user-profile")
async def save_user_profile(request: Request):
    """Save encrypted user profile from onboarding."""
    data = await request.json()

    profile = {
        "name": data.get("name", ""),
        "role": data.get("role", ""),
        "interests": data.get("interests", []),
        "communication_style": data.get("communication_style", ""),
        "tools": data.get("tools", ""),
        "created_at": time.time(),
    }

    enc = _get_memory_encryption()
    user_id = data.get("user_id", "default")

    if enc:
        encrypted = enc.encrypt(user_id, json.dumps(profile))
        _save_encrypted_profile(user_id, encrypted)
    else:
        _save_plaintext_profile(user_id, profile)

    # Also save individual preferences to MemoryManager for AI context injection
    try:
        from backend.metacognition.memory_manager import MemoryManager
        mm = MemoryManager()
        if profile["name"]:
            mm.propose_preference("user.name", profile["name"], source="explicit")
        if profile["role"]:
            mm.propose_preference("user.role", profile["role"], source="explicit")
        if profile["communication_style"]:
            mm.propose_preference(
                "user.communication_style",
                profile["communication_style"],
                source="explicit",
            )
        if profile["tools"]:
            mm.propose_preference("user.tools", profile["tools"], source="explicit")
        if profile["interests"]:
            mm.propose_preference(
                "user.interests",
                ", ".join(profile["interests"]),
                source="explicit",
            )
    except Exception as exc:
        logger.warning("Failed to save profile preferences: %s", exc)

    return {"status": "ok", "encrypted": enc is not None}


@router.get("/user-profile")
async def get_user_profile(user_id: str = "default"):
    """Return the decrypted user profile (for settings page / re-onboarding)."""
    enc = _get_memory_encryption()
    profile = _load_profile(user_id, enc)
    if profile:
        return {"status": "ok", "profile": profile}
    return {"status": "not_found", "profile": None}

@router.get("/settings/notifications")
async def get_notification_settings():
    """Return current SMS/Text notification settings."""
    settings = notifications.get_settings()
    return _mask_settings(settings)


@router.post("/settings/notifications")
async def update_notification_settings(settings: dict):
    """Update phone, carrier, and enable/disable status."""
    current = notifications.get_settings()
    settings = _preserve_secrets(settings, current)
    notifications.save_settings(settings)
    return {"status": "ok", "settings": _mask_settings(settings)}


@router.get("/settings/cloud")
async def get_cloud_settings():
    """Return current cloud configuration (Gemini)."""
    settings = gemini_client.get_settings()
    return _mask_settings(settings)


@router.post("/settings/cloud")
async def update_cloud_settings(settings: dict):
    """Update Gemini API key."""
    current = gemini_client.get_settings()
    settings = _preserve_secrets(settings, current)
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
