"""
Job Pipeline data models.

Maps to the Phase 1 Job Pipeline tables defined in backend/core/schema.py:
  jobs, job_nodes, job_files, job_audit_log, pipeline_templates

The "train" metaphor:
  planner (engine) -> node executor (cars) -> reviewer (caboose)

All JSON-stored fields (tools_allowed, retry_policy_json, depends_on, nodes_json)
are deserialized to proper Python types on load and re-serialized on write.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("localmind.jobs.models")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL = object()


def parse_json_field(value: str | None, default: Any = None) -> Any:
    """Safely parse a JSON string field from the database.

    Returns *default* when *value* is None, empty, or cannot be decoded.
    """
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse JSON field (returning default): %r", value)
        return default


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    """Lifecycle states for a Job."""

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLING = "cancelling"


class NodeStatus(str, Enum):
    """Lifecycle states for a job Node (car in the train)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_INPUT = "awaiting_input"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------

_DEFAULT_BACKOFF_MS: list[int] = [1000, 5000, 15000]


@dataclass
class RetryPolicy:
    """Retry configuration for a node execution attempt."""

    max_attempts: int = 3
    backoff_ms: list[int] = field(default_factory=lambda: list(_DEFAULT_BACKOFF_MS))

    @classmethod
    def from_json(cls, s: str | None) -> RetryPolicy:
        """Deserialize from a JSON string (or None → defaults)."""
        data = parse_json_field(s, default={})
        if not isinstance(data, dict):
            logger.warning("retry_policy_json is not a dict; using defaults")
            data = {}
        return cls(
            max_attempts=int(data.get("max_attempts", 3)),
            backoff_ms=list(data.get("backoff_ms", _DEFAULT_BACKOFF_MS)),
        )

    def to_json(self) -> str:
        """Serialize to a JSON string for DB storage."""
        return json.dumps(
            {"max_attempts": self.max_attempts, "backoff_ms": self.backoff_ms},
            separators=(",", ":"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"max_attempts": self.max_attempts, "backoff_ms": self.backoff_ms}


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

# Fields excluded from the public API response
_JOB_INTERNAL_FIELDS: frozenset[str] = frozenset(
    {"error", "cost_cents", "review_count", "max_reviews"}
)


@dataclass
class Job:
    """Represents a row in the *jobs* table."""

    id: str
    workspace_id: str
    title: str
    description: str | None
    source: str
    source_ref: str | None
    status: str  # Raw string so callers can use JobStatus.value or compare freely
    priority: int
    requester: str | None
    # 'quick' — agent auto-generates nodes
    # 'pipeline' — user (or template) defines nodes
    mode: str
    template_id: str | None
    result_summary: str | None
    review_count: int
    max_reviews: int
    error: str | None
    cost_cents: float
    created_at: str
    updated_at: str

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        """Build a Job from a sqlite3.Row (dict-style access)."""
        return cls(
            id=row["id"],
            workspace_id=row["workspace_id"],
            title=row["title"],
            description=row["description"],
            source=row["source"],
            source_ref=row["source_ref"],
            status=row["status"],
            priority=row["priority"],
            requester=row["requester"],
            mode=row["mode"],
            template_id=row["template_id"],
            result_summary=row["result_summary"],
            review_count=row["review_count"],
            max_reviews=row["max_reviews"],
            error=row["error"],
            cost_cents=float(row["cost_cents"] or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Full representation including internal / operational fields."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "source_ref": self.source_ref,
            "status": self.status,
            "priority": self.priority,
            "requester": self.requester,
            "mode": self.mode,
            "template_id": self.template_id,
            "result_summary": self.result_summary,
            "review_count": self.review_count,
            "max_reviews": self.max_reviews,
            "error": self.error,
            "cost_cents": self.cost_cents,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_api_dict(self) -> dict[str, Any]:
        """Public API representation — excludes internal/operational fields."""
        return {
            k: v
            for k, v in self.to_dict().items()
            if k not in _JOB_INTERNAL_FIELDS
        }


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """Represents a row in the *job_nodes* table (a "car" in the train)."""

    id: str
    job_id: str
    sequence: int
    title: str
    instructions: str | None
    # DB stores JSON string; exposed as list[str]
    tools_allowed: list[str]
    expected_output: str | None
    status: str  # Raw string; compare with NodeStatus.value
    input_json: str | None
    output_json: str | None
    error: str | None
    auto_generated: bool
    input_schema_json: str | None
    output_schema_json: str | None
    side_effects_json: str | None
    retry_policy: RetryPolicy
    timeout_sec: int
    rollback_strategy: str | None
    acceptance_tests_json: str | None
    # DB stores JSON array; exposed as list[str]
    depends_on: list[str]
    created_at: str
    updated_at: str

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Node:
        """Build a Node from a sqlite3.Row."""
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            sequence=row["sequence"],
            title=row["title"],
            instructions=row["instructions"],
            tools_allowed=parse_json_field(row["tools_allowed"], default=[]),
            expected_output=row["expected_output"],
            status=row["status"],
            input_json=row["input_json"],
            output_json=row["output_json"],
            error=row["error"],
            auto_generated=bool(row["auto_generated"]),
            input_schema_json=row["input_schema_json"],
            output_schema_json=row["output_schema_json"],
            side_effects_json=row["side_effects_json"],
            retry_policy=RetryPolicy.from_json(row["retry_policy_json"]),
            timeout_sec=int(row["timeout_sec"] or 300),
            rollback_strategy=row["rollback_strategy"],
            acceptance_tests_json=row["acceptance_tests_json"],
            depends_on=parse_json_field(row["depends_on"], default=[]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Full representation including all fields."""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "sequence": self.sequence,
            "title": self.title,
            "instructions": self.instructions,
            "tools_allowed": self.tools_allowed,
            "expected_output": self.expected_output,
            "status": self.status,
            "input_json": self.input_json,
            "output_json": self.output_json,
            "error": self.error,
            "auto_generated": self.auto_generated,
            "input_schema_json": self.input_schema_json,
            "output_schema_json": self.output_schema_json,
            "side_effects_json": self.side_effects_json,
            "retry_policy": self.retry_policy.to_dict(),
            "timeout_sec": self.timeout_sec,
            "rollback_strategy": self.rollback_strategy,
            "acceptance_tests_json": self.acceptance_tests_json,
            "depends_on": self.depends_on,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# JobFile
# ---------------------------------------------------------------------------


@dataclass
class JobFile:
    """Represents a row in the *job_files* table."""

    id: str
    job_id: str
    node_id: str | None
    filename: str
    file_path: str
    file_type: str
    mime_type: str | None
    size_bytes: int | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> JobFile:
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            node_id=row["node_id"],
            filename=row["filename"],
            file_path=row["file_path"],
            file_type=row["file_type"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "node_id": self.node_id,
            "filename": self.filename,
            "file_path": self.file_path,
            "file_type": self.file_type,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
        }


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """Represents a row in the *job_audit_log* table."""

    id: str
    job_id: str
    node_id: str | None
    action: str
    detail: str | None
    actor: str | None
    timestamp: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AuditEntry:
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            node_id=row["node_id"],
            action=row["action"],
            detail=row["detail"],
            actor=row["actor"],
            timestamp=row["timestamp"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "node_id": self.node_id,
            "action": self.action,
            "detail": self.detail,
            "actor": self.actor,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# PipelineTemplate
# ---------------------------------------------------------------------------


@dataclass
class PipelineTemplate:
    """Represents a row in the *pipeline_templates* table."""

    id: str
    workspace_id: str
    name: str
    description: str | None
    # DB stores JSON string; exposed as list[dict]
    nodes: list[dict[str, Any]]
    created_by: str | None
    use_count: int
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> PipelineTemplate:
        return cls(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            description=row["description"],
            nodes=parse_json_field(row["nodes_json"], default=[]),
            created_by=row["created_by"],
            use_count=int(row["use_count"] or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "description": self.description,
            "nodes": self.nodes,
            "created_by": self.created_by,
            "use_count": self.use_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
