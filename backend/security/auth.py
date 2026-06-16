"""
LocalMind Security — API Key Authentication
=============================================
Manages API key lifecycle (create, validate, revoke, list) and request-level
rate limiting.  Keys are stored as SHA-256 hashes in the ``api_keys`` table
(schema created by ``backend/core/schema.py``).

Bootstrap mode: when no API keys exist in the database the system treats every
request as an admin user, allowing the first operator to create keys.

Rate limiting uses an in-process sliding window (per-key, 60 RPM default).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from backend.config import DB_PATH

logger = logging.getLogger("localmind.security.auth")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RATE_LIMIT_RPM: int = int(os.getenv("RATE_LIMIT_RPM", "60"))

# FastAPI security scheme — reads the ``X-API-Key`` header.
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RateLimitExceeded(HTTPException):
    """Raised when a key exceeds its per-minute request allowance."""

    def __init__(self, detail: str = "Rate limit exceeded") -> None:
        super().__init__(status_code=429, detail=detail)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of *raw_key*."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _get_conn() -> sqlite3.Connection:
    """Return a new SQLite connection with WAL mode and row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# In-memory sliding-window rate limiter
# ---------------------------------------------------------------------------


class _SlidingWindowLimiter:
    """Thread-safe-ish sliding window rate limiter (single-process)."""

    def __init__(self, max_requests: int, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # key_id -> list of timestamps
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key_id: str) -> bool:
        """Return ``True`` if the request is allowed, ``False`` if rate-limited."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        hits = self._hits[key_id]
        # Prune expired entries
        self._hits[key_id] = hits = [t for t in hits if t > cutoff]
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True


_limiter = _SlidingWindowLimiter(max_requests=RATE_LIMIT_RPM)

# ---------------------------------------------------------------------------
# APIKeyManager
# ---------------------------------------------------------------------------


class APIKeyManager:
    """CRUD manager for API keys stored in SQLite.

    Keys are generated as 32-byte URL-safe tokens prefixed with ``lm_``.
    Only the SHA-256 hash is persisted; the raw key is returned exactly once
    at creation time.
    """

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @staticmethod
    def create_key(
        user_id: str,
        role: str = "operator",
        description: str = "",
    ) -> str:
        """Create a new API key for *user_id*.

        Parameters
        ----------
        user_id:
            ID of the user who owns this key (must exist in ``users`` table).
        role:
            One of ``admin``, ``operator``, ``viewer``.
        description:
            Human-readable label for the key.

        Returns
        -------
        str
            The raw API key (``lm_...``). This is the **only** time the raw
            key is available — it is not stored anywhere.
        """
        raw_key = "lm_" + secrets.token_urlsafe(32)
        key_hash = _hash_key(raw_key)
        key_id = str(uuid.uuid4())
        now = _now_iso()

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO api_keys (id, user_id, key_hash, name, role, rate_limit, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (key_id, user_id, key_hash, description, role, RATE_LIMIT_RPM, now),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "API key created: id=%s user=%s role=%s",
            key_id, user_id, role,
        )
        return raw_key

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    @staticmethod
    def validate_key(raw_key: str) -> Optional[dict]:
        """Validate *raw_key* against the database.

        Returns a dict with key metadata on success, or ``None`` if the key
        is invalid or revoked.
        """
        key_hash = _hash_key(raw_key)

        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT id, user_id, role, name, rate_limit, created_at, last_used_at
                FROM api_keys
                WHERE key_hash = ? AND revoked_at IS NULL
                """,
                (key_hash,),
            ).fetchone()

            if row is None:
                return None

            # Update last_used_at
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (_now_iso(), row["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "key_id": row["id"],
            "user_id": row["user_id"],
            "role": row["role"],
            "description": row["name"],
            "rate_limit": row["rate_limit"],
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"],
        }

    # ------------------------------------------------------------------
    # Revoke
    # ------------------------------------------------------------------

    @staticmethod
    def revoke_key(key_id: str) -> None:
        """Soft-revoke an API key by setting ``revoked_at``."""
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ?",
                (_now_iso(), key_id),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("API key revoked: %s", key_id)

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    @staticmethod
    def list_keys(user_id: Optional[str] = None) -> list[dict]:
        """Return all API keys, optionally filtered by *user_id*.

        Revoked keys are included (with ``revoked_at`` populated).
        """
        conn = _get_conn()
        try:
            if user_id:
                rows = conn.execute(
                    """
                    SELECT id, user_id, role, name, rate_limit,
                           created_at, last_used_at, revoked_at
                    FROM api_keys
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    """,
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, user_id, role, name, rate_limit,
                           created_at, last_used_at, revoked_at
                    FROM api_keys
                    ORDER BY created_at DESC
                    """
                ).fetchall()
        finally:
            conn.close()

        return [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "role": r["role"],
                "description": r["name"],
                "rate_limit": r["rate_limit"],
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
                "revoked_at": r["revoked_at"],
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Bootstrap check — are any keys in the database?
# ---------------------------------------------------------------------------


def _has_any_keys() -> bool:
    """Return ``True`` if at least one API key exists (including revoked ones).

    This ensures that once the system has been bootstrapped, it does not
    revert to an unauthenticated state if all keys are revoked.
    """
    conn = _get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM api_keys").fetchone()
        return row["cnt"] > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FastAPI dependency — get_current_user
# ---------------------------------------------------------------------------


_BOOTSTRAP_USER: dict = {
    "user_id": "bootstrap",
    "role": "admin",
    "key_id": "bootstrap",
}


def authenticate_request(request: Request) -> dict:
    """Synchronous authentication for use in HTTP middleware.

    Extracts ``X-API-Key`` from the request headers and validates it.
    Same logic as ``get_current_user`` but does not use FastAPI DI.
    """
    api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    client_ip = request.client.host if request.client else "unknown"

    # Bootstrap mode
    if not _has_any_keys():
        return _BOOTSTRAP_USER

    if not api_key:
        logger.warning("Auth failure: missing X-API-Key header (ip=%s)", client_ip)
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    key_meta = APIKeyManager.validate_key(api_key)
    if key_meta is None:
        logger.warning("Auth failure: invalid API key (ip=%s)", client_ip)
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    if not _limiter.check(key_meta["key_id"]):
        logger.warning(
            "Rate limit exceeded: key=%s user=%s (ip=%s)",
            key_meta["key_id"], key_meta["user_id"], client_ip,
        )
        raise RateLimitExceeded(
            detail=f"Rate limit exceeded ({RATE_LIMIT_RPM} requests/min)"
        )

    return {
        "user_id": key_meta["user_id"],
        "role": key_meta["role"],
        "key_id": key_meta["key_id"],
    }


async def get_current_user(
    request: Request,
    api_key: Optional[str] = Security(_api_key_header),
) -> dict:
    """FastAPI dependency that authenticates the caller via ``X-API-Key``.

    Returns a user-context dict::

        {"user_id": str, "role": str, "key_id": str}

    Behaviour:
    - If no API keys exist in the database (fresh install), all requests are
      treated as admin (bootstrap mode).
    - If the header is missing or invalid, returns 401.
    - If the key's rate limit is exceeded, returns 429.

    Auth failures are logged with the source IP.
    """
    client_ip = request.client.host if request.client else "unknown"

    # Bootstrap mode — no keys in the system yet
    if not _has_any_keys():
        return _BOOTSTRAP_USER

    # Missing header
    if not api_key:
        logger.warning("Auth failure: missing X-API-Key header (ip=%s)", client_ip)
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header",
        )

    # Validate
    key_meta = APIKeyManager.validate_key(api_key)
    if key_meta is None:
        logger.warning("Auth failure: invalid API key (ip=%s)", client_ip)
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key",
        )

    # Rate limit check
    if not _limiter.check(key_meta["key_id"]):
        logger.warning(
            "Rate limit exceeded: key=%s user=%s (ip=%s)",
            key_meta["key_id"], key_meta["user_id"], client_ip,
        )
        raise RateLimitExceeded(
            detail=f"Rate limit exceeded ({RATE_LIMIT_RPM} requests/min)"
        )

    return {
        "user_id": key_meta["user_id"],
        "role": key_meta["role"],
        "key_id": key_meta["key_id"],
    }
