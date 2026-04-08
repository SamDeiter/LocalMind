"""
Policy Engine — LocalMind enterprise task worker.

Every tool invocation passes through evaluate() before execution.
If this module says no, the tool call does not happen.

Tables used (created by backend/core/schema.py):
  - approval_policies  (workspace-level configurable rules)
  - approvals          (human-in-the-loop approval requests)
"""

from __future__ import annotations

import fnmatch
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from backend.config import DB_PATH

logger = logging.getLogger("localmind.core.policy")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tools that require external network egress.
EGRESS_TOOLS: frozenset[str] = frozenset(
    {"web_search", "browser", "gmail", "gemini"}
)

# File-delete tool names recognised by the engine.
_DELETE_TOOLS: frozenset[str] = frozenset(
    {"delete_file", "remove_file", "file_delete", "unlink"}
)

# Browser / URL tools that carry a URL argument.
_URL_TOOLS: frozenset[str] = frozenset({"browser", "web_search"})

# URL argument key candidates (checked in order).
_URL_ARG_KEYS: tuple[str, ...] = ("url", "query", "href", "uri", "navigate")

# Path argument key candidates (checked in order).
_PATH_ARG_KEYS: tuple[str, ...] = ("path", "file_path", "filepath", "filename", "dest", "destination")

# SSRF-blocked netloc patterns (exact match or prefix).
_SSRF_BLOCK_EXACT: frozenset[str] = frozenset({"localhost", "127.0.0.1"})
_SSRF_BLOCK_SCHEME: frozenset[str] = frozenset({"file"})
_SSRF_BLOCK_PREFIX: str = "169.254."  # link-local

# Rate-limit: maximum tool calls per node.
_MAX_TOOL_CALLS_PER_NODE: int = 100

# Bulk-delete threshold (single node).
_MAX_DELETES_PER_NODE: int = 10


# ---------------------------------------------------------------------------
# Enums & Dataclasses
# ---------------------------------------------------------------------------


