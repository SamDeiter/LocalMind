import json
import logging
import os
import sqlite3
import time
import httpx
from pathlib import Path
from fastapi import APIRouter
from backend.config import OLLAMA_BASE_URL
from backend.utils.http_client import get_async_client
from backend.db import DB_PATH

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

router = APIRouter(prefix="/api")
logger = logging.getLogger("localmind.routes.system")

# Module-level start time for uptime calculation
_START_TIME = time.time()

# ⚡ Bolt: Prime psutil CPU calculation at module load.
# This allows us to use interval=None in the route handler for non-blocking
# CPU percentage retrieval, saving ~100ms of event loop block per request.
if _PSUTIL_AVAILABLE:
    psutil.cpu_percent(interval=None)

@router.get("/debug/code-check")
async def code_check():
    """Verify the running code has the latest features loaded."""
    from backend.logic.chat_service import ChatService
    has_infer = hasattr(ChatService, '_infer_tool_call')
    has_escalate = hasattr(ChatService, '_escalate_model')
    # Test synthetic tool call
    test_result = None
    if has_infer:
        test_result = ChatService._infer_tool_call("install the reddit apk")
    return {
        "has_infer_tool_call": has_infer,
        "has_escalate_model": has_escalate,
        "synthetic_test": str(test_result) if test_result else "N/A",
    }

