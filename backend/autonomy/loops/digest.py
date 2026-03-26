import asyncio
import logging
import time
from backend.digest import generate_digest

logger = logging.getLogger("localmind.autonomy.digest")

async def run_digest_loop(engine):
    """Every 6 hours: generate a daily digest summary."""
    await asyncio.sleep(60)  # Let things warm up first
    while True:
        try:
            if engine.enabled:
                try:
                    digest = generate_digest()
                    if digest:
                        engine._emit_activity("completed", f"📊 Daily digest generated")
                except Exception as exc:
                    logger.warning(f"Digest generation failed: {exc}")
            await asyncio.sleep(6 * 3600)  # 6 hours
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Digest loop error: {exc}")
            await asyncio.sleep(3600)
