import asyncio
import logging
import time
from ..utils import log_event

logger = logging.getLogger("localmind.autonomy.research")

async def run_auto_research_loop(engine):
    """Every 2h (or when bored): perform automated web research to find new problems."""
    await asyncio.sleep(60)
    while True:
        try:
            if engine.enabled and not engine.is_user_active():
                await engine._run_auto_research()
                engine.status["research"]["last_run"] = time.time()
            
            await asyncio.sleep(2 * 3600)  # 2 hours
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Auto-research loop error: {exc}")
            await asyncio.sleep(3600)
