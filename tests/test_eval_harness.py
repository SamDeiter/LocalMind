"""Tests for the eval harness, seed cases, scoring, and API routes.

Covers:
  - Seed case definitions (get_seed_cases structure & required keys)
  - Scoring: substring match, regex match, pass and fail scenarios
  - Harness: run_case returns structured result, run_all returns summary
  - Cases: load_cases_to_db is idempotent
  - API routes via FastAPI TestClient (GET /cases, POST /run, GET /runs, GET /trend)

All LLM / Ollama calls are mocked — no real API calls are made.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seed_cases():
    """Return the list of seed eval cases."""
    from backend.eval.cases import get_seed_cases
    return get_seed_cases()


@pytest.fixture()
def harness(tmp_path):
    """Return an EvalHarness pointed at a temp DB with eval tables.

    Ollama is mocked so no real HTTP calls happen.
    """
    db_path = str(tmp_path / "harness_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS eval_cases (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            input_json TEXT NOT NULL,
            expected_output_json TEXT NOT NULL,
            artifact_type TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS eval_runs (
            id TEXT PRIMARY KEY,
            eval_case_id TEXT NOT NULL,
            job_id TEXT,
            status TEXT NOT NULL,
            score REAL,
            details_json TEXT,
            model_config_json TEXT,
            ran_at TEXT NOT NULL,
            duration_ms INTEGER
        );
    """)
    conn.close()

    from backend.eval.harness import EvalHarness
    h = EvalHarness(db_path=db_path)
    # Force Ollama to appear unavailable so mock responses are used
    h._ollama_available = False
    return h


# ===================================================================
# TestSeedCases — verify seed case definitions
# ===================================================================

class TestSeedCases:

    def test_get_seed_cases_returns_list(self, seed_cases):
        assert isinstance(seed_cases, list)
        assert len(seed_cases) > 0

    def test_seed_cases_have_required_keys(self, seed_cases):
        required = {"id", "title", "prompt", "expected_output", "expected_type",
                     "tools_allowed", "max_tokens", "timeout_sec", "scoring"}
        for case in seed_cases:
            missing = required - set(case.keys())
            assert not missing, f"Case '{case.get('id', '?')}' missing keys: {missing}"

    def test_seed_case_ids_are_unique(self, seed_cases):
        ids = [c["id"] for c in seed_cases]
        assert len(ids) == len(set(ids)), "Duplicate seed case IDs found"

    def test_seed_cases_expected_type_valid(self, seed_cases):
        valid_types = {"substring", "regex"}
        for case in seed_cases:
            assert case["expected_type"] in valid_types, (
                f"Case '{case['id']}' has invalid expected_type: {case['expected_type']}"
            )

    def test_seed_cases_scoring_valid(self, seed_cases):
        valid_scoring = {"pass_fail", "partial"}
        for case in seed_cases:
            assert case["scoring"] in valid_scoring, (
                f"Case '{case['id']}' has invalid scoring: {case['scoring']}"
            )


# ===================================================================
# TestScoreResult — test the real harness scoring logic
# ===================================================================

class TestScoreResult:
    """Test EvalHarness.score_result with real harness instance."""

    def test_substring_match_pass(self, harness):
        case = {"expected_output": '"line_count"', "expected_type": "substring", "scoring": "pass_fail"}
        result = harness.score_result(case, '{"line_count": 42, "filename": "test.txt"}')
        assert result["match"] is True
        assert result["status"] == "passed"
        assert result["score"] == 1.0

    def test_substring_match_fail(self, harness):
        case = {"expected_output": '"line_count"', "expected_type": "substring", "scoring": "pass_fail"}
        result = harness.score_result(case, "No matching content here")
        assert result["match"] is False
        assert result["status"] == "failed"
        assert result["score"] == 0.0

    def test_regex_match_pass(self, harness):
        case = {"expected_output": r'"status":\s*"written"', "expected_type": "regex", "scoring": "pass_fail"}
        result = harness.score_result(case, '{"status": "written"}')
        assert result["match"] is True
        assert result["status"] == "passed"

    def test_regex_match_fail(self, harness):
        case = {"expected_output": r'"status":\s*"written"', "expected_type": "regex", "scoring": "pass_fail"}
        result = harness.score_result(case, '{"status": "error"}')
        assert result["match"] is False
        assert result["status"] == "failed"

    def test_regex_multiline_match(self, harness):
        case = {"expected_output": r'"change":\s*29', "expected_type": "regex", "scoring": "pass_fail"}
        result = harness.score_result(case, '{\n  "change": 29.00\n}')
        assert result["match"] is True
        assert result["score"] == 1.0

    def test_partial_scoring_gives_half_on_miss(self, harness):
        """With scoring=partial, a non-empty output that doesn't match gets 0.5."""
        case = {"expected_output": "impossible_string", "expected_type": "substring", "scoring": "partial"}
        result = harness.score_result(case, "Some output that doesnt match")
        assert result["match"] is False
        assert result["score"] == 0.5
        assert result["status"] == "partial"


