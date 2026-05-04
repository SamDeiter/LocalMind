"""
Overnight research scheduler.

A tiny asyncio loop that, when enabled, fires a random research-lane job at
a configurable interval. Default = disabled. State persists to a JSON
sidecar so it survives restart.

Public API:
  - load_config() / save_config()
  - start_scheduler(loop)    — call once on app startup
  - stop_scheduler()         — call on shutdown
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.research.scheduler")

CONFIG_DIR = Path.home() / ".localmind"
CONFIG_FILE = CONFIG_DIR / "research_scheduler.json"

DEFAULT_CONFIG = {
    "enabled": False,
    "interval_hours": 8,        # next run at last_run_at + interval_hours
    "lane": "random",            # or one of the lane keys
    "last_run_at": 0,            # epoch seconds
    "last_run_job_id": None,
    "last_error": None,
}

_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None
_check_interval_seconds = 60   # how often to re-read config and decide
_min_interval_hours = 1        # never let users set < 1h to avoid abuse


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read scheduler config: %s", exc)
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
    return merged


def save_config(cfg: dict) -> dict:
    """Validate, clamp, and persist scheduler config."""
    out = load_config()
    if "enabled" in cfg:
        out["enabled"] = bool(cfg["enabled"])
    if "interval_hours" in cfg:
        try:
            iv = int(cfg["interval_hours"])
        except (TypeError, ValueError):
            iv = out["interval_hours"]
        out["interval_hours"] = max(_min_interval_hours, min(168, iv))
    if "lane" in cfg:
        lane = (cfg["lane"] or "random").strip().lower()
        out["lane"] = lane
    if "last_run_at" in cfg:
        try:
            out["last_run_at"] = int(cfg["last_run_at"])
        except (TypeError, ValueError):
            pass
    if "last_run_job_id" in cfg:
        out["last_run_job_id"] = cfg["last_run_job_id"]
    if "last_error" in cfg:
        out["last_error"] = cfg["last_error"]
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(out, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.error("Could not persist scheduler config: %s", exc)
    return out


def status_payload() -> dict:
    cfg = load_config()
    next_run_at = (
        cfg["last_run_at"] + cfg["interval_hours"] * 3600
        if cfg.get("last_run_at")
        else None
    )
    return {
        **cfg,
        "next_run_at": next_run_at,
        "running": _task is not None and not _task.done(),
        "min_interval_hours": _min_interval_hours,
    }


async def _fire_one_run() -> None:
    """Pick a lane, call the same code path as POST /api/research/lane,
    and persist the result back into config."""
    cfg = load_config()
    lane = cfg.get("lane") or "random"
    try:
        # Reuse the route handler so we share validation + paper picking.
        from backend.routes.research_routes import run_research_lane
        result = await run_research_lane({"lane": lane})
        save_config({
            "last_run_at": int(time.time()),
            "last_run_job_id": result.get("job_id"),
            "last_error": None,
        })
        logger.info(
            "Scheduler fired lane=%s job=%s paper=%s",
            result.get("lane"),
            result.get("job_id"),
            (result.get("paper") or {}).get("title", "")[:80],
        )
    except Exception as exc:
        logger.warning("Scheduler run failed: %s", exc)
        save_config({
            "last_run_at": int(time.time()),
            "last_error": str(exc)[:240],
        })


async def _loop_body():
    logger.info("Research scheduler loop started")
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            cfg = load_config()
            if cfg.get("enabled"):
                last = cfg.get("last_run_at") or 0
                interval_s = max(_min_interval_hours, int(cfg.get("interval_hours", 8))) * 3600
                if (time.time() - last) >= interval_s:
                    # Slight jitter so multiple instances don't fire in lockstep.
                    await asyncio.sleep(random.uniform(0, 30))
                    if not _stop_event.is_set():
                        await _fire_one_run()
        except Exception as exc:
            logger.exception("Scheduler tick crashed (will keep going): %s", exc)
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=_check_interval_seconds)
        except asyncio.TimeoutError:
            continue
    logger.info("Research scheduler loop stopped")


def start_scheduler() -> None:
    """Idempotent — safe to call from app startup."""
    global _task, _stop_event
    if _task is not None and not _task.done():
        return
    _stop_event = asyncio.Event()
    _task = asyncio.create_task(_loop_body(), name="research_scheduler")


def stop_scheduler() -> None:
    """Signal the loop to exit; do not block."""
    global _stop_event
    if _stop_event is not None:
        _stop_event.set()


async def run_now() -> dict:
    """Force-fire one run regardless of schedule. Returns the run result."""
    await _fire_one_run()
    return status_payload()
