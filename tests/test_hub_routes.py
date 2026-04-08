"""Tests for Cross-Project Hub API routes (backend.routes.hub)."""

import json
import sqlite3
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# _UnclosableConnection — prevents in-memory DB from vanishing on .close()
# ---------------------------------------------------------------------------

class _UnclosableConnection:
    """Wraps a sqlite3.Connection so that .close() is a no-op."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self):
        """Intentional no-op."""

    def real_close(self):
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _init_test_db() -> _UnclosableConnection:
    """Create an in-memory SQLite DB with cross-project hub tables."""
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=OFF")
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

        CREATE TABLE IF NOT EXISTS cross_project_patterns (
            id TEXT PRIMARY KEY,
            pattern_type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            source_project_id TEXT,
            related_project_ids TEXT,
            confidence REAL DEFAULT 0.0,
            occurrences INTEGER DEFAULT 1,
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            metadata TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_patterns_type
            ON cross_project_patterns(pattern_type);
    """)
    return _UnclosableConnection(raw)


@pytest.fixture
def _patch_db():
    """Patch DB connections for both project_registry and cross_project modules.

    Also resets the module-level singletons so that freshly-constructed
    instances pick up the patched _conn method.
    """
    db = _init_test_db()

    def _fake_conn(self_ignored=None):
        return db

    import backend.core.project_registry as pr_mod
    import backend.research.cross_project as cp_mod

    # Reset singletons so the patched _conn is used
    old_registry = pr_mod._registry
    old_miner = cp_mod._miner
    pr_mod._registry = None
    cp_mod._miner = None

    targets = [
        "backend.core.project_registry.ProjectRegistry._conn",
        "backend.research.cross_project.CrossProjectMiner._conn",
    ]
    patches = [patch(t, _fake_conn) for t in targets]
    for p in patches:
        p.start()

    yield db

    for p in patches:
        p.stop()

    # Restore singletons
    pr_mod._registry = old_registry
    cp_mod._miner = old_miner
    db.real_close()


@pytest.fixture
def client():
    """Create a FastAPI TestClient wrapping only the hub router."""
    from backend.routes.hub import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPostProjects:
    """POST /api/hub/projects should register a project."""

    def test_register_project(self, client, _patch_db, tmp_path):
        project_dir = tmp_path / "new_proj"
        project_dir.mkdir()

        resp = client.post(
            "/api/hub/projects",
            json={
                "name": "MyProject",
                "path": str(project_dir),
                "description": "A test project",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["project"]["name"] == "MyProject"
        assert data["project"]["path"] == str(project_dir)

    def test_register_nonexistent_path_fails(self, client, _patch_db):
        resp = client.post(
            "/api/hub/projects",
            json={
                "name": "Ghost",
                "path": "/nonexistent/path/does/not/exist",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "not a directory" in data["error"].lower() or "does not exist" in data["error"].lower()


class TestGetProjects:
    """GET /api/hub/projects should list all registered projects."""

    def test_list_empty(self, client, _patch_db):
        resp = client.get("/api/hub/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert data["projects"] == []
        assert data["count"] == 0

    def test_list_returns_registered(self, client, _patch_db, tmp_path):
        # Register two projects
        for name in ("alpha", "beta"):
            d = tmp_path / name
            d.mkdir()
            client.post(
                "/api/hub/projects",
                json={"name": name, "path": str(d)},
            )

        resp = client.get("/api/hub/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        names = {p["name"] for p in data["projects"]}
        assert names == {"alpha", "beta"}


class TestDeleteProject:
    """DELETE /api/hub/projects/{id} should remove a project."""

    def test_delete_existing(self, client, _patch_db, tmp_path):
        d = tmp_path / "deleteme"
        d.mkdir()

        create_resp = client.post(
            "/api/hub/projects",
            json={"name": "DeleteMe", "path": str(d)},
        )
        project_id = create_resp.json()["project"]["id"]

        del_resp = client.delete(f"/api/hub/projects/{project_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["ok"] is True

        # Verify it's gone
        list_resp = client.get("/api/hub/projects")
        assert list_resp.json()["count"] == 0

    def test_delete_nonexistent_returns_not_found(self, client, _patch_db):
        resp = client.delete("/api/hub/projects/fake-id-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False


class TestGetInsights:
    """GET /api/hub/insights should return structured insight data."""

    def test_insights_empty_db(self, client, _patch_db):
        resp = client.get("/api/hub/insights")
        assert resp.status_code == 200
        data = resp.json()
        assert "insights" in data
        insights = data["insights"]
        assert "summary" in insights
        assert "suggestions" in insights
        assert "by_type" in insights

    def test_insights_with_data(self, client, _patch_db, tmp_path):
        # Register two projects with shared deps
        proj_a = tmp_path / "ins_a"
        proj_a.mkdir()
        (proj_a / "requirements.txt").write_text("flask\npydantic\n")
        (proj_a / "app.py").write_text("from flask import Flask\n")

        proj_b = tmp_path / "ins_b"
        proj_b.mkdir()
        (proj_b / "requirements.txt").write_text("flask\nuvicorn\n")
        (proj_b / "server.py").write_text("from flask import Flask\n")

        client.post("/api/hub/projects", json={"name": "InsA", "path": str(proj_a)})
        client.post("/api/hub/projects", json={"name": "InsB", "path": str(proj_b)})

        # Trigger mining
        mine_resp = client.post("/api/hub/mine")
        assert mine_resp.status_code == 200

        # Now check insights
        resp = client.get("/api/hub/insights")
        assert resp.status_code == 200
        data = resp.json()
        insights = data["insights"]
        assert insights["summary"]["total_patterns"] >= 1
