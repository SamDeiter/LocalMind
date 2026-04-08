"""Piper TTS service — fast, local neural text-to-speech via the piper CLI."""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path

from backend.config import WORKSPACE_ROOT

logger = logging.getLogger("localmind.tts.piper")

# ── Piper config ────────────────────────────────────────────────────
PIPER_BIN = os.getenv("PIPER_BIN", "piper")  # Path to piper executable
PIPER_VOICES_DIR = Path(
    os.getenv("PIPER_VOICES_DIR", str(WORKSPACE_ROOT / "piper_voices"))
)
TTS_CACHE_DIR = WORKSPACE_ROOT / "tts_cache"
TTS_ENABLED = os.getenv("TTS_ENABLED", "false").lower() == "true"
TTS_DEFAULT_VOICE = os.getenv("TTS_DEFAULT_VOICE", "en_US-lessac-medium")
TTS_MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "5000"))


class PiperTTSService:
    """Wraps the Piper CLI to provide async text-to-speech synthesis with
    SHA-256 caching so identical requests are served from disk."""

    def __init__(self) -> None:
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        PIPER_VOICES_DIR.mkdir(parents=True, exist_ok=True)
        self.cache_dir = TTS_CACHE_DIR
        self.voices_dir = PIPER_VOICES_DIR
        self.default_voice = TTS_DEFAULT_VOICE
        self.max_chars = TTS_MAX_CHARS
        logger.info(
            "PiperTTSService initialised  cache=%s  voices=%s",
            self.cache_dir,
            self.voices_dir,
        )

    # ── availability ────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True when the piper binary is found on PATH."""
        return shutil.which(PIPER_BIN) is not None

    # ── voice listing ───────────────────────────────────────────────

    def list_voices(self) -> list[dict]:
        """Scan PIPER_VOICES_DIR for *.onnx model files and return metadata."""
        voices: list[dict] = []
        if not self.voices_dir.exists():
            return voices

        for onnx_path in sorted(self.voices_dir.rglob("*.onnx")):
            name = onnx_path.stem  # e.g. en_US-lessac-medium
            language = name.split("-")[0] if "-" in name else "unknown"
            quality = name.rsplit("-", 1)[-1] if "-" in name else "unknown"
            size_mb = round(onnx_path.stat().st_size / (1024 * 1024), 1)

            # Try to read the companion .json config for richer metadata
            json_path = onnx_path.with_suffix(".onnx.json")
            if not json_path.exists():
                json_path = onnx_path.with_suffix(".json")
            if json_path.exists():
                try:
                    meta = json.loads(json_path.read_text(encoding="utf-8"))
                    language = (
                        meta.get("language", {}).get("code", language)
                        if isinstance(meta.get("language"), dict)
                        else meta.get("language", language)
                    )
                    quality = meta.get("quality", quality)
                except Exception:
                    pass

            voices.append(
                {
                    "name": name,
                    "language": language,
                    "quality": quality,
                    "size_mb": size_mb,
                }
            )

        return voices

    # ── synthesis ───────────────────────────────────────────────────

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
    ) -> dict:
        """Synthesise *text* to a WAV file using the piper CLI.

        Returns a result dict with ``ok``, ``audio_path``, ``duration_ms``,
        ``cached``, and ``voice`` on success, or ``ok=False`` with ``error``.
        """
        # Validate input length
        if len(text) > self.max_chars:
            return {
                "ok": False,
                "error": (
                    f"Text too long ({len(text)} chars). "
                    f"Maximum is {self.max_chars} characters."
                ),
            }

        if not text.strip():
            return {"ok": False, "error": "Text is empty."}

        voice = voice or self.default_voice

        # Check piper availability
        if not self.is_available():
            return {
                "ok": False,
                "error": (
                    "Piper TTS is not installed or not on PATH. "
                    "Install it from https://github.com/rhasspy/piper — "
                    "download the release for your platform, extract it, and "
                    "either add the directory to PATH or set the PIPER_BIN "
                    "env var to the full path of the piper executable."
                ),
            }

        # Check cache
        key = self._cache_key(text, voice, speed)
        cached_path = self.cache_dir / f"{key}.wav"
        if cached_path.exists():
            logger.debug("Cache hit for key=%s", key)
            return {
                "ok": True,
                "audio_path": str(cached_path),
                "filename": cached_path.name,
                "duration_ms": self._wav_duration_ms(cached_path),
                "cached": True,
                "voice": voice,
            }

        # Locate model file
        model_path = self._find_model(voice)
        if model_path is None:
            return {
                "ok": False,
                "error": (
                    f"Voice model '{voice}' not found in {self.voices_dir}. "
                    f"Download .onnx voice models from "
                    f"https://github.com/rhasspy/piper/blob/master/VOICES.md"
                ),
            }

        # Run piper CLI
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                PIPER_BIN,
                "--model", str(model_path),
                "--output_file", str(cached_path),
                "--length_scale", str(round(1.0 / speed, 4)),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(input=text.encode("utf-8"))
        except FileNotFoundError:
            return {
                "ok": False,
                "error": (
                    "Failed to execute piper binary. Ensure it is installed "
                    "and the PIPER_BIN env var points to the correct path."
                ),
            }
        except Exception as exc:
            logger.exception("Piper subprocess failed")
            return {"ok": False, "error": f"Piper process error: {exc}"}

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            logger.error("Piper exited %d: %s", proc.returncode, err_msg)
            # Clean up partial file
            cached_path.unlink(missing_ok=True)
            return {"ok": False, "error": f"Piper error (exit {proc.returncode}): {err_msg}"}

        if not cached_path.exists():
            return {"ok": False, "error": "Piper did not produce an output file."}

        logger.info(
            "Synthesised %d chars in %d ms  voice=%s  file=%s",
            len(text), elapsed_ms, voice, cached_path.name,
        )

        return {
            "ok": True,
            "audio_path": str(cached_path),
            "filename": cached_path.name,
            "duration_ms": self._wav_duration_ms(cached_path),
            "synthesis_ms": elapsed_ms,
            "cached": False,
            "voice": voice,
        }

    # ── audio file retrieval ────────────────────────────────────────

    def get_audio_file(self, filename: str) -> Path | None:
        """Return the full path to a cached audio file, or None."""
        # Sanitise: prevent directory traversal
        safe_name = Path(filename).name
        path = self.cache_dir / safe_name
        if path.exists() and path.is_file():
            return path
        return None

    # ── cache management ────────────────────────────────────────────

    def clear_cache(self) -> int:
        """Delete every file in the cache directory. Returns count deleted."""
        count = 0
        if not self.cache_dir.exists():
            return count
        for f in self.cache_dir.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    count += 1
                except OSError as exc:
                    logger.warning("Could not delete %s: %s", f, exc)
        logger.info("Cleared %d cached audio files", count)
        return count

    # ── internal helpers ────────────────────────────────────────────

    def _cache_key(self, text: str, voice: str, speed: float) -> str:
        """SHA-256 of text+voice+speed → hex string."""
        raw = f"{text}|{voice}|{speed}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _find_model(self, voice: str) -> Path | None:
        """Locate a .onnx model by voice name inside PIPER_VOICES_DIR."""
        # Direct match
        direct = self.voices_dir / f"{voice}.onnx"
        if direct.exists():
            return direct

        # Recursive search
        for p in self.voices_dir.rglob(f"{voice}.onnx"):
            return p

        return None

    @staticmethod
    def _wav_duration_ms(path: Path) -> int:
        """Estimate WAV duration from file header (PCM 16-bit assumed)."""
        try:
            size = path.stat().st_size
            # Standard WAV header is 44 bytes; Piper outputs 16-bit mono 22050 Hz
            data_size = max(size - 44, 0)
            # bytes / (sample_rate * channels * bytes_per_sample) * 1000
            return int(data_size / (22050 * 1 * 2) * 1000)
        except Exception:
            return 0


# ── Module singleton ────────────────────────────────────────────────

_service: PiperTTSService | None = None


def get_tts_service() -> PiperTTSService:
    """Return (or create) the singleton PiperTTSService instance."""
    global _service
    if _service is None:
        _service = PiperTTSService()
    return _service
