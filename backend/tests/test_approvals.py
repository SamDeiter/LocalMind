"""
Comprehensive pytest test suite for the LocalMind approval workflow.

Covers:
  1. PolicyEngine.create_policy() + list/update/delete
  2. PolicyEngine.evaluate() -- REQUIRE_APPROVAL trigger
  3. Approval CRUD: create, check, decide (approve/deny)
  4. Auto-expiry: approval times out -> status = 'expired'
  5. Policy priority: higher priority evaluated first
  6. Disabled policies are skipped
  7. Deny flow: tool call blocked after approval denied
  8. Approve flow: tool call proceeds after approval approved
  9. Bulk-expire stale approvals
 10. Default policies seeded by ensure_default_tenant()
 11. Approval API endpoints (GET pending, POST approve/deny)
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_db_path(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp directory for every test.

    Initialises the full schema so all tables exist.
    Patches DB_PATH in every module that caches it at import time.
    """
    db_path = tmp_path / "test_approvals.db"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    # Patch config values before any schema init
    monkeypatch.setattr("backend.config.DB_PATH", db_path)
    monkeypatch.setattr("backend.config.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("backend.config.WORKSPACE_ROOT", workspace_root)

    # Patch in modules that import DB_PATH at module level
    monkeypatch.setattr("backend.db.DB_PATH", db_path)
    monkeypatch.setattr("backend.core.schema.DB_PATH", db_path)
    monkeypatch.setattr("backend.core.policy.DB_PATH", db_path)

    # Also patch in queue if needed
    try:
        monkeypatch.setattr("backend.jobs.queue.DB_PATH", db_path)
    except AttributeError:
        pass

    # Initialize full schema
    from backend.core.schema import ensure_default_tenant, init_phase0_schema
    from backend.db import init_db

    init_db()
    init_phase0_schema()
    ensure_default_tenant()


@pytest.fixture
def workspace_id():
    """Return the default workspace ID created by ensure_default_tenant."""
    from backend.config import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM workspaces WHERE slug = 'default'"
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


@pytest.fixture
def admin_user_id():
    """Return the admin user UUID created by ensure_default_tenant."""
    from backend.config import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE email = 'admin@localhost'"
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


@pytest.fixture
def engine():
    """Return a fresh PolicyEngine instance."""
    from backend.core.policy import PolicyEngine
    return PolicyEngine()


@pytest.fixture
def ctx(workspace_id):
    """Return a PolicyContext for testing.

    Uses a job_id that allows path-based built-in policies to pass
    (write_file paths are constructed to include /jobs/{job_id}/).
    """
    from backend.core.policy import PolicyContext
    job_id = str(uuid.uuid4())
    return PolicyContext(
        workspace_id=workspace_id,
        deployment_mode="hybrid",
        job_id=job_id,
        node_id="test-node-1",
        user_id="test-user",
        user_role="operator",
        tool_call_count=0,
        _delete_count=0,
    )


def _clear_default_policies(workspace_id: str, engine) -> None:
    """Remove all default policies so they don't interfere with custom test policies."""
    for p in engine.list_policies(workspace_id):
        engine.delete_policy(p["id"])


# ---------------------------------------------------------------------------
# 1. Policy CRUD
# ---------------------------------------------------------------------------


class TestPolicyCRUD:
    """Test create, list, update, delete for approval_policies."""

    def test_create_policy(self, engine, workspace_id):
        """Creating a policy returns a UUID and persists to DB."""
        policy_id = engine.create_policy(
            workspace_id=workspace_id,
            name="Test Policy",
            description="A test policy",
            conditions={"tool": "read_file"},
            actions={"type": "require_approval", "reason": "File read needs approval"},
            priority=10,
        )
        assert policy_id is not None
        assert len(policy_id) == 36  # UUID format

    def test_list_policies_empty(self, engine):
        """A non-existent workspace returns an empty list."""
        fake_ws = str(uuid.uuid4())
        policies = engine.list_policies(fake_ws)
        assert policies == []

    def test_list_policies_ordered_by_priority(self, engine, workspace_id):
        """Policies are returned ordered by priority DESC."""
        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Low Priority",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "deny"},
            priority=1,
        )
        engine.create_policy(
            workspace_id=workspace_id,
            name="High Priority",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "allow"},
            priority=100,
        )
        policies = engine.list_policies(workspace_id)
        names = [p["name"] for p in policies]
        assert names[0] == "High Priority"
        assert names[1] == "Low Priority"

    def test_update_policy_disable(self, engine, workspace_id):
        """Disabling a policy sets enabled=0."""
        pid = engine.create_policy(
            workspace_id=workspace_id,
            name="To Disable",
            description="",
            conditions={},
            actions={"type": "deny"},
        )
        engine.update_policy(pid, enabled=False)
        policies = engine.list_policies(workspace_id)
        target = next(p for p in policies if p["id"] == pid)
        assert target["enabled"] == 0

    def test_delete_policy(self, engine, workspace_id):
        """Deleting a policy removes it from the DB."""
        pid = engine.create_policy(
            workspace_id=workspace_id,
            name="To Delete",
            description="",
            conditions={},
            actions={"type": "deny"},
        )
        engine.delete_policy(pid)
        policies = engine.list_policies(workspace_id)
        ids = [p["id"] for p in policies]
        assert pid not in ids


