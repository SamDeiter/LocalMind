"""
transcription.py - Local audio transcription via Whisper (Phase F2)
===================================================================
Wraps OpenAI's open-source Whisper package for fully-local audio -> text.
Used by Report Forge to ingest recorded interview sessions.

# requires: openai-whisper  (install via `pip install openai-whisper`; also needs ffmpeg on PATH)

Contract:
    transcribe_audio(file_path, model_name="base") -> {
        "text":       str,
        "duration_s": float,
        "language":   str,
    }

Notes:
  - Whisper accepts a file path directly — we do NOT stream bytes.
  - Model is loaded lazily and cached per-process (loading is expensive).
  - `base` is the default: ~74M params, CPU-friendly, runs locally on any Mac/PC.
  - Raises ImportError with an install hint if the package isn't present.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("localmind.report_forge.transcription")

_MODEL_CACHE: dict[str, object] = {}

_INSTALL_HINT = (
    "openai-whisper is not installed. Install it with:\n"
    "    pip install openai-whisper\n"
    "and ensure `ffmpeg` is available on PATH."
)


def _load_whisper(model_name: str):
    """Import whisper lazily so the module can be imported even without the dep."""
    try:
        import whisper  # type: ignore
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc

    if model_name not in _MODEL_CACHE:
        logger.info("Loading Whisper model: %s", model_name)
        _MODEL_CACHE[model_name] = whisper.load_model(model_name)
    return _MODEL_CACHE[model_name]


def _transcribe_sync(file_path: str, model_name: str) -> dict:
    """Blocking transcription — run inside a thread from the async wrapper."""
    model = _load_whisper(model_name)
    # fp16 defaults to True on GPU; Whisper auto-detects. We let it decide.
    result = model.transcribe(file_path)
    text = (result.get("text") or "").strip()
    language = result.get("language") or "unknown"

    # Whisper doesn't always return duration; approximate from last segment end.
    duration_s = 0.0
    segments = result.get("segments") or []
    if segments:
        last = segments[-1]
        duration_s = float(last.get("end", 0.0) or 0.0)

    return {
        "text": text,
        "duration_s": duration_s,
        "language": language,
    }


async def transcribe_audio(
    file_path: str,
    model_name: str = "base",
) -> dict:
    """Transcribe a local audio file with Whisper.

    Args:
        file_path: absolute path to a Whisper-supported audio file (wav/mp3/m4a/...).
        model_name: Whisper model size — "tiny", "base", "small", "medium", "large".
                    Default "base" is fastest useful local option.

    Returns:
        {"text": str, "duration_s": float, "language": str}

    Raises:
        ImportError:       if `openai-whisper` is not installed.
        FileNotFoundError: if the audio file is missing.
    """
    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    t0 = time.monotonic()
    # Whisper is CPU/GPU heavy — run off the event loop.
    result = await asyncio.to_thread(_transcribe_sync, file_path, model_name)
    elapsed = time.monotonic() - t0
    logger.info(
        "Transcribed %s (%.1fs audio) in %.1fs using whisper:%s",
        file_path, result["duration_s"], elapsed, model_name,
    )
    return result
