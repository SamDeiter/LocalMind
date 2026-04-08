"""
Comprehensive pytest tests for LocalMind health check endpoints.

Covers:
- HealthResult / CheckResult dataclass serialisation
- HealthChecker.check_liveness() — DB accessible / unreachable
- HealthChecker.check_readiness() — Ollama up / down / no models
- HealthChecker.check_deep() — disk, queue depth, VRAM probes
- FastAPI endpoint handler logic (/health, /health/ready, /health/deep)
- Edge cases: missing to_dict, mixed statuses, exception propagation

All tests run WITHOUT external services (no Ollama, no GPU, no real DB).
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.telemetry import CheckResult, HealthChecker, HealthResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def checker():
    """Return a fresh HealthChecker instance for each test."""
    return HealthChecker()


@pytest.fixture
def healthy_result():
    """A HealthResult that is fully healthy with two passing checks."""
    return HealthResult(
        healthy=True,
        checks={
            "db": CheckResult(status="pass", message="SQLite DB accessible", latency_ms=0.5),
            "ollama": CheckResult(status="pass", message="Ollama reachable, 2 model(s)", latency_ms=3.1),
        },
    )


@pytest.fixture
def unhealthy_result():
    """A HealthResult with a failing check."""
    return HealthResult(
        healthy=False,
        checks={
            "db": CheckResult(status="fail", message="DB unreachable: disk I/O error", latency_ms=1.0),
        },
    )


@pytest.fixture
def warn_result():
    """A HealthResult with only warnings (still considered healthy by deep check logic)."""
    return HealthResult(
        healthy=True,
        checks={
            "disk": CheckResult(status="warn", message="Low disk: 0.80 GB free", latency_ms=0.2),
            "vram": CheckResult(status="warn", message="VRAM check unavailable", latency_ms=0.1),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# TestHealthResultFormat
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthResultFormat:
    """Verify HealthResult and CheckResult serialisation."""

    def test_to_dict_returns_healthy_flag(self, healthy_result: HealthResult):
        d = healthy_result.to_dict()
        assert d["healthy"] is True

    def test_to_dict_returns_unhealthy_flag(self, unhealthy_result: HealthResult):
        d = unhealthy_result.to_dict()
        assert d["healthy"] is False

    def test_to_dict_contains_all_check_keys(self, healthy_result: HealthResult):
        d = healthy_result.to_dict()
        assert set(d["checks"].keys()) == {"db", "ollama"}

    def test_to_dict_check_fields(self, healthy_result: HealthResult):
        d = healthy_result.to_dict()
        db_check = d["checks"]["db"]
        assert db_check["status"] == "pass"
        assert db_check["message"] == "SQLite DB accessible"
        assert db_check["latency_ms"] == 0.5

    def test_to_dict_empty_checks(self):
        result = HealthResult(healthy=True, checks={})
        d = result.to_dict()
        assert d == {"healthy": True, "checks": {}}

    def test_to_dict_preserves_warn_status(self, warn_result: HealthResult):
        d = warn_result.to_dict()
        assert d["checks"]["disk"]["status"] == "warn"

    def test_check_result_defaults(self):
        cr = CheckResult(status="pass", message="ok")
        assert cr.latency_ms == 0.0

    def test_health_result_default_checks_empty(self):
        result = HealthResult(healthy=True)
        assert result.checks == {}
        assert result.to_dict() == {"healthy": True, "checks": {}}


# ═══════════════════════════════════════════════════════════════════════════
# TestHealthLiveness
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthLiveness:
    """Tests for HealthChecker.check_liveness()."""

    @pytest.mark.asyncio
    async def test_liveness_db_accessible(self, checker: HealthChecker):
        """When the DB is accessible, liveness is healthy."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value = None

        with patch("backend.core.telemetry._connect", return_value=mock_conn):
            result = await checker.check_liveness()

        assert result.healthy is True
        assert "db" in result.checks
        assert result.checks["db"].status == "pass"
        mock_conn.execute.assert_called_once_with("SELECT 1")
        mock_conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_liveness_db_unreachable(self, checker: HealthChecker):
        """When _connect raises, liveness fails."""
        with patch(
            "backend.core.telemetry._connect",
            side_effect=Exception("disk I/O error"),
        ):
            result = await checker.check_liveness()

        assert result.healthy is False
        assert result.checks["db"].status == "fail"
        assert "disk I/O error" in result.checks["db"].message

    @pytest.mark.asyncio
    async def test_liveness_db_query_fails(self, checker: HealthChecker):
        """When the DB opens but the query fails, liveness fails."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("database is locked")

        with patch("backend.core.telemetry._connect", return_value=mock_conn):
            result = await checker.check_liveness()

        assert result.healthy is False
        assert result.checks["db"].status == "fail"
        assert "database is locked" in result.checks["db"].message

    @pytest.mark.asyncio
    async def test_liveness_latency_is_numeric(self, checker: HealthChecker):
        mock_conn = MagicMock()
        with patch("backend.core.telemetry._connect", return_value=mock_conn):
            result = await checker.check_liveness()

        assert isinstance(result.checks["db"].latency_ms, float)

    @pytest.mark.asyncio
    async def test_liveness_returns_health_result_type(self, checker: HealthChecker):
        mock_conn = MagicMock()
        with patch("backend.core.telemetry._connect", return_value=mock_conn):
            result = await checker.check_liveness()

        assert isinstance(result, HealthResult)


# ═══════════════════════════════════════════════════════════════════════════
# TestHealthReadiness
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthReadiness:
    """Tests for HealthChecker.check_readiness()."""

    @pytest.mark.asyncio
    async def test_readiness_ollama_with_models(self, checker: HealthChecker):
        """Ollama reachable with models loaded -> healthy."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "qwen3:8b"}, {"name": "llama3:8b"}]
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check_readiness()

        assert result.healthy is True
        assert result.checks["ollama"].status == "pass"
        assert "2 model(s)" in result.checks["ollama"].message

    @pytest.mark.asyncio
    async def test_readiness_ollama_no_models(self, checker: HealthChecker):
        """Ollama reachable but no models -> unhealthy."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"models": []}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check_readiness()

        assert result.healthy is False
        assert result.checks["ollama"].status == "fail"
        assert "no models loaded" in result.checks["ollama"].message

    @pytest.mark.asyncio
    async def test_readiness_ollama_unreachable(self, checker: HealthChecker):
        """Connection error to Ollama -> unhealthy."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check_readiness()

        assert result.healthy is False
        assert result.checks["ollama"].status == "fail"
        assert "unreachable" in result.checks["ollama"].message.lower()

    @pytest.mark.asyncio
    async def test_readiness_ollama_http_error(self, checker: HealthChecker):
        """Ollama returns 500 -> unhealthy."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("500 Internal Server Error")

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check_readiness()

        assert result.healthy is False
        assert result.checks["ollama"].status == "fail"

    @pytest.mark.asyncio
    async def test_readiness_model_names_in_message(self, checker: HealthChecker):
        """Model names appear in the success message."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "deepseek-r1:14b"}]
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check_readiness()

        assert "deepseek-r1:14b" in result.checks["ollama"].message

    @pytest.mark.asyncio
    async def test_readiness_truncates_model_list_at_five(self, checker: HealthChecker):
        """Only the first 5 model names appear even if more are loaded."""
        models = [{"name": f"model-{i}"} for i in range(8)]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"models": models}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check_readiness()

        msg = result.checks["ollama"].message
        assert "8 model(s)" in msg
        # model-5, model-6, model-7 should NOT appear (only first 5 shown)
        assert "model-5" not in msg