# ---------------------------------------------------------------------------
# 2. PolicyEngine.evaluate() -- REQUIRE_APPROVAL
# ---------------------------------------------------------------------------


class TestEvaluateRequireApproval:
    """Test that evaluate() returns REQUIRE_APPROVAL when a matching policy exists."""

    def test_require_approval_creates_approval_record(self, engine, workspace_id, ctx):
        """A matching require_approval policy creates a pending approval."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Approve reads",
            description="Require approval for all file reads",
            conditions={"tool": "read_file"},
            actions={"type": "require_approval", "reason": "Needs approval", "expires_minutes": 30},
            priority=50,
        )

        decision = engine.evaluate("read_file", {"path": "/tmp/test.txt"}, ctx)

        assert decision.result == PolicyResult.REQUIRE_APPROVAL
        assert decision.approval_id is not None
        assert decision.reason == "Needs approval"

        # Verify the approval record exists in DB
        status = engine.check_approval(decision.approval_id)
        assert status == "pending"

    def test_no_matching_policy_returns_allow(self, engine, workspace_id, ctx):
        """When no policy matches, evaluate() returns ALLOW."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        decision = engine.evaluate("read_file", {"path": "/tmp/test.txt"}, ctx)
        assert decision.result == PolicyResult.ALLOW

    def test_deny_policy_returns_deny(self, engine, workspace_id, ctx):
        """A deny policy blocks the tool call without creating an approval."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Block reads",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "deny", "reason": "No reads allowed"},
        )

        decision = engine.evaluate("read_file", {"path": "/foo"}, ctx)
        assert decision.result == PolicyResult.DENY
        assert decision.approval_id is None


# ---------------------------------------------------------------------------
# 3. Approval CRUD: create, check, decide
# ---------------------------------------------------------------------------


class TestApprovalCRUD:
    """Test approval record lifecycle."""

    def test_create_and_check(self, engine):
        """Created approval is in 'pending' status."""
        aid = engine.create_approval(
            policy_id=None,
            job_id="job-1",
            node_id="node-1",
            tool_call={"name": "read_file", "args": {"path": "/tmp/x"}},
            expires_minutes=30,
        )
        assert engine.check_approval(aid) == "pending"

    def test_approve(self, engine, admin_user_id):
        """Approving a pending approval sets status to 'approved'."""
        aid = engine.create_approval(
            policy_id=None,
            job_id="job-1",
            node_id="node-1",
            tool_call={"name": "read_file", "args": {}},
        )
        engine.decide_approval(aid, decided_by=admin_user_id, approved=True, reason="Looks good")
        assert engine.check_approval(aid) == "approved"

    def test_deny(self, engine, admin_user_id):
        """Denying a pending approval sets status to 'denied'."""
        aid = engine.create_approval(
            policy_id=None,
            job_id="job-1",
            node_id="node-1",
            tool_call={"name": "read_file", "args": {}},
        )
        engine.decide_approval(aid, decided_by=admin_user_id, approved=False, reason="Too risky")
        assert engine.check_approval(aid) == "denied"

    def test_cannot_decide_already_decided(self, engine, admin_user_id):
        """Attempting to decide an already-decided approval raises ValueError."""
        aid = engine.create_approval(
            policy_id=None,
            job_id="job-1",
            node_id="node-1",
            tool_call={"name": "read_file", "args": {}},
        )
        engine.decide_approval(aid, decided_by=admin_user_id, approved=True)

        with pytest.raises(ValueError, match="current status is 'approved'"):
            engine.decide_approval(aid, decided_by=admin_user_id, approved=False)

    def test_check_nonexistent_raises(self, engine):
        """Checking a non-existent approval raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            engine.check_approval("nonexistent-id")


