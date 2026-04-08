"""Swarm coordination data models for Phase 5 multi-agent system."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class Delegation:
    id: str = field(default_factory=_uuid)
    parent_job_id: str = ""
    child_job_id: str = ""
    delegation_type: str = "sub_task"  # sub_task | parallel | pipeline
    context_json: Optional[str] = None
    status: str = "active"  # active | completed | failed | cancelled
    created_at: str = field(default_factory=_now)
    completed_at: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "Delegation":
        return cls(**{k: row[k] for k in row.keys()})

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_job_id": self.parent_job_id,
            "child_job_id": self.child_job_id,
            "delegation_type": self.delegation_type,
            "context": json.loads(self.context_json) if self.context_json else None,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


@dataclass
class SharedMemoryEntry:
    id: str = field(default_factory=_uuid)
    tree_root_job_id: str = ""
    key: str = ""
    value_json: str = "{}"
    written_by_job_id: str = ""
    written_by_node_id: Optional[str] = None
    version: int = 1
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def from_row(cls, row) -> "SharedMemoryEntry":
        return cls(**{k: row[k] for k in row.keys()})

    @property
    def value(self) -> Any:
        return json.loads(self.value_json)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tree_root_job_id": self.tree_root_job_id,
            "key": self.key,
            "value": self.value,
            "written_by_job_id": self.written_by_job_id,
            "written_by_node_id": self.written_by_node_id,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ResourceLock:
    id: str = field(default_factory=_uuid)
    resource_path: str = ""
    lock_type: str = "exclusive"  # exclusive | shared
    held_by_job_id: str = ""
    held_by_node_id: Optional[str] = None
    acquired_at: str = field(default_factory=_now)
    expires_at: str = ""
    released_at: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "ResourceLock":
        return cls(**{k: row[k] for k in row.keys()})

    @property
    def is_expired(self) -> bool:
        return self.expires_at < _now() and self.released_at is None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "resource_path": self.resource_path,
            "lock_type": self.lock_type,
            "held_by_job_id": self.held_by_job_id,
            "held_by_node_id": self.held_by_node_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "released_at": self.released_at,
            "is_expired": self.is_expired,
        }


@dataclass
class AgentMessage:
    id: str = field(default_factory=_uuid)
    tree_root_job_id: str = ""
    from_job_id: str = ""
    to_job_id: Optional[str] = None  # None = broadcast
    message_type: str = "data"  # data | request | response | signal
    subject: Optional[str] = None
    body_json: str = "{}"
    status: str = "pending"  # pending | delivered | read | expired
    created_at: str = field(default_factory=_now)
    read_at: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "AgentMessage":
        return cls(**{k: row[k] for k in row.keys()})

    @property
    def body(self) -> Any:
        return json.loads(self.body_json)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tree_root_job_id": self.tree_root_job_id,
            "from_job_id": self.from_job_id,
            "to_job_id": self.to_job_id,
            "message_type": self.message_type,
            "subject": self.subject,
            "body": self.body,
            "status": self.status,
            "created_at": self.created_at,
            "read_at": self.read_at,
        }
