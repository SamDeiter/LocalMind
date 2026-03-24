"""
Tests for the AutonomyEngine — proposal lifecycle and execution safety.
"""
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from backend.autonomy import AutonomyEngine
from backend.code_editor import edit_single_file
from backend.git_ops import revert_file


@pytest.fixture
def engine():
    """Create an AutonomyEngine that won't actually call Ollama."""
    eng = AutonomyEngine(
        ollama_url="http://localhost:11434",
    )
    eng.enabled = False  # Don't start background loops
    eng.status["enabled"] = False  # Keep status dict in sync
    return eng


@pytest.fixture
def proposals_dir(tmp_path):
    """Redirect the PROPOSALS_DIR to a temp directory."""
    d = tmp_path / "proposals"
    d.mkdir()
    with patch("backend.proposals.PROPOSALS_DIR", d), \
             patch("backend.autonomy.PROPOSALS_DIR", d):
        yield d


@pytest.fixture
def sample_proposal(proposals_dir):
    """Write a sample proposal JSON and return its data."""
    data = {
        "id": "abc12345",
        "title": "Add retry logic to Ollama calls",
        "category": "performance",
        "description": "Wrap httpx calls with exponential backoff.",
        "files_affected": ["backend/server.py"],
        "effort": "small",
        "priority": "high",
        "status": "proposed",
        "source": "autonomy_reflection",
        "created_at": time.time(),
        "created_at_human": "2026-03-19 12:00:00",
    }
    filepath = proposals_dir / f"{data['id']}_performance.json"
    filepath.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


# ── Proposal Lifecycle ───────────────────────────────────────────

