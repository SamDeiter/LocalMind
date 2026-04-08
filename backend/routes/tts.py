"""TTS API routes — synthesise text, list voices, manage cache."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import FileResponse

logger = logging.getLogger("localmind.routes.tts")

router = APIRouter(prefix="/api/tts", tags=["tts"])


# ── Request / response models ──────────────────────────────────────

class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to synthesise")
    voice: Optional[str] = Field(None, description="Voice model name (e.g. en_US-lessac-medium)")
    speed: float = Field(1.0, gt=0.1, le=5.0, description="Playback speed multiplier")


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/status")
async def tts_status():
    """Check whether TTS is available on this server."""
    from backend.tts.piper_service import TTS_ENABLED, TTS_DEFAULT_VOICE, get_tts_service

    svc = get_tts_service()
    return {
        "enabled": TTS_ENABLED,
        "available": svc.is_available(),
        "default_voice": TTS_DEFAULT_VOICE,
    }


@router.get("/voices")
async def tts_voices():
    """List voice models available in the voices directory."""
    from backend.tts.piper_service import get_tts_service

    svc = get_tts_service()
    voices = svc.list_voices()
    return {"voices": voices, "count": len(voices)}


@router.post("/synthesize")
async def tts_synthesize(req: SynthesizeRequest):
    """Synthesise text to speech using Piper TTS."""
    from backend.tts.piper_service import get_tts_service

    svc = get_tts_service()
    result = await svc.synthesize(text=req.text, voice=req.voice, speed=req.speed)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("error", "Synthesis failed"))
    return result


@router.get("/audio/{filename}")
async def tts_audio(filename: str):
    """Serve a cached audio WAV file."""
    from backend.tts.piper_service import get_tts_service

    svc = get_tts_service()
    path = svc.get_audio_file(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(
        path=str(path),
        media_type="audio/wav",
        filename=path.name,
    )


@router.post("/cache/clear")
async def tts_cache_clear():
    """Delete all cached TTS audio files."""
    from backend.tts.piper_service import get_tts_service

    svc = get_tts_service()
    cleared = svc.clear_cache()
    return {"ok": True, "cleared": cleared}
