"""
routes/admin.py — Admin API Routes
====================================
Endpoints for managing API keys and querying the audit log.
All routes require the ``admin`` role.

Endpoints
---------
GET    /api/admin/keys          — List all API keys
POST   /api/admin/keys          — Create a new API key
DELETE /api/admin/keys/{key_id} — Revoke an API key
GET    /api/admin/audit         — Query job audit log
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.config import DB_PATH
from backend.core.audit import get_audit_logger
from backend.security.auth import APIKeyManager
from backend.security.rbac import require_admin

logger = logging.getLogger("localmind.routes.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CreateKeyRequest(BaseModel):
    """Request body for creating a new API key."""
    user_id: str = Field(..., description="ID of the user who will own this key")
    role: str = Field("operator", description="Role: admin | operator | viewer")
    description: str = Field("", description="Human-readable label for the key")


class CreateKeyResponse(BaseModel):
    """Response after creating an API key — contains the raw key (shown once)."""
    raw_key: str
    message: str = "Store this key securely — it will not be shown again."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ROLES = {"admin", "operator", "viewer"}


def _get_conn() -> sqlite3.Connection:
    """Return a new SQLite connection with WAL mode and row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/keys", dependencies=[Depends(require_admin)])
async def list_keys():
    """List all API keys (admin only).

    Returns key metadata — never the raw key or hash.
    """
    keys = APIKeyManager.list_keys()
    return {"keys": keys}


@router.post("/keys", dependencies=[Depends(require_admin)])
async def create_key(body: CreateKeyRequest):
    """Create a new API key (admin only).

    The response contains the raw key exactly once.
    """
    if body.role not in _VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{body.role}'. Must be one of: {', '.join(sorted(_VALID_ROLES))}",
        )

    # Verify the target user exists
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE id = ?", (body.user_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"User '{body.user_id}' not found",
        )

    raw_key = APIKeyManager.create_key(
        user_id=body.user_id,
        role=body.role,
        description=body.description,
    )

    logger.info("Admin created API key for user=%s role=%s", body.user_id, body.role)
    return CreateKeyResponse(raw_key=raw_key)


@router.delete("/keys/{key_id}", dependencies=[Depends(require_admin)])
async def revoke_key(key_id: str):
    """Revoke an API key (admin only).

    Soft-deletes the key by setting ``revoked_at``. Existing sessions using
    this key will fail on the next request.
    """
    # Verify the key exists
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM api_keys WHERE id = ?", (key_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")

    APIKeyManager.revoke_key(key_id)
    logger.info("Admin revoked API key: %s", key_id)
    return {"status": "revoked", "key_id": key_id}


@router.get("/audit", dependencies=[Depends(require_admin)])
async def query_audit_log(
    action: Optional[str] = Query(None, description="Filter by action type"),
    actor: Optional[str] = Query(None, description="Filter by actor"),
    job_id: Optional[str] = Query(None, description="Filter by job ID"),
    since: Optional[str] = Query(None, description="ISO timestamp — return entries after this time"),
    limit: int = Query(100, ge=1, le=1000, description="Max entries to return"),
):
    """Query the job audit log (admin only).

    Returns the most recent audit entries, optionally filtered by action,
    actor, job_id, and/or timestamp.  Delegates to
    :class:`backend.core.audit.AuditLogger` for consistent query logic.
    """
    al = get_audit_logger()
    entries = al.query(
        action=action,
        actor=actor,
        since=since,
        job_id=job_id,
        limit=limit,
    )
    return {"entries": entries, "count": len(entries)}
