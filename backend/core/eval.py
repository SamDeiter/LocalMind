"""
Evaluation & Template Lifecycle — LocalMind enterprise task worker.

Manages evaluation test cases, eval run tracking, template version promotion,
prompt versioning, and model registry.

Tables used (created by backend/core/schema.py):
  - eval_cases          (workspace-scoped evaluation test definitions)
  - eval_runs           (individual evaluation run results)
  - template_versions   (versioned template lifecycle: draft -> candidate -> approved)
  - prompt_versions     (versioned system/node prompts with lifecycle)
  - model_registry      (available models with capabilities and defaults)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from backend.config import DB_PATH

logger = logging.getLogger("localmind.core.eval")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_conn() -> sqlite3.Connection:
    """Open a short-lived SQLite connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generate a new hex UUID (no dashes)."""
    return uuid.uuid4().hex


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convert a sqlite3.Row to a plain dict, or return None."""
    return dict(row) if row else None


# ── Valid lifecycle states ───────────────────────────────────────────────────

_TEMPLATE_STATES = ("draft", "candidate", "approved", "deprecated")
_TEMPLATE_PROMOTION_MAP: dict[str, set[str]] = {
    "draft": {"candidate"},
    "candidate": {"approved"},
}

_PROMPT_STATES = ("draft", "approved", "deprecated")
_PROMPT_PROMOTION_MAP: dict[str, set[str]] = {
    "draft": {"approved"},
}

_EVAL_STATUSES = ("passed", "failed", "partial")


# =============================================================================
# EvalCaseManager
# =============================================================================