# ===================================================================
# TestRunCase — test single case execution (mocked LLM)
# ===================================================================

class TestRunCase:

    def test_run_case_returns_structured_result(self, harness, seed_cases):
        """run_case returns a dict with case_id, title, status, score, duration_ms."""
        case = seed_cases[0]
        result = harness.run_case(case)

        assert "case_id" in result
        assert "title" in result
        assert "status" in result
        assert "score" in result
        assert "duration_ms" in result
        assert "run_id" in result
        assert result["case_id"] == case["id"]

    def test_run_case_with_mock_returns_scored_result(self, harness, seed_cases):
        """Mock responses go through the scoring pipeline."""
        case = seed_cases[0]
        result = harness.run_case(case)
        # Mock response won't match most expected outputs
        assert result["status"] in ("passed", "failed", "partial")
        assert isinstance(result["score"], float)


# ===================================================================
# TestRunAll — test full harness run (mocked LLM)
# ===================================================================

class TestRunAll:

    def test_run_all_returns_summary(self, harness):
        """run_all returns a summary with pass_count, fail_count, results."""
        summary = harness.run_all()
        assert "pass_count" in summary
        assert "fail_count" in summary
        assert "results" in summary
        assert "total_cases" in summary
        assert summary["pass_count"] + summary["fail_count"] + summary.get("partial_count", 0) == summary["total_cases"]

    def test_run_all_with_filtered_cases(self, harness, seed_cases):
        """run_all accepts a cases list to filter what runs."""
        subset = seed_cases[:2]
        summary = harness.run_all(cases=subset)
        assert summary["total_cases"] == 2
        assert len(summary["results"]) == 2


# ===================================================================
# TestLoadCasesToDb — DB insertion and idempotency
# ===================================================================

class TestLoadCasesToDb:

    def test_load_cases_inserts_seed_cases(self, tmp_path):
        """load_cases_to_db inserts seed cases into eval_cases table."""
        db_path = str(tmp_path / "test_eval.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS eval_cases (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                input_json TEXT NOT NULL,
                expected_output_json TEXT NOT NULL,
                artifact_type TEXT,
                created_at TEXT NOT NULL
            );
        """)
        conn.close()

        from backend.eval.cases import get_seed_cases, load_cases_to_db
        inserted = load_cases_to_db(db_path)
        assert inserted == len(get_seed_cases())

    def test_load_cases_is_idempotent(self, tmp_path):
        """Calling load_cases_to_db twice inserts 0 on the second call."""
        db_path = str(tmp_path / "test_eval_idem.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS eval_cases (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                input_json TEXT NOT NULL,
                expected_output_json TEXT NOT NULL,
                artifact_type TEXT,
                created_at TEXT NOT NULL
            );
        """)
        conn.close()

        from backend.eval.cases import load_cases_to_db
        first = load_cases_to_db(db_path)
        assert first > 0
        second = load_cases_to_db(db_path)
        assert second == 0


# ===================================================================
# TestEvalAPI — FastAPI route tests with TestClient
# ===================================================================

