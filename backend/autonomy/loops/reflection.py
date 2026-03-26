import asyncio
import logging
import time
import json
from ..utils import log_event, sample_code_snippets

logger = logging.getLogger("localmind.autonomy.reflection")

async def run_reflection_loop(engine):
    """Every 5-15 min: look at code and suggest improvements."""
    await asyncio.sleep(20)
    while True:
        try:
            # Handle manual trigger or wait for timeout
            try:
                # We use the backoff or interval from engine
                # For now simple placeholder logic for backoff
                interval = 300 if engine.proposals.count_active() < 3 else 900
                await asyncio.wait_for(
                    engine._manual_reflection_event.wait(),
                    timeout=interval,
                )
                engine._manual_reflection_event.clear()
                logger.info("⚡ Executing manual reflection")
            except asyncio.TimeoutError:
                pass

            if engine.enabled and not engine.is_user_active():
                await engine._run_reflection()
                engine.status["reflection"]["last_run"] = time.time()
                
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Reflection loop error: {exc}")
            await asyncio.sleep(300)
