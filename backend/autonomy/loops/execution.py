
import asyncio
import logging
import time
from ..config import CIRCUIT_BREAKER_THRESHOLD

logger = logging.getLogger("localmind.autonomy.execution")

async def run_execution_loop(engine):
    """Every 3 min: pick an approved proposal and execute it."""
    await asyncio.sleep(10)
    while True:
        try:
            try:
                await asyncio.wait_for(
                    engine._manual_execution_event.wait(),
                    timeout=engine._current_backoff,
                )
                engine._manual_execution_event.clear()
            except asyncio.TimeoutError:
                pass

            if engine.enabled and not engine.is_user_active():
                # Circuit breaker check
                if engine._circuit_open_until > time.time():
                    continue

                while True:
                    had_work = await engine._execute_next_proposal()
                    if not had_work:
                        break
                    await asyncio.sleep(5)
                    if not engine.enabled or engine.is_user_active():
                        break
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Execution loop error: {exc}")
            await asyncio.sleep(60)