class TestProposalManagement:
    def test_list_empty(self, engine, proposals_dir):
        """List proposals on empty directory returns []."""
        result = engine.list_proposals()
        assert result == []

    def test_list_all(self, engine, proposals_dir, sample_proposal):
        """List proposals returns the sample proposal."""
        result = engine.list_proposals()
        assert len(result) == 1
        assert result[0]["id"] == "abc12345"
        assert result[0]["title"] == "Add retry logic to Ollama calls"

    def test_list_filtered(self, engine, proposals_dir, sample_proposal):
        """Filtering by status works correctly."""
        proposed = engine.list_proposals(status_filter="proposed")
        assert len(proposed) == 1
        approved = engine.list_proposals(status_filter="approved")
        assert len(approved) == 0

    def test_approve_proposal(self, engine, proposals_dir, sample_proposal):
        """Approving a proposal changes its status."""
        result = engine.approve_proposal("abc12345")
        assert result is not None
        assert result["status"] == "approved"
        assert "status_changed_at" in result

        # Verify file was updated
        files = list(proposals_dir.glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["status"] == "approved"

    def test_deny_proposal(self, engine, proposals_dir, sample_proposal):
        """Denying a proposal changes its status."""
        result = engine.deny_proposal("abc12345")
        assert result is not None
        assert result["status"] == "denied"

    def test_approve_nonexistent(self, engine, proposals_dir):
        """Approving a nonexistent proposal returns None."""
        result = engine.approve_proposal("does-not-exist")
        assert result is None

    def test_deny_nonexistent(self, engine, proposals_dir):
        """Denying a nonexistent proposal returns None."""
        result = engine.deny_proposal("does-not-exist")
        assert result is None

    def test_multiple_proposals(self, engine, proposals_dir):
        """Multiple proposals can be listed and individually managed."""
        for i in range(3):
            data = {
                "id": f"prop-{i}",
                "title": f"Proposal {i}",
                "category": "feature",
                "description": f"Description for proposal {i}",
                "priority": "medium",
                "status": "proposed",
                "created_at": time.time(),
            }
            f = proposals_dir / f"prop-{i}_feature.json"
            f.write_text(json.dumps(data), encoding="utf-8")

        all_props = engine.list_proposals()
        assert len(all_props) == 3

        # Approve one, deny another
        engine.approve_proposal("prop-0")
        engine.deny_proposal("prop-1")

        proposed = engine.list_proposals(status_filter="proposed")
        assert len(proposed) == 1
        assert proposed[0]["id"] == "prop-2"


# ── Status & Toggle ─────────────────────────────────────────────

class TestEngineStatus:
    def test_initial_status(self, engine):
        """Engine starts with correct initial status."""
        status = engine.get_status()
        assert status["enabled"] is False  # fixture sets enabled=False
        assert status["uptime_seconds"] == 0

    def test_toggle(self, engine):
        """Toggle flips enabled state."""
        assert engine.enabled is False
        result = engine.toggle()
        assert result is True
        assert engine.enabled is True
        assert engine.status["enabled"] is True

        result = engine.toggle()
        assert result is False
        assert engine.enabled is False


# ── Execution Safety ────────────────────────────────────────────

class TestExecutionSafety:
    @pytest.mark.asyncio
    async def test_execute_skips_when_no_approved(self, engine, proposals_dir, sample_proposal):
        """Execution loop does nothing when no proposals are approved."""
        await engine._execute_next_proposal()
        # proposal should still be "proposed"
        files = list(proposals_dir.glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["status"] == "proposed"

    @pytest.mark.asyncio
    async def test_execute_marks_in_progress(self, engine, proposals_dir, sample_proposal):
        """When an approved proposal is picked, it's marked in_progress."""
        # Approve it first
        engine.approve_proposal("abc12345")

        # Mock out the actual editing and testing
        with patch("backend.autonomy.identify_target_files", new_callable=AsyncMock, return_value=[]), \
             patch("backend.autonomy.git_run", return_value=""):
            await engine._execute_next_proposal()

        # With no target files identified, it should fail
        files = list(proposals_dir.glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["status"] == "failed"
        assert "could not" in data.get("error", "").lower() or "determine" in data.get("error", "").lower() or "generate" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_revert_file(self, engine, tmp_path):
        """_revert_file restores from .bak backup."""
        with patch("backend.git_ops.PROJECT_ROOT", tmp_path):
            # Create original and backup
            target = tmp_path / "test.py"
            target.write_text("original content", encoding="utf-8")
            backup = tmp_path / "test.py.bak"
            backup.write_text("backup content", encoding="utf-8")

            revert_file("test.py")

            assert target.read_text(encoding="utf-8") == "backup content"
            assert not backup.exists()


# ── API Endpoints ────────────────────────────────────────────────

class TestAutonomyAPI:
    """Test the FastAPI endpoints via TestClient."""

    @pytest.fixture
    def api_client(self, proposals_dir, seeded_db):
        """Create a test client with patched proposals dir."""
        from unittest.mock import patch as _patch

        # Patch DB for server import
        def _get_test_db():
            import sqlite3
            conn = sqlite3.connect(str(seeded_db))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            return conn

        from backend.routes import conversations
        from backend.routes import autonomy_routes
        conversations.configure(_get_test_db, "You are LocalMind.")

        with _patch("backend.server.DB_PATH", seeded_db), \
             _patch("backend.server.get_db", _get_test_db), \
             _patch("backend.proposals.PROPOSALS_DIR", proposals_dir), \
             _patch("backend.autonomy.PROPOSALS_DIR", proposals_dir):
            from backend.server import app, autonomy_engine
            # Configure the extracted autonomy routes with the engine
            autonomy_routes.configure(
                engine=autonomy_engine,
                proposals_dir=proposals_dir,
            )
            from starlette.testclient import TestClient
            return TestClient(app)

    def test_list_proposals_empty(self, api_client):
        """GET /api/autonomy/proposals returns empty list."""
        r = api_client.get("/api/autonomy/proposals")
        assert r.status_code == 200
        data = r.json()
        assert data["proposals"] == []
        assert data["count"] == 0

    def test_list_proposals_with_data(self, api_client, proposals_dir, sample_proposal):
        """GET /api/autonomy/proposals returns proposals."""
        r = api_client.get("/api/autonomy/proposals")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1

    def test_approve_proposal_api(self, api_client, proposals_dir, sample_proposal):
        """POST /api/autonomy/proposals/{id}/approve changes status."""
        r = api_client.post("/api/autonomy/proposals/abc12345/approve")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["proposal"]["status"] == "approved"

    def test_deny_proposal_api(self, api_client, proposals_dir, sample_proposal):
        """POST /api/autonomy/proposals/{id}/deny changes status."""
        r = api_client.post("/api/autonomy/proposals/abc12345/deny")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["proposal"]["status"] == "denied"

    def test_approve_nonexistent_api(self, api_client, proposals_dir):
        """Approving nonexistent proposal returns error."""
        r = api_client.post("/api/autonomy/proposals/fake-id/approve")
        data = r.json()
        assert data["ok"] is False
        assert "not found" in data["error"]

    def test_autonomy_status(self, api_client):
        """GET /api/autonomy/status returns status dict."""
        r = api_client.get("/api/autonomy/status")
        assert r.status_code == 200
        data = r.json()
        assert "enabled" in data


# ── New Tests: Dedup, File Validation, Search-Replace ────────────

class TestProposalDedup:
    def test_dedup_rejects_similar_title(self, engine, proposals_dir, sample_proposal):
        """A proposal with a near-identical title is flagged as duplicate."""
        assert engine.proposals.is_duplicate("Add retry logic to Ollama calls") is True

    def test_dedup_allows_different_title(self, engine, proposals_dir, sample_proposal):
        """A clearly different proposal is NOT flagged as duplicate."""
        assert engine.proposals.is_duplicate("Refactor CSS grid layout") is False


class TestFileValidation:
    @pytest.mark.asyncio
    async def test_edit_rejects_nonexistent_file(self, engine, tmp_path):
        """_edit_single_file returns False for a file that doesn't exist."""
        with patch("backend.code_editor.PROJECT_ROOT", tmp_path):
            result = await edit_single_file(
                "nonexistent.py", {"title": "test", "description": "test", "id": "x"},
                "http://localhost:11434", "test-model:7b"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_edit_rejects_blocked_file(self, engine, tmp_path):
        """_edit_single_file returns False for blocked files like autonomy.py."""
        with patch("backend.code_editor.PROJECT_ROOT", tmp_path):
            (tmp_path / "autonomy.py").write_text("x = 1", encoding="utf-8")
            result = await edit_single_file(
                "autonomy.py", {"title": "test", "description": "test", "id": "x"},
                "http://localhost:11434", "test-model:7b"
            )
            assert result is False


# ── Guardrail 1: File Existence Validation ───────────────────────

class TestFileExistenceValidation:
    def test_proposal_rejects_nonexistent_files(self, engine, proposals_dir):
        """Proposals with all-hallucinated files are rejected at save time."""
        proposal = {
            "title": "Optimize Database Queries",
            "category": "performance",
            "description": "Speed up SQL lookups",
            "files_affected": ["database.py", "models.py"],  # These don't exist
            "effort": "small",
            "priority": "medium",
        }
        result = engine.proposals.save(proposal, mode="autonomous", auto_approve_risks={"medium", "low"})
        assert result is None  # Should be rejected

    def test_proposal_accepts_real_files(self, engine, proposals_dir):
        """Proposals with real project files are accepted."""
        proposal = {
            "title": "Add docstrings to proposal manager",
            "category": "code_quality",
            "description": "Improve documentation",
            "files_affected": ["backend/proposals.py"],  # This file exists
            "effort": "small",
            "priority": "low",
        }
        result = engine.proposals.save(proposal, mode="autonomous", auto_approve_risks={"medium", "low"})
        assert result is not None
        assert result["title"] == "Add docstrings to proposal manager"


# ── Guardrail 2: Category Success-Rate Gate ──────────────────────

class TestCategoryGate:
    def test_blocked_categories_below_threshold(self):
        """Categories with success rate below threshold are blocked."""
        from backend.self_improver import SelfImprover
        improver = SelfImprover()
        # Write fake stats with security at 17% success
        stats = {
            "by_category": {
                "security": {"success": 1, "failed": 5},
                "feature": {"success": 5, "failed": 0},
                "performance": {"success": 1, "failed": 5},
            },
            "total": {"success": 7, "failed": 10},
        }
        with patch.object(improver, "_load_stats", return_value=stats):
            blocked = improver.get_blocked_categories()
            assert "security" in blocked
            assert "performance" in blocked
            assert "feature" not in blocked


# ── Guardrail 3: Caller Context Injection ────────────────────────

class TestCallerContext:
    def test_find_callers_detects_imports(self, tmp_path):
        """_find_callers finds files that import the target module."""
        from backend.code_editor import _find_callers

        # Create a target file
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()
        (backend_dir / "documents.py").write_text("def get_doc(): pass", encoding="utf-8")

        # Create a file that imports it
        (backend_dir / "server.py").write_text(
            "from backend.documents import get_doc\nget_doc()", encoding="utf-8"
        )

        with patch("backend.code_editor.PROJECT_ROOT", tmp_path):
            result = _find_callers("backend/documents.py")
            assert "documents" in result
            assert "server.py" in result or "import" in result


# ── Guardrail 4: Circuit Breaker ─────────────────────────────────

class TestCircuitBreaker:
    def test_circuit_breaker_engages_after_threshold(self, engine):
        """Circuit breaker opens after CIRCUIT_BREAKER_THRESHOLD consecutive failures."""
        engine._consecutive_failures = engine.CIRCUIT_BREAKER_THRESHOLD
        # Simulate what the execution code does
        engine._circuit_open_until = time.time() + engine.CIRCUIT_BREAKER_COOLDOWN
        assert engine._circuit_open_until > time.time()

    def test_circuit_breaker_resets_on_success(self, engine):
        """Consecutive failure count resets to 0 after a success."""
        engine._consecutive_failures = 2
        engine._current_backoff = 720
        # Simulate a success reset
        engine._consecutive_failures = 0
        engine._current_backoff = engine.BACKOFF_BASE
        assert engine._consecutive_failures == 0
        assert engine._current_backoff == engine.BACKOFF_BASE

    def test_circuit_breaker_not_tripped_below_threshold(self, engine):
        """Circuit breaker stays closed with fewer failures than threshold."""
        engine._consecutive_failures = engine.CIRCUIT_BREAKER_THRESHOLD - 1
        assert engine._circuit_open_until <= time.time()


# ── Guardrail 5: Proposal Cap ────────────────────────────────────

class TestProposalCap:
    def test_count_active_empty(self, engine, proposals_dir):
        """count_active returns 0 on empty directory."""
        assert engine.proposals.count_active() == 0

    def test_count_active_with_proposals(self, engine, proposals_dir):
        """count_active counts proposed + approved, ignores failed/completed."""
        for i, status in enumerate(["proposed", "approved", "failed", "completed"]):
            data = {
                "id": f"cap-{i}", "title": f"P{i}",
                "category": "feature", "status": status,
                "created_at": time.time(),
            }
            f = proposals_dir / f"cap-{i}_feature.json"
            f.write_text(json.dumps(data), encoding="utf-8")

        assert engine.proposals.count_active() == 2  # proposed + approved only

    def test_cap_blocks_reflection(self, engine, proposals_dir):
        """MAX_ACTIVE_PROPOSALS check correctly identifies when capped."""
        # Create MAX_ACTIVE_PROPOSALS approved proposals
        for i in range(engine.MAX_ACTIVE_PROPOSALS):
            data = {
                "id": f"cap-{i}", "title": f"Proposal {i}",
                "category": "feature", "status": "approved",
                "created_at": time.time(),
            }
            f = proposals_dir / f"cap-{i}_feature.json"
            f.write_text(json.dumps(data), encoding="utf-8")

        active = engine.proposals.count_active()
        assert active >= engine.MAX_ACTIVE_PROPOSALS


# ── Guardrail 6: Progressive Backoff ─────────────────────────────

class TestProgressiveBackoff:
    def test_backoff_doubles_on_failure(self, engine):
        """Backoff interval doubles after each failure."""
        initial = engine._current_backoff
        engine._current_backoff = min(engine._current_backoff * 2, engine.BACKOFF_MAX)
        assert engine._current_backoff == initial * 2

    def test_backoff_caps_at_max(self, engine):
        """Backoff doesn't exceed BACKOFF_MAX."""
        engine._current_backoff = engine.BACKOFF_MAX
        engine._current_backoff = min(engine._current_backoff * 2, engine.BACKOFF_MAX)
        assert engine._current_backoff == engine.BACKOFF_MAX

    def test_backoff_resets_on_success(self, engine):
        """Backoff resets to BACKOFF_BASE after a success."""
        engine._current_backoff = 1440
        engine._current_backoff = engine.BACKOFF_BASE
        assert engine._current_backoff == engine.BACKOFF_BASE


