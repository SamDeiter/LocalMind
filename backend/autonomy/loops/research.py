import asyncio
import logging
import time
from ..utils import log_event

logger = logging.getLogger("localmind.autonomy.research")

_self_discovery_done = False


async def _maybe_run_self_discovery():
    """Run AI self-discovery once if no profile exists yet."""
    global _self_discovery_done
    if _self_discovery_done:
        return
    _self_discovery_done = True
    try:
        from backend.autonomy.self_discovery import get_self_discovery
        service = get_self_discovery()
        if service.get_profile() is None:
            logger.info("No AI profile found — running initial self-discovery")
            await service.discover(force=False)
            logger.info("Initial self-discovery complete")
    except Exception as exc:
        logger.warning("Self-discovery during research loop failed (non-fatal): %s", exc)


async def _maybe_run_skill_learning(engine):
    """Trigger one learning cycle per day when the system is idle."""
    if engine.is_user_active():
        return
    try:
        from backend.autonomy.skill_learner import get_skill_learner

        learner = get_skill_learner()
        stats = learner.get_stats()
        # Learn at most once per 24 hours
        if time.time() - stats.get("last_learned", 0) > 86400:
            logger.info("Idle + no recent learning — starting skill learning cycle")
            result = await learner.learn()
            topic = result.get("topic", "unknown")
            applied = result.get("applied", False)
            logger.info(
                "Skill learning complete: topic=%s, applied=%s", topic, applied
            )
    except Exception as exc:
        logger.warning("Skill learning during research loop failed (non-fatal): %s", exc)


async def run_auto_research_loop(engine):
    """Every 2h (or when bored): perform automated web research to find new problems."""
    await asyncio.sleep(60)
    while True:
        try:
            # One-time self-discovery on first research cycle
            await _maybe_run_self_discovery()

            if engine.enabled and not engine.is_user_active():
                await engine._run_auto_research()
                engine.status.research.last_run = time.time()

                # After research, optionally trigger a learning cycle when idle
                await _maybe_run_skill_learning(engine)

            await asyncio.sleep(2 * 3600)  # 2 hours
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Auto-research loop error: {exc}")
            await asyncio.sleep(3600)
