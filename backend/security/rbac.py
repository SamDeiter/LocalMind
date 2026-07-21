"""
LocalMind Security — Role-Based Access Control (RBAC)
======================================================
Defines role permissions and provides FastAPI dependencies for route-level
authorisation.

Roles
-----
``admin``    — Full access to every endpoint, including ``/api/admin/*``.
``operator`` — Create/view/cancel own jobs, chat, manage memories/documents/files.
``viewer``   — Read-only access to all ``/api/`` routes.

The bootstrap user (no keys in the database) is always treated as ``admin``
by the auth layer, so RBAC never blocks a fresh install.
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Depends, HTTPException, Request

from backend.security.auth import get_current_user

logger = logging.getLogger("localmind.security.rbac")

# ---------------------------------------------------------------------------
# Permission matrix
# ---------------------------------------------------------------------------

# Each role maps to a list of (HTTP method, path prefix) tuples.
# ``"*"`` as a method means any HTTP method is allowed on that prefix.

ROLE_PERMISSIONS: dict[str, list[tuple[str, str]]] = {
    "viewer": [
        ("GET", "/api/"),
    ],
    "operator": [
        # Read everything
        ("GET", "/api/"),
        # Write access for task-worker routes
        ("POST",   "/api/chat"),
        ("PUT",    "/api/chat"),
        ("DELETE", "/api/chat"),
        ("POST",   "/api/jobs"),
        ("PUT",    "/api/jobs"),
        ("DELETE", "/api/jobs"),
        ("POST",   "/api/conversations"),
        ("PUT",    "/api/conversations"),
        ("DELETE", "/api/conversations"),
        ("POST",   "/api/memory"),
        ("PUT",    "/api/memory"),
        ("DELETE", "/api/memory"),
        ("POST",   "/api/documents"),
        ("PUT",    "/api/documents"),
        ("DELETE", "/api/documents"),
        ("POST",   "/api/files"),
        ("PUT",    "/api/files"),
        ("DELETE", "/api/files"),
    ],
    "admin": [
        # Everything
        ("*", "/api/"),
    ],
}


# ---------------------------------------------------------------------------
# Permission check logic
# ---------------------------------------------------------------------------


def _is_allowed(role: str, method: str, path: str) -> bool:
    """Return ``True`` if *role* may perform *method* on *path*."""
    permissions = ROLE_PERMISSIONS.get(role, [])
    method_upper = method.upper()
    for allowed_method, allowed_prefix in permissions:
        # Ensure that the requested path matches the allowed prefix exactly
        # or as a complete path segment (e.g., followed by a trailing slash)
        # to prevent prefix-collision bypasses like /api/chat-admin matching /api/chat.
        if path == allowed_prefix or path.startswith(allowed_prefix.rstrip("/") + "/"):
            if allowed_method == "*" or allowed_method == method_upper:
                return True
    return False


# ---------------------------------------------------------------------------
# Middleware-style check (used by the auth middleware in server.py)
# ---------------------------------------------------------------------------


def check_permission(role: str, method: str, path: str) -> None:
    """Raise 403 if *role* is not allowed to perform *method* on *path*.

    This is called by the HTTP middleware after authentication succeeds.
    """
    if not _is_allowed(role, method, path):
        logger.warning(
            "RBAC denied: role=%s method=%s path=%s", role, method, path,
        )
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' is not permitted to {method} {path}",
        )


# ---------------------------------------------------------------------------
# FastAPI dependency factories
# ---------------------------------------------------------------------------


def require_role(*roles: str) -> Callable:
    """Return a FastAPI dependency that checks if the authenticated user has
    one of the specified *roles*.

    Usage::

        @router.get("/api/admin/keys", dependencies=[Depends(require_role("admin"))])
        async def list_keys(): ...
    """

    async def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            logger.warning(
                "RBAC denied: user=%s role=%s required=%s",
                user["user_id"], user["role"], roles,
            )
            raise HTTPException(
                status_code=403,
                detail=f"This endpoint requires one of these roles: {', '.join(roles)}",
            )
        return user

    return _dependency


# Convenience shortcut for admin-only routes
require_admin = require_role("admin")
