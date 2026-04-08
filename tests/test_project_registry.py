"""Tests for ProjectRegistry (backend.core.project_registry)."""

import json
import sqlite3
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# _UnclosableConnection — prevents in-memory DB from vanishing on .close()
# ---------------------------------------------------------------------------

class _UnclosableConnection:
    """Wraps a sqlite3.Connection so that .close() is a no-op.

    Production code calls conn.close() in finally blocks.  For in-memory test
    databases that would destroy the data between calls, so we suppress it.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self):
        """Intentional no-op."""

    def real_close(self):
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _init_test_db() -> _UnclosableConnection:
    """Create an in-memory SQLite DB with project_registry table."""
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    raw.executescript("""
        CREATE TABLE IF NOT EXISTS project_registry (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            description TEXT,
            language_breakdown TEXT,
            file_count INTEGER DEFAULT 0,
            total_lines INTEGER DEFAULT 0,
            last_scanned_at REAL,
            active INTEGER DEFAULT 1,
            created_at REAL NOT NULL,
            metadata TEXT DEFAULT '{}'
        );
    """)
    return _UnclosableConnection(raw)


@pytest.fixture
def registry_db():
    """Yield an in-memory DB and a patched ProjectRegistry instance."""
    db = _init_test_db()

    def _fake_conn(self_ignored=None):
        return db

    with patch(
        "backend.core.project_registry.ProjectRegistry._conn", _fake_conn
    ):
        from backend.core.project_registry import ProjectRegistry
        reg = ProjectRegistry()
        yield reg, db

    db.real_close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegisterProject:
    """register() should insert a row and return a dict."""

    def test_register_returns_dict(self, registry_db, tmp_path):
        reg, db = registry_db
        project_dir = tmp_path / "proj_a"
        project_dir.mkdir()

        result = reg.register("Project A", str(project_dir), description="desc A")

        assert isinstance(result, dict)
        assert result["name"] == "Project A"
        assert result["path"] == str(project_dir)
        assert result["description"] == "desc A"
        assert result["active"] == 1
        assert "id" in result
        assert "created_at" in result

    def test_duplicate_path_raises(self, registry_db, tmp_path):
        reg, db = registry_db
        project_dir = tmp_path / "proj_dup"
        project_dir.mkdir()

        reg.register("First", str(project_dir))
        with pytest.raises(ValueError, match="already registered"):
            reg.register("Second", str(project_dir))


class TestListProjects:
    """list_projects() should return all registered rows."""

    def test_list_returns_all(self, registry_db, tmp_path):
        reg, db = registry_db
        dirs = []
        for name in ("alpha", "beta", "gamma"):
            d = tmp_path / name
            d.mkdir()
            dirs.append(d)
            reg.register(name, str(d))

        projects = reg.list_projects()
        assert len(projects) == 3
        names = {p["name"] for p in projects}
        assert names == {"alpha", "beta", "gamma"}

    def test_list_empty_returns_empty(self, registry_db):
        reg, db = registry_db
        assert reg.list_projects() == []


class TestGetProject:
    """get_project() by id should return the correct project or None."""

    def test_get_by_id(self, registry_db, tmp_path):
        reg, db = registry_db
        d = tmp_path / "get_me"
        d.mkdir()
        created = reg.register("GetMe", str(d))

        fetched = reg.get_project(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]
        assert fetched["name"] == "GetMe"

    def test_get_nonexistent_returns_none(self, registry_db):
        reg, db = registry_db
        assert reg.get_project("nonexistent-id") is None


class TestUnregister:
    """unregister() should delete the row and return True/False."""

    def test_unregister_existing(self, registry_db, tmp_path):
        reg, db = registry_db
        d = tmp_path / "bye"
        d.mkdir()
        created = reg.register("Bye", str(d))

        assert reg.unregister(created["id"]) is True
        assert reg.get_project(created["id"]) is None

    def test_unregister_nonexistent_returns_false(self, registry_db):
        reg, db = registry_db
        assert reg.unregister("no-such-id") is False


class TestScanProject:
    """scan_project() should walk the directory and populate stats."""

    def test_scan_populates_file_stats(self, registry_db, tmp_path):
        reg, db = registry_db
        d = tmp_path / "scanme"
        d.mkdir()
        (d / "app.py").write_text("print('hello')\nprint('world')\n")
        (d / "index.js").write_text("console.log('hi');\n")

        created = reg.register("ScanMe", str(d))
        scanned = reg.scan_project(created["id"])

        assert scanned["file_count"] >= 2
        assert scanned["total_lines"] >= 3
        assert scanned["last_scanned_at"] is not None
        # language_breakdown should contain python and javascript
        breakdown = json.loads(scanned["language_breakdown"])
        assert "python" in breakdown
        assert "javascript" in breakdown