class TestEvalAPI:

    @pytest.fixture(autouse=True)
    def _setup_client(self, tmp_path):
        """Create a FastAPI TestClient wrapping only the eval router.

        Patches DB_PATH and the harness so no real LLM calls are made.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.routes.eval_routes import router

        app = FastAPI()
        app.include_router(router)

        # Prepare a real SQLite DB for the cases/runs endpoints
        self.db_path = str(tmp_path / "eval_api_test.db")
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS eval_cases (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                input_json TEXT NOT NULL,
                expected_output_json TEXT NOT NULL,
                artifact_type TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS eval_runs (
                id TEXT PRIMARY KEY,
                eval_case_id TEXT NOT NULL,
                job_id TEXT,
                status TEXT NOT NULL,
                score REAL,
                details_json TEXT,
                model_config_json TEXT,
                ran_at TEXT NOT NULL,
                duration_ms INTEGER
            );
        """)
        conn.close()

        # Pre-load seed cases
        from backend.eval.cases import load_cases_to_db
        load_cases_to_db(self.db_path)

        # Patch backend.config.DB_PATH — route handlers import it lazily
        self._patches = [
            patch("backend.config.DB_PATH", self.db_path),
        ]
        for p in self._patches:
            p.start()

        self.client = TestClient(app)
        yield
        for p in self._patches:
            p.stop()

    def test_get_cases_returns_list(self):
        """GET /api/evals/cases returns a list of seed cases."""
        resp = self.client.get("/api/evals/cases")
        assert resp.status_code == 200
        data = resp.json()
        assert "cases" in data
        assert isinstance(data["cases"], list)
        assert data["count"] > 0

    def test_post_run_triggers_eval(self):
        """POST /api/evals/run triggers a run and returns results."""
        mock_summary = {
            "pass_count": 5,
            "fail_count": 0,
            "total_cases": 5,
            "pass_rate": 1.0,
            "results": [],
        }

        with patch("backend.eval.harness.EvalHarness") as MockHarness:
            MockHarness.return_value.run_all.return_value = mock_summary
            resp = self.client.post("/api/evals/run", json={})

        assert resp.status_code == 201
        data = resp.json()
        assert data["pass_count"] == 5
        assert data["pass_rate"] == 1.0

    def test_post_run_with_case_ids(self):
        """POST /api/evals/run with case_ids filters the run."""
        mock_summary = {
            "pass_count": 2,
            "fail_count": 0,
            "total_cases": 2,
            "pass_rate": 1.0,
            "results": [],
        }

        with patch("backend.eval.harness.EvalHarness") as MockHarness:
            MockHarness.return_value.run_all.return_value = mock_summary
            resp = self.client.post("/api/evals/run", json={
                "case_ids": ["eval_seed_read_file_01", "eval_seed_codegen_python_01"],
            })

        assert resp.status_code == 201
        assert resp.json()["total_cases"] == 2

    def test_get_runs_returns_paginated_list(self):
        """GET /api/evals/runs returns paginated run results."""
        now = _now_iso()
        conn = sqlite3.connect(self.db_path)
        for i in range(3):
            conn.execute(
                """INSERT INTO eval_runs (id, eval_case_id, status, score, ran_at, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_uid(), "eval_seed_read_file_01", "passed", 1.0, now, 100 + i),
            )
        conn.commit()
        conn.close()

        def _test_conn():
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            return c

        with patch("backend.core.eval._get_conn", _test_conn):
            resp = self.client.get("/api/evals/runs?limit=10&offset=0")

        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert data["count"] == 3
        assert data["limit"] == 10

    def test_get_trend_returns_trend_data(self):
        """GET /api/evals/trend returns trend data for sparkline charts."""
        now = _now_iso()
        conn = sqlite3.connect(self.db_path)
        for i in range(5):
            conn.execute(
                """INSERT INTO eval_runs (id, eval_case_id, status, score, ran_at, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_uid(), "eval_seed_read_file_01", "passed", 0.8 + i * 0.04, now, 200),
            )
        conn.commit()
        conn.close()

        def _test_conn():
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            return c

        with patch("backend.core.eval._get_conn", _test_conn):
            resp = self.client.get("/api/evals/trend?n=5")

        assert resp.status_code == 200
        data = resp.json()
        assert "trend" in data
        assert len(data["trend"]) == 5
        for entry in data["trend"]:
            assert "run_id" in entry
            assert "pass_rate" in entry

    def test_get_run_by_id(self):
        """GET /api/evals/runs/{run_id} returns a single run."""
        now = _now_iso()
        run_id = _uid()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO eval_runs (id, eval_case_id, status, score, ran_at, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, "eval_seed_read_file_01", "passed", 1.0, now, 150),
        )
        conn.commit()
        conn.close()

        def _test_conn():
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            return c

        with patch("backend.core.eval._get_conn", _test_conn):
            resp = self.client.get(f"/api/evals/runs/{run_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["run"]["id"] == run_id
        assert data["run"]["status"] == "passed"

    def test_get_run_not_found(self):
        """GET /api/evals/runs/{run_id} returns 404 for unknown ID."""
        fake_id = _uid()

        def _test_conn():
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            return c

        with patch("backend.core.eval._get_conn", _test_conn):
            resp = self.client.get(f"/api/evals/runs/{fake_id}")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
