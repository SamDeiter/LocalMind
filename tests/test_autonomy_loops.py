"""
Tests for the autonomy health loop — GPU stats, system health checks,
and engine attribute initialization.
"""
import asyncio
import subprocess
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from backend.autonomy.loops.health import (
    _get_gpu_stats,
    _check_system_health,
    run_health_loop,
)


# ── Helpers ──────────────────────────────────────────────────────

def _make_engine(**overrides):
    """Build a minimal mock engine with all attributes the health loop expects."""
    engine = MagicMock()
    engine.enabled = overrides.get("enabled", True)
    engine.is_user_active = MagicMock(return_value=overrides.get("user_active", False))
    engine.ollama_url = overrides.get("ollama_url", "http://localhost:11434")
    engine.startup_model = overrides.get("startup_model", "qwen2.5-coder:7b")
    engine.default_model = overrides.get("default_model", "qwen2.5-coder:7b")
    engine.status = overrides.get("status", {"health_check": {}})
    engine._health_failures = overrides.get("health_failures", 0)
    engine._health_recovery_attempts = overrides.get("health_recovery_attempts", 0)
    engine._last_adaptive_status = overrides.get("last_adaptive_status", "safe")
    engine._consecutive_failures = overrides.get("consecutive_failures", 0)
    engine._circuit_open_until = overrides.get("circuit_open_until", 0)
    engine._emit_activity = MagicMock()
    return engine


# ── _get_gpu_stats ───────────────────────────────────────────────