class EvalCaseManager:
    """Manage evaluation test cases scoped to a workspace.

    Each case defines an input, expected output, and optional artifact type
    so that automated evaluation runs can compare actual vs. expected results.
    """

    def create_case(
        self,
        workspace_id: str,
        name: str,
        description: str,
        input_json: str,
        expected_output_json: str,
        artifact_type: Optional[str] = None,
    ) -> str:
        """Create a new evaluation test case.

        Args:
            workspace_id: Workspace this case belongs to.
            name: Human-readable case name.
            description: What the case tests.
            input_json: JSON string of input data for the evaluation.
            expected_output_json: JSON string of expected output.
            artifact_type: Optional artifact type filter (e.g. 'document', 'code').

        Returns:
            The new case ID (hex UUID).
        """
        case_id = _new_id()
        now = _now()

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO eval_cases
                    (id, workspace_id, name, description, input_json,
                     expected_output_json, artifact_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (case_id, workspace_id, name, description,
                 input_json, expected_output_json, artifact_type, now),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Created eval case: id=%s name=%r workspace=%s", case_id, name, workspace_id)
        return case_id

    def get_case(self, case_id: str) -> dict | None:
        """Return a single eval case by ID, or None if not found.

        Returns:
            Dict with all case fields, or None.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM eval_cases WHERE id = ?", (case_id,)
            ).fetchone()
        finally:
            conn.close()

        return _row_to_dict(row)

    def list_cases(self, workspace_id: str) -> list[dict]:
        """Return all eval cases for a workspace, ordered by creation time.

        Args:
            workspace_id: Workspace to list cases for.

        Returns:
            List of case dicts.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM eval_cases
                WHERE workspace_id = ?
                ORDER BY created_at ASC
                """,
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()

        return [dict(r) for r in rows]

    def delete_case(self, case_id: str) -> None:
        """Delete an eval case and all associated runs.

        Args:
            case_id: The case to delete.
        """
        conn = _get_conn()
        try:
            # Delete runs first (child records).
            conn.execute(
                "DELETE FROM eval_runs WHERE eval_case_id = ?", (case_id,)
            )
            conn.execute(
                "DELETE FROM eval_cases WHERE id = ?", (case_id,)
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Deleted eval case: id=%s (and associated runs)", case_id)


# =============================================================================
# EvalRunTracker
# =============================================================================


class EvalRunTracker:
    """Track evaluation runs — each run scores a case execution against expectations.

    Runs move through: started -> completed (passed/failed/partial).
    """

    def start_run(
        self,
        eval_case_id: str,
        job_id: Optional[str] = None,
        model_config_json: Optional[str] = None,
    ) -> str:
        """Start a new evaluation run.

        Args:
            eval_case_id: The eval case being tested.
            job_id: Optional job that produced the output.
            model_config_json: Optional JSON describing model configuration used.

        Returns:
            The new run ID (hex UUID).
        """
        run_id = _new_id()
        now = _now()

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO eval_runs
                    (id, eval_case_id, job_id, status, model_config_json, ran_at)
                VALUES (?, ?, ?, 'running', ?, ?)
                """,
                (run_id, eval_case_id, job_id, model_config_json, now),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Started eval run: id=%s case=%s job=%s", run_id, eval_case_id, job_id)
        return run_id

    def complete_run(
        self,
        run_id: str,
        status: str,
        score: float,
        details_json: str,
        duration_ms: int,
    ) -> None:
        """Complete an evaluation run with results.

        Args:
            run_id: The run to complete.
            status: One of 'passed', 'failed', 'partial'.
            score: Numeric score (0.0 - 1.0 typical, but not enforced).
            details_json: JSON string with detailed comparison results.
            duration_ms: Wall-clock duration in milliseconds.

        Raises:
            ValueError: If status is not one of the allowed values.
        """
        if status not in _EVAL_STATUSES:
            raise ValueError(
                f"Invalid eval run status '{status}'. "
                f"Must be one of: {', '.join(_EVAL_STATUSES)}"
            )

        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE eval_runs
                SET status = ?, score = ?, details_json = ?, duration_ms = ?
                WHERE id = ?
                """,
                (status, score, details_json, duration_ms, run_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Completed eval run: id=%s status=%s score=%.3f duration=%dms",
            run_id, status, score, duration_ms,
        )

    def get_run(self, run_id: str) -> dict | None:
        """Return a single eval run by ID, or None if not found.

        Returns:
            Dict with all run fields, or None.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM eval_runs WHERE id = ?", (run_id,)
            ).fetchone()
        finally:
            conn.close()

        return _row_to_dict(row)

    def list_runs(
        self,
        eval_case_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> list[dict]:
        """List eval runs, optionally filtered by case or workspace.

        Args:
            eval_case_id: Filter to runs for this specific case.
            workspace_id: Filter to runs whose case belongs to this workspace.

        Returns:
            List of run dicts, ordered by ran_at DESC.
        """
        conn = _get_conn()
        try:
            if eval_case_id:
                rows = conn.execute(
                    """
                    SELECT * FROM eval_runs
                    WHERE eval_case_id = ?
                    ORDER BY ran_at DESC
                    """,
                    (eval_case_id,),
                ).fetchall()
            elif workspace_id:
                rows = conn.execute(
                    """
                    SELECT er.* FROM eval_runs er
                    JOIN eval_cases ec ON er.eval_case_id = ec.id
                    WHERE ec.workspace_id = ?
                    ORDER BY er.ran_at DESC
                    """,
                    (workspace_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM eval_runs ORDER BY ran_at DESC"
                ).fetchall()
        finally:
            conn.close()

        return [dict(r) for r in rows]

    def compare_runs(
        self,
        baseline_run_id: str,
        candidate_run_id: str,
    ) -> dict:
        """Compare two eval runs and return a regression/improvement analysis.

        Args:
            baseline_run_id: The reference run (e.g. current production).
            candidate_run_id: The run being evaluated against the baseline.

        Returns:
            Dict with keys:
              - baseline: full baseline run dict
              - candidate: full candidate run dict
              - score_delta: candidate.score - baseline.score
              - verdict: 'improvement', 'regression', or 'unchanged'
              - details: human-readable summary

        Raises:
            ValueError: If either run is not found or still running.
        """
        baseline = self.get_run(baseline_run_id)
        candidate = self.get_run(candidate_run_id)

        if baseline is None:
            raise ValueError(f"Baseline run '{baseline_run_id}' not found.")
        if candidate is None:
            raise ValueError(f"Candidate run '{candidate_run_id}' not found.")

        if baseline["status"] == "running":
            raise ValueError(f"Baseline run '{baseline_run_id}' is still running.")
        if candidate["status"] == "running":
            raise ValueError(f"Candidate run '{candidate_run_id}' is still running.")

        baseline_score = baseline.get("score") or 0.0
        candidate_score = candidate.get("score") or 0.0
        score_delta = candidate_score - baseline_score

        # Determine verdict.
        if abs(score_delta) < 1e-9:
            verdict = "unchanged"
        elif score_delta > 0:
            verdict = "improvement"
        else:
            verdict = "regression"

        # Build details summary.
        status_changed = baseline["status"] != candidate["status"]
        details_parts = [
            f"Baseline: score={baseline_score:.3f} status={baseline['status']}",
            f"Candidate: score={candidate_score:.3f} status={candidate['status']}",
            f"Score delta: {score_delta:+.3f}",
            f"Verdict: {verdict}",
        ]
        if status_changed:
            details_parts.append(
                f"Status changed: {baseline['status']} -> {candidate['status']}"
            )

        # Duration comparison.
        baseline_dur = baseline.get("duration_ms") or 0
        candidate_dur = candidate.get("duration_ms") or 0
        if baseline_dur > 0 and candidate_dur > 0:
            dur_delta = candidate_dur - baseline_dur
            dur_pct = (dur_delta / baseline_dur) * 100
            details_parts.append(
                f"Duration delta: {dur_delta:+d}ms ({dur_pct:+.1f}%)"
            )

        return {
            "baseline": baseline,
            "candidate": candidate,
            "score_delta": score_delta,
            "verdict": verdict,
            "status_changed": status_changed,
            "duration_delta_ms": candidate_dur - baseline_dur if (baseline_dur and candidate_dur) else None,
            "details": " | ".join(details_parts),
        }


# =============================================================================
# TemplateVersionManager
# =============================================================================


class TemplateVersionManager:
    """Manage template version lifecycle: draft -> candidate -> approved -> deprecated.

    Each template_id can have many versions. Only one version should be
    'approved' at a time per template (enforced by get_active_version returning
    the latest approved).
    """

    def create_version(
        self,
        template_id: str,
        nodes_json: str,
    ) -> str:
        """Create a new template version (starts as 'draft').

        Automatically increments the version_number based on existing versions
        for the given template_id.

        Args:
            template_id: The template this version belongs to.
            nodes_json: JSON string defining the template's node graph.

        Returns:
            The new version ID (hex UUID).
        """
        version_id = _new_id()
        now = _now()

        conn = _get_conn()
        try:
            # Get next version number.
            row = conn.execute(
                """
                SELECT COALESCE(MAX(version_number), 0) AS max_ver
                FROM template_versions
                WHERE template_id = ?
                """,
                (template_id,),
            ).fetchone()
            next_version = row["max_ver"] + 1

            conn.execute(
                """
                INSERT INTO template_versions
                    (id, template_id, version_number, nodes_json,
                     lifecycle_state, created_at)
                VALUES (?, ?, ?, ?, 'draft', ?)
                """,
                (version_id, template_id, next_version, nodes_json, now),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Created template version: id=%s template=%s v%d",
            version_id, template_id, next_version,
        )
        return version_id

    def promote(
        self,
        version_id: str,
        promoted_by: str,
        eval_run_id: Optional[str] = None,
        target_state: str = "candidate",
    ) -> None:
        """Promote a template version through the lifecycle.

        Valid transitions:
          - draft -> candidate
          - candidate -> approved

        Args:
            version_id: The version to promote.
            promoted_by: User ID performing the promotion.
            eval_run_id: Optional eval run that justifies this promotion.
            target_state: The desired new state ('candidate' or 'approved').

        Raises:
            ValueError: If the transition is not valid.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT lifecycle_state, template_id FROM template_versions WHERE id = ?",
                (version_id,),
            ).fetchone()

            if row is None:
                raise ValueError(f"Template version '{version_id}' not found.")

            current_state = row["lifecycle_state"]
            allowed_targets = _TEMPLATE_PROMOTION_MAP.get(current_state, set())

            if target_state not in allowed_targets:
                raise ValueError(
                    f"Cannot promote template version from '{current_state}' to '{target_state}'. "
                    f"Allowed targets: {allowed_targets or 'none (terminal state)'}."
                )

            now = _now()
            conn.execute(
                """
                UPDATE template_versions
                SET lifecycle_state = ?, promoted_by = ?, promoted_at = ?, eval_run_id = ?
                WHERE id = ?
                """,
                (target_state, promoted_by, now, eval_run_id, version_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Promoted template version %s: %s -> %s (by %s)",
            version_id, current_state, target_state, promoted_by,
        )

    def deprecate(self, version_id: str) -> None:
        """Mark a template version as deprecated.

        Any non-deprecated version can be deprecated.

        Args:
            version_id: The version to deprecate.

        Raises:
            ValueError: If the version is not found or already deprecated.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT lifecycle_state FROM template_versions WHERE id = ?",
                (version_id,),
            ).fetchone()

            if row is None:
                raise ValueError(f"Template version '{version_id}' not found.")
            if row["lifecycle_state"] == "deprecated":
                raise ValueError(f"Template version '{version_id}' is already deprecated.")

            now = _now()
            conn.execute(
                """
                UPDATE template_versions
                SET lifecycle_state = 'deprecated', promoted_at = ?
                WHERE id = ?
                """,
                (now, version_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Deprecated template version: id=%s", version_id)

    def get_active_version(self, template_id: str) -> dict | None:
        """Return the latest 'approved' version for a template, or None.

        Args:
            template_id: The template to look up.

        Returns:
            Dict with all version fields, or None if no approved version exists.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT * FROM template_versions
                WHERE template_id = ? AND lifecycle_state = 'approved'
                ORDER BY version_number DESC
                LIMIT 1
                """,
                (template_id,),
            ).fetchone()
        finally:
            conn.close()

        return _row_to_dict(row)

    def list_versions(self, template_id: str) -> list[dict]:
        """Return all versions for a template, ordered by version number DESC.

        Args:
            template_id: The template to list versions for.

        Returns:
            List of version dicts.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM template_versions
                WHERE template_id = ?
                ORDER BY version_number DESC
                """,
                (template_id,),
            ).fetchall()
        finally:
            conn.close()

        return [dict(r) for r in rows]


# =============================================================================
# PromptVersionManager
# =============================================================================


class PromptVersionManager:
    """Version system and node prompts with a draft -> approved lifecycle.

    Prompts are identified by name (e.g. 'system_default', 'node_research').
    Each name can have multiple versions. Only the latest 'approved' version
    is active.
    """

    def create_version(self, name: str, content: str) -> str:
        """Create a new prompt version (starts as 'draft').

        Automatically increments the version_number for the given prompt name.

        Args:
            name: The prompt identifier (e.g. 'system_default').
            content: The full prompt text.

        Returns:
            The new version ID (hex UUID).
        """
        version_id = _new_id()
        now = _now()

        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(version_number), 0) AS max_ver
                FROM prompt_versions
                WHERE name = ?
                """,
                (name,),
            ).fetchone()
            next_version = row["max_ver"] + 1

            conn.execute(
                """
                INSERT INTO prompt_versions
                    (id, name, version_number, content, lifecycle_state, created_at)
                VALUES (?, ?, ?, ?, 'draft', ?)
                """,
                (version_id, name, next_version, content, now),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Created prompt version: id=%s name=%r v%d",
            version_id, name, next_version,
        )
        return version_id

    def promote(self, version_id: str, target_state: str = "approved") -> None:
        """Promote a prompt version through the lifecycle.

        Valid transition: draft -> approved.

        Args:
            version_id: The version to promote.
            target_state: The desired new state (must be 'approved').

        Raises:
            ValueError: If the transition is not valid.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT lifecycle_state FROM prompt_versions WHERE id = ?",
                (version_id,),
            ).fetchone()

            if row is None:
                raise ValueError(f"Prompt version '{version_id}' not found.")

            current_state = row["lifecycle_state"]
            allowed_targets = _PROMPT_PROMOTION_MAP.get(current_state, set())

            if target_state not in allowed_targets:
                raise ValueError(
                    f"Cannot promote prompt version from '{current_state}' to '{target_state}'. "
                    f"Allowed targets: {allowed_targets or 'none (terminal state)'}."
                )

            conn.execute(
                """
                UPDATE prompt_versions
                SET lifecycle_state = ?
                WHERE id = ?
                """,
                (target_state, version_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Promoted prompt version %s: %s -> %s",
            version_id, current_state, target_state,
        )

    def get_active(self, name: str) -> dict | None:
        """Return the latest 'approved' version for a prompt name, or None.

        Args:
            name: The prompt identifier.

        Returns:
            Dict with all version fields, or None if no approved version exists.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT * FROM prompt_versions
                WHERE name = ? AND lifecycle_state = 'approved'
                ORDER BY version_number DESC
                LIMIT 1
                """,
                (name,),
            ).fetchone()
        finally:
            conn.close()

        return _row_to_dict(row)

    def list_versions(self, name: str) -> list[dict]:
        """Return all versions for a prompt name, ordered by version number DESC.

        Args:
            name: The prompt identifier.

        Returns:
            List of version dicts.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM prompt_versions
                WHERE name = ?
                ORDER BY version_number DESC
                """,
                (name,),
            ).fetchall()
        finally:
            conn.close()

        return [dict(r) for r in rows]


