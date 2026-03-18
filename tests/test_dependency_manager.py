"""
Tests for the Dependency Lifecycle Manager.
"""
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.tools.dependency_manager import (
    InstallPackageTool,
    UninstallPackageTool,
    ListDependenciesTool,
    get_all_dependencies,
    get_idle_dependencies,
    mark_dependency_used,
    pin_dependency,
    _load_deps,
    _save_deps,
)


@pytest.fixture
def tmp_deps_file(tmp_path):
    """Redirect the deps file to a temp path."""
    deps_path = tmp_path / ".dependencies.json"
    with patch("backend.tools.dependency_manager._deps_path", deps_path):
        yield deps_path


class TestDependencyTracking:
    """Tests for the low-level tracking helpers."""

    def test_empty_deps(self, tmp_deps_file):
        assert _load_deps() == []

    def test_save_and_load(self, tmp_deps_file):
        deps = [
            {"package": "pandas", "version": "2.1.0", "status": "ACTIVE",
             "last_used_at": time.time(), "installed_at": time.time(), "reason": "test"},
        ]
        _save_deps(deps)
        loaded = _load_deps()
        assert len(loaded) == 1
        assert loaded[0]["package"] == "pandas"

    def test_mark_used(self, tmp_deps_file):
        now = time.time()
        _save_deps([{"package": "requests", "last_used_at": now - 100000, "status": "ACTIVE"}])
        mark_dependency_used("requests")
        loaded = _load_deps()
        assert loaded[0]["last_used_at"] > now - 10  # Updated to recent

    def test_pin_dependency(self, tmp_deps_file):
        _save_deps([{"package": "numpy", "status": "ACTIVE", "last_used_at": 0}])
        assert pin_dependency("numpy") is True
        loaded = _load_deps()
        assert loaded[0]["status"] == "PINNED"

    def test_pin_nonexistent(self, tmp_deps_file):
        _save_deps([])
        assert pin_dependency("nonexistent") is False


class TestIdleDetection:
    """Tests for idle dependency detection."""

    def test_no_idle_when_recent(self, tmp_deps_file):
        _save_deps([{"package": "pandas", "status": "ACTIVE", "last_used_at": time.time()}])
        assert len(get_idle_dependencies()) == 0

    def test_idle_after_7_days(self, tmp_deps_file):
        old_time = time.time() - (8 * 86400)  # 8 days ago
        _save_deps([{"package": "pandas", "status": "ACTIVE", "last_used_at": old_time}])
        idle = get_idle_dependencies()
        assert len(idle) == 1
        assert idle[0]["package"] == "pandas"

    def test_pinned_never_idle(self, tmp_deps_file):
        old_time = time.time() - (30 * 86400)  # 30 days ago
        _save_deps([{"package": "core-lib", "status": "PINNED", "last_used_at": old_time}])
        assert len(get_idle_dependencies()) == 0


class TestInstallTool:
    """Tests for the InstallPackageTool."""

    @pytest.mark.asyncio
    async def test_install_empty_package(self, tmp_deps_file):
        tool = InstallPackageTool()
        result = await tool.execute(package="", reason="test")
        assert result["success"] is False
        assert "required" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_install_success(self, tmp_deps_file):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Successfully installed test-pkg-1.0.0"
        mock_result.stderr = ""

        with patch("backend.tools.dependency_manager.subprocess.run", return_value=mock_result):
            tool = InstallPackageTool()
            result = await tool.execute(package="test-pkg", reason="testing")
            assert result["success"] is True
            # Verify it was tracked
            deps = _load_deps()
            assert len(deps) == 1
            assert deps[0]["package"] == "test-pkg"

    @pytest.mark.asyncio
    async def test_install_failure(self, tmp_deps_file):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ERROR: No matching distribution"
        mock_result.stdout = ""

        with patch("backend.tools.dependency_manager.subprocess.run", return_value=mock_result):
            tool = InstallPackageTool()
            result = await tool.execute(package="nonexistent-pkg-xyz", reason="test")
            assert result["success"] is False


class TestListTool:
    """Tests for the ListDependenciesTool."""

    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_deps_file):
        tool = ListDependenciesTool()
        result = await tool.execute()
        assert result["success"] is True
        assert "No tracked" in result["result"]

    @pytest.mark.asyncio
    async def test_list_with_deps(self, tmp_deps_file):
        _save_deps([
            {"package": "pandas", "version": "2.1.0", "status": "ACTIVE",
             "last_used_at": time.time(), "reason": "data work"},
        ])
        tool = ListDependenciesTool()
        result = await tool.execute()
        assert result["success"] is True
        assert "pandas" in result["result"]