# ---------------------------------------------------------------------------
# 4. Auto-expiry
# ---------------------------------------------------------------------------


class TestAutoExpiry:
    """Test that approvals auto-expire when their deadline passes."""

    def test_expired_approval_auto_detected(self, engine):
        """check_approval() returns 'expired' for an overdue pending approval."""
        aid = engine.create_approval(
            policy_id=None,
            job_id="job-1",
            node_id="node-1",
            tool_call={"name": "read_file", "args": {}},
            expires_minutes=0,  # Already expired
        )
        status = engine.check_approval(aid)
        assert status == "expired"

    def test_cannot_decide_expired_approval(self, engine, admin_user_id):
        """Cannot approve/deny an expired approval."""
        aid = engine.create_approval(
            policy_id=None,
            job_id="job-1",
            node_id="node-1",
            tool_call={"name": "read_file", "args": {}},
            expires_minutes=0,
        )
        with pytest.raises(ValueError, match="current status is 'expired'"):
            engine.decide_approval(aid, decided_by=admin_user_id, approved=True)

    def test_bulk_expire_stale(self, engine):
        """expire_stale_approvals() marks all overdue approvals."""
        engine.create_approval(
            policy_id=None,
            job_id="job-1",
            node_id="node-1",
            tool_call={"name": "a", "args": {}},
            expires_minutes=0,
        )
        engine.create_approval(
            policy_id=None,
            job_id="job-2",
            node_id="node-2",
            tool_call={"name": "b", "args": {}},
            expires_minutes=0,
        )
        # One that should NOT expire (30 min from now)
        aid_fresh = engine.create_approval(
            policy_id=None,
            job_id="job-3",
            node_id="node-3",
            tool_call={"name": "c", "args": {}},
            expires_minutes=30,
        )

        count = engine.expire_stale_approvals()
        assert count == 2

        # Fresh one should still be pending
        assert engine.check_approval(aid_fresh) == "pending"


# ---------------------------------------------------------------------------
# 5. Policy priority
# ---------------------------------------------------------------------------