class PolicyResult(Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    DRY_RUN = "dry_run"


@dataclass
class PolicyDecision:
    result: PolicyResult
    policy_id: str | None = None
    reason: str = ""
    approval_id: str | None = None  # set when result == REQUIRE_APPROVAL


@dataclass
class PolicyContext:
    workspace_id: str
    deployment_mode: str          # e.g. "strict-local", "hybrid", "cloud"
    job_id: str
    node_id: str
    user_id: str = ""
    user_role: str = "operator"   # e.g. "admin", "operator", "viewer"
    job_classification: str | None = None
    tool_call_count: int = 0      # running count in this node (for rate limit)

    # Internal mutable counters injected by the engine per-node per-session.
    # Not persisted — callers must track and pass in the right value.
    _delete_count: int = field(default=0, repr=False)


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Built-in policy helpers
# ---------------------------------------------------------------------------


def _extract_path(tool_args: dict[str, Any]) -> str | None:
    """Return the first path-like argument value from tool_args."""
    for key in _PATH_ARG_KEYS:
        val = tool_args.get(key)
        if isinstance(val, str) and val:
            return val
    # Check nested dicts one level deep.
    for val in tool_args.values():
        if isinstance(val, dict):
            for key in _PATH_ARG_KEYS:
                inner = val.get(key)
                if isinstance(inner, str) and inner:
                    return inner
    return None


def _extract_url(tool_name: str, tool_args: dict[str, Any]) -> str | None:
    """Return a URL string from tool_args for URL-carrying tools."""
    for key in _URL_ARG_KEYS:
        val = tool_args.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _is_ssrf_url(url: str) -> bool:
    """Return True if the URL targets a blocked SSRF destination."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Block file:// URIs.
    if parsed.scheme in _SSRF_BLOCK_SCHEME:
        return True

    netloc = parsed.netloc or ""
    # Strip port.
    host = netloc.split(":")[0].lower()

    if host in _SSRF_BLOCK_EXACT:
        return True

    if host.startswith(_SSRF_BLOCK_PREFIX):
        return True

    return False


def _check_builtin_policies(
    tool_name: str,
    tool_args: dict[str, Any],
    context: PolicyContext,
) -> PolicyDecision | None:
    """
    Evaluate all hardcoded built-in policies.

    Returns a PolicyDecision if a built-in policy fires, else None (caller
    should continue to workspace policies).
    """

    # ── 1. Rate limit: max 100 tool calls per node ──────────────────────────
    if context.tool_call_count >= _MAX_TOOL_CALLS_PER_NODE:
        return PolicyDecision(
            result=PolicyResult.DENY,
            reason=(
                f"Rate limit exceeded: {context.tool_call_count} tool calls in node "
                f"'{context.node_id}' (max {_MAX_TOOL_CALLS_PER_NODE})."
            ),
        )

    # ── 2. Bulk file delete: deny >10 deletes in a single node ──────────────
    if tool_name in _DELETE_TOOLS:
        if context._delete_count >= _MAX_DELETES_PER_NODE:
            return PolicyDecision(
                result=PolicyResult.DENY,
                reason=(
                    f"Bulk delete limit exceeded: {context._delete_count} file deletes "
                    f"in node '{context.node_id}' (max {_MAX_DELETES_PER_NODE})."
                ),
            )

    # ── 3. Strict-local egress block ────────────────────────────────────────
    if context.deployment_mode == "strict-local" and tool_name in EGRESS_TOOLS:
        return PolicyDecision(
            result=PolicyResult.DENY,
            reason=(
                f"Tool '{tool_name}' requires external network egress, which is "
                f"blocked in strict-local deployment mode."
            ),
        )

    # ── 4. SSRF: block browser / web_search targeting internal addresses ────
    if tool_name in _URL_TOOLS:
        url = _extract_url(tool_name, tool_args)
        if url and _is_ssrf_url(url):
            return PolicyDecision(
                result=PolicyResult.DENY,
                reason=(
                    f"SSRF protection: tool '{tool_name}' attempted to access "
                    f"blocked address '{url}'."
                ),
            )

    # ── 5. File writes outside the job directory ─────────────────────────────
    _WRITE_TOOLS: frozenset[str] = frozenset(
        {"write_file", "save_file", "file_write", "create_file", "append_file"}
    )
    if tool_name in _WRITE_TOOLS:
        path = _extract_path(tool_args)
        if path:
            # Normalise separators for comparison.
            norm_path = path.replace("\\", "/")
            job_dir_fragment = f"/jobs/{context.job_id}"
            workspace_fragment = "LocalMind_Workspace"
            # Allow writes inside the job-specific directory.
            if job_dir_fragment not in norm_path and workspace_fragment not in norm_path:
                return PolicyDecision(
                    result=PolicyResult.DENY,
                    reason=(
                        f"File write outside job directory denied. Path '{path}' "
                        f"is not under the job directory for job '{context.job_id}'."
                    ),
                )

    return None  # no built-in policy matched


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------


def _evaluate_conditions(
    conditions: dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
    context: PolicyContext,
) -> bool:
    """
    Evaluate a conditions dict against a tool call and its context.

    All specified keys must match (AND logic). Returns True if all match.

    Supported condition keys:
      tool              — exact tool name or list of names
      tool_not          — inverse: tool name must NOT be in this value
      deployment_mode   — must equal context.deployment_mode
      job_classification — must equal context.job_classification
      path_matches      — glob pattern matched against the path arg
      domain_not_in     — list of domains; URL domain must NOT be in the list
      tool_requires_egress — bool; True fires when tool is in EGRESS_TOOLS
    """
    if not conditions:
        return True  # empty conditions = always match

    for key, value in conditions.items():

        # ── tool ────────────────────────────────────────────────────────────
        if key == "tool":
            allowed = [value] if isinstance(value, str) else list(value)
            if tool_name not in allowed:
                return False

        # ── tool_not ────────────────────────────────────────────────────────
        elif key == "tool_not":
            excluded = [value] if isinstance(value, str) else list(value)
            if tool_name in excluded:
                return False

        # ── deployment_mode ─────────────────────────────────────────────────
        elif key == "deployment_mode":
            modes = [value] if isinstance(value, str) else list(value)
            if context.deployment_mode not in modes:
                return False

        # ── job_classification ──────────────────────────────────────────────
        elif key == "job_classification":
            classes = [value] if isinstance(value, str) else list(value)
            if context.job_classification not in classes:
                return False

        # ── path_matches ─────────────────────────────────────────────────────
        elif key == "path_matches":
            pattern = str(value)
            path = _extract_path(tool_args) or ""
            # fnmatch on the basename and the full path.
            import os as _os
            basename = _os.path.basename(path)
            if not (fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(basename, pattern)):
                return False

        # ── domain_not_in ────────────────────────────────────────────────────
        elif key == "domain_not_in":
            blocked_domains: list[str] = list(value)
            url = _extract_url(tool_name, tool_args) or ""
            if url:
                try:
                    host = urlparse(url).netloc.split(":")[0].lower()
                except Exception:
                    host = ""
                if host in (d.lower() for d in blocked_domains):
                    return False  # domain IS in the blocked list → no match

        # ── tool_requires_egress ─────────────────────────────────────────────
        elif key == "tool_requires_egress":
            requires_egress = bool(value)
            tool_is_egress = tool_name in EGRESS_TOOLS
            if requires_egress != tool_is_egress:
                return False

        else:
            # Unknown condition key — log and skip (permissive for forward-compat).
            logger.warning("Unknown policy condition key '%s' — skipping.", key)

    return True  # all conditions satisfied


# ---------------------------------------------------------------------------
# PolicyEngine
# ---------------------------------------------------------------------------


class PolicyEngine:
    """
    Single enforcement point for all tool calls.

    Usage::

        engine = PolicyEngine()
        decision = engine.evaluate("browser", {"url": "https://example.com"}, ctx)
        if decision.result != PolicyResult.ALLOW:
            # do not execute tool
    """

    # ── Core evaluation ─────────────────────────────────────────────────────

    def evaluate(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        context: PolicyContext,
    ) -> PolicyDecision:
        """
        Evaluate a pending tool call against all active policies.

        Evaluation order:
          1. Built-in hardcoded policies (always active).
          2. Workspace policies from DB, ordered by priority DESC.
          3. Default: ALLOW.

        Returns the first matching PolicyDecision.
        """
        # ── Phase 1: built-in policies ──────────────────────────────────────
        builtin = _check_builtin_policies(tool_name, tool_args, context)
        if builtin is not None:
            logger.info(
                "Built-in policy fired for tool '%s' in job '%s': %s — %s",
                tool_name, context.job_id, builtin.result.value, builtin.reason,
            )
            return builtin

        # ── Phase 2: workspace DB policies ─────────────────────────────────
        try:
            policies = self._load_workspace_policies(context.workspace_id)
        except Exception:
            logger.exception(
                "Failed to load workspace policies for '%s'; defaulting ALLOW.",
                context.workspace_id,
            )
            return PolicyDecision(result=PolicyResult.ALLOW, reason="DB error; defaulting to allow.")

        for policy in policies:
            try:
                conditions = json.loads(policy["conditions_json"] or "{}")
                actions = json.loads(policy["actions_json"] or "{}")
            except json.JSONDecodeError:
                logger.warning("Policy '%s' has invalid JSON — skipping.", policy["id"])
                continue

            if not _evaluate_conditions(conditions, tool_name, tool_args, context):
                continue

            # Matched — parse action.
            action_type = actions.get("type", "allow").lower()
            reason = actions.get("reason", f"Matched policy '{policy['name']}'.")
            policy_id = policy["id"]

            if action_type == "deny":
                logger.info(
                    "Workspace policy '%s' DENY for tool '%s' in job '%s'.",
                    policy["name"], tool_name, context.job_id,
                )
                return PolicyDecision(
                    result=PolicyResult.DENY,
                    policy_id=policy_id,
                    reason=reason,
                )

            if action_type == "require_approval":
                expires_minutes = int(actions.get("expires_minutes", 30))
                tool_call = {"name": tool_name, "args": tool_args}
                approval_id = self.create_approval(
                    policy_id=policy_id,
                    job_id=context.job_id,
                    node_id=context.node_id,
                    tool_call=tool_call,
                    expires_minutes=expires_minutes,
                )
                logger.info(
                    "Workspace policy '%s' REQUIRE_APPROVAL for tool '%s' — approval_id='%s'.",
                    policy["name"], tool_name, approval_id,
                )
                return PolicyDecision(
                    result=PolicyResult.REQUIRE_APPROVAL,
                    policy_id=policy_id,
                    reason=reason,
                    approval_id=approval_id,
                )

            if action_type == "dry_run":
                logger.info(
                    "Workspace policy '%s' DRY_RUN for tool '%s' in job '%s'.",
                    policy["name"], tool_name, context.job_id,
                )
                return PolicyDecision(
                    result=PolicyResult.DRY_RUN,
                    policy_id=policy_id,
                    reason=reason,
                )

            # action_type == "allow" (or unknown — default to allow)
            return PolicyDecision(
                result=PolicyResult.ALLOW,
                policy_id=policy_id,
                reason=reason,
            )

        # ── Phase 3: default allow ──────────────────────────────────────────
        return PolicyDecision(result=PolicyResult.ALLOW, reason="No policy matched; default allow.")

    # ── Policy CRUD ─────────────────────────────────────────────────────────

    def create_policy(
        self,
        workspace_id: str,
        name: str,
        description: str,
        conditions: dict[str, Any],
        actions: dict[str, Any],
        priority: int = 0,
    ) -> str:
        """Create a new workspace policy. Returns the new policy_id."""
        policy_id = str(uuid.uuid4())
        now = _now_iso()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO approval_policies
                    (id, workspace_id, name, description, conditions_json,
                     actions_json, priority, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    policy_id,
                    workspace_id,
                    name,
                    description,
                    json.dumps(conditions),
                    json.dumps(actions),
                    priority,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Created policy '%s' (id=%s) for workspace '%s'.", name, policy_id, workspace_id)
        return policy_id

    def list_policies(self, workspace_id: str) -> list[dict[str, Any]]:
        """Return all policies for a workspace, ordered by priority DESC."""
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, workspace_id, name, description, conditions_json,
                       actions_json, priority, enabled, created_at
                FROM approval_policies
                WHERE workspace_id = ?
                ORDER BY priority DESC, created_at ASC
                """,
                (workspace_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_policy(self, policy_id: str, enabled: bool) -> None:
        """Enable or disable a policy."""
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE approval_policies SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, policy_id),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("Policy '%s' enabled=%s.", policy_id, enabled)

    def delete_policy(self, policy_id: str) -> None:
        """Permanently delete a policy."""
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM approval_policies WHERE id = ?", (policy_id,))
            conn.commit()
        finally:
            conn.close()
        logger.info("Policy '%s' deleted.", policy_id)

    # ── Approval CRUD ────────────────────────────────────────────────────────

    def create_approval(
        self,
        policy_id: str | None,
        job_id: str,
        node_id: str,
        tool_call: dict[str, Any],
        expires_minutes: int = 30,
    ) -> str:
        """
        Create a new approval request.

        Returns the approval_id. The approval starts in 'pending' status.
        """
        approval_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(minutes=expires_minutes)).isoformat()

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO approvals
                    (id, policy_id, job_id, node_id, tool_call_json,
                     status, requested_at, expires_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id,
                    policy_id,
                    job_id,
                    node_id,
                    json.dumps(tool_call),
                    now.isoformat(),
                    expires_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Approval request created: id='%s', job='%s', node='%s', expires='%s'.",
            approval_id, job_id, node_id, expires_at,
        )
        return approval_id

    def check_approval(self, approval_id: str) -> str:
        """
        Return the current status of an approval.

        Returns one of: 'pending', 'approved', 'denied', 'expired'.
        Raises ValueError if the approval_id is not found.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT status, expires_at FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise ValueError(f"Approval '{approval_id}' not found.")

        status = row["status"]
        # Lazily expire if still pending and past expiry.
        if status == "pending":
            expires_at = datetime.fromisoformat(row["expires_at"])
            if datetime.now(timezone.utc) >= expires_at:
                self._mark_expired(approval_id)
                return "expired"

        return status

    def decide_approval(
        self,
        approval_id: str,
        decided_by: str,
        approved: bool,
        reason: str = "",
    ) -> None:
        """
        Record a human decision on a pending approval.

        Sets status to 'approved' or 'denied'.
        Raises ValueError if the approval is not in 'pending' status.
        """
        current_status = self.check_approval(approval_id)
        if current_status != "pending":
            raise ValueError(
                f"Cannot decide approval '{approval_id}': current status is '{current_status}'."
            )

        new_status = "approved" if approved else "denied"
        now = _now_iso()

        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE approvals
                SET status = ?, decided_by = ?, decided_at = ?, reason = ?
                WHERE id = ?
                """,
                (new_status, decided_by, now, reason, approval_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Approval '%s' decided: %s by '%s'. Reason: %s",
            approval_id, new_status, decided_by, reason or "(none)",
        )

    def expire_stale_approvals(self) -> int:
        """
        Mark all pending approvals whose expires_at has passed as 'expired'.

        Returns the count of approvals just expired.
        """
        now = _now_iso()
        conn = _get_conn()
        try:
            cursor = conn.execute(
                """
                UPDATE approvals
                SET status = 'expired'
                WHERE status = 'pending' AND expires_at <= ?
                """,
                (now,),
            )
            count = cursor.rowcount
            conn.commit()
        finally:
            conn.close()

        if count:
            logger.info("Expired %d stale approval(s).", count)
        return count

    # ── Private helpers ──────────────────────────────────────────────────────

    def _load_workspace_policies(self, workspace_id: str) -> list[sqlite3.Row]:
        """Load enabled workspace policies ordered by priority DESC."""
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, name, description, conditions_json, actions_json, priority
                FROM approval_policies
                WHERE workspace_id = ? AND enabled = 1
                ORDER BY priority DESC, created_at ASC
                """,
                (workspace_id,),
            ).fetchall()
            return list(rows)
        finally:
            conn.close()

    def _mark_expired(self, approval_id: str) -> None:
        """Set a single approval to 'expired'."""
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE approvals SET status = 'expired' WHERE id = ?",
                (approval_id,),
            )
            conn.commit()
        finally:
            conn.close()