# =============================================================================
# ModelRegistry
# =============================================================================


class ModelRegistry:
    """Track available models and their capabilities.

    The model_registry table stores model metadata. Workspace scoping and
    default-role assignments are stored inside capabilities_json as:
        {"workspace_id": "...", "default_for": "execution", ...}

    This avoids schema migration while providing the full API surface.
    """

    def register_model(
        self,
        workspace_id: str,
        name: str,
        provider: str,
        model_id: str,
        capabilities_json: Optional[str] = None,
        default_for: Optional[str] = None,
    ) -> str:
        """Register a new model in the registry.

        Args:
            workspace_id: Workspace this model is available in.
            name: Human-readable model name (e.g. 'GPT-4o').
            provider: Provider identifier (e.g. 'openai', 'ollama', 'anthropic').
            model_id: Provider-specific model identifier.
            capabilities_json: Optional JSON string with capability metadata.
            default_for: Optional role this model is default for
                         ('execution', 'planning', 'review').

        Returns:
            The new registry entry ID (hex UUID).
        """
        entry_id = _new_id()
        now = _now()

        # Merge workspace_id and default_for into capabilities.
        caps = json.loads(capabilities_json) if capabilities_json else {}
        caps["workspace_id"] = workspace_id
        if default_for:
            caps["default_for"] = default_for
        merged_caps = json.dumps(caps)

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO model_registry
                    (id, name, provider, model_id, capabilities_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
                """,
                (entry_id, name, provider, model_id, merged_caps, now),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Registered model: id=%s name=%r provider=%s model_id=%s workspace=%s",
            entry_id, name, provider, model_id, workspace_id,
        )
        return entry_id

    def get_model(
        self,
        model_id_or_name: str,
        workspace_id: str,
    ) -> dict | None:
        """Look up a model by registry ID or name within a workspace.

        Tries ID match first, then falls back to name match scoped to workspace.

        Args:
            model_id_or_name: Registry entry ID or model name.
            workspace_id: Workspace scope for the lookup.

        Returns:
            Dict with model fields (including parsed capabilities), or None.
        """
        conn = _get_conn()
        try:
            # Try by ID first.
            row = conn.execute(
                "SELECT * FROM model_registry WHERE id = ?",
                (model_id_or_name,),
            ).fetchone()

            if row is not None:
                result = dict(row)
                caps = json.loads(result.get("capabilities_json") or "{}")
                if caps.get("workspace_id") == workspace_id:
                    result["_capabilities"] = caps
                    return result
                # ID matched but wrong workspace — fall through to name search.

            # Try by name within workspace.
            rows = conn.execute(
                "SELECT * FROM model_registry WHERE name = ? AND status = 'active'",
                (model_id_or_name,),
            ).fetchall()

            for r in rows:
                d = dict(r)
                caps = json.loads(d.get("capabilities_json") or "{}")
                if caps.get("workspace_id") == workspace_id:
                    d["_capabilities"] = caps
                    return d
        finally:
            conn.close()

        return None

    def list_models(
        self,
        workspace_id: str,
        provider: Optional[str] = None,
    ) -> list[dict]:
        """List all active models for a workspace, optionally filtered by provider.

        Args:
            workspace_id: Workspace to list models for.
            provider: Optional provider filter.

        Returns:
            List of model dicts.
        """
        conn = _get_conn()
        try:
            if provider:
                rows = conn.execute(
                    """
                    SELECT * FROM model_registry
                    WHERE provider = ? AND status = 'active'
                    ORDER BY created_at DESC
                    """,
                    (provider,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM model_registry
                    WHERE status = 'active'
                    ORDER BY created_at DESC
                    """,
                ).fetchall()
        finally:
            conn.close()

        # Filter to workspace via capabilities_json.
        results = []
        for r in rows:
            d = dict(r)
            caps = json.loads(d.get("capabilities_json") or "{}")
            if caps.get("workspace_id") == workspace_id:
                d["_capabilities"] = caps
                results.append(d)

        return results

    def set_default(self, model_id: str, default_for: str) -> None:
        """Set a model as the default for a given role.

        First clears any existing default for the same workspace + role,
        then sets the new default.

        Args:
            model_id: Registry entry ID to make the default.
            default_for: Role this model is default for
                         ('execution', 'planning', 'review').

        Raises:
            ValueError: If the model is not found or not active.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT capabilities_json, status FROM model_registry WHERE id = ?",
                (model_id,),
            ).fetchone()

            if row is None:
                raise ValueError(f"Model '{model_id}' not found.")
            if row["status"] != "active":
                raise ValueError(f"Model '{model_id}' is not active (status={row['status']}).")

            caps = json.loads(row["capabilities_json"] or "{}")
            workspace_id = caps.get("workspace_id")

            # Clear existing defaults for this workspace + role.
            if workspace_id:
                all_models = conn.execute(
                    "SELECT id, capabilities_json FROM model_registry WHERE status = 'active'"
                ).fetchall()

                for m in all_models:
                    m_caps = json.loads(m["capabilities_json"] or "{}")
                    if (m_caps.get("workspace_id") == workspace_id
                            and m_caps.get("default_for") == default_for
                            and m["id"] != model_id):
                        m_caps.pop("default_for", None)
                        conn.execute(
                            "UPDATE model_registry SET capabilities_json = ? WHERE id = ?",
                            (json.dumps(m_caps), m["id"]),
                        )

            # Set the new default.
            caps["default_for"] = default_for
            conn.execute(
                "UPDATE model_registry SET capabilities_json = ? WHERE id = ?",
                (json.dumps(caps), model_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Set model %s as default for '%s'", model_id, default_for)

    def get_default(self, workspace_id: str, role: str) -> dict | None:
        """Get the default model for a workspace and role.

        Args:
            workspace_id: Workspace to look up.
            role: The role ('execution', 'planning', 'review').

        Returns:
            Dict with model fields, or None if no default is set.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM model_registry WHERE status = 'active'"
            ).fetchall()
        finally:
            conn.close()

        for r in rows:
            d = dict(r)
            caps = json.loads(d.get("capabilities_json") or "{}")
            if (caps.get("workspace_id") == workspace_id
                    and caps.get("default_for") == role):
                d["_capabilities"] = caps
                return d

        return None

    def disable_model(self, model_id: str) -> None:
        """Disable a model (set status to 'disabled').

        Args:
            model_id: Registry entry ID to disable.

        Raises:
            ValueError: If the model is not found.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM model_registry WHERE id = ?", (model_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Model '{model_id}' not found.")

            conn.execute(
                "UPDATE model_registry SET status = 'disabled' WHERE id = ?",
                (model_id,),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Disabled model: id=%s", model_id)