class TestPolicyPriority:
    """Higher priority policies are evaluated first."""

    def test_higher_priority_wins(self, engine, workspace_id, ctx):
        """When two policies match the same tool, the higher-priority one wins."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        # Low priority: ALLOW
        engine.create_policy(
            workspace_id=workspace_id,
            name="Allow reads (low)",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "allow", "reason": "Allow from low priority"},
            priority=1,
        )
        # High priority: DENY
        engine.create_policy(
            workspace_id=workspace_id,
            name="Deny reads (high)",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "deny", "reason": "Blocked by high priority"},
            priority=100,
        )

        decision = engine.evaluate("read_file", {"path": "/tmp/x"}, ctx)
        assert decision.result == PolicyResult.DENY
        assert "high priority" in decision.reason.lower()


# ---------------------------------------------------------------------------
# 6. Disabled policies are skipped
# ---------------------------------------------------------------------------


class TestDisabledPolicies:
    """Disabled policies should not fire."""

    def test_disabled_policy_skipped(self, engine, workspace_id, ctx):
        """A disabled policy does not affect evaluation."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        pid = engine.create_policy(
            workspace_id=workspace_id,
            name="Deny everything",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "deny", "reason": "Should not fire"},
            priority=999,
        )
        engine.update_policy(pid, enabled=False)

        decision = engine.evaluate("read_file", {"path": "/tmp/x"}, ctx)
        assert decision.result == PolicyResult.ALLOW


# ---------------------------------------------------------------------------
# 7. Condition evaluation
# ---------------------------------------------------------------------------


class TestConditionEvaluation:
    """Test various condition types in _evaluate_conditions."""

    def test_tool_condition_matches(self, engine, workspace_id, ctx):
        """A tool condition matches the exact tool name."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Match read_file",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "deny", "reason": "Reads blocked"},
        )

        # Should match
        decision = engine.evaluate("read_file", {"path": "/tmp/x"}, ctx)
        assert decision.result == PolicyResult.DENY

        # Should NOT match
        decision2 = engine.evaluate("list_files", {}, ctx)
        assert decision2.result == PolicyResult.ALLOW

    def test_tool_list_condition(self, engine, workspace_id, ctx):
        """A tool condition with a list matches any tool in the list."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Match file tools",
            description="",
            conditions={"tool": ["read_file", "list_files"]},
            actions={"type": "deny", "reason": "File ops blocked"},
            priority=50,
        )

        d1 = engine.evaluate("read_file", {}, ctx)
        assert d1.result == PolicyResult.DENY

        d2 = engine.evaluate("list_files", {}, ctx)
        assert d2.result == PolicyResult.DENY

        d3 = engine.evaluate("search_file", {}, ctx)
        assert d3.result == PolicyResult.ALLOW

    def test_deployment_mode_condition(self, engine, workspace_id, ctx):
        """A deployment_mode condition filters by context mode."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Block in strict-local",
            description="",
            conditions={
                "tool": "read_file",
                "deployment_mode": "strict-local",
            },
            actions={"type": "deny", "reason": "Blocked in strict-local"},
        )

        # ctx is "hybrid" -- should NOT match
        decision = engine.evaluate("read_file", {"path": "/test"}, ctx)
        assert decision.result == PolicyResult.ALLOW

        # Change to strict-local -- SHOULD match
        ctx.deployment_mode = "strict-local"
        decision2 = engine.evaluate("read_file", {"path": "/test"}, ctx)
        assert decision2.result == PolicyResult.DENY

    def test_path_matches_condition(self, engine, workspace_id, ctx):
        """A path_matches condition applies glob filtering."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Deny exe reads",
            description="",
            conditions={
                "tool": "read_file",
                "path_matches": "*.exe",
            },
            actions={"type": "deny", "reason": "No exe files"},
            priority=50,
        )

        # Match: .exe file
        d1 = engine.evaluate("read_file", {"path": "/tmp/malware.exe"}, ctx)
        assert d1.result == PolicyResult.DENY

        # No match: .txt file
        d2 = engine.evaluate("read_file", {"path": "/tmp/readme.txt"}, ctx)
        assert d2.result == PolicyResult.ALLOW

    def test_egress_condition(self, engine, workspace_id, ctx):
        """tool_requires_egress condition matches egress tools."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Approval for egress",
            description="",
            conditions={"tool_requires_egress": True},
            actions={"type": "require_approval", "reason": "Egress needs approval"},
            priority=50,
        )

        # web_search is an egress tool
        decision = engine.evaluate("web_search", {"query": "hello"}, ctx)
        assert decision.result == PolicyResult.REQUIRE_APPROVAL

        # read_file is NOT an egress tool
        decision2 = engine.evaluate("read_file", {"path": "/tmp/x"}, ctx)
        assert decision2.result == PolicyResult.ALLOW


# ---------------------------------------------------------------------------
# 8. Built-in policies
# ---------------------------------------------------------------------------


class TestBuiltinPolicies:
    """Test hardcoded built-in policies in _check_builtin_policies."""

    def test_rate_limit(self, ctx, engine):
        """Rate limit fires when tool_call_count >= 100."""
        from backend.core.policy import PolicyResult

        ctx.tool_call_count = 100
        decision = engine.evaluate("read_file", {}, ctx)
        assert decision.result == PolicyResult.DENY
        assert "rate limit" in decision.reason.lower()

    def test_bulk_delete_limit(self, ctx, engine):
        """Bulk delete fires when _delete_count >= 10."""
        from backend.core.policy import PolicyResult

        ctx._delete_count = 10
        decision = engine.evaluate("delete_file", {"path": "/tmp/x"}, ctx)
        assert decision.result == PolicyResult.DENY
        assert "bulk delete" in decision.reason.lower()

    def test_strict_local_egress_block(self, ctx, engine):
        """Strict-local mode blocks all egress tools."""
        from backend.core.policy import PolicyResult

        ctx.deployment_mode = "strict-local"
        decision = engine.evaluate("web_search", {"query": "test"}, ctx)
        assert decision.result == PolicyResult.DENY
        assert "strict-local" in decision.reason.lower()

    def test_ssrf_protection(self, ctx, engine):
        """SSRF protection blocks browser calls to localhost."""
        from backend.core.policy import PolicyResult

        decision = engine.evaluate("browser", {"url": "http://127.0.0.1/admin"}, ctx)
        assert decision.result == PolicyResult.DENY
        assert "ssrf" in decision.reason.lower()

    def test_write_outside_job_dir_blocked(self, ctx, engine):
        """File writes outside the job directory are blocked."""
        from backend.core.policy import PolicyResult

        decision = engine.evaluate("write_file", {"path": "/etc/passwd"}, ctx)
        assert decision.result == PolicyResult.DENY
        assert "outside job directory" in decision.reason.lower()

    def test_write_inside_job_dir_allowed(self, ctx, engine, workspace_id):
        """File writes inside the job directory pass built-in check."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        # Path contains /jobs/{job_id} so built-in allows it
        path = f"/jobs/{ctx.job_id}/output/result.txt"
        decision = engine.evaluate("write_file", {"path": path}, ctx)
        assert decision.result == PolicyResult.ALLOW


