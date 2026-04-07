"""
Tests for config validation — backend/config.py validate_config().

Since config.py runs validate_config() at import time, we must patch the
module-level globals before calling the function.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Force-import so the module is loaded (its import-time validate_config()
# runs with real defaults, which should pass on any dev machine).
import backend.config as _cfg


# ── Helpers ──────────────────────────────────────────────────────

def _call_validate_config(**overrides):
    """
    Call validate_config() with patched module-level config values.

    Applies sensible defaults so only the override under test needs to
    be specified.
    """
    tmp = Path(tempfile.mkdtemp())
    defaults = {
        "GPU_VRAM_GB": 10,
        "SERVER_PORT": 8000,
        "MAX_CONTEXT_TOKENS": 8192,
        "DEFAULT_CONTEXT_WINDOW": 8192,
        "MAX_AGENT_ITERATIONS": 5,
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "WORKSPACE_ROOT": tmp,
    }
    defaults.update(overrides)

    with patch.object(_cfg, "GPU_VRAM_GB", defaults["GPU_VRAM_GB"]), \
         patch.object(_cfg, "SERVER_PORT", defaults["SERVER_PORT"]), \
         patch.object(_cfg, "MAX_CONTEXT_TOKENS", defaults["MAX_CONTEXT_TOKENS"]), \
         patch.object(_cfg, "DEFAULT_CONTEXT_WINDOW", defaults["DEFAULT_CONTEXT_WINDOW"]), \
         patch.object(_cfg, "MAX_AGENT_ITERATIONS", defaults["MAX_AGENT_ITERATIONS"]), \
         patch.object(_cfg, "OLLAMA_BASE_URL", defaults["OLLAMA_BASE_URL"]), \
         patch.object(_cfg, "WORKSPACE_ROOT", defaults["WORKSPACE_ROOT"]):
        _cfg.validate_config()


# ── Valid Config ─────────────────────────────────────────────────

class TestValidConfig:
    def test_valid_config_passes(self):
        """Default values should pass without SystemExit."""
        _call_validate_config()  # should not raise


# ── Invalid GPU_VRAM_GB ──────────────────────────────────────────

class TestInvalidGpuVram:
    def test_zero_vram_causes_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            _call_validate_config(GPU_VRAM_GB=0)
        assert "GPU_VRAM_GB" in str(exc_info.value)

    def test_negative_vram_causes_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            _call_validate_config(GPU_VRAM_GB=-4)
        assert "GPU_VRAM_GB" in str(exc_info.value)


# ── Invalid PORT ─────────────────────────────────────────────────

class TestInvalidPort:
    def test_zero_port_causes_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            _call_validate_config(SERVER_PORT=0)
        assert "PORT" in str(exc_info.value)

    def test_port_too_high_causes_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            _call_validate_config(SERVER_PORT=70000)
        assert "PORT" in str(exc_info.value)


# ── Non-writable Workspace ──────────────────────────────────────

class TestNonWritableWorkspace:
    def test_non_writable_workspace_causes_exit(self):
        """When WORKSPACE_ROOT write-test raises PermissionError, validate_config
        collects the error and calls sys.exit."""
        bad_path = MagicMock(spec=Path)
        bad_path.mkdir = MagicMock()
        test_file = MagicMock()
        test_file.write_text.side_effect = PermissionError("read-only filesystem")
        bad_path.__truediv__ = MagicMock(return_value=test_file)

        with pytest.raises(SystemExit) as exc_info:
            _call_validate_config(WORKSPACE_ROOT=bad_path)
        assert "WORKSPACE" in str(exc_info.value)


# ── Invalid Context / Iteration Values ──────────────────────────

class TestInvalidContextValues:
    def test_zero_context_tokens_causes_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            _call_validate_config(MAX_CONTEXT_TOKENS=0)
        assert "MAX_CONTEXT_TOKENS" in str(exc_info.value)

    def test_zero_agent_iterations_causes_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            _call_validate_config(MAX_AGENT_ITERATIONS=0)
        assert "MAX_AGENT_ITERATIONS" in str(exc_info.value)
