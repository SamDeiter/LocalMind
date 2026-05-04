"""
LocalMind scheduler package — time-trigger substrate for Phase F autonomous missions.

Exposes:
  - get_scheduler() -> SchedulerWorker singleton
  - set_engine(engine) -> inject the AutonomyEngine instance used by fire callbacks
  - SchedulerWorker with async start() / stop()

The scheduler wraps APScheduler's AsyncIOScheduler with a SQLAlchemyJobStore
backed by the project SQLite DB.  On startup, it reconciles itself against
the `scheduled_missions` control-plane table, re-adding an APScheduler job
for every enabled row (replace_existing=True so restart is idempotent).

Serialization points — server.py lifespan wires these up.
"""

from .engine import SchedulerWorker, get_scheduler, set_engine

__all__ = ["SchedulerWorker", "get_scheduler", "set_engine"]