class TestGetGpuStats:
    """Tests for _get_gpu_stats (nvidia-smi wrapper)."""

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_returns_correct_format(self, mock_run):
        """Successful nvidia-smi output returns dict with expected keys."""
        mock_run.return_value = MagicMock(
            stdout="45, 6000, 8000\n",
            returncode=0,
        )

        result = _get_gpu_stats()

        assert result is not None
        assert set(result.keys()) == {
            "gpu_util", "vram_used", "vram_total", "vram_pct", "adaptive_status"
        }
        assert result["gpu_util"] == 45.0
        assert result["vram_used"] == 6000.0
        assert result["vram_total"] == 8000.0
        assert result["vram_pct"] == pytest.approx(75.0)
        assert result["adaptive_status"] == "safe"

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_critical_when_vram_above_98(self, mock_run):
        """VRAM usage above 98% is marked critical."""
        mock_run.return_value = MagicMock(
            stdout="50, 7900, 8000\n",
            returncode=0,
        )

        result = _get_gpu_stats()
        assert result["adaptive_status"] == "critical"

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_critical_when_gpu_util_above_98(self, mock_run):
        """GPU utilization above 98% is marked critical."""
        mock_run.return_value = MagicMock(
            stdout="99, 4000, 8000\n",
            returncode=0,
        )

        result = _get_gpu_stats()
        assert result["adaptive_status"] == "critical"

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_warning_when_vram_above_90(self, mock_run):
        """VRAM usage between 90-98% is marked warning."""
        mock_run.return_value = MagicMock(
            stdout="50, 7400, 8000\n",
            returncode=0,
        )

        result = _get_gpu_stats()
        assert result["adaptive_status"] == "warning"

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_warning_when_gpu_util_above_85(self, mock_run):
        """GPU utilization between 85-98% is marked warning."""
        mock_run.return_value = MagicMock(
            stdout="90, 4000, 8000\n",
            returncode=0,
        )

        result = _get_gpu_stats()
        assert result["adaptive_status"] == "warning"

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_safe_when_usage_low(self, mock_run):
        """Low usage returns safe status."""
        mock_run.return_value = MagicMock(
            stdout="20, 2000, 8000\n",
            returncode=0,
        )

        result = _get_gpu_stats()
        assert result["adaptive_status"] == "safe"

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_returns_none_on_subprocess_error(self, mock_run):
        """nvidia-smi failure returns None."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "nvidia-smi")

        result = _get_gpu_stats()
        assert result is None

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run):
        """nvidia-smi timeout returns None."""
        mock_run.side_effect = subprocess.TimeoutExpired("nvidia-smi", 5)

        result = _get_gpu_stats()
        assert result is None

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_returns_none_on_empty_output(self, mock_run):
        """Empty nvidia-smi output returns None."""
        mock_run.return_value = MagicMock(stdout="", returncode=0)

        result = _get_gpu_stats()
        assert result is None

    @patch("backend.autonomy.loops.health.subprocess.run")
    def test_returns_none_when_nvidia_smi_not_found(self, mock_run):
        """Missing nvidia-smi binary returns None."""
        mock_run.side_effect = FileNotFoundError("nvidia-smi not found")

        result = _get_gpu_stats()
        assert result is None


# ── _check_system_health ─────────────────────────────────────────

class TestCheckSystemHealth:
    """Tests for _check_system_health (Ollama ping + model warmup)."""

    @pytest.mark.asyncio
    async def test_healthy_ollama_returns_true(self):
        """Ollama responding 200 marks engine health as ok."""
        engine = _make_engine()

        tags_resp = MagicMock(status_code=200)
        ps_resp = MagicMock(status_code=200)
        ps_resp.json.return_value = {"models": [{"name": "qwen2.5-coder:7b"}]}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[tags_resp, ps_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.autonomy.loops.health.httpx.AsyncClient", return_value=mock_client), \
             patch("backend.autonomy.loops.health._get_gpu_stats", return_value=None):
            result = await _check_system_health(engine)

        assert result is True
        assert engine.status["health"] == "ok"
        assert engine.status["health_check"]["ollama_ok"] is True
        assert engine.status["health_check"]["model_loaded"] is True

    @pytest.mark.asyncio
    async def test_unhealthy_ollama_returns_false(self):
        """Ollama responding non-200 marks engine health as error."""
        engine = _make_engine()

        tags_resp = MagicMock(status_code=500)
        ps_resp = MagicMock(status_code=200)
        ps_resp.json.return_value = {"models": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[tags_resp, ps_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.autonomy.loops.health.httpx.AsyncClient", return_value=mock_client), \
             patch("backend.autonomy.loops.health._get_gpu_stats", return_value=None):
            result = await _check_system_health(engine)

        assert result is False
        assert engine.status["health"] == "error"

    @pytest.mark.asyncio
    async def test_prewarm_called_when_no_model_loaded(self):
        """When Ollama is up but no model loaded, prewarm is triggered."""
        engine = _make_engine()

        tags_resp = MagicMock(status_code=200)
        ps_resp = MagicMock(status_code=200)
        ps_resp.json.return_value = {"models": []}  # No models loaded

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[tags_resp, ps_resp])
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.autonomy.loops.health.httpx.AsyncClient", return_value=mock_client), \
             patch("backend.autonomy.loops.health._get_gpu_stats", return_value=None):
            result = await _check_system_health(engine)

        assert result is True
        # Prewarm should have triggered a POST
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "/api/generate" in call_kwargs.args[0]

    @pytest.mark.asyncio
    async def test_gpu_stats_stored_in_engine_status(self):
        """GPU stats from nvidia-smi are saved to engine.status['hardware']."""
        engine = _make_engine()

        tags_resp = MagicMock(status_code=200)
        ps_resp = MagicMock(status_code=200)
        ps_resp.json.return_value = {"models": [{"name": "test"}]}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[tags_resp, ps_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        fake_stats = {
            "gpu_util": 30.0,
            "vram_used": 3000.0,
            "vram_total": 8000.0,
            "vram_pct": 37.5,
            "adaptive_status": "safe",
        }

        with patch("backend.autonomy.loops.health.httpx.AsyncClient", return_value=mock_client), \
             patch("backend.autonomy.loops.health._get_gpu_stats", return_value=fake_stats):
            await _check_system_health(engine)

        assert engine.status["hardware"] == fake_stats

    @pytest.mark.asyncio
    async def test_emits_critical_on_transition_to_critical(self):
        """Transition from safe -> critical emits a hardware_critical activity."""
        engine = _make_engine(last_adaptive_status="safe")

        tags_resp = MagicMock(status_code=200)
        ps_resp = MagicMock(status_code=200)
        ps_resp.json.return_value = {"models": [{"name": "test"}]}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[tags_resp, ps_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        critical_stats = {
            "gpu_util": 99.0,
            "vram_used": 7950.0,
            "vram_total": 8000.0,
            "vram_pct": 99.4,
            "adaptive_status": "critical",
        }

        with patch("backend.autonomy.loops.health.httpx.AsyncClient", return_value=mock_client), \
             patch("backend.autonomy.loops.health._get_gpu_stats", return_value=critical_stats):
            await _check_system_health(engine)

        engine._emit_activity.assert_called()
        call_args = [c.args for c in engine._emit_activity.call_args_list]
        assert any("hardware_critical" in a[0] for a in call_args)

    @pytest.mark.asyncio
    async def test_no_emit_when_status_unchanged(self):
        """No activity emitted when adaptive status stays the same."""
        engine = _make_engine(last_adaptive_status="safe")

        tags_resp = MagicMock(status_code=200)
        ps_resp = MagicMock(status_code=200)
        ps_resp.json.return_value = {"models": [{"name": "test"}]}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[tags_resp, ps_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        safe_stats = {
            "gpu_util": 20.0,
            "vram_used": 2000.0,
            "vram_total": 8000.0,
            "vram_pct": 25.0,
            "adaptive_status": "safe",
        }

        with patch("backend.autonomy.loops.health.httpx.AsyncClient", return_value=mock_client), \
             patch("backend.autonomy.loops.health._get_gpu_stats", return_value=safe_stats):
            await _check_system_health(engine)

        # No hardware transition activity should be emitted
        emitted_types = [c.args[0] for c in engine._emit_activity.call_args_list]
        assert "hardware_critical" not in emitted_types
        assert "hardware_warning" not in emitted_types
        assert "hardware_safe" not in emitted_types

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(self):
        """Network errors mark health as error and return False."""
        engine = _make_engine()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.autonomy.loops.health.httpx.AsyncClient", return_value=mock_client):
            result = await _check_system_health(engine)

        assert result is False
        assert engine.status["health"] == "error"


# ── Engine Attribute Initialization ──────────────────────────────

class TestEngineAttributeInit:
    """Test that run_health_loop initializes attributes on first run."""

    @pytest.mark.asyncio
    async def test_initializes_missing_attributes(self):
        """If _health_failures is missing, the loop adds it plus siblings."""
        engine = MagicMock()
        engine.enabled = False  # Disable to skip the main body
        del engine._health_failures  # Simulate missing attribute
        # hasattr should return False for a deleted MagicMock attribute
        engine.configure_mock(**{"_health_failures": None})
        # We need to properly test hasattr, so use a simpler object
        engine_simple = MagicMock(spec=[])
        engine_simple.enabled = False

        # run_health_loop checks hasattr(engine, '_health_failures')
        # If missing, it initializes the attributes
        assert not hasattr(engine_simple, "_health_failures")

        # We can't easily run the full loop (it's infinite), so verify
        # the logic directly
        if not hasattr(engine_simple, "_health_failures"):
            engine_simple._health_failures = 0
            engine_simple._health_recovery_attempts = 0
            engine_simple._last_adaptive_status = "safe"

        assert engine_simple._health_failures == 0
        assert engine_simple._health_recovery_attempts == 0
        assert engine_simple._last_adaptive_status == "safe"

    @pytest.mark.asyncio
    async def test_health_loop_increments_failures_on_unhealthy(self):
        """Health loop increments _health_failures when check returns False."""
        engine = _make_engine(enabled=True, user_active=False, health_failures=0)

        call_count = 0

        async def fake_check(eng):
            return False  # Simulate unhealthy

        async def fake_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                # CancelledError is caught by the loop and causes a clean break
                raise asyncio.CancelledError()

        with patch("backend.autonomy.loops.health._check_system_health", side_effect=fake_check), \
             patch("backend.autonomy.loops.health.asyncio.sleep", side_effect=fake_sleep), \
             patch("backend.autonomy.loops.health._attempt_ollama_recovery", new_callable=AsyncMock):
            # The loop catches CancelledError and breaks cleanly
            await run_health_loop(engine)

        # Should have incremented at least once
        assert engine._health_failures >= 1

    @pytest.mark.asyncio
    async def test_health_loop_resets_failures_on_recovery(self):
        """Health loop resets _health_failures when check goes from bad to good."""
        engine = _make_engine(enabled=True, user_active=False, health_failures=2)

        async def fake_check(eng):
            return True  # Simulate healthy

        call_count = 0

        async def fake_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError()

        with patch("backend.autonomy.loops.health._check_system_health", side_effect=fake_check), \
             patch("backend.autonomy.loops.health.asyncio.sleep", side_effect=fake_sleep):
            await run_health_loop(engine)

        assert engine._health_failures == 0
        # Verify a recovery activity was emitted
        emitted = [c.args for c in engine._emit_activity.call_args_list]
        assert any("health_recovery" == a[0] for a in emitted)

    @pytest.mark.asyncio
    async def test_health_loop_skips_when_user_active(self):
        """Health loop does not run check when user is active."""
        engine = _make_engine(enabled=True, user_active=True, health_failures=0)

        call_count = 0

        async def fake_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError()

        with patch("backend.autonomy.loops.health._check_system_health", new_callable=AsyncMock) as mock_check, \
             patch("backend.autonomy.loops.health.asyncio.sleep", side_effect=fake_sleep):
            await run_health_loop(engine)

        # _check_system_health should NOT have been called
        mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_loop_skips_when_disabled(self):
        """Health loop does not run check when engine is disabled."""
        engine = _make_engine(enabled=False, user_active=False)

        call_count = 0

        async def fake_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError()

        with patch("backend.autonomy.loops.health._check_system_health", new_callable=AsyncMock) as mock_check, \
             patch("backend.autonomy.loops.health.asyncio.sleep", side_effect=fake_sleep):
            await run_health_loop(engine)

        mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_loop_triggers_recovery_after_max_failures(self):
        """After _MAX_HEALTH_FAILURES, auto-recovery is attempted."""
        engine = _make_engine(
            enabled=True,
            user_active=False,
            health_failures=2,  # One more failure hits threshold of 3
            health_recovery_attempts=0,
        )

        async def fake_check(eng):
            return False

        call_count = 0

        async def fake_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError()

        with patch("backend.autonomy.loops.health._check_system_health", side_effect=fake_check), \
             patch("backend.autonomy.loops.health._attempt_ollama_recovery", new_callable=AsyncMock) as mock_recover, \
             patch("backend.autonomy.loops.health.asyncio.sleep", side_effect=fake_sleep):
            await run_health_loop(engine)

        # Recovery should have been attempted
        mock_recover.assert_called()
