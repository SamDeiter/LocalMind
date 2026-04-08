"""Background learning loop — triggers self-discovery and skill learning."""
import asyncio
import logging
import time

logger = logging.getLogger("localmind.learning.loop")

_self_discovery_done = False

async def run_learning_loop():
    """Background loop: self-discovery once, then skill learning daily."""
    await asyncio.sleep(120)  # Wait 2 min for server to settle

    global _self_discovery_done

    while True:
        try:
            # One-time self-discovery
            if not _self_discovery_done:
                try:
                    from backend.autonomy.self_discovery import get_self_discovery
                    svc = get_self_discovery()
                    if svc.get_profile() is None:
                        logger.info("Running initial self-discovery...")
                        await svc.discover()
                        logger.info("Self-discovery complete")
                    _self_discovery_done = True
                except Exception as exc:
                    logger.warning("Self-discovery failed: %s", exc)

            # Skill learning (once per cycle)
            try:
                from backend.autonomy.skill_learner import get_skill_learner
                learner = get_skill_learner()
                stats = learner.get_stats()
                last = stats.get("last_learned", 0)
                if time.time() - last > 86400:  # Once per day
                    logger.info("Running skill learning cycle...")
                    await learner.learn()
                    logger.info("Skill learning cycle complete")
            except Exception as exc:
                logger.warning("Skill learning failed: %s", exc)

            await asyncio.sleep(2 * 3600)  # Check every 2 hours
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Learning loop error: %s", exc)
            await asyncio.sleep(3600)
