"""
Tests for the Proposal-Approval flow.
"""
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.tools.propose_action import (
    ProposeActionTool,
    resolve_approval,
    get_pending_requests,
    _load_approval_log,
    _save_approval_log,
)


@pytest.fixture
def tmp_approval_log(tmp_path):
    """Redirect the approval log to a temp file."""
    log_path = tmp_path / ".approvals.json"
    with patch("backend.tools.propose_action._approval_log_path", log_path):
        yield log_path


class TestResolveApproval:
    """Tests for the resolve_approval helper."""

    def test_resolve_unknown_id(self, tmp_approval_log):
        """Resolving a non-existent request returns False."""
        assert resolve_approval("nonexistent-id", True) is False

    @pytest.mark.asyncio
    async def test_approve_flow(self, tmp_approval_log):
        """Create a proposal, approve it, verify the decision."""
        tool = ProposeActionTool()

        # Start the proposal in a background task
        async def proposal():
            return await tool.execute(
                action_type="install_package",
                description="Install pandas",
                reason="Data analysis",
                risk_level="LOW",
            )

        task = asyncio.create_task(proposal())

        # Give the tool time to register the pending request
        await asyncio.sleep(0.1)

        # Find and approve the pending request
        pending = get_pending_requests()
        assert len(pending) >= 1
        request_id = pending[-1]["request_id"]
        assert resolve_approval(request_id, True) is True

        # Wait for the tool to return
        result = await task
        assert result["approved"] is True
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_deny_flow(self, tmp_approval_log):
        """Create a proposal, deny it, verify the decision."""
        tool = ProposeActionTool()

        async def proposal():
            return await tool.execute(
                action_type="system_command",
                description="Run rm -rf /",
                reason="Testing",
                risk_level="HIGH",
            )

        task = asyncio.create_task(proposal())
        await asyncio.sleep(0.1)

        pending = get_pending_requests()
        assert len(pending) >= 1
        request_id = pending[-1]["request_id"]
        assert resolve_approval(request_id, False) is True

        result = await task
        assert result["approved"] is False
        assert result["success"] is False


class TestAuditLog:
    """Tests for the approval audit trail."""

    def test_empty_log(self, tmp_approval_log):
        """Fresh log returns empty list."""
        assert _load_approval_log() == []

    def test_save_and_load(self, tmp_approval_log):
        """Saved entries can be loaded back."""
        entries = [
            {"request_id": "test-1", "action_type": "install_package", "decision": "pending"},
            {"request_id": "test-2", "action_type": "web_submit", "decision": "approved"},
        ]
        _save_approval_log(entries)
        loaded = _load_approval_log()
        assert len(loaded) == 2
        assert loaded[0]["request_id"] == "test-1"

    def test_get_pending_filters(self, tmp_approval_log):
        """get_pending_requests only returns entries with decision=pending."""
        entries = [
            {"request_id": "a", "decision": "pending"},
            {"request_id": "b", "decision": "approved"},
            {"request_id": "c", "decision": "pending"},
        ]
        _save_approval_log(entries)
        pending = get_pending_requests()
        assert len(pending) == 2
        assert all(e["decision"] == "pending" for e in pending)