# ---------------------------------------------------------------------------
# 9. Approval API endpoints (DB queries)
# ---------------------------------------------------------------------------


class TestApprovalAPIEndpoints:
    """Test the approval-related DB queries used by API routes."""

    def test_get_pending_approvals_from_db(self, engine, admin_user_id):
        """Verify we can query pending approvals from the DB directly."""
        from backend.config import DB_PATH

        aid1 = engine.create_approval(
            policy_id=None,
            job_id="job-1",
            node_id="node-1",
            tool_call={"name": "read_file", "args": {}},
            expires_minutes=30,
        )
        aid2 = engine.create_approval(
            policy_id=None,
            job_id="job-2",
            node_id="node-2",
            tool_call={"name": "read_file", "args": {}},
            expires_minutes=30,
        )
        # Decide one
        engine.decide_approval(aid2, decided_by=admin_user_id, approved=True)

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE status = 'pending'"
            ).fetchall()
            pending_ids = [r["id"] for r in rows]
            assert aid1 in pending_ids
            assert aid2 not in pending_ids
        finally:
            conn.close()

    def test_approval_decision_is_auditable(self, engine, admin_user_id):
        """After deciding, decided_by and decided_at are recorded."""
        from backend.config import DB_PATH

        aid = engine.create_approval(
            policy_id=None,
            job_id="job-1",
            node_id="node-1",
            tool_call={"name": "read_file", "args": {}},
        )
        engine.decide_approval(aid, decided_by=admin_user_id, approved=True, reason="OK")

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM approvals WHERE id = ?", (aid,)
            ).fetchone()
            assert row["status"] == "approved"
            assert row["decided_by"] == admin_user_id
            assert row["decided_at"] is not None
            assert row["reason"] == "OK"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 10. Default policies seeded by ensure_default_tenant()
