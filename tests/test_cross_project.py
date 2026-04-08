"""Tests for CrossProjectMiner (backend.research.cross_project)."""

import json
import sqlite3
from unittest.mock import patch

import pytest


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
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=OFF")  # skip FK checks for test convenience
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


def _register_project(db, project_id: str, name: str, path: str):
    """Insert a project_registry row directly."""
    import time
    db.execute(
        "INSERT INTO project_registry (id, name, path, active, created_at) "
        "VALUES (?, ?, ?, 1, ?)",
        (project_id, name, path, time.time()),
    )
    db.commit()


@pytest.fixture
def miner_db():
    """Yield a CrossProjectMiner wired to an in-memory DB."""
    db = _init_test_db()

    def _fake_conn(self_ignored=None):
        return db

    with patch(
        "backend.research.cross_project.CrossProjectMiner._conn", _fake_conn
    ):
        from backend.research.cross_project import CrossProjectMiner
        miner = CrossProjectMiner()
        yield miner, db

    db.real_close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMineAllWithFewProjects:
    """mine_all() should return early when <2 active projects."""

    def test_zero_projects_returns_empty(self, miner_db):
        miner, db = miner_db
        result = miner.mine_all()
        assert result["patterns_found"] == 0
        assert len(result["insights"]) >= 1
        assert "at least 2" in result["insights"][0].lower()

    def test_one_project_returns_empty(self, miner_db, tmp_path):
        miner, db = miner_db
        d = tmp_path / "solo"
        d.mkdir()
        _register_project(db, "p1", "Solo", str(d))

        result = miner.mine_all()
        assert result["patterns_found"] == 0


class TestSharedDependencyDetection:
    """Shared dependencies across 2+ projects should be detected."""

    def test_shared_requirements_txt(self, miner_db, tmp_path):
        miner, db = miner_db

        # Create two project dirs with overlapping requirements.txt
        proj_a = tmp_path / "proj_a"
        proj_a.mkdir()
        (proj_a / "requirements.txt").write_text("fastapi>=0.100\nuvicorn\npydantic\n")
        (proj_a / "main.py").write_text("import fastapi\n")

        proj_b = tmp_path / "proj_b"
        proj_b.mkdir()
        (proj_b / "requirements.txt").write_text("fastapi>=0.95\nrequests\npydantic\n")
        (proj_b / "app.py").write_text("import fastapi\n")

        _register_project(db, "pa", "ProjA", str(proj_a))
        _register_project(db, "pb", "ProjB", str(proj_b))

        result = miner.mine_all()
        assert result["patterns_found"] >= 1

        # Verify shared deps were stored
        patterns = miner.get_patterns(pattern_type="dependency")
        dep_titles = [p["title"] for p in patterns]
        # Both projects share fastapi and pydantic
        assert any("fastapi" in t.lower() for t in dep_titles)
        assert any("pydantic" in t.lower() for t in dep_titles)


class TestPatternUpsertIdempotent:
    """Running mine_all() twice should upsert, not duplicate patterns."""

    def test_no_duplicate_patterns(self, miner_db, tmp_path):
        miner, db = miner_db

        proj_a = tmp_path / "idempotent_a"
        proj_a.mkdir()
        (proj_a / "requirements.txt").write_text("numpy\npandas\n")
        (proj_a / "code.py").write_text("import numpy\n")

        proj_b = tmp_path / "idempotent_b"
        proj_b.mkdir()
        (proj_b / "requirements.txt").write_text("numpy\nscipy\n")
        (proj_b / "code.py").write_text("import numpy\n")

        _register_project(db, "ia", "IdempA", str(proj_a))
        _register_project(db, "ib", "IdempB", str(proj_b))

        miner.mine_all()
        first_count = db.execute(
            "SELECT COUNT(*) as cnt FROM cross_project_patterns"
        ).fetchone()["cnt"]

        miner.mine_all()
        second_count = db.execute(
            "SELECT COUNT(*) as cnt FROM cross_project_patterns"
        ).fetchone()["cnt"]

        assert second_count == first_count


class TestArchitecturePatternDetection:
    """Projects with shared architectural markers should generate patterns."""

    def test_shared_backend_frontend_pattern(self, miner_db, tmp_path):
        miner, db = miner_db

        for name, pid in [("arch_a", "aa"), ("arch_b", "ab")]:
            d = tmp_path / name
            d.mkdir()
            (d / "backend").mkdir()
            (d / "frontend").mkdir()
            (d / "tests").mkdir()
            # Add a code file so the project is scannable
            (d / "backend" / "app.py").write_text("# app\n")
            _register_project(db, pid, name, str(d))

        result = miner.mine_all()
        assert result["patterns_found"] >= 1

        patterns = miner.get_patterns(pattern_type="architecture")
        titles = [p["title"] for p in patterns]
        assert any("backend/frontend" in t.lower() for t in titles)


class TestGetInsights:
    """get_insights() should return structured summary data."""

    def test_returns_summary_suggestions_by_type(self, miner_db, tmp_path):
        miner, db = miner_db

        # Seed two projects with shared deps so there is data to report on
        proj_a = tmp_path / "insight_a"
        proj_a.mkdir()
        (proj_a / "requirements.txt").write_text("flask\npytest\n")
        (proj_a / "app.py").write_text("from flask import Flask\n")

        proj_b = tmp_path / "insight_b"
        proj_b.mkdir()
        (proj_b / "requirements.txt").write_text("flask\nblack\n")
        (proj_b / "server.py").write_text("from flask import Flask\n")

        _register_project(db, "ia2", "InsightA", str(proj_a))
        _register_project(db, "ib2", "InsightB", str(proj_b))

        miner.mine_all()

        insights = miner.get_insights()
        assert "summary" in insights
        assert "suggestions" in insights
        assert "by_type" in insights
        assert isinstance(insights["summary"], dict)
        assert isinstance(insights["suggestions"], list)
        assert len(insights["suggestions"]) >= 1
        assert insights["summary"]["total_patterns"] >= 1

    def test_empty_db_returns_valid_structure(self, miner_db):
        miner, db = miner_db
        insights = miner.get_insights()
        assert "summary" in insights
        assert "suggestions" in insights
        assert "by_type" in insights
        assert insights["summary"]["total_patterns"] == 0
