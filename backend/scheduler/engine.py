"""
SchedulerWorker — wraps APScheduler's AsyncIOScheduler to run scheduled
missions out of the scheduled_missions control-plane table.

Contract (consumed by server.py lifespan in a later wiring pass):
    from backend.scheduler import get_scheduler, set_engine
    set_engine(autonomy_engine_instance)
    worker = get_scheduler()
    await worker.start()
    ...
    await worker.stop()

Behavioral guarantees:
  * Uses SQLAlchemyJobStore(sqlite:///<DB_PATH>) with check_same_thread=False
    so APScheduler can safely touch the job store across asyncio tasks.
  * On start(), reconciles — reads every enabled row from scheduled_missions
    and calls add_job(..., replace_existing=True) so a restart never leaves
    stale jobs or misses a trigger.
  * Each fire callback is FIRE-AND-FORGET: it schedules the engine coroutine
    via asyncio.create_task and attaches a done-callback that persists status
    via crud.mark_run.  APScheduler's worker thread is NEVER awaited on the
    engine's long-running mission.
  * coalesce=True + misfire_grace_time=3600 on every add_job — if the process
    was asleep past a scheduled fire, a single make-up fire is allowed within
    an hour, then subsequent missed fires are coalesced.
  * AutonomyEngine and GoalPlanner are imported lazily inside _fire to avoid
    circular imports during module load.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.config import DB_PATH

from . import crud

logger = logging.getLogger("localmind.scheduler.engine")


# ── module-level singletons ────────────────────────────────────────────────
_engine_ref: Any = None          # injected AutonomyEngine instance
_engine_lock = threading.Lock()  # guard set_engine / get_engine

_worker_singleton: Optional["SchedulerWorker"] = None
_worker_lock = threading.Lock()


def set_engine(engine: Any) -> None:
    """Inject the AutonomyEngine instance used by fire callbacks.

    Called by server.py lifespan after the AutonomyEngine has been
    constructed (engine may depend on the swarm coordinator, so this is
    done explicitly rather than via import).
    """
    global _engine_ref
    with _engine_lock:
        _engine_ref = engine
        logger.info("AutonomyEngine injected into scheduler: %s", type(engine).__name__)


def _get_engine() -> Any:
    with _engine_lock:
        return _engine_ref


def get_scheduler() -> "SchedulerWorker":
    """Return the process-wide SchedulerWorker (lazy-constructed)."""
    global _worker_singleton
    with _worker_lock:
        if _worker_singleton is None:
            _worker_singleton = SchedulerWorker()
        return _worker_singleton


# ── fire callback ──────────────────────────────────────────────────────────
# APScheduler's SQLAlchemyJobStore pickles the callable, so we keep the fire
# function at module scope (NOT a method) with only primitive arguments.
def _fire(mission_id: str) -> None:
    """APScheduler fire target for a scheduled mission.

    Runs on APScheduler's AsyncIO scheduler — we must not block here.  We
    kick off the engine mission on the running event loop via
    asyncio.create_task and return immediately so the scheduler is free for
    the next tick.  A done-callback persists status via crud.mark_run.
    """
    # 1. Look up the row.  If disabled or deleted, no-op.
    row = crud.get_mission(mission_id)
    if row is None:
        logger.warning("Scheduled mission %s fired but row is missing — skipping", mission_id)
        return
    if not row.get("enabled"):
        logger.info("Scheduled mission %s fired but is disabled — skipping", mission_id)
        return

    engine = _get_engine()
    if engine is None:
        logger.error(
            "Scheduled mission %s fired but no AutonomyEngine is registered — "
            "call backend.scheduler.set_engine() during startup",
            mission_id,
        )
        crud.mark_run(mission_id, status="error", error="no_engine_registered")
        return

    # 2. Lazy imports — avoid circular deps at module load.
    from backend.autonomy.goal_planner import GoalPlanner  # noqa: WPS433

    goal_text: str = row["goal_text"]

    async def _run() -> Dict[str, Any]:
        planner = GoalPlanner()
        plan = await planner.create_plan(goal_text, {})
        result = await engine.execute_mission({
            "mission_id": mission_id,
            "plan": plan.to_dict(),
        })
        return result

    # 3. Fire-and-forget on the running loop.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # APScheduler's AsyncIOScheduler should guarantee a running loop, but
        # if we somehow ended up on a bare thread, fall back to run+record.
        logger.error("Scheduled mission %s: no running loop in _fire", mission_id)
        crud.mark_run(mission_id, status="error", error="no_running_loop")
        return

    task = loop.create_task(_run(), name=f"scheduled_mission:{mission_id}")

    def _on_done(t: asyncio.Task) -> None:
        if t.cancelled():
            crud.mark_run(mission_id, status="cancelled", error=None)
            logger.info("Scheduled mission %s cancelled", mission_id)
            return
        exc = t.exception()
        if exc is not None:
            crud.mark_run(mission_id, status="error", error=repr(exc))
            logger.exception("Scheduled mission %s failed", mission_id, exc_info=exc)
            return
        crud.mark_run(mission_id, status="success", error=None)
        logger.info("Scheduled mission %s completed successfully", mission_id)

    task.add_done_callback(_on_done)


# ── SchedulerWorker ────────────────────────────────────────────────────────
class SchedulerWorker:
    """Thin async wrapper around APScheduler's AsyncIOScheduler.

    Lifecycle:
        worker = get_scheduler()
        await worker.start()    # initializes scheduler, reconciles jobs
        ...                     # CRUD layer adds/removes/updates missions
        await worker.stop()     # graceful shutdown

    Cron/job management (called from CRUD or routes in a later pass):
        worker.schedule_mission(row)
        worker.unschedule_mission(mission_id)
    """

    JOB_ID_PREFIX = "mission:"

    def __init__(self) -> None:
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._started = False

    # ── internal helpers ───────────────────────────────────────────────
    @staticmethod
    def _job_id(mission_id: str) -> str:
        return f"{SchedulerWorker.JOB_ID_PREFIX}{mission_id}"

    def _build_scheduler(self) -> AsyncIOScheduler:
        jobstore = SQLAlchemyJobStore(
            url=f"sqlite:///{DB_PATH}",
            engine_options={"connect_args": {"check_same_thread": False}},
        )
        scheduler = AsyncIOScheduler(
            jobstores={"default": jobstore},
            timezone=ZoneInfo("UTC"),
        )
        return scheduler

    def _ensure_scheduler(self) -> AsyncIOScheduler:
        if self._scheduler is None:
            self._scheduler = self._build_scheduler()
        return self._scheduler

    # ── public API ─────────────────────────────────────────────────────
    async def start(self) -> None:
        """Start APScheduler and reconcile enabled missions from the DB.

        This is a MUST per the spec — every enabled row in scheduled_missions
        gets re-registered with replace_existing=True so the process can be
        restarted without losing triggers or double-firing.
        """
        if self._started:
            logger.debug("SchedulerWorker.start() called but already started")
            return

        scheduler = self._ensure_scheduler()
        scheduler.start()
        self._started = True
        logger.info("SchedulerWorker started (jobstore=sqlite:///%s)", DB_PATH)

        # Reconcile: re-add every enabled row.
        missions = crud.list_enabled_missions()
        logger.info("Reconciling %d enabled scheduled missions", len(missions))
        for row in missions:
            try:
                self._add_or_replace_job(row)
            except Exception as exc:  # noqa: BLE001 — log + continue, don't fail startup
                logger.exception(
                    "Failed to reconcile scheduled mission %s (%s): %s",
                    row.get("id"),
                    row.get("name"),
                    exc,
                )

    async def stop(self) -> None:
        """Gracefully shut down APScheduler.  Idempotent."""
        if not self._started or self._scheduler is None:
            return
        try:
            # wait=False → let in-flight fire-and-forget tasks continue on
            # the event loop; we only stop dispatching new ones.
            self._scheduler.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error shutting down scheduler: %s", exc)
        self._started = False
        logger.info("SchedulerWorker stopped")

    # ── job management ────────────────────────────────────────────────
    def _add_or_replace_job(self, row: Dict[str, Any]) -> None:
        """Register (or replace) a single APScheduler job for a mission row."""
        scheduler = self._ensure_scheduler()

        mission_id: str = row["id"]
        cron_expr: str = row["cron_expr"]
        tz_name: str = row.get("timezone") or "UTC"

        # Defensive: validate here too.  CRUD validates at write time, but
        # the table could have been populated out-of-band.
        try:
            tz = ZoneInfo(tz_name)
        except Exception as exc:
            raise ValueError(f"Invalid timezone {tz_name!r} on mission {mission_id}") from exc

        trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)

        scheduler.add_job(
            _fire,
            trigger=trigger,
            args=[mission_id],
            id=self._job_id(mission_id),
            name=row.get("name") or mission_id,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logger.info(
            "Scheduled mission %s registered (cron=%r tz=%s)",
            mission_id,
            cron_expr,
            tz_name,
        )

    def schedule_mission(self, row: Dict[str, Any]) -> None:
        """Public: register/replace a job from a scheduled_missions row.

        Safe to call before start() — the job is added to the configured
        jobstore either way; AsyncIOScheduler will pick it up on start.
        """
        self._add_or_replace_job(row)

    def unschedule_mission(self, mission_id: str) -> bool:
        """Public: remove a job by mission id.  Returns True if removed."""
        scheduler = self._ensure_scheduler()
        job_id = self._job_id(mission_id)
        try:
            scheduler.remove_job(job_id)
            logger.info("Scheduled mission %s unregistered", mission_id)
            return True
        except Exception as exc:  # apscheduler.jobstores.base.JobLookupError etc.
            logger.debug("unschedule_mission(%s) no-op: %s", mission_id, exc)
            return False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def scheduler(self) -> Optional[AsyncIOScheduler]:
        """Underlying APScheduler instance (for diagnostics / tests)."""
        return self._scheduler