# ═══════════════════════════════════════════════════════════════════════════
# TestHealthDeep
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthDeep:
    """Tests for HealthChecker.check_deep()."""

    def _mock_ollama_ps(self, models=None):
        """Return a patched httpx.AsyncClient that returns VRAM data."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"models": models or []}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_deep_all_healthy(self, checker: HealthChecker):
        """Plenty of disk, empty queue, no VRAM -> all pass."""
        # Disk: 50 GB free
        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        # DB: 0 pending jobs
        mock_row = {"cnt": 0}
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=self._mock_ollama_ps()),
        ):
            result = await checker.check_deep()

        assert result.healthy is True
        assert result.checks["disk"].status == "pass"
        assert result.checks["queue_depth"].status == "pass"
        assert result.checks["vram"].status == "pass"

    @pytest.mark.asyncio
    async def test_deep_low_disk_warns(self, checker: HealthChecker):
        """Disk below 1 GB but above 256 MB -> warn, still healthy."""
        mock_usage = MagicMock()
        mock_usage.free = int(0.8 * (1024 ** 3))  # 0.8 GB

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 0}

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=self._mock_ollama_ps()),
        ):
            result = await checker.check_deep()

        assert result.healthy is True  # warn != fail
        assert result.checks["disk"].status == "warn"
        assert "Low disk" in result.checks["disk"].message

    @pytest.mark.asyncio
    async def test_deep_critically_low_disk_fails(self, checker: HealthChecker):
        """Disk below 256 MB -> fail, unhealthy."""
        mock_usage = MagicMock()
        mock_usage.free = int(0.1 * (1024 ** 3))  # 0.1 GB

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 0}

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=self._mock_ollama_ps()),
        ):
            result = await checker.check_deep()

        assert result.healthy is False
        assert result.checks["disk"].status == "fail"
        assert "Critically low" in result.checks["disk"].message

    @pytest.mark.asyncio
    async def test_deep_disk_check_exception(self, checker: HealthChecker):
        """disk_usage raises -> fail."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 0}

        with (
            patch("backend.core.telemetry.shutil.disk_usage", side_effect=OSError("permission denied")),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=self._mock_ollama_ps()),
        ):
            result = await checker.check_deep()

        assert result.healthy is False
        assert result.checks["disk"].status == "fail"
        assert "permission denied" in result.checks["disk"].message

    @pytest.mark.asyncio
    async def test_deep_queue_buildup_warns(self, checker: HealthChecker):
        """Queue between 51-200 pending jobs -> warn."""
        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 75}

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=self._mock_ollama_ps()),
        ):
            result = await checker.check_deep()

        assert result.healthy is True  # warn != fail
        assert result.checks["queue_depth"].status == "warn"
        assert "75" in result.checks["queue_depth"].message

    @pytest.mark.asyncio
    async def test_deep_queue_overflow_fails(self, checker: HealthChecker):
        """Queue above 200 pending jobs -> fail."""
        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 250}

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=self._mock_ollama_ps()),
        ):
            result = await checker.check_deep()

        assert result.healthy is False
        assert result.checks["queue_depth"].status == "fail"
        assert "overflow" in result.checks["queue_depth"].message.lower()

    @pytest.mark.asyncio
    async def test_deep_queue_db_missing_treated_as_pass(self, checker: HealthChecker):
        """If the jobs table does not exist, queue check is pass (skipped)."""
        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("no such table: jobs")

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=self._mock_ollama_ps()),
        ):
            result = await checker.check_deep()

        assert result.checks["queue_depth"].status == "pass"
        assert "skipped" in result.checks["queue_depth"].message.lower()

    @pytest.mark.asyncio
    async def test_deep_vram_with_loaded_models(self, checker: HealthChecker):
        """VRAM check reports loaded model sizes."""
        vram_models = [
            {"name": "qwen3:8b", "size_vram": 4 * (1024 ** 3)},  # 4 GB
        ]

        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 0}

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch(
                "backend.core.telemetry.httpx.AsyncClient",
                return_value=self._mock_ollama_ps(vram_models),
            ),
        ):
            result = await checker.check_deep()

        assert result.checks["vram"].status == "pass"
        assert "qwen3:8b" in result.checks["vram"].message
        assert "4096MB" in result.checks["vram"].message

    @pytest.mark.asyncio
    async def test_deep_vram_ollama_unreachable_warns(self, checker: HealthChecker):
        """If /api/ps is unreachable, VRAM check warns (non-fatal)."""
        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 0}

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await checker.check_deep()

        # VRAM warn is non-fatal so overall should still be healthy
        assert result.healthy is True
        assert result.checks["vram"].status == "warn"
        assert "unavailable" in result.checks["vram"].message.lower()

    @pytest.mark.asyncio
    async def test_deep_no_running_models_vram_pass(self, checker: HealthChecker):
        """No models loaded in VRAM -> pass with informational message."""
        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 0}

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=self._mock_ollama_ps([])),
        ):
            result = await checker.check_deep()

        assert result.checks["vram"].status == "pass"
        assert "No models" in result.checks["vram"].message

    @pytest.mark.asyncio
    async def test_deep_healthy_is_true_when_only_warns(self, checker: HealthChecker):
        """Deep check: warn != fail, so healthy should be True."""
        # Low disk (warn) + Ollama unreachable for VRAM (warn)
        mock_usage = MagicMock()
        mock_usage.free = int(0.8 * (1024 ** 3))

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 0}

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await checker.check_deep()

        assert result.healthy is True
        assert result.checks["disk"].status == "warn"
        assert result.checks["vram"].status == "warn"