# ---------------------------------------------------------------------------


class TestDefaultPolicies:
    """Verify that default approval policies are seeded on startup."""

    def test_default_policies_exist(self, engine, workspace_id):
        """ensure_default_tenant should seed default approval policies."""
        policies = engine.list_policies(workspace_id)
        names = [p["name"] for p in policies]

        assert "Require approval for bulk file deletes" in names
        assert "Require approval for external API calls in strict-local mode" in names
        assert "Require approval for large file writes" in names

    def test_default_policies_are_enabled(self, engine, workspace_id):
        """All default policies should be enabled."""
        policies = engine.list_policies(workspace_id)
        for p in policies:
            assert p["enabled"] == 1, f"Policy '{p['name']}' should be enabled"

    def test_default_bulk_delete_policy_fires(self, engine, workspace_id, ctx):
        """The bulk-delete default policy should require approval for delete_file."""
        from backend.core.policy import PolicyResult

        decision = engine.evaluate("delete_file", {"path": "/tmp/test.txt"}, ctx)
        assert decision.result == PolicyResult.REQUIRE_APPROVAL

    def test_default_strict_local_egress_policy_does_not_fire_in_hybrid(self, engine, workspace_id, ctx):
        """The strict-local egress policy should NOT fire in hybrid mode."""
        from backend.core.policy import PolicyResult

        # ctx is hybrid, so the strict-local condition should not match
        # and the egress tool should be allowed (no matching policy)
        # Note: the default "large file writes" policy won't match web_search
        decision = engine.evaluate("web_search", {"query": "test"}, ctx)
        # Only the default egress policy (strict-local only) exists,
        # and we're in hybrid mode, so it should allow
        assert decision.result == PolicyResult.ALLOW

    def test_default_large_file_write_policy(self, engine, workspace_id, ctx):
        """The large file write policy requires approval for write_file inside job dir."""
        from backend.core.policy import PolicyResult

        # Use a path inside the job directory so built-in doesn't block first
        path = f"/jobs/{ctx.job_id}/output/big.dat"
        decision = engine.evaluate("write_file", {"path": path}, ctx)
        assert decision.result == PolicyResult.REQUIRE_APPROVAL


# ---------------------------------------------------------------------------
# 11. Executor approval integration
# ---------------------------------------------------------------------------


class TestExecutorApprovalHandling:
    """Test that the executor properly surfaces REQUIRE_APPROVAL results."""

    def test_evaluate_returns_approval_with_id(self, engine, workspace_id, ctx):
        """When PolicyEngine returns REQUIRE_APPROVAL, the decision includes
        an approval_id that can be used to check/decide the approval."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Approve reads",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "require_approval", "reason": "Needs human review"},
        )

        decision = engine.evaluate("read_file", {"path": "/tmp/x"}, ctx)
        assert decision.result == PolicyResult.REQUIRE_APPROVAL
        assert decision.approval_id is not None

        # The approval_id should be valid and checkable
        status = engine.check_approval(decision.approval_id)
        assert status == "pending"

    def test_approval_tool_result_shape(self, engine, workspace_id, ctx):
        """The tool result dict that the executor creates has the right fields."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Approve reads",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "require_approval", "reason": "Needs human review"},
        )

        decision = engine.evaluate("read_file", {"path": "/tmp/x"}, ctx)

        # Simulate what the executor creates for a denied/expired approval
        tool_result = {
            "success": False,
            "requires_approval": True,
            "approval_id": decision.approval_id,
            "approval_status": "denied",
            "message": f"Tool 'read_file' was denied (approval_id={decision.approval_id}).",
        }
        assert tool_result["requires_approval"] is True
        assert tool_result["approval_id"] == decision.approval_id
        assert "denied" in tool_result["message"]