def _get_db_conn() -> sqlite3.Connection:
    """Open a SQLite connection with project-standard pragmas."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _db_size_mb() -> float:
    """Return the database file size in megabytes."""
    try:
        return round(os.path.getsize(str(DB_PATH)) / (1024 * 1024), 2)
    except Exception:
        return 0.0


def _job_counts() -> dict:
    """Query job counts from the jobs table. Returns active and completed counts."""
    active = 0
    completed = 0
    try:
        conn = _get_db_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE status = 'running'"
        ).fetchone()
        active = row["cnt"] if row else 0
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE status = 'completed'"
        ).fetchone()
        completed = row["cnt"] if row else 0
        conn.close()
    except Exception:
        pass
    return {"active_jobs": active, "total_jobs_completed": completed}


@router.get("/health")
async def health_check():
    """Check server and Ollama connectivity with enhanced system metrics."""
    # Ollama status
    try:
        client = get_async_client()
        resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2.0)
        ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False

    # System metrics via psutil (graceful fallback)
    cpu_percent = None
    memory_percent = None
    disk_percent = None
    if _PSUTIL_AVAILABLE:
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            memory_percent = mem.percent
            disk = psutil.disk_usage("/")
            disk_percent = disk.percent
        except Exception:
            pass

    jobs = _job_counts()

    return {
        "server": True,
        "ollama": ollama_ok,
        "uptime_sec": round(time.time() - _START_TIME, 1),
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "disk_percent": disk_percent,
        "ollama_status": "connected" if ollama_ok else "unreachable",
        "db_size_mb": _db_size_mb(),
        "active_jobs": jobs["active_jobs"],
        "total_jobs_completed": jobs["total_jobs_completed"],
    }

@router.get("/version")
async def get_version():
    """Return the current build version."""
    version_file = Path(__file__).parent.parent.parent / "version.json"
    if version_file.exists():
        try:
            with open(version_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": "unknown", "build": 0}

@router.get("/hardware")
async def hardware_status():
    """Get system and Ollama hardware usage."""
    # ⚡ Bolt: Use interval=None to avoid blocking the event loop for 100ms.
    # Returns the average CPU usage since the last call (or module load).
    cpu_pct = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    system = {
        "cpu_percent": cpu_pct,
        "ram_used_gb": round(mem.used / (1024**3), 1),
        "ram_total_gb": round(mem.total / (1024**3), 1),
        "ram_percent": mem.percent,
    }

    models = []
    try:
        client = get_async_client()
        r = await client.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=2.0)
        data = r.json()
        for m in data.get("models", []):
            models.append({
                "name": m.get("name", "unknown"),
                "size_gb": round(m.get("size", 0) / (1024**3), 1),
                "vram_gb": round(m.get("size_vram", 0) / (1024**3), 1),
                "processor": m.get("details", {}).get("quantization_level", ""),
            })
    except Exception:
        pass

    return {"loaded": len(models) > 0, "models": models, "system": system}

@router.get("/models")
async def list_models():
    """List available Ollama models."""
    try:
        client = get_async_client()
        resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3.0)
        data = resp.json()
        models = [
            {"name": m["name"], "size": m.get("size", 0)}
            for m in data.get("models", [])
        ]
        return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Readiness / Deep Health / Metrics / Alerts endpoints
# ---------------------------------------------------------------------------

@router.get("/health/ready")
async def health_ready():
    """Readiness check: DB accessible and basic services up."""
    checks = {}

    # DB check
    db_ok = False
    try:
        conn = _get_db_conn()
        conn.execute("SELECT 1")
        conn.close()
        db_ok = True
        checks["db"] = "pass"
    except Exception as exc:
        checks["db"] = f"fail: {exc}"

    # Ollama check
    ollama_ok = False
    try:
        client = get_async_client()
        resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2.0)
        ollama_ok = resp.status_code == 200
        checks["ollama"] = "pass" if ollama_ok else "fail"
    except Exception:
        checks["ollama"] = "fail"

    status = "ok" if (db_ok and ollama_ok) else "degraded"
    return {"status": status, "checks": checks}


@router.get("/health/deep")
async def health_deep():
    """Deep health check using the telemetry HealthChecker."""
    from backend.core.telemetry import health_checker

    result = await health_checker.check_deep()
    return result.to_dict() if hasattr(result, "to_dict") else {"healthy": result.healthy}


@router.get("/metrics/summary")
async def metrics_summary():
    """Return job throughput, average duration, error rate, and tokens used.

    Queries both the jobs table and the telemetry metrics table to build a
    comprehensive summary.
    """
    # Job-level stats from the jobs table
    total_jobs = 0
    completed_jobs = 0
    failed_jobs = 0
    running_jobs = 0
    total_cost_cents = 0.0

    try:
        conn = _get_db_conn()
        rows = conn.execute(
            "SELECT status, cost_cents FROM jobs"
        ).fetchall()
        conn.close()

        for row in rows:
            total_jobs += 1
            s = row["status"]
            if s == "completed":
                completed_jobs += 1
            elif s == "failed":
                failed_jobs += 1
            elif s == "running":
                running_jobs += 1
            total_cost_cents += row["cost_cents"] or 0.0
    except Exception:
        pass

    error_rate = round((failed_jobs / total_jobs) * 100, 2) if total_jobs > 0 else 0.0

    # Eval run stats
    eval_total = 0
    eval_avg_score = None
    eval_avg_duration_ms = 0.0
    try:
        conn = _get_db_conn()
        eval_rows = conn.execute(
            "SELECT score, duration_ms FROM eval_runs"
        ).fetchall()
        conn.close()

        scores = []
        durations = []
        for r in eval_rows:
            eval_total += 1
            if r["score"] is not None:
                scores.append(r["score"])
            if r["duration_ms"] is not None:
                durations.append(r["duration_ms"])

        if scores:
            eval_avg_score = round(sum(scores) / len(scores), 3)
        if durations:
            eval_avg_duration_ms = round(sum(durations) / len(durations), 1)
    except Exception:
        pass

    # Telemetry-level metrics (LLM tokens, tool calls, etc.)
    telemetry_summary = {}
    try:
        from backend.core.telemetry import metrics_collector
        telemetry_summary = metrics_collector.get_metrics_summary()
    except Exception:
        pass

    tokens_in = telemetry_summary.get("llm_calls", {}).get("tokens_in", 0)
    tokens_out = telemetry_summary.get("llm_calls", {}).get("tokens_out", 0)
    avg_llm_latency_ms = telemetry_summary.get("llm_calls", {}).get("avg_latency_ms", 0)
    job_completions = telemetry_summary.get("job_completions", {})

    return {
        "jobs": {
            "total": total_jobs,
            "completed": completed_jobs,
            "failed": failed_jobs,
            "running": running_jobs,
            "error_rate_pct": error_rate,
            "total_cost_cents": round(total_cost_cents, 4),
        },
        "throughput": {
            "avg_job_duration_ms": job_completions.get("avg_duration_ms", 0),
            "avg_llm_latency_ms": avg_llm_latency_ms,
        },
        "tokens": {
            "total_in": tokens_in,
            "total_out": tokens_out,
            "total": tokens_in + tokens_out,
        },
        "eval_runs": {
            "total": eval_total,
            "avg_score": eval_avg_score,
            "avg_duration_ms": eval_avg_duration_ms,
        },
        "telemetry": telemetry_summary,
    }


@router.get("/token-estimate")
async def token_estimate(text: str = "", model: str = ""):
    """Return a token-count breakdown for the given text using the heuristic estimator."""
    from backend.core.token_budget import TokenEstimator
    result = TokenEstimator.estimate_prompt_tokens(input_data=text, model_id=model or None)
    return result


@router.get("/alerts/recent")
async def alerts_recent():
    """Return recent alerts from the AlertManager threshold checks."""
    from backend.core.telemetry import alert_manager

    try:
        alerts = alert_manager.check_thresholds()
    except Exception as exc:
        logger.warning("AlertManager.check_thresholds failed: %s", exc)
        alerts = []

    return {"alerts": alerts, "count": len(alerts)}