# ═══════════════════════════════════════════════════════════════════════════
# TestHealthEdgeCases
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthEdgeCases:
    """Edge cases for health result handling and endpoint fallback logic."""

    def test_result_without_to_dict_method(self):
        """
        The endpoint handlers have a fallback:
            result.to_dict() if hasattr(result, 'to_dict') else {"healthy": result.healthy}
        Ensure the fallback path works when to_dict is absent.
        """

        @dataclass
        class BareResult:
            healthy: bool

        bare = BareResult(healthy=True)
        assert not hasattr(bare, "to_dict")

        # Simulate the endpoint fallback logic
        output = bare.to_dict() if hasattr(bare, "to_dict") else {"healthy": bare.healthy}
        assert output == {"healthy": True}

    def test_result_without_to_dict_unhealthy(self):
        """Fallback path with unhealthy result."""

        @dataclass
        class BareResult:
            healthy: bool

        bare = BareResult(healthy=False)
        output = bare.to_dict() if hasattr(bare, "to_dict") else {"healthy": bare.healthy}
        assert output == {"healthy": False}

    def test_health_result_has_to_dict(self):
        """Confirm the real HealthResult always has to_dict."""
        result = HealthResult(healthy=True)
        assert hasattr(result, "to_dict")
        assert callable(result.to_dict)

    @pytest.mark.asyncio
    async def test_liveness_result_has_to_dict(self):
        """Liveness result from the checker has to_dict."""
        checker = HealthChecker()
        mock_conn = MagicMock()
        with patch("backend.core.telemetry._connect", return_value=mock_conn):
            result = await checker.check_liveness()
        assert hasattr(result, "to_dict")
        d = result.to_dict()
        assert "healthy" in d
        assert "checks" in d

    @pytest.mark.asyncio
    async def test_endpoint_logic_with_to_dict(self, healthy_result: HealthResult):
        """Simulate the endpoint handler when to_dict exists."""
        output = (
            healthy_result.to_dict()
            if hasattr(healthy_result, "to_dict")
            else {"healthy": healthy_result.healthy}
        )
        assert output["healthy"] is True
        assert "db" in output["checks"]
        assert output["checks"]["db"]["status"] == "pass"

    @pytest.mark.asyncio
    async def test_concurrent_liveness_calls(self):
        """Multiple concurrent liveness calls should not interfere."""
        import asyncio

        checker = HealthChecker()
        mock_conn = MagicMock()

        with patch("backend.core.telemetry._connect", return_value=mock_conn):
            results = await asyncio.gather(
                checker.check_liveness(),
                checker.check_liveness(),
                checker.check_liveness(),
            )

        assert all(r.healthy is True for r in results)
        assert len(results) == 3

    def test_check_result_with_zero_latency(self):
        """CheckResult accepts zero latency."""
        cr = CheckResult(status="pass", message="instant", latency_ms=0.0)
        assert cr.latency_ms == 0.0

    def test_check_result_with_large_latency(self):
        """CheckResult accepts large latency values."""
        cr = CheckResult(status="fail", message="timeout", latency_ms=30000.0)
        assert cr.latency_ms == 30000.0

    def test_health_result_mixed_statuses(self):
        """A mix of pass and fail yields unhealthy in the standard check."""
        result = HealthResult(
            healthy=False,
            checks={
                "a": CheckResult(status="pass", message="ok"),
                "b": CheckResult(status="fail", message="bad"),
                "c": CheckResult(status="warn", message="meh"),
            },
        )
        d = result.to_dict()
        assert d["healthy"] is False
        assert d["checks"]["a"]["status"] == "pass"
        assert d["checks"]["b"]["status"] == "fail"
        assert d["checks"]["c"]["status"] == "warn"

    @pytest.mark.asyncio
    async def test_deep_vram_uses_size_field_as_fallback(self):
        """When size_vram is missing, the checker falls back to 'size'."""
        checker = HealthChecker()

        vram_models = [{"name": "test-model", "size": 2 * (1024 ** 3)}]  # 2 GB

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"models": vram_models}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 0}

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await checker.check_deep()

        assert "test-model" in result.checks["vram"].message
        assert "2048MB" in result.checks["vram"].message

    @pytest.mark.asyncio
    async def test_readiness_models_key_missing_from_response(self):
        """If the Ollama response lacks 'models' key, treat as empty."""
        checker = HealthChecker()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}  # no "models" key

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client):
            result = await checker.check_readiness()

        assert result.healthy is False
        assert result.checks["ollama"].status == "fail"

    @pytest.mark.asyncio
    async def test_deep_queue_row_none_treated_as_zero(self):
        """If fetchone returns None, pending count defaults to 0."""
        checker = HealthChecker()

        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"models": []}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await checker.check_deep()

        assert result.checks["queue_depth"].status == "pass"
        assert "0 pending" in result.checks["queue_depth"].message

    @pytest.mark.asyncio
    async def test_deep_multiple_vram_models_summed(self):
        """Multiple loaded models have their VRAM summed in the message."""
        checker = HealthChecker()

        vram_models = [
            {"name": "model-a", "size_vram": 1024 * (1024 ** 2)},  # 1024 MB
            {"name": "model-b", "size_vram": 2048 * (1024 ** 2)},  # 2048 MB
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"models": vram_models}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_usage = MagicMock()
        mock_usage.free = 50 * (1024 ** 3)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"cnt": 0}

        with (
            patch("backend.core.telemetry.shutil.disk_usage", return_value=mock_usage),
            patch("backend.core.telemetry._connect", return_value=mock_conn),
            patch("backend.core.telemetry.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await checker.check_deep()

        msg = result.checks["vram"].message
        assert "3072MB" in msg
        assert "model-a" in msg
        assert "model-b" in msg


# ═══════════════════════════════════════════════════════════════════════════
# TestEndpointHandlerLogic
# ═══════════════════════════════════════════════════════════════════════════

class TestEndpointHandlerLogic:
    """
    Test the FastAPI endpoint handler logic by calling the handler
    functions directly with a mocked health_checker.
    """

    @pytest.mark.asyncio
    async def test_health_liveness_endpoint_returns_dict(self):
        """The /health endpoint returns the to_dict() output."""
        mock_result = HealthResult(
            healthy=True,
            checks={"db": CheckResult(status="pass", message="ok", latency_ms=0.3)},
        )
        mock_checker = AsyncMock()
        mock_checker.check_liveness.return_value = mock_result

        with patch("backend.server.health_checker", mock_checker):
            from backend.server import health_liveness
            response = await health_liveness()

        assert response["healthy"] is True
        assert response["checks"]["db"]["status"] == "pass"

    @pytest.mark.asyncio
    async def test_health_readiness_endpoint_returns_dict(self):
        """The /health/ready endpoint returns the to_dict() output."""
        mock_result = HealthResult(
            healthy=False,
            checks={"ollama": CheckResult(status="fail", message="down", latency_ms=5.0)},
        )
        mock_checker = AsyncMock()
        mock_checker.check_readiness.return_value = mock_result

        with patch("backend.server.health_checker", mock_checker):
            from backend.server import health_readiness
            response = await health_readiness()

        assert response["healthy"] is False
        assert response["checks"]["ollama"]["status"] == "fail"

    @pytest.mark.asyncio
    async def test_health_deep_endpoint_returns_dict(self):
        """The /health/deep endpoint returns the to_dict() output."""
        mock_result = HealthResult(
            healthy=True,
            checks={
                "disk": CheckResult(status="pass", message="ok", latency_ms=0.1),
                "queue_depth": CheckResult(status="pass", message="ok", latency_ms=0.2),
                "vram": CheckResult(status="pass", message="ok", latency_ms=0.3),
            },
        )
        mock_checker = AsyncMock()
        mock_checker.check_deep.return_value = mock_result

        with patch("backend.server.health_checker", mock_checker):
            from backend.server import health_deep
            response = await health_deep()

        assert response["healthy"] is True
        assert len(response["checks"]) == 3

    @pytest.mark.asyncio
    async def test_endpoint_fallback_when_no_to_dict(self):
        """If the result object lacks to_dict, endpoints return minimal dict."""

        @dataclass
        class MinimalResult:
            healthy: bool

        mock_checker = AsyncMock()
        mock_checker.check_liveness.return_value = MinimalResult(healthy=True)

        with patch("backend.server.health_checker", mock_checker):
            from backend.server import health_liveness
            response = await health_liveness()

        assert response == {"healthy": True}