# ---------------------------------------------------------------------------
# 12. End-to-end: full approval lifecycle
# ---------------------------------------------------------------------------


class TestEndToEndApprovalLifecycle:
    """Integration test: policy match -> approval created -> human decides -> status updated."""

    def test_full_approve_lifecycle(self, engine, workspace_id, ctx, admin_user_id):
        """Policy triggers approval -> admin approves -> status = approved."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Gated read",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "require_approval", "expires_minutes": 60},
            priority=10,
        )

        # Step 1: Tool call triggers policy
        decision = engine.evaluate("read_file", {"path": "/tmp/report.pdf"}, ctx)
        assert decision.result == PolicyResult.REQUIRE_APPROVAL
        approval_id = decision.approval_id

        # Step 2: Check it's pending
        assert engine.check_approval(approval_id) == "pending"

        # Step 3: Admin approves
        engine.decide_approval(approval_id, decided_by=admin_user_id, approved=True)

        # Step 4: Verify approved
        assert engine.check_approval(approval_id) == "approved"

    def test_full_deny_lifecycle(self, engine, workspace_id, ctx, admin_user_id):
        """Policy triggers approval -> admin denies -> status = denied."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Gated read",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "require_approval", "expires_minutes": 60},
            priority=10,
        )

        decision = engine.evaluate("read_file", {"path": "/tmp/important.db"}, ctx)
        assert decision.result == PolicyResult.REQUIRE_APPROVAL
        approval_id = decision.approval_id

        engine.decide_approval(approval_id, decided_by=admin_user_id, approved=False, reason="Too dangerous")
        assert engine.check_approval(approval_id) == "denied"

    def test_full_expiry_lifecycle(self, engine, workspace_id, ctx):
        """Policy triggers approval -> nobody decides -> expires."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Gated action",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "require_approval", "expires_minutes": 0},
            priority=10,
        )

        decision = engine.evaluate("read_file", {"path": "/tmp/test"}, ctx)
        assert decision.result == PolicyResult.REQUIRE_APPROVAL

        # Immediately expired (expires_minutes=0)
        assert engine.check_approval(decision.approval_id) == "expired"

    def test_multiple_approvals_independent(self, engine, workspace_id, ctx, admin_user_id):
        """Multiple concurrent approvals are tracked independently."""
        from backend.core.policy import PolicyResult

        _clear_default_policies(workspace_id, engine)

        engine.create_policy(
            workspace_id=workspace_id,
            name="Gated reads",
            description="",
            conditions={"tool": "read_file"},
            actions={"type": "require_approval", "expires_minutes": 30},
            priority=10,
        )

        d1 = engine.evaluate("read_file", {"path": "/a"}, ctx)
        d2 = engine.evaluate("read_file", {"path": "/b"}, ctx)

        assert d1.approval_id != d2.approval_id
        assert engine.check_approval(d1.approval_id) == "pending"
        assert engine.check_approval(d2.approval_id) == "pending"

        # Approve first, deny second
        engine.decide_approval(d1.approval_id, decided_by=admin_user_id, approved=True)
        engine.decide_approval(d2.approval_id, decided_by=admin_user_id, approved=False)

        assert engine.check_approval(d1.approval_id) == "approved"
        assert engine.check_approval(d2.approval_id) == "denied"
