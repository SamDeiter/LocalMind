"""
CI monitoring tests for LocalMind enhanced health/metrics API routes.

Covers:
- GET /api/health enhanced fields (uptime_sec, cpu_percent, etc.)
- GET /api/health/ready readiness check
- GET /api/health/deep deep health check via HealthChecker
- GET /api/metrics/summary structured metrics data
- GET /api/alerts/recent alert list
- Uptime increases over time
- Graceful handling when psutil is unavailable

All tests run WITHOUT external services (no Ollama, no GPU, no real DB).
Uses FastAPI TestClient matching existing test patterns.
"""

from __future__ import annotations

import time
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes.system import router, _START_TIME


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with the system router for testing."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client():
    """Return a TestClient wired to a minimal FastAPI app with system routes."""
    app = _make_app()
    return TestClient(app)


@pytest.fixture
def mock_ollama_ok():
    """Mock httpx.AsyncClient so Ollama appears reachable with models."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"models": [{"name": "qwen3:8b"}]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.fixture
def mock_ollama_down():
    """Mock httpx.AsyncClient so Ollama is unreachable."""
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("Connection refused")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.fixture
def mock_db_conn():
    """Mock _get_db_conn returning a MagicMock that handles job queries."""
    conn = MagicMock()
    # Default: SELECT 1 succeeds, job count queries return 0
    def _execute_side_effect(sql, *args):
        result = MagicMock()
        if "SELECT 1" in sql:
            return result
        if "COUNT(*)" in sql and "running" in sql:
            result.fetchone.return_value = {"cnt": 2}
            return result
        if "COUNT(*)" in sql and "completed" in sql:
            result.fetchone.return_value = {"cnt": 10}
            return result
        if "FROM jobs" in sql:
            result.fetchall.return_value = [
                {"status": "completed", "cost_cents": 1.5},
                {"status": "completed", "cost_cents": 2.0},
                {"status": "failed", "cost_cents": 0.5},
                {"status": "running", "cost_cents": 0.0},
            ]
            return result
        if "FROM eval_runs" in sql:
            result.fetchall.return_value = [
                {"score": 0.85, "duration_ms": 1200},
                {"score": 0.92, "duration_ms": 800},
            ]
            return result
        result.fetchone.return_value = {"cnt": 0}
        result.fetchall.return_value = []
        return result

    conn.execute.side_effect = _execute_side_effect
    return conn


# =========================================================================
# TestHealthEndpointEnhanced
# =========================================================================

class TestHealthEndpointEnhanced:
    """Tests for the enhanced GET /api/health endpoint."""

    def test_health_returns_enhanced_fields(self, client, mock_ollama_ok, mock_db_conn):
        """Health response includes uptime_sec, cpu_percent, memory_percent, etc."""
        with (
            patch("backend.routes.system.httpx.AsyncClient", return_value=mock_ollama_ok),
            patch("backend.routes.system._get_db_conn", return_value=mock_db_conn),
            patch("backend.routes.system._PSUTIL_AVAILABLE", True),
            patch("backend.routes.system.psutil") as mock_psutil,
        ):
            mock_psutil.cpu_percent.return_value = 42.5
            mock_mem = MagicMock()
            mock_mem.percent = 65.3
            mock_psutil.virtual_memory.return_value = mock_mem
            mock_disk = MagicMock()
            mock_disk.percent = 55.0
            mock_psutil.disk_usage.return_value = mock_disk

            resp = client.get("/api/health")

        assert resp.status_code == 200
        data = resp.json()

        assert data["server"] is True
        assert "uptime_sec" in data
        assert isinstance(data["uptime_sec"], (int, float))
        assert data["uptime_sec"] >= 0
        assert "cpu_percent" in data
        assert "memory_percent" in data
        assert "disk_percent" in data
        assert "ollama_status" in data
        assert "db_size_mb" in data
        assert "active_jobs" in data
        assert "total_jobs_completed" in data

    def test_health_ollama_connected(self, client, mock_ollama_ok, mock_db_conn):
        """When Ollama is up, ollama_status is 'connected'."""
        with (
            patch("backend.routes.system.httpx.AsyncClient", return_value=mock_ollama_ok),
            patch("backend.routes.system._get_db_conn", return_value=mock_db_conn),
            patch("backend.routes.system._PSUTIL_AVAILABLE", False),
        ):
            resp = client.get("/api/health")

        data = resp.json()
        assert data["ollama"] is True
        assert data["ollama_status"] == "connected"

    def test_health_ollama_unreachable(self, client, mock_ollama_down, mock_db_conn):
        """When Ollama is down, ollama_status is 'unreachable'."""
        with (
            patch("backend.routes.system.httpx.AsyncClient", return_value=mock_ollama_down),
            patch("backend.routes.system._get_db_conn", return_value=mock_db_conn),
            patch("backend.routes.system._PSUTIL_AVAILABLE", False),
        ):
            resp = client.get("/api/health")

        data = resp.json()
        assert data["ollama"] is False
        assert data["ollama_status"] == "unreachable"

    def test_health_handles_missing_psutil(self, client, mock_ollama_ok, mock_db_conn):
        """When psutil is unavailable, cpu/memory/disk are null."""
        with (
            patch("backend.routes.system.httpx.AsyncClient", return_value=mock_ollama_ok),
            patch("backend.routes.system._get_db_conn", return_value=mock_db_conn),
            patch("backend.routes.system._PSUTIL_AVAILABLE", False),
        ):
            resp = client.get("/api/health")

        data = resp.json()
        assert data["cpu_percent"] is None
        assert data["memory_percent"] is None
        assert data["disk_percent"] is None
        # Server should still report healthy
        assert data["server"] is True


# =========================================================================
# TestHealthReady
# =========================================================================

class TestHealthReady:
    """Tests for GET /api/health/ready."""

    def test_ready_returns_ok_when_services_up(self, client, mock_ollama_ok, mock_db_conn):
        """Readiness check returns 'ok' when DB and Ollama are accessible."""
        with (
            patch("backend.routes.system.httpx.AsyncClient", return_value=mock_ollama_ok),
            patch("backend.routes.system._get_db_conn", return_value=mock_db_conn),
        ):
            resp = client.get("/api/health/ready")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["checks"]["db"] == "pass"
        assert data["checks"]["ollama"] == "pass"

    def test_ready_degraded_when_ollama_down(self, client, mock_ollama_down, mock_db_conn):
        """Readiness check returns 'degraded' when Ollama is unreachable."""
        with (
            patch("backend.routes.system.httpx.AsyncClient", return_value=mock_ollama_down),
            patch("backend.routes.system._get_db_conn", return_value=mock_db_conn),
        ):
            resp = client.get("/api/health/ready")

        data = resp.json()
        assert data["status"] == "degraded"
        assert data["checks"]["ollama"] == "fail"

    def test_ready_degraded_when_db_down(self, client, mock_ollama_ok):
        """Readiness returns 'degraded' when DB is inaccessible."""
        with (
            patch("backend.routes.system.httpx.AsyncClient", return_value=mock_ollama_ok),
            patch(
                "backend.routes.system._get_db_conn",
                side_effect=Exception("disk I/O error"),
            ),
        ):
            resp = client.get("/api/health/ready")

        data = resp.json()
        assert data["status"] == "degraded"
        assert "fail" in data["checks"]["db"]


# =========================================================================
# TestHealthDeep
# =========================================================================

class TestHealthDeep:
    """Tests for GET /api/health/deep."""

    def test_deep_returns_detailed_checks(self, client):
        """Deep health check returns a structured response with checks."""
        from backend.core.telemetry import HealthResult, CheckResult

        mock_result = HealthResult(
            healthy=True,
            checks={
                "disk": CheckResult(status="pass", message="Disk OK: 50.0 GB free", latency_ms=0.5),
                "queue_depth": CheckResult(status="pass", message="Queue OK: 0 pending jobs", latency_ms=0.3),
                "vram": CheckResult(status="pass", message="No models currently loaded in VRAM", latency_ms=1.0),
            },
        )

        mock_checker = MagicMock()
        mock_checker.check_deep = AsyncMock(return_value=mock_result)

        with patch("backend.core.telemetry.health_checker", mock_checker):
            resp = client.get("/api/health/deep")

        assert resp.status_code == 200
        data = resp.json()
        assert "healthy" in data
        assert data["healthy"] is True
        assert "checks" in data
        assert "disk" in data["checks"]
        assert "queue_depth" in data["checks"]
        assert "vram" in data["checks"]


# =========================================================================
# TestMetricsSummary
# =========================================================================

class TestMetricsSummary:
    """Tests for GET /api/metrics/summary."""

    def test_metrics_summary_returns_structured_data(self, client, mock_db_conn):
        """Metrics summary returns jobs, throughput, tokens, eval_runs sections."""
        mock_telemetry = {
            "llm_calls": {
                "total": 50,
                "tokens_in": 10000,
                "tokens_out": 5000,
                "avg_latency_ms": 250.0,
                "total_cost_cents": 0.0,
            },
            "tool_calls": {
                "total": 30,
                "success": 28,
                "error": 2,
                "avg_duration_ms": 120.0,
            },
            "job_completions": {
                "total": 4,
                "completed": 3,
                "failed": 1,
                "avg_duration_ms": 5000.0,
                "total_cost_cents": 4.0,
            },
        }

        with (
            patch("backend.routes.system._get_db_conn", return_value=mock_db_conn),
            patch("backend.routes.system.metrics_collector", create=True) as mock_mc,
        ):
            mock_mc.get_metrics_summary.return_value = mock_telemetry
            with patch("backend.core.telemetry.metrics_collector", mock_mc):
                resp = client.get("/api/metrics/summary")

        assert resp.status_code == 200
        data = resp.json()

        # Top-level sections exist
        assert "jobs" in data
        assert "throughput" in data
        assert "tokens" in data
        assert "eval_runs" in data
        assert "telemetry" in data

        # Jobs section has expected keys
        jobs = data["jobs"]
        assert "total" in jobs
        assert "completed" in jobs
        assert "failed" in jobs
        assert "running" in jobs
        assert "error_rate_pct" in jobs

        # Tokens section
        tokens = data["tokens"]
        assert "total_in" in tokens
        assert "total_out" in tokens
        assert "total" in tokens

    def test_metrics_summary_handles_empty_db(self, client):
        """Metrics summary gracefully handles missing tables / empty DB."""
        mock_conn = MagicMock()
        # When jobs table is missing, _job_counts handles it.
        # But get_metrics_summary needs to return a valid aggregate row or None.
        mock_conn.execute.side_effect = [Exception("no such table: jobs"), MagicMock()]
        # Configure the second call (to metrics table) to return None to simulate empty/missing metrics
        mock_conn.execute.return_value.fetchone.return_value = None

        with (
            patch("backend.routes.system._get_db_conn", return_value=mock_conn),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
        ):
            resp = client.get("/api/metrics/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert data["jobs"]["total"] == 0
        assert data["jobs"]["error_rate_pct"] == 0.0
        assert data["tokens"]["total"] == 0


# =========================================================================
# TestAlertsRecent
# =========================================================================

class TestAlertsRecent:
    """Tests for GET /api/alerts/recent."""

    def test_alerts_recent_returns_list(self, client):
        """Alerts endpoint returns a list and count."""
        mock_alerts = [
            {
                "alert_type": "high_failure_rate",
                "message": "Job failure rate 30.0% exceeds threshold 25.0%",
                "details": {"total": 10, "failed": 3, "rate_pct": 30.0},
                "fired_at": "2026-04-08T12:00:00+00:00",
            }
        ]

        with patch("backend.core.telemetry.alert_manager") as mock_am:
            mock_am.check_thresholds.return_value = mock_alerts
            resp = client.get("/api/alerts/recent")

        assert resp.status_code == 200
        data = resp.json()
        assert "alerts" in data
        assert "count" in data
        assert isinstance(data["alerts"], list)
        assert data["count"] == len(data["alerts"])

    def test_alerts_recent_empty_when_no_issues(self, client):
        """Alerts endpoint returns empty list when all SLOs are met."""
        with patch("backend.core.telemetry.alert_manager") as mock_am:
            mock_am.check_thresholds.return_value = []
            resp = client.get("/api/alerts/recent")

        assert resp.status_code == 200
        data = resp.json()
        assert data["alerts"] == []
        assert data["count"] == 0

    def test_alerts_recent_handles_exception(self, client):
        """Alerts endpoint gracefully handles AlertManager errors."""
        with patch("backend.core.telemetry.alert_manager") as mock_am:
            mock_am.check_thresholds.side_effect = Exception("DB locked")
            resp = client.get("/api/alerts/recent")

        assert resp.status_code == 200
        data = resp.json()
        assert data["alerts"] == []
        assert data["count"] == 0


# =========================================================================
# TestUptime
# =========================================================================

class TestUptime:
    """Tests for uptime calculation."""

    def test_uptime_increases_over_time(self, client, mock_ollama_ok, mock_db_conn):
        """Uptime should be a positive number that reflects elapsed time."""
        with (
            patch("backend.routes.system.httpx.AsyncClient", return_value=mock_ollama_ok),
            patch("backend.routes.system._get_db_conn", return_value=mock_db_conn),
            patch("backend.routes.system._PSUTIL_AVAILABLE", False),
        ):
            resp1 = client.get("/api/health")
            data1 = resp1.json()

            # Small delay to ensure uptime increases
            time.sleep(0.05)

            resp2 = client.get("/api/health")
            data2 = resp2.json()

        assert data2["uptime_sec"] >= data1["uptime_sec"]
        assert data1["uptime_sec"] > 0

    def test_uptime_is_numeric(self, client, mock_ollama_ok, mock_db_conn):
        """Uptime value is always a number."""
        with (
            patch("backend.routes.system.httpx.AsyncClient", return_value=mock_ollama_ok),
            patch("backend.routes.system._get_db_conn", return_value=mock_db_conn),
            patch("backend.routes.system._PSUTIL_AVAILABLE", False),
        ):
            resp = client.get("/api/health")

        data = resp.json()
        assert isinstance(data["uptime_sec"], (int, float))
