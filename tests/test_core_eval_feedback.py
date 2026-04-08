"""
Comprehensive tests for LocalMind eval, feedback, telemetry, and token_budget modules.

Each module is tested in its own section with 6+ tests.  All database-backed
tests use ``tmp_path`` for isolated SQLite instances and ``unittest.mock.patch``
to redirect ``DB_PATH`` so production data is never touched.
"""

import json
import math
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — schema bootstrap
# ---------------------------------------------------------------------------

def _bootstrap_db(db_path: Path) -> None:
    """Create the full Phase 0 schema + memory tables in an isolated DB.

    Patches DB_PATH in both ``backend.core.schema`` (where it is used by
    ``init_phase0_schema``) and ``backend.config`` for completeness.
    """
    with patch("backend.core.schema.DB_PATH", db_path), \
         patch("backend.config.DB_PATH", db_path):
        from backend.core.schema import init_phase0_schema
        init_phase0_schema()

    # The memories + FTS5 tables live in the agent memory store; create
    # them manually so MemoryPromoter tests work.
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'semantic',
            subcategory TEXT DEFAULT '',
            source TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            created_at REAL NOT NULL,
            accessed_at REAL NOT NULL,
            access_count INTEGER DEFAULT 0,
            relevance_score REAL DEFAULT 1.0
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content,
            category,
            subcategory,
            content='memories',
            content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content, category, subcategory)
            VALUES (new.id, new.content, new.category, new.subcategory);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, category, subcategory)
            VALUES ('delete', old.id, old.content, old.category, old.subcategory);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, category, subcategory)
            VALUES ('delete', old.id, old.content, old.category, old.subcategory);
            INSERT INTO memories_fts(rowid, content, category, subcategory)
            VALUES (new.id, new.content, new.category, new.subcategory);
        END;
    """)
    conn.commit()
    conn.close()


def _seed_default_tenant(db_path: Path) -> dict:
    """Insert a minimal org/workspace/user and return their IDs."""
    from datetime import datetime, timezone
    import uuid

    now = datetime.now(timezone.utc).isoformat()
    org_id = uuid.uuid4().hex
    ws_id = uuid.uuid4().hex
    user_id = uuid.uuid4().hex

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (?,?,?,?,?)",
        (org_id, "TestOrg", "test-org", now, now),
    )
    conn.execute(
        "INSERT INTO workspaces (id, org_id, name, slug, deployment_mode, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (ws_id, org_id, "TestWS", "test-ws", "hybrid", now, now),
    )
    conn.execute(
        "INSERT INTO users (id, org_id, email, display_name, role, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, org_id, "test@test.com", "Tester", "admin", now, now),
    )
    conn.commit()
    conn.close()

    return {"org_id": org_id, "workspace_id": ws_id, "user_id": user_id}


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — backend.core.eval
# ═══════════════════════════════════════════════════════════════════════════


class TestEvalCaseManager:
    """Tests for EvalCaseManager CRUD operations."""

    def test_create_and_get_case(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalCaseManager

            mgr = EvalCaseManager()
            case_id = mgr.create_case(
                workspace_id=tenant["workspace_id"],
                name="greeting-test",
                description="Tests greeting generation",
                input_json='{"prompt": "hello"}',
                expected_output_json='{"reply": "Hi!"}',
                artifact_type="text",
            )

            assert case_id  # non-empty hex UUID
            assert len(case_id) == 32

            fetched = mgr.get_case(case_id)
            assert fetched is not None
            assert fetched["name"] == "greeting-test"
            assert fetched["workspace_id"] == tenant["workspace_id"]
            assert fetched["artifact_type"] == "text"

    def test_list_cases_scoped_by_workspace(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalCaseManager

            mgr = EvalCaseManager()
            mgr.create_case(tenant["workspace_id"], "c1", "d1", "{}", "{}")
            mgr.create_case(tenant["workspace_id"], "c2", "d2", "{}", "{}")

            cases = mgr.list_cases(tenant["workspace_id"])
            assert len(cases) == 2
            assert cases[0]["name"] == "c1"
            assert cases[1]["name"] == "c2"

            # Different workspace returns empty
            empty = mgr.list_cases("nonexistent-ws")
            assert empty == []

    def test_delete_case_removes_runs(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalCaseManager, EvalRunTracker

            case_mgr = EvalCaseManager()
            run_mgr = EvalRunTracker()

            case_id = case_mgr.create_case(
                tenant["workspace_id"], "to-delete", "desc", "{}", "{}"
            )
            run_id = run_mgr.start_run(case_id)
            assert run_mgr.get_run(run_id) is not None

            case_mgr.delete_case(case_id)

            assert case_mgr.get_case(case_id) is None
            assert run_mgr.get_run(run_id) is None

    def test_get_case_not_found(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalCaseManager

            mgr = EvalCaseManager()
            assert mgr.get_case("nonexistent") is None


class TestEvalRunTracker:
    """Tests for EvalRunTracker lifecycle and comparison."""

    def test_start_and_complete_run(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalCaseManager, EvalRunTracker

            case_id = EvalCaseManager().create_case(
                tenant["workspace_id"], "case", "d", "{}", "{}"
            )
            tracker = EvalRunTracker()
            run_id = tracker.start_run(case_id, model_config_json='{"model":"test"}')

            run = tracker.get_run(run_id)
            assert run["status"] == "running"

            tracker.complete_run(run_id, "passed", 0.95, '{"match":true}', 1200)

            completed = tracker.get_run(run_id)
            assert completed["status"] == "passed"
            assert completed["score"] == 0.95
            assert completed["duration_ms"] == 1200

    def test_complete_run_invalid_status(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalCaseManager, EvalRunTracker

            case_id = EvalCaseManager().create_case(
                tenant["workspace_id"], "case", "d", "{}", "{}"
            )
            tracker = EvalRunTracker()
            run_id = tracker.start_run(case_id)

            with pytest.raises(ValueError, match="Invalid eval run status"):
                tracker.complete_run(run_id, "invalid_status", 0.5, "{}", 100)

    def test_compare_runs_improvement(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalCaseManager, EvalRunTracker

            case_id = EvalCaseManager().create_case(
                tenant["workspace_id"], "case", "d", "{}", "{}"
            )
            tracker = EvalRunTracker()

            baseline_id = tracker.start_run(case_id)
            tracker.complete_run(baseline_id, "passed", 0.70, "{}", 500)

            candidate_id = tracker.start_run(case_id)
            tracker.complete_run(candidate_id, "passed", 0.90, "{}", 300)

            result = tracker.compare_runs(baseline_id, candidate_id)
            assert result["verdict"] == "improvement"
            assert result["score_delta"] == pytest.approx(0.20, abs=1e-6)
            assert result["status_changed"] is False
            assert result["duration_delta_ms"] == -200

    def test_compare_runs_regression(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalCaseManager, EvalRunTracker

            case_id = EvalCaseManager().create_case(
                tenant["workspace_id"], "case", "d", "{}", "{}"
            )
            tracker = EvalRunTracker()

            baseline_id = tracker.start_run(case_id)
            tracker.complete_run(baseline_id, "passed", 0.90, "{}", 300)

            candidate_id = tracker.start_run(case_id)
            tracker.complete_run(candidate_id, "failed", 0.40, "{}", 600)

            result = tracker.compare_runs(baseline_id, candidate_id)
            assert result["verdict"] == "regression"
            assert result["status_changed"] is True

    def test_compare_runs_not_found(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalRunTracker

            tracker = EvalRunTracker()
            with pytest.raises(ValueError, match="not found"):
                tracker.compare_runs("nope1", "nope2")

    def test_list_runs_by_case(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import EvalCaseManager, EvalRunTracker

            case_id = EvalCaseManager().create_case(
                tenant["workspace_id"], "case", "d", "{}", "{}"
            )
            tracker = EvalRunTracker()
            tracker.start_run(case_id)
            tracker.start_run(case_id)

            runs = tracker.list_runs(eval_case_id=case_id)
            assert len(runs) == 2


class TestTemplateVersionManager:
    """Tests for template version lifecycle."""

    def test_create_and_promote_to_candidate(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import TemplateVersionManager

            mgr = TemplateVersionManager()
            v_id = mgr.create_version("tmpl-1", '{"nodes":[]}')

            versions = mgr.list_versions("tmpl-1")
            assert len(versions) == 1
            assert versions[0]["lifecycle_state"] == "draft"
            assert versions[0]["version_number"] == 1

            mgr.promote(v_id, promoted_by=tenant["user_id"], target_state="candidate")

            v = mgr.list_versions("tmpl-1")[0]
            assert v["lifecycle_state"] == "candidate"

    def test_promote_candidate_to_approved(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import TemplateVersionManager

            mgr = TemplateVersionManager()
            v_id = mgr.create_version("tmpl-2", '{}')
            mgr.promote(v_id, promoted_by=tenant["user_id"], target_state="candidate")
            mgr.promote(v_id, promoted_by=tenant["user_id"], target_state="approved")

            active = mgr.get_active_version("tmpl-2")
            assert active is not None
            assert active["lifecycle_state"] == "approved"

    def test_promote_invalid_transition(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import TemplateVersionManager

            mgr = TemplateVersionManager()
            v_id = mgr.create_version("tmpl-3", '{}')

            # draft -> approved is not a valid transition
            with pytest.raises(ValueError, match="Cannot promote"):
                mgr.promote(v_id, promoted_by=tenant["user_id"], target_state="approved")

    def test_deprecate_version(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import TemplateVersionManager

            mgr = TemplateVersionManager()
            v_id = mgr.create_version("tmpl-4", '{}')

            mgr.deprecate(v_id)
            v = mgr.list_versions("tmpl-4")[0]
            assert v["lifecycle_state"] == "deprecated"

    def test_deprecate_already_deprecated(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import TemplateVersionManager

            mgr = TemplateVersionManager()
            v_id = mgr.create_version("tmpl-5", '{}')
            mgr.deprecate(v_id)

            with pytest.raises(ValueError, match="already deprecated"):
                mgr.deprecate(v_id)

    def test_auto_increment_version_number(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import TemplateVersionManager

            mgr = TemplateVersionManager()
            mgr.create_version("tmpl-6", '{"v":1}')
            mgr.create_version("tmpl-6", '{"v":2}')
            mgr.create_version("tmpl-6", '{"v":3}')

            versions = mgr.list_versions("tmpl-6")
            assert len(versions) == 3
            # list_versions returns DESC order
            assert versions[0]["version_number"] == 3
            assert versions[2]["version_number"] == 1


class TestPromptVersionManager:
    """Tests for prompt version lifecycle."""

    def test_create_and_approve(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import PromptVersionManager

            mgr = PromptVersionManager()
            v_id = mgr.create_version("system_default", "You are a helpful assistant.")

            mgr.promote(v_id, target_state="approved")

            active = mgr.get_active(name="system_default")
            assert active is not None
            assert active["content"] == "You are a helpful assistant."

    def test_get_active_returns_none_when_no_approved(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import PromptVersionManager

            mgr = PromptVersionManager()
            mgr.create_version("draft_only", "Draft content")

            assert mgr.get_active("draft_only") is None


class TestModelRegistry:
    """Tests for model registry CRUD."""

    def test_register_and_get_model(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import ModelRegistry

            reg = ModelRegistry()
            mid = reg.register_model(
                workspace_id=tenant["workspace_id"],
                name="GPT-4o",
                provider="openai",
                model_id="gpt-4o-2024",
                default_for="execution",
            )

            model = reg.get_model(mid, tenant["workspace_id"])
            assert model is not None
            assert model["name"] == "GPT-4o"
            assert model["_capabilities"]["default_for"] == "execution"

    def test_list_models_by_provider(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import ModelRegistry

            reg = ModelRegistry()
            reg.register_model(tenant["workspace_id"], "M1", "ollama", "qwen:7b")
            reg.register_model(tenant["workspace_id"], "M2", "openai", "gpt-4o")
            reg.register_model(tenant["workspace_id"], "M3", "ollama", "llama3:70b")

            ollama_models = reg.list_models(tenant["workspace_id"], provider="ollama")
            assert len(ollama_models) == 2
            all_names = {m["name"] for m in ollama_models}
            assert all_names == {"M1", "M3"}

    def test_set_and_get_default(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import ModelRegistry

            reg = ModelRegistry()
            mid = reg.register_model(
                tenant["workspace_id"], "Default", "ollama", "qwen:14b"
            )
            reg.set_default(mid, "planning")

            default = reg.get_default(tenant["workspace_id"], "planning")
            assert default is not None
            assert default["id"] == mid

    def test_disable_model(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import ModelRegistry

            reg = ModelRegistry()
            mid = reg.register_model(
                tenant["workspace_id"], "ToDisable", "ollama", "test"
            )
            reg.disable_model(mid)

            # Disabled model should not appear in active list
            models = reg.list_models(tenant["workspace_id"])
            assert all(m["id"] != mid for m in models)

    def test_get_model_by_name(self, tmp_path):
        db = tmp_path / "eval.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        with patch("backend.core.eval.DB_PATH", db):
            from backend.core.eval import ModelRegistry

            reg = ModelRegistry()
            reg.register_model(
                tenant["workspace_id"], "ByName", "anthropic", "claude-3"
            )

            model = reg.get_model("ByName", tenant["workspace_id"])
            assert model is not None
            assert model["provider"] == "anthropic"


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — backend.core.feedback
# ═══════════════════════════════════════════════════════════════════════════


class TestCorrectionManager:
    """Tests for CorrectionManager submit/review cycle."""

    def _make_job(self, db_path, tenant):
        """Insert a stub job row so foreign keys are satisfied."""
        from datetime import datetime, timezone
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        job_id = uuid.uuid4().hex
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO jobs (id, workspace_id, title, source, status, requester, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, tenant["workspace_id"], "test job", "web", "completed",
             tenant["user_id"], now, now),
        )
        conn.commit()
        conn.close()
        return job_id

    def test_submit_and_get_correction(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)
        job_id = self._make_job(db, tenant)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import CorrectionManager

            mgr = CorrectionManager()
            cid = mgr.submit_correction(
                job_id=job_id,
                submitted_by=tenant["user_id"],
                correction_type="factual",
                original_value="Paris",
                corrected_value="London",
                user_instruction="Wrong capital city",
            )

            assert len(cid) == 32

            correction = mgr.get_correction(cid)
            assert correction is not None
            assert correction["correction_type"] == "factual"
            assert correction["status"] == "pending"
            assert correction["original_value"] == "Paris"

    def test_submit_invalid_type(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)
        job_id = self._make_job(db, tenant)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import CorrectionManager

            mgr = CorrectionManager()
            with pytest.raises(ValueError, match="Invalid correction_type"):
                mgr.submit_correction(
                    job_id=job_id,
                    submitted_by=tenant["user_id"],
                    correction_type="nonexistent",
                )

    def test_list_corrections_filter_by_status(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)
        job_id = self._make_job(db, tenant)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import CorrectionManager

            mgr = CorrectionManager()
            mgr.submit_correction(job_id, tenant["user_id"], "factual")
            mgr.submit_correction(job_id, tenant["user_id"], "formatting")

            pending = mgr.list_corrections(status="pending")
            assert len(pending) == 2

            dismissed = mgr.list_corrections(status="dismissed")
            assert len(dismissed) == 0

    def test_review_correction_promote(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)
        job_id = self._make_job(db, tenant)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import CorrectionManager

            mgr = CorrectionManager()
            cid = mgr.submit_correction(job_id, tenant["user_id"], "factual")

            mgr.review_correction(cid, tenant["user_id"], "promote")

            c = mgr.get_correction(cid)
            assert c["status"] == "promoted"
            assert c["reviewed_by"] == tenant["user_id"]

    def test_review_correction_invalid_action(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)
        job_id = self._make_job(db, tenant)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import CorrectionManager

            mgr = CorrectionManager()
            cid = mgr.submit_correction(job_id, tenant["user_id"], "factual")

            with pytest.raises(ValueError, match="Invalid action"):
                mgr.review_correction(cid, tenant["user_id"], "approve")

    def test_get_pending_corrections(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)
        job_id = self._make_job(db, tenant)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import CorrectionManager

            mgr = CorrectionManager()
            c1 = mgr.submit_correction(job_id, tenant["user_id"], "factual")
            c2 = mgr.submit_correction(job_id, tenant["user_id"], "formatting")
            mgr.review_correction(c1, tenant["user_id"], "dismiss")

            pending = mgr.get_pending_corrections()
            assert len(pending) == 1
            assert pending[0]["id"] == c2


class TestMemoryPromoter:
    """Tests for promoting corrections to memories."""

    def _setup(self, db, tenant):
        """Insert a job and correction, return (job_id, correction_id)."""
        from datetime import datetime, timezone
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        job_id = uuid.uuid4().hex
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO jobs (id, workspace_id, title, source, status, requester, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, tenant["workspace_id"], "job", "web", "completed",
             tenant["user_id"], now, now),
        )
        conn.commit()
        conn.close()

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import CorrectionManager
            cid = CorrectionManager().submit_correction(
                job_id, tenant["user_id"], "factual",
                original_value="old", corrected_value="new",
            )
        return job_id, cid

    def test_promote_to_memory(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)
        _, cid = self._setup(db, tenant)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import MemoryPromoter, CorrectionManager

            promoter = MemoryPromoter()
            mem_id = promoter.promote_to_memory(
                correction_id=cid,
                promoted_by=tenant["user_id"],
                memory_content="Always use Oxford commas",
                memory_type="org_style_rule",
                scope_type="workspace",
                scope_id=tenant["workspace_id"],
            )

            assert isinstance(mem_id, int)
            assert mem_id > 0

            # Correction status should be updated
            c = CorrectionManager().get_correction(cid)
            assert c["status"] == "promoted"

    def test_promote_invalid_memory_type(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)
        _, cid = self._setup(db, tenant)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import MemoryPromoter

            promoter = MemoryPromoter()
            with pytest.raises(ValueError, match="Invalid memory_type"):
                promoter.promote_to_memory(
                    cid, tenant["user_id"], "content", memory_type="bad_type"
                )

    def test_promote_invalid_scope_type(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)
        _, cid = self._setup(db, tenant)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import MemoryPromoter

            promoter = MemoryPromoter()
            with pytest.raises(ValueError, match="Invalid scope_type"):
                promoter.promote_to_memory(
                    cid, tenant["user_id"], "content", scope_type="bad_scope"
                )

    def test_promote_nonexistent_correction(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        _seed_default_tenant(db)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import MemoryPromoter

            promoter = MemoryPromoter()
            with pytest.raises(ValueError, match="Correction not found"):
                promoter.promote_to_memory("nonexistent", "u1", "content")


class TestFileDiffCorrector:
    """Tests for extracting corrections from file diffs."""

    def test_compute_changes_replacement(self):
        from backend.core.feedback import FileDiffCorrector

        changes = FileDiffCorrector._compute_changes(
            "Hello world\nLine two\n",
            "Hello earth\nLine two\n",
        )

        assert len(changes) == 1
        assert "Hello world" in changes[0]["original"]
        assert "Hello earth" in changes[0]["corrected"]

    def test_compute_changes_insertion(self):
        from backend.core.feedback import FileDiffCorrector

        changes = FileDiffCorrector._compute_changes(
            "Line one\nLine three\n",
            "Line one\nLine two\nLine three\n",
        )

        assert len(changes) >= 1
        # At least one change should contain the inserted line
        all_corrected = " ".join(c["corrected"] for c in changes)
        assert "Line two" in all_corrected

    def test_compute_changes_no_diff(self):
        from backend.core.feedback import FileDiffCorrector

        changes = FileDiffCorrector._compute_changes(
            "Same content\n",
            "Same content\n",
        )
        assert changes == []


class TestCorrectionStats:
    """Tests for correction analytics."""

    def test_get_stats_empty(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import CorrectionStats

            stats = CorrectionStats().get_stats()
            assert stats["total"] == 0
            assert stats["by_type"] == {}
            assert stats["by_status"] == {}
            assert stats["promotion_rate"] == 0.0

    def test_get_stats_with_corrections(self, tmp_path):
        db = tmp_path / "fb.db"
        _bootstrap_db(db)
        tenant = _seed_default_tenant(db)

        # Seed a job
        from datetime import datetime, timezone
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        job_id = uuid.uuid4().hex
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO jobs (id, workspace_id, title, source, status, requester, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, tenant["workspace_id"], "j", "web", "completed",
             tenant["user_id"], now, now),
        )
        conn.commit()
        conn.close()

        with patch("backend.core.feedback.DB_PATH", db):
            from backend.core.feedback import CorrectionManager, CorrectionStats

            mgr = CorrectionManager()
            c1 = mgr.submit_correction(job_id, tenant["user_id"], "factual")
            c2 = mgr.submit_correction(job_id, tenant["user_id"], "formatting")
            mgr.review_correction(c1, tenant["user_id"], "promote")
            mgr.review_correction(c2, tenant["user_id"], "dismiss")

            stats = CorrectionStats().get_stats()
            assert stats["total"] == 2
            assert stats["by_type"]["factual"] == 1
            assert stats["by_type"]["formatting"] == 1
            assert stats["promotion_rate"] == 0.5


# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — backend.core.telemetry
# ═══════════════════════════════════════════════════════════════════════════


class TestTraceManager:
    """Tests for TraceManager span lifecycle."""

    def test_start_trace(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import TraceManager, init_telemetry_schema
            init_telemetry_schema()

            tm = TraceManager()
            trace = tm.start_trace("test-trace")

            assert trace.trace_id
            assert trace.name == "test-trace"
            assert trace.spans == []

    def test_start_and_end_span(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import TraceManager, init_telemetry_schema
            init_telemetry_schema()

            tm = TraceManager()
            trace = tm.start_trace("span-test")
            span = tm.start_span(trace.trace_id, "llm_call", attributes={"model": "qwen"})

            assert span.status == "running"
            assert span.attributes == {"model": "qwen"}

            tm.end_span(span.span_id, status="ok", attributes={"tokens": 42})

            full_trace = tm.get_trace(trace.trace_id)
            assert len(full_trace["spans"]) == 1
            s = full_trace["spans"][0]
            assert s["status"] == "ok"
            assert s["attributes"]["model"] == "qwen"
            assert s["attributes"]["tokens"] == 42
            assert s["duration_ms"] is not None
            assert s["duration_ms"] >= 0

    def test_span_context_manager_ok(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import TraceManager, init_telemetry_schema
            init_telemetry_schema()

            tm = TraceManager()
            trace = tm.start_trace("cm-test")

            with tm.span(trace.trace_id, "tool_call", {"tool": "search"}) as s:
                s.attributes["result_count"] = 5

            full = tm.get_trace(trace.trace_id)
            assert full["spans"][0]["status"] == "ok"
            assert full["spans"][0]["attributes"]["result_count"] == 5

    def test_span_context_manager_error(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import TraceManager, init_telemetry_schema
            init_telemetry_schema()

            tm = TraceManager()
            trace = tm.start_trace("err-test")

            with pytest.raises(RuntimeError):
                with tm.span(trace.trace_id, "failing_op") as s:
                    raise RuntimeError("boom")

            full = tm.get_trace(trace.trace_id)
            assert full["spans"][0]["status"] == "error"

    def test_get_trace_empty(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import TraceManager, init_telemetry_schema
            init_telemetry_schema()

            tm = TraceManager()
            result = tm.get_trace("nonexistent-trace-id")
            assert result["trace_id"] == "nonexistent-trace-id"
            assert result["spans"] == []

    def test_nested_spans(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import TraceManager, init_telemetry_schema
            init_telemetry_schema()

            tm = TraceManager()
            trace = tm.start_trace("nested")

            parent = tm.start_span(trace.trace_id, "parent_op")
            child = tm.start_span(trace.trace_id, "child_op", parent_span_id=parent.span_id)

            tm.end_span(child.span_id, status="ok")
            tm.end_span(parent.span_id, status="ok")

            full = tm.get_trace(trace.trace_id)
            assert len(full["spans"]) == 2
            child_span = [s for s in full["spans"] if s["name"] == "child_op"][0]
            assert child_span["parent_span_id"] == parent.span_id


class TestMetricsCollector:
    """Tests for MetricsCollector record and query methods."""

    def test_record_llm_call(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import MetricsCollector, init_telemetry_schema
            init_telemetry_schema()

            mc = MetricsCollector()
            mid = mc.record_llm_call("qwen:14b", 100, 50, 1500.0, 0.0)
            assert mid  # non-empty UUID string

    def test_record_tool_call(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import MetricsCollector, init_telemetry_schema
            init_telemetry_schema()

            mc = MetricsCollector()
            mid = mc.record_tool_call("web_search", "ok", 250.0)
            assert mid

    def test_record_job_completion(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import MetricsCollector, init_telemetry_schema
            init_telemetry_schema()

            mc = MetricsCollector()
            mid = mc.record_job_completion("job-1", "completed", 5000.0, 3, 0.05)
            assert mid

    def test_get_metrics_summary(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import MetricsCollector, init_telemetry_schema
            init_telemetry_schema()

            mc = MetricsCollector()
            mc.record_llm_call("qwen:14b", 100, 50, 1000.0)
            mc.record_llm_call("qwen:14b", 200, 100, 2000.0)
            mc.record_tool_call("search", "ok", 300.0)
            mc.record_tool_call("write_file", "error", 100.0)
            mc.record_job_completion("j1", "completed", 5000.0, 3, 0.1)
            mc.record_job_completion("j2", "failed", 3000.0, 2, 0.05)

            summary = mc.get_metrics_summary()

            assert summary["llm_calls"]["total"] == 2
            assert summary["llm_calls"]["tokens_in"] == 300
            assert summary["llm_calls"]["tokens_out"] == 150
            assert summary["llm_calls"]["avg_latency_ms"] == 1500.0

            assert summary["tool_calls"]["total"] == 2
            assert summary["tool_calls"]["success"] == 1
            assert summary["tool_calls"]["error"] == 1

            assert summary["job_completions"]["total"] == 2
            assert summary["job_completions"]["completed"] == 1
            assert summary["job_completions"]["failed"] == 1

    def test_get_metrics_summary_empty(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import MetricsCollector, init_telemetry_schema
            init_telemetry_schema()

            mc = MetricsCollector()
            summary = mc.get_metrics_summary()

            assert summary["llm_calls"]["total"] == 0
            assert summary["tool_calls"]["total"] == 0
            assert summary["job_completions"]["total"] == 0


class TestAlertManager:
    """Tests for AlertManager threshold evaluation and alert firing."""

    def test_fire_alert_returns_payload(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import AlertManager, init_telemetry_schema
            init_telemetry_schema()

            am = AlertManager()
            payload = am.fire_alert(
                "high_failure_rate",
                "50% failure rate",
                {"total": 10, "failed": 5},
            )

            assert payload["alert_type"] == "high_failure_rate"
            assert payload["message"] == "50% failure rate"
            assert payload["details"]["failed"] == 5
            assert "fired_at" in payload

    def test_check_thresholds_no_alerts(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import (
                AlertManager, MetricsCollector, init_telemetry_schema,
            )
            init_telemetry_schema()

            mc = MetricsCollector()
            # All jobs completed successfully, low latency
            mc.record_job_completion("j1", "completed", 1000.0, 2, 0.01)
            mc.record_job_completion("j2", "completed", 2000.0, 3, 0.02)

            am = AlertManager(failure_rate_pct=25.0, p95_latency_ms=60000)
            alerts = am.check_thresholds()

            # No failure rate or latency alerts expected
            alert_types = [a["alert_type"] for a in alerts]
            assert "high_failure_rate" not in alert_types
            assert "high_latency" not in alert_types

    def test_check_thresholds_high_failure_rate(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import (
                AlertManager, MetricsCollector, init_telemetry_schema,
            )
            init_telemetry_schema()

            # Also create a jobs table so queue_depth check doesn't error
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, status TEXT, "
                "workspace_id TEXT, title TEXT, source TEXT, created_at TEXT, updated_at TEXT)"
            )
            conn.commit()
            conn.close()

            mc = MetricsCollector()
            # 3 out of 4 jobs failed = 75% failure rate
            mc.record_job_completion("j1", "completed", 1000.0, 2, 0.01)
            mc.record_job_completion("j2", "failed", 2000.0, 3, 0.02)
            mc.record_job_completion("j3", "failed", 1500.0, 2, 0.01)
            mc.record_job_completion("j4", "failed", 3000.0, 1, 0.03)

            am = AlertManager(failure_rate_pct=25.0)
            alerts = am.check_thresholds()

            alert_types = [a["alert_type"] for a in alerts]
            assert "high_failure_rate" in alert_types


class TestHealthChecker:
    """Tests for HealthChecker (liveness only -- readiness/deep need network)."""

    @pytest.mark.asyncio
    async def test_check_liveness_pass(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import HealthChecker, init_telemetry_schema
            init_telemetry_schema()

            hc = HealthChecker()
            result = await hc.check_liveness()

            assert result.healthy is True
            assert "db" in result.checks
            assert result.checks["db"].status == "pass"

    @pytest.mark.asyncio
    async def test_check_liveness_to_dict(self, tmp_path):
        db = tmp_path / "tel.db"

        with patch("backend.core.telemetry.DB_PATH", db):
            from backend.core.telemetry import HealthChecker, init_telemetry_schema
            init_telemetry_schema()

            hc = HealthChecker()
            result = await hc.check_liveness()
            d = result.to_dict()

            assert isinstance(d, dict)
            assert d["healthy"] is True
            assert "db" in d["checks"]
            assert d["checks"]["db"]["status"] == "pass"


# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — backend.core.token_budget
# ═══════════════════════════════════════════════════════════════════════════


class TestTokenEstimator:
    """Tests for heuristic token estimation."""

    def test_estimate_empty_string(self):
        from backend.core.token_budget import TokenEstimator

        assert TokenEstimator.estimate_tokens("") == 0

    def test_estimate_basic_text(self):
        from backend.core.token_budget import TokenEstimator

        # "Hello world" = 11 chars, ~4 chars/token -> ~3 tokens
        result = TokenEstimator.estimate_tokens("Hello world")
        assert result >= 1
        assert result == math.ceil(11 / 4.0)

    def test_estimate_model_specific_ratio(self):
        from backend.core.token_budget import TokenEstimator

        text = "a" * 100
        # qwen family uses 3.8 chars/token
        qwen_est = TokenEstimator.estimate_tokens(text, "qwen2.5-coder:14b")
        default_est = TokenEstimator.estimate_tokens(text)

        assert qwen_est == math.ceil(100 / 3.8)
        assert default_est == math.ceil(100 / 4.0)
        assert qwen_est > default_est  # lower ratio = more tokens

    def test_estimate_prompt_tokens_breakdown(self):
        from backend.core.token_budget import TokenEstimator

        result = TokenEstimator.estimate_prompt_tokens(
            system_prompt="You are a helpful assistant.",
            instructions="Summarize the document.",
            tool_schemas='{"tools":[]}',
            input_data="The quick brown fox jumps.",
        )

        assert "system" in result
        assert "tools" in result
        assert "instructions" in result
        assert "input" in result
        assert "total" in result
        assert result["total"] == (
            result["system"] + result["tools"] + result["instructions"] + result["input"]
        )

    def test_estimate_always_at_least_one(self):
        from backend.core.token_budget import TokenEstimator

        # Even a single character should produce at least 1 token
        assert TokenEstimator.estimate_tokens("x") == 1


class TestContextWindowFor:
    """Tests for context_window_for lookup."""

    def test_known_model(self):
        from backend.core.token_budget import context_window_for

        assert context_window_for("qwen2.5-coder:32b") == 32768
        assert context_window_for("gemini-flash") == 1_000_000

    def test_unknown_model_uses_default(self):
        from backend.core.token_budget import context_window_for, DEFAULT_CONTEXT_WINDOW

        assert context_window_for("unknown-model") == DEFAULT_CONTEXT_WINDOW

    def test_none_model_uses_default(self):
        from backend.core.token_budget import context_window_for, DEFAULT_CONTEXT_WINDOW

        assert context_window_for(None) == DEFAULT_CONTEXT_WINDOW


class TestTokenBudgetAllocator:
    """Tests for budget allocation logic."""

    def test_basic_allocation(self):
        from backend.core.token_budget import TokenBudgetAllocator, TokenEstimator

        alloc = TokenBudgetAllocator()
        node = {
            "id": "node-1",
            "system_prompt": "You are helpful.",
            "instructions": "Summarize this document.",
            "tools_allowed": "",
            "input_json": "Short input text.",
            "expected_output": "",
        }
        budget = alloc.allocate_budget(node, context_window=8192)

        assert budget.context_window == 8192
        assert budget.safety_margin == math.ceil(8192 * 0.10)
        assert budget.total > 0
        assert budget.budget_remaining >= 0
        assert budget.system_tokens > 0
        assert budget.instruction_tokens > 0

    def test_input_truncation_warning(self):
        from backend.core.token_budget import TokenBudgetAllocator

        alloc = TokenBudgetAllocator()
        # Make input far larger than the context window
        huge_input = "x" * 100000
        node = {
            "id": "node-big",
            "system_prompt": "",
            "instructions": "",
            "tools_allowed": "",
            "input_json": huge_input,
            "expected_output": "",
        }
        budget = alloc.allocate_budget(node, context_window=1024)

        # Input should have been capped, budget_remaining should be near 0
        assert budget.total <= budget.context_window
        # The input_tokens should be less than the naive estimate
        from backend.core.token_budget import TokenEstimator
        naive = TokenEstimator.estimate_tokens(huge_input)
        assert budget.input_tokens < naive

    def test_output_tokens_at_least_quarter(self):
        from backend.core.token_budget import TokenBudgetAllocator

        alloc = TokenBudgetAllocator()
        node = {"id": "n", "system_prompt": "", "instructions": "",
                "tools_allowed": "", "input_json": "", "expected_output": ""}
        budget = alloc.allocate_budget(node, context_window=8192)

        usable = 8192 - budget.safety_margin
        assert budget.output_tokens >= usable // 4


class TestPromptCompiler:
    """Tests for prompt compilation with token tracking."""

    def test_compile_basic(self):
        from backend.core.token_budget import PromptCompiler

        compiler = PromptCompiler()
        node = {
            "system_prompt": "You are a task worker.",
            "instructions": "Process the input.",
            "tools_allowed": "",
        }
        result = compiler.compile(node, input_data={"text": "hello"})

        assert "[SYSTEM]" in result.prompt_text
        assert "[INSTRUCTIONS]" in result.prompt_text
        assert "[INPUT]" in result.prompt_text
        assert result.token_counts["total"] > 0
        assert result.truncated is False

    def test_compile_with_truncation(self):
        from backend.core.token_budget import PromptCompiler

        compiler = PromptCompiler()
        node = {
            "system_prompt": "System.",
            "instructions": "Instructions.",
            "tools_allowed": "",
        }
        huge_data = {"content": "x" * 100000}
        result = compiler.compile(node, huge_data, context_window=512)

        assert result.truncated is True
        assert result.truncation_warning is not None

    def test_apply_input_filter_index(self):
        from backend.core.token_budget import PromptCompiler

        result = PromptCompiler._apply_input_filter(
            {"slides": ["a", "b", "c", "d", "e"], "title": "Deck"},
            {"slides": [0, 2, 4]},
        )

        assert result["slides"] == ["a", "c", "e"]
        assert result["title"] == "Deck"  # unfiltered keys preserved

    def test_apply_input_filter_none(self):
        from backend.core.token_budget import PromptCompiler

        data = {"key": "value"}
        assert PromptCompiler._apply_input_filter(data, None) is data

    def test_compact_tools_strips_examples(self):
        from backend.core.token_budget import PromptCompiler

        schema = json.dumps([{
            "name": "search",
            "description": "A" * 200,
            "examples": ["example1", "example2"],
            "parameters": {"query": {"type": "string"}},
        }])
        compacted = PromptCompiler._compact_tools(schema)
        parsed = json.loads(compacted)

        assert "examples" not in parsed[0]
        # Description should be truncated to 120 chars (117 + "...")
        assert len(parsed[0]["description"]) == 120

    def test_compact_tools_passthrough_non_json(self):
        from backend.core.token_budget import PromptCompiler

        plain = "web_search,read_file,write_file"
        assert PromptCompiler._compact_tools(plain) == plain


class TestRetryContextOptimizer:
    """Tests for retry prompt optimisation."""

    def test_compile_retry_prompt(self):
        from backend.core.token_budget import RetryContextOptimizer

        opt = RetryContextOptimizer()
        node = {
            "id": "node-retry",
            "system_prompt": "Be helpful.",
            "instructions": "Generate a report.",
            "tools_allowed": "",
            "input_json": "Full document text here " * 100,
        }
        result = opt.compile_retry_prompt(
            node,
            error_message="JSON parse error at line 5",
            failed_input_section="The problematic section",
        )

        assert "RETRY" in result.prompt_text
        assert "JSON parse error" in result.prompt_text
        assert "problematic section" in result.prompt_text
        assert result.token_counts["total"] > 0

    def test_compile_retry_without_failed_section(self):
        from backend.core.token_budget import RetryContextOptimizer

        opt = RetryContextOptimizer()
        node = {
            "id": "node-retry2",
            "system_prompt": "",
            "instructions": "Do stuff.",
            "tools_allowed": "",
            "input_json": "Some input data",
        }
        result = opt.compile_retry_prompt(
            node,
            error_message="Timeout exceeded",
        )

        assert "RETRY" in result.prompt_text
        assert "Timeout exceeded" in result.prompt_text


class TestTokenUsageTracker:
    """Tests for TokenUsageTracker recording and budget alerting."""

    def test_record_usage_creates_row(self, tmp_path):
        db = tmp_path / "tok.db"
        _bootstrap_db(db)

        with patch("backend.core.token_budget.DB_PATH", db):
            from backend.core.token_budget import TokenUsageTracker

            TokenUsageTracker.record_usage(
                job_id="job1",
                node_id="node1",
                attempt_id="attempt1",
                model_id="qwen:14b",
                tokens_in=500,
                tokens_out=200,
                estimated_cost_cents=0.01,
            )

            # Verify the row was created
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM node_attempts WHERE id = ?", ("attempt1",)
            ).fetchone()
            conn.close()

            assert row is not None
            assert row["tokens_in"] == 500
            assert row["tokens_out"] == 200
            assert row["model_used"] == "qwen:14b"

    def test_record_usage_updates_existing_row(self, tmp_path):
        db = tmp_path / "tok.db"
        _bootstrap_db(db)

        # Pre-insert a node_attempts row
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO node_attempts (id, node_id, attempt_number, status, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("att-existing", "n1", 1, "running", now),
        )
        conn.commit()
        conn.close()

        with patch("backend.core.token_budget.DB_PATH", db):
            from backend.core.token_budget import TokenUsageTracker

            TokenUsageTracker.record_usage(
                "j1", "n1", "att-existing", "qwen:7b", 100, 50, 0.005
            )

            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM node_attempts WHERE id = ?", ("att-existing",)
            ).fetchone()
            conn.close()

            assert row["tokens_in"] == 100
            assert row["tokens_out"] == 50
            assert row["model_used"] == "qwen:7b"

    def test_check_budget_alert_triggers(self):
        from backend.core.token_budget import TokenUsageTracker, TokenBudget

        budget = TokenBudget(
            system_tokens=100, tool_tokens=50, instruction_tokens=50,
            input_tokens=200, output_tokens=100, safety_margin=50,
            total=500, context_window=1000, budget_remaining=450,
        )

        # 2x budget = 1000 tokens. Using 1001 should trigger.
        assert TokenUsageTracker.check_budget_alert("n1", 1001, budget) is True

    def test_check_budget_alert_no_trigger(self):
        from backend.core.token_budget import TokenUsageTracker, TokenBudget

        budget = TokenBudget(
            system_tokens=100, tool_tokens=50, instruction_tokens=50,
            input_tokens=200, output_tokens=100, safety_margin=50,
            total=500, context_window=1000, budget_remaining=450,
        )

        # Using exactly 1000 should NOT trigger (needs > 2x)
        assert TokenUsageTracker.check_budget_alert("n1", 999, budget) is False
        assert TokenUsageTracker.check_budget_alert("n1", 1000, budget) is False
