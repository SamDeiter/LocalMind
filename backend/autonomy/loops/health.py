import asyncio
import logging
import subprocess
import time
import httpx
from ..config import CIRCUIT_BREAKER_THRESHOLD

logger = logging.getLogger("localmind.autonomy.health")

# Track consecutive health failures for auto-recovery
_MAX_HEALTH_FAILURES = 3

async def run_health_loop(engine):
    """Every 30s: ping Ollama, pre-warm model, auto-recover if down."""
    if not hasattr(engine, '_health_failures'):
        engine._health_failures = 0
        engine._health_recovery_attempts = 0
        engine._last_adaptive_status = "safe"
    await asyncio.sleep(5)
    while True:
        try:
            if engine.enabled and not engine.is_user_active():
                healthy = await _check_system_health(engine)

                if healthy:
                    if engine._health_failures > 0:
                        engine._emit_activity(
                            "health_recovery",
                            f"💚 System recovered after {engine._health_failures} failures"
                        )
                        engine._health_failures = 0
                        engine._health_recovery_attempts = 0
                        engine._last_adaptive_status = "safe"
                else:
                    engine._health_failures += 1
                    engine._emit_activity(
                        "health_warning",
                        f"⚠️ Health check failed ({engine._health_failures}/{_MAX_HEALTH_FAILURES})"
                    )

                    # Auto-recovery: try to restart Ollama
                    if engine._health_failures >= _MAX_HEALTH_FAILURES and engine._health_recovery_attempts < 3:
                        engine._health_recovery_attempts += 1
                        engine._emit_activity(
                            "health_recovery_attempt",
                            f"🔄 Auto-recovery attempt {engine._health_recovery_attempts}/3 — restarting Ollama..."
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

            await asyncio.sleep(15)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Health loop error: {exc}")
            await asyncio.sleep(30)

def _get_gpu_stats():
    """Query nvidia-smi for GPU utilization and VRAM usage."""
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=5
        )
        line = res.stdout.strip()
        if not line:
            return None
        parts = [p.strip() for p in line.split(",")]
        used = float(parts[1])
        total = float(parts[2])
        util = float(parts[0])
        pct = (used / total) * 100
        
        status = "safe"
        if pct > 98 or util > 98:
            status = "critical"
        elif pct > 90 or util > 85:
            status = "warning"
            
        return {
            "gpu_util": util,
            "vram_used": used,
            "vram_total": total,
            "vram_pct": pct,
            "adaptive_status": status
        }
    except Exception:
        return None

async def _check_system_health(engine):
    """Ping Ollama and check if a model is loaded. Returns True if healthy."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{engine.ollama_url}/api/tags")
            ollama_ok = resp.status_code == 200

            ps_resp = await client.get(f"{engine.ollama_url}/api/ps")
            ps_data = ps_resp.json()
            models_loaded = len(ps_data.get("models", [])) > 0

            engine.status["health_check"] = {
                "last_run": time.time(),
                "ollama_ok": ollama_ok,
                "model_loaded": models_loaded,
                "consecutive_failures": engine._health_failures,
                "recovery_attempts": engine._health_recovery_attempts,
            }
            engine.status["health"] = "ok" if ollama_ok else "error"

            # Update GPU/VRAM stats
            stats = _get_gpu_stats()
            if stats:
                engine.status["hardware"] = stats

                # Only emit notification on state TRANSITION (edge-triggered)
                current_status = stats["adaptive_status"]

                if current_status == "critical" and engine._last_adaptive_status != "critical":
                    engine._emit_activity(
                        "hardware_critical",
                        f"🛑 VRAM CRITICAL ({stats['vram_pct']:.1f}%) — Throttling Swarm to prevent crash"
                    )
                elif current_status == "warning" and engine._last_adaptive_status == "safe":
                    engine._emit_activity(
                        "hardware_warning",
                        f"⚠️ VRAM High ({stats['vram_pct']:.1f}%) — Enabling adaptive scaling"
                    )
                elif current_status == "safe" and engine._last_adaptive_status != "safe":
                    engine._emit_activity(
                        "hardware_safe",
                        "💚 VRAM recovered — All systems safe"
                    )

                engine._last_adaptive_status = current_status

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
