import asyncio
import logging
import subprocess
import time
import httpx
from ..config import CIRCUIT_BREAKER_THRESHOLD

logger = logging.getLogger("localmind.autonomy.health")

# Track consecutive health failures for auto-recovery
_health_failures = 0
_MAX_HEALTH_FAILURES = 3
_recovery_attempts = 0

async def run_health_loop(engine):
    """Every 30s: ping Ollama, pre-warm model, auto-recover if down."""
    global _health_failures, _recovery_attempts
    await asyncio.sleep(5)
    while True:
        try:
            if engine.enabled and not engine.is_user_active():
                healthy = await _check_system_health(engine)

                if healthy:
                    if _health_failures > 0:
                        engine._emit_activity(
                            "health_recovery",
                            f"💚 System recovered after {_health_failures} failures"
                        )
                        _health_failures = 0
                        _recovery_attempts = 0
                else:
                    _health_failures += 1
                    engine._emit_activity(
                        "health_warning",
                        f"⚠️ Health check failed ({_health_failures}/{_MAX_HEALTH_FAILURES})"
                    )

                    # Auto-recovery: try to restart Ollama
                    if _health_failures >= _MAX_HEALTH_FAILURES and _recovery_attempts < 3:
                        _recovery_attempts += 1
                        engine._emit_activity(
                            "health_recovery_attempt",
                            f"🔄 Auto-recovery attempt {_recovery_attempts}/3 — restarting Ollama..."
                        )
                        await _attempt_ollama_recovery(engine)

                # Auto-recovery if circuit breaker is stuck
                if engine._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    if time.time() > engine._circuit_open_until:
                        logger.info("Circuit breaker cooldown expired -- resetting")
                        engine._consecutive_failures = 0
                        engine._emit_activity(
                            "health_recovery",
                            "🔓 Circuit breaker reset — resuming operations"
                        )

            await asyncio.sleep(30)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Health loop error: {exc}")
            await asyncio.sleep(30)

async def _check_system_health(engine):
    """Ping Ollama and check if a model is loaded. Returns True if healthy."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{engine.ollama_url}/api/tags")
            ollama_ok = resp.status_code == 200

            ps_resp = await client.get(f"{engine.ollama_url}/api/ps")
            ps_data = ps_resp.json()
            models_loaded = len(ps_data.get("models", [])) > 0

            engine.status["health_check"] = {
                "last_run": time.time(),
                "ollama_ok": ollama_ok,
                "model_loaded": models_loaded,
                "consecutive_failures": _health_failures,
                "recovery_attempts": _recovery_attempts,
            }
            engine.status["health"] = "ok" if ollama_ok else "error"

            # Pre-warm model if Ollama is up but no model loaded
            if ollama_ok and not models_loaded:
                logger.info("No model in VRAM — pre-warming...")
                await _prewarm_model(engine, client)

            return ollama_ok
    except Exception as e:
        logger.warning(f"Health check failed: {e}")
        engine.status["health_check"]["ollama_ok"] = False
        engine.status["health"] = "error"
        return False

async def _prewarm_model(engine, client):
    """Send a minimal prompt to load the model into VRAM."""
    try:
        engine._emit_activity("health_prewarm", "🔥 Pre-warming model into VRAM...")
        await client.post(
            f"{engine.ollama_url}/api/generate",
            json={
                "model": engine.startup_model or engine.default_model,
                "prompt": "Hello",
                "stream": False,
            },
            timeout=60.0,
        )
        engine.status["health_check"]["model_loaded"] = True
        logger.info("✅ Model pre-warmed successfully")
    except Exception as e:
        logger.warning(f"Model pre-warm failed: {e}")

async def _attempt_ollama_recovery(engine):
    """Try to restart the Ollama serve process."""
    try:
        # On Windows, start ollama serve in background
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        logger.info("🔄 Ollama restart command sent — waiting 10s for startup...")
        await asyncio.sleep(10)

        # Verify it came back
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{engine.ollama_url}/api/tags")
                if resp.status_code == 200:
                    engine._emit_activity(
                        "health_recovery",
                        "💚 Ollama auto-recovered successfully!"
                    )
                    logger.info("✅ Ollama auto-recovery succeeded")
                    return
        except Exception:
            pass

        engine._emit_activity(
            "health_warning",
            "⚠️ Ollama auto-recovery failed — manual intervention may be needed"
        )
    except FileNotFoundError:
        logger.error("Ollama binary not found in PATH")
        engine._emit_activity(
            "health_warning",
            "⚠️ Cannot auto-recover: ollama not found in PATH"
        )
    except Exception as e:
        logger.error(f"Ollama recovery failed: {e}")
