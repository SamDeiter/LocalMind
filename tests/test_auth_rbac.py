"""
Comprehensive tests for LocalMind auth, RBAC, and admin modules.

Covers:
  1. backend.security.auth   — APIKeyManager, _SlidingWindowLimiter,
                                 authenticate_request, get_current_user
  2. backend.security.rbac   — check_permission, require_role, require_admin
  3. backend.routes.admin    — GET/POST/DELETE /api/admin/keys, GET /api/admin/audit
"""

from __future__ import annotations

import hashlib
import time
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

import pytest
from fastapi import HTTPException

from backend.security.auth import (
    APIKeyManager,
    RateLimitExceeded,
    _SlidingWindowLimiter,
    _hash_key,
    authenticate_request,
    get_current_user,
    _BOOTSTRAP_USER,
)
from backend.security.rbac import (
    ROLE_PERMISSIONS,
    check_permission,
    require_role,
    require_admin,
    _is_allowed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_conn(rows=None, fetchone_val=None):
    """Build a mock sqlite3 connection with configurable execute results."""
    conn = MagicMock()
    cursor = MagicMock()

    if fetchone_val is not None:
        cursor.fetchone.return_value = fetchone_val
    else:
        cursor.fetchone.return_value = None

    if rows is not None:
        cursor.fetchall.return_value = rows
    else:
        cursor.fetchall.return_value = []

    conn.execute.return_value = cursor
    return conn


class _FakeHeaders(dict):
    """Dict subclass so MagicMock doesn't intercept .get()."""
    pass


def _make_request(api_key=None, client_host="127.0.0.1"):
    """Build a mock FastAPI Request with optional X-API-Key header."""
    req = MagicMock()
    headers = _FakeHeaders()
    if api_key is not None:
        headers["X-API-Key"] = api_key
        headers["x-api-key"] = api_key
    req.headers = headers
    client = MagicMock()
    client.host = client_host
    req.client = client
    return req


# ---------------------------------------------------------------------------
# 1. TestAPIKeyManager
# ---------------------------------------------------------------------------

class TestAPIKeyManager:
    """Tests for APIKeyManager CRUD static methods."""

    @patch("backend.security.auth._get_conn")
    def test_create_key_returns_lm_prefix(self, mock_conn_fn):
        mock_conn_fn.return_value = _make_mock_conn()
        raw = APIKeyManager.create_key("user1", "operator", "my key")
        assert raw.startswith("lm_")

    @patch("backend.security.auth._get_conn")
    def test_create_key_returns_string(self, mock_conn_fn):
        mock_conn_fn.return_value = _make_mock_conn()
        raw = APIKeyManager.create_key("user1")
        assert isinstance(raw, str)
        assert len(raw) > 10

    @patch("backend.security.auth._get_conn")
    def test_create_key_stores_hash_not_plaintext(self, mock_conn_fn):
        conn = _make_mock_conn()
        mock_conn_fn.return_value = conn
        raw = APIKeyManager.create_key("user1", "admin", "desc")
        # The INSERT should have been called with the hash, not the raw key
        call_args = conn.execute.call_args_list[0]
        sql, params = call_args[0]
        stored_hash = params[2]  # key_hash is the 3rd param
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert stored_hash == expected
        assert raw not in params  # raw key must never be in the INSERT params

    @patch("backend.security.auth._get_conn")
    def test_create_key_with_default_role(self, mock_conn_fn):
        conn = _make_mock_conn()
        mock_conn_fn.return_value = conn
        APIKeyManager.create_key("user1")
        call_args = conn.execute.call_args_list[0]
        params = call_args[0][1]
        role_param = params[4]  # role is the 5th param
        assert role_param == "operator"

    @patch("backend.security.auth._get_conn")
    def test_create_key_commits_to_db(self, mock_conn_fn):
        conn = _make_mock_conn()
        mock_conn_fn.return_value = conn
        APIKeyManager.create_key("user1")
        conn.commit.assert_called_once()

    @patch("backend.security.auth._get_conn")
    def test_create_key_closes_connection(self, mock_conn_fn):
        conn = _make_mock_conn()
        mock_conn_fn.return_value = conn
        APIKeyManager.create_key("user1")
        conn.close.assert_called_once()

    @patch("backend.security.auth._get_conn")
    def test_create_key_unique_each_call(self, mock_conn_fn):
        mock_conn_fn.return_value = _make_mock_conn()
        k1 = APIKeyManager.create_key("user1")
        k2 = APIKeyManager.create_key("user1")
        assert k1 != k2

    @patch("backend.security.auth._get_conn")
    def test_validate_key_valid(self, mock_conn_fn):
        row = {
            "id": "key-123",
            "user_id": "user1",
            "role": "operator",
            "name": "my key",
            "rate_limit": 60,
            "created_at": "2025-01-01T00:00:00",
            "last_used_at": None,
        }
        conn = _make_mock_conn(fetchone_val=row)
        mock_conn_fn.return_value = conn
        result = APIKeyManager.validate_key("lm_somerawkey")
        assert result is not None
        assert result["key_id"] == "key-123"
        assert result["user_id"] == "user1"
        assert result["role"] == "operator"
        assert result["description"] == "my key"

    @patch("backend.security.auth._get_conn")
    def test_validate_key_invalid_returns_none(self, mock_conn_fn):
        conn = _make_mock_conn(fetchone_val=None)
        mock_conn_fn.return_value = conn
        result = APIKeyManager.validate_key("lm_badkey")
        assert result is None

    @patch("backend.security.auth._get_conn")
    def test_validate_key_updates_last_used(self, mock_conn_fn):
        row = {
            "id": "key-123",
            "user_id": "user1",
            "role": "admin",
            "name": "",
            "rate_limit": 60,
            "created_at": "2025-01-01T00:00:00",
            "last_used_at": None,
        }
        conn = _make_mock_conn(fetchone_val=row)
        mock_conn_fn.return_value = conn
        APIKeyManager.validate_key("lm_somerawkey")
        # The second execute call should be the UPDATE last_used_at
        assert conn.execute.call_count >= 2
        update_call = conn.execute.call_args_list[1]
        assert "UPDATE" in update_call[0][0]
        assert "last_used_at" in update_call[0][0]

    @patch("backend.security.auth._get_conn")
    def test_validate_key_uses_sha256_hash(self, mock_conn_fn):
        conn = _make_mock_conn(fetchone_val=None)
        mock_conn_fn.return_value = conn
        raw = "lm_testkey123"
        APIKeyManager.validate_key(raw)
        select_call = conn.execute.call_args_list[0]
        params = select_call[0][1]
        expected_hash = hashlib.sha256(raw.encode()).hexdigest()
        assert params[0] == expected_hash

    @patch("backend.security.auth._get_conn")
    def test_revoke_key_sets_revoked_at(self, mock_conn_fn):
        conn = _make_mock_conn()
        mock_conn_fn.return_value = conn
        APIKeyManager.revoke_key("key-abc")
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "revoked_at" in sql
        assert "key-abc" in call_args[0][1]
        conn.commit.assert_called_once()

    @patch("backend.security.auth._get_conn")
    def test_revoke_key_closes_connection(self, mock_conn_fn):
        conn = _make_mock_conn()
        mock_conn_fn.return_value = conn
        APIKeyManager.revoke_key("key-abc")
        conn.close.assert_called_once()

    @patch("backend.security.auth._get_conn")
    def test_list_keys_returns_all(self, mock_conn_fn):
        rows = [
            {"id": "k1", "user_id": "u1", "role": "admin", "name": "key1",
             "rate_limit": 60, "created_at": "t1", "last_used_at": None, "revoked_at": None},
            {"id": "k2", "user_id": "u2", "role": "viewer", "name": "key2",
             "rate_limit": 60, "created_at": "t2", "last_used_at": None, "revoked_at": "t3"},
        ]
        conn = _make_mock_conn(rows=rows)
        mock_conn_fn.return_value = conn
        result = APIKeyManager.list_keys()
        assert len(result) == 2
        assert result[0]["id"] == "k1"
        assert result[1]["revoked_at"] == "t3"

    @patch("backend.security.auth._get_conn")
    def test_list_keys_filters_by_user_id(self, mock_conn_fn):
        rows = [
            {"id": "k1", "user_id": "u1", "role": "admin", "name": "key1",
             "rate_limit": 60, "created_at": "t1", "last_used_at": None, "revoked_at": None},
        ]
        conn = _make_mock_conn(rows=rows)
        mock_conn_fn.return_value = conn
        result = APIKeyManager.list_keys(user_id="u1")
        assert len(result) == 1
        # Verify WHERE user_id = ? was used
        call_sql = conn.execute.call_args_list[0][0][0]
        assert "user_id" in call_sql

    @patch("backend.security.auth._get_conn")
    def test_list_keys_no_user_filter_no_where_clause(self, mock_conn_fn):
        conn = _make_mock_conn(rows=[])
        mock_conn_fn.return_value = conn
        APIKeyManager.list_keys()
        call_sql = conn.execute.call_args_list[0][0][0]
        assert "WHERE" not in call_sql


# ---------------------------------------------------------------------------
# 2. TestSlidingWindowLimiter
# ---------------------------------------------------------------------------

class TestSlidingWindowLimiter:
    """Tests for the in-memory sliding window rate limiter."""

    def test_allows_requests_under_limit(self):
        lim = _SlidingWindowLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert lim.check("key1") is True

    def test_blocks_requests_over_limit(self):
        lim = _SlidingWindowLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            lim.check("key1")
        assert lim.check("key1") is False

    def test_blocks_exactly_at_limit(self):
        lim = _SlidingWindowLimiter(max_requests=1, window_seconds=60)
        assert lim.check("key1") is True
        assert lim.check("key1") is False

    def test_window_slides_old_requests_expire(self):
        lim = _SlidingWindowLimiter(max_requests=2, window_seconds=1)
        assert lim.check("key1") is True
        assert lim.check("key1") is True
        assert lim.check("key1") is False
        # Simulate time passing by back-dating the stored hits
        lim._hits["key1"] = [time.monotonic() - 2.0]
        assert lim.check("key1") is True

    def test_different_keys_tracked_independently(self):
        lim = _SlidingWindowLimiter(max_requests=2, window_seconds=60)
        assert lim.check("key1") is True
        assert lim.check("key1") is True
        assert lim.check("key1") is False
        # key2 should still be allowed
        assert lim.check("key2") is True
        assert lim.check("key2") is True

    def test_configurable_limit(self):
        lim = _SlidingWindowLimiter(max_requests=10, window_seconds=60)
        for _ in range(10):
            assert lim.check("k") is True
        assert lim.check("k") is False

    def test_configurable_window_seconds(self):
        lim = _SlidingWindowLimiter(max_requests=5, window_seconds=120)
        assert lim.window_seconds == 120

    def test_zero_limit_blocks_all(self):
        lim = _SlidingWindowLimiter(max_requests=0, window_seconds=60)
        assert lim.check("key1") is False

    def test_prune_only_expired_entries(self):
        lim = _SlidingWindowLimiter(max_requests=5, window_seconds=10)
        now = time.monotonic()
        # Insert some old and some recent hits
        lim._hits["k"] = [now - 20, now - 15, now - 1, now]
        result = lim.check("k")
        assert result is True
        # Old entries should be pruned; only recent ones (now-1, now) + new one
        assert len(lim._hits["k"]) == 3

    def test_empty_state_allows_first_request(self):
        lim = _SlidingWindowLimiter(max_requests=100, window_seconds=60)
        assert lim._hits == {}
        assert lim.check("brand_new_key") is True


# ---------------------------------------------------------------------------
# 3. TestAuthenticateRequest
# ---------------------------------------------------------------------------

class TestAuthenticateRequest:
    """Tests for authenticate_request (sync middleware helper)."""

    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_missing_header_returns_401(self, _):
        req = _make_request(api_key=None)
        with pytest.raises(HTTPException) as exc_info:
            authenticate_request(req)
        assert exc_info.value.status_code == 401
        assert "Missing" in exc_info.value.detail

    @patch("backend.security.auth.APIKeyManager.validate_key", return_value=None)
    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_invalid_key_returns_401(self, _, __):
        req = _make_request(api_key="lm_invalid")
        with pytest.raises(HTTPException) as exc_info:
            authenticate_request(req)
        assert exc_info.value.status_code == 401
        assert "Invalid" in exc_info.value.detail

    @patch("backend.security.auth._limiter")
    @patch("backend.security.auth.APIKeyManager.validate_key")
    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_valid_key_returns_user_context(self, _, mock_validate, mock_limiter):
        mock_validate.return_value = {
            "key_id": "k1", "user_id": "u1", "role": "operator",
            "description": "test", "rate_limit": 60,
            "created_at": "t", "last_used_at": None,
        }
        mock_limiter.check.return_value = True
        req = _make_request(api_key="lm_validkey")
        result = authenticate_request(req)
        assert result["user_id"] == "u1"
        assert result["role"] == "operator"
        assert result["key_id"] == "k1"

    @patch("backend.security.auth._limiter")
    @patch("backend.security.auth.APIKeyManager.validate_key")
    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_rate_limited_key_returns_429(self, _, mock_validate, mock_limiter):
        mock_validate.return_value = {
            "key_id": "k1", "user_id": "u1", "role": "operator",
            "description": "", "rate_limit": 60,
            "created_at": "t", "last_used_at": None,
        }
        mock_limiter.check.return_value = False
        req = _make_request(api_key="lm_validkey")
        with pytest.raises(RateLimitExceeded) as exc_info:
            authenticate_request(req)
        assert exc_info.value.status_code == 429

    @patch("backend.security.auth._has_any_keys", return_value=False)
    def test_bootstrap_mode_returns_admin(self, _):
        req = _make_request(api_key=None)
        result = authenticate_request(req)
        assert result["role"] == "admin"
        assert result["user_id"] == "bootstrap"

    @patch("backend.security.auth._has_any_keys", return_value=False)
    def test_bootstrap_mode_ignores_any_key(self, _):
        """When no keys exist, even a provided key is ignored — bootstrap wins."""
        req = _make_request(api_key="lm_whatever")
        result = authenticate_request(req)
        assert result == _BOOTSTRAP_USER

    @patch("backend.security.auth.APIKeyManager.validate_key", return_value=None)
    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_revoked_key_returns_401(self, _, __):
        """A revoked key returns None from validate_key, which yields 401."""
        req = _make_request(api_key="lm_revokedkey")
        with pytest.raises(HTTPException) as exc_info:
            authenticate_request(req)
        assert exc_info.value.status_code == 401

    @patch("backend.security.auth.logger")
    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_logs_missing_header_failure(self, _, mock_logger):
        req = _make_request(api_key=None)
        with pytest.raises(HTTPException):
            authenticate_request(req)
        mock_logger.warning.assert_called()
        log_msg = mock_logger.warning.call_args[0][0]
        assert "missing" in log_msg.lower() or "Missing" in log_msg

    @patch("backend.security.auth.logger")
    @patch("backend.security.auth.APIKeyManager.validate_key", return_value=None)
    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_logs_invalid_key_failure(self, _, __, mock_logger):
        req = _make_request(api_key="lm_badkey")
        with pytest.raises(HTTPException):
            authenticate_request(req)
        mock_logger.warning.assert_called()
        log_msg = mock_logger.warning.call_args[0][0]
        assert "invalid" in log_msg.lower() or "Invalid" in log_msg

    @patch("backend.security.auth.logger")
    @patch("backend.security.auth._limiter")
    @patch("backend.security.auth.APIKeyManager.validate_key")
    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_logs_rate_limit_exceeded(self, _, mock_validate, mock_limiter, mock_logger):
        mock_validate.return_value = {
            "key_id": "k1", "user_id": "u1", "role": "operator",
            "description": "", "rate_limit": 60,
            "created_at": "t", "last_used_at": None,
        }
        mock_limiter.check.return_value = False
        req = _make_request(api_key="lm_key")
        with pytest.raises(RateLimitExceeded):
            authenticate_request(req)
        mock_logger.warning.assert_called()
        log_msg = mock_logger.warning.call_args[0][0]
        assert "rate limit" in log_msg.lower() or "Rate limit" in log_msg

    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_reads_lowercase_header(self, _):
        """authenticate_request also checks 'x-api-key' (lowercase)."""
        req = MagicMock()
        headers = _FakeHeaders({"x-api-key": "lm_lowerkey"})
        req.headers = headers
        req.client = MagicMock()
        req.client.host = "1.2.3.4"
        with patch("backend.security.auth.APIKeyManager.validate_key", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                authenticate_request(req)
            assert exc_info.value.status_code == 401

    @patch("backend.security.auth._has_any_keys", return_value=True)
    def test_client_none_does_not_crash(self, _):
        """If request.client is None, should still work (use 'unknown')."""
        req = MagicMock()
        headers = _FakeHeaders()
        req.headers = headers
        req.client = None
        with pytest.raises(HTTPException) as exc_info:
            authenticate_request(req)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 4. TestGetCurrentUser (async counterpart)
# ---------------------------------------------------------------------------

class TestGetCurrentUser:
    """Tests for the async get_current_user FastAPI dependency."""

    @pytest.mark.asyncio
    @patch("backend.security.auth._has_any_keys", return_value=False)
    async def test_bootstrap_mode(self, _):
        req = _make_request()
        result = await get_current_user(req, api_key=None)
        assert result["role"] == "admin"
        assert result["user_id"] == "bootstrap"

    @pytest.mark.asyncio
    @patch("backend.security.auth._has_any_keys", return_value=True)
    async def test_missing_key_raises_401(self, _):
        req = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(req, api_key=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("backend.security.auth.APIKeyManager.validate_key", return_value=None)
    @patch("backend.security.auth._has_any_keys", return_value=True)
    async def test_invalid_key_raises_401(self, _, __):
        req = _make_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(req, api_key="lm_bad")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("backend.security.auth._limiter")
    @patch("backend.security.auth.APIKeyManager.validate_key")
    @patch("backend.security.auth._has_any_keys", return_value=True)
    async def test_valid_key_returns_context(self, _, mock_validate, mock_limiter):
        mock_validate.return_value = {
            "key_id": "k9", "user_id": "alice", "role": "admin",
            "description": "a key", "rate_limit": 60,
            "created_at": "t", "last_used_at": None,
        }
        mock_limiter.check.return_value = True
        req = _make_request()
        result = await get_current_user(req, api_key="lm_goodkey")
        assert result["user_id"] == "alice"
        assert result["role"] == "admin"
        assert result["key_id"] == "k9"

    @pytest.mark.asyncio
    @patch("backend.security.auth._limiter")
    @patch("backend.security.auth.APIKeyManager.validate_key")
    @patch("backend.security.auth._has_any_keys", return_value=True)
    async def test_rate_limited_raises_429(self, _, mock_validate, mock_limiter):
        mock_validate.return_value = {
            "key_id": "k1", "user_id": "u1", "role": "operator",
            "description": "", "rate_limit": 60,
            "created_at": "t", "last_used_at": None,
        }
        mock_limiter.check.return_value = False
        req = _make_request()
        with pytest.raises(RateLimitExceeded):
            await get_current_user(req, api_key="lm_key")


# ---------------------------------------------------------------------------
# 5. TestCheckPermission
# ---------------------------------------------------------------------------

class TestCheckPermission:
    """Tests for check_permission (RBAC middleware check)."""

    def test_admin_can_get_api(self):
        check_permission("admin", "GET", "/api/jobs")  # should not raise

    def test_admin_can_post_api(self):
        check_permission("admin", "POST", "/api/admin/keys")

    def test_admin_can_delete_admin_keys(self):
        check_permission("admin", "DELETE", "/api/admin/keys/abc")

    def test_admin_can_access_any_method(self):
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            check_permission("admin", method, "/api/something")

    def test_operator_can_get_any_api(self):
        check_permission("operator", "GET", "/api/jobs")
        check_permission("operator", "GET", "/api/admin/keys")

    def test_operator_can_post_jobs(self):
        check_permission("operator", "POST", "/api/jobs")

    def test_operator_can_delete_jobs(self):
        check_permission("operator", "DELETE", "/api/jobs/123")

    def test_operator_cannot_delete_admin_keys(self):
        with pytest.raises(HTTPException) as exc_info:
            check_permission("operator", "DELETE", "/api/admin/keys/abc")
        assert exc_info.value.status_code == 403

    def test_operator_cannot_post_admin_keys(self):
        with pytest.raises(HTTPException) as exc_info:
            check_permission("operator", "POST", "/api/admin/keys")
        assert exc_info.value.status_code == 403

    def test_viewer_can_get_api(self):
        check_permission("viewer", "GET", "/api/jobs")

    def test_viewer_cannot_post(self):
        with pytest.raises(HTTPException) as exc_info:
            check_permission("viewer", "POST", "/api/jobs")
        assert exc_info.value.status_code == 403

    def test_viewer_cannot_delete(self):
        with pytest.raises(HTTPException) as exc_info:
            check_permission("viewer", "DELETE", "/api/jobs/123")
        assert exc_info.value.status_code == 403

    def test_viewer_cannot_put(self):
        with pytest.raises(HTTPException) as exc_info:
            check_permission("viewer", "PUT", "/api/memory/1")
        assert exc_info.value.status_code == 403

    def test_unknown_role_denied(self):
        with pytest.raises(HTTPException) as exc_info:
            check_permission("hacker", "GET", "/api/jobs")
        assert exc_info.value.status_code == 403

    def test_empty_role_denied(self):
        with pytest.raises(HTTPException) as exc_info:
            check_permission("", "GET", "/api/jobs")
        assert exc_info.value.status_code == 403

    def test_non_api_path_denied_for_all(self):
        """Paths not starting with /api/ are not in any permission set."""
        for role in ("admin", "operator", "viewer"):
            # admin has ("*", "/api/") so /other/ is not covered
            if role == "admin":
                with pytest.raises(HTTPException):
                    check_permission("admin", "GET", "/other/path")
            else:
                with pytest.raises(HTTPException):
                    check_permission(role, "GET", "/other/path")


# ---------------------------------------------------------------------------
# 6. TestIsAllowed (internal helper)
# ---------------------------------------------------------------------------

class TestIsAllowed:
    """Tests for the _is_allowed internal function."""

    def test_admin_wildcard_method(self):
        assert _is_allowed("admin", "PATCH", "/api/foo") is True

    def test_operator_allowed_post_chat(self):
        assert _is_allowed("operator", "POST", "/api/chat") is True

    def test_operator_disallowed_post_admin(self):
        assert _is_allowed("operator", "POST", "/api/admin/keys") is False

    def test_viewer_get_allowed(self):
        assert _is_allowed("viewer", "GET", "/api/anything") is True

    def test_viewer_post_disallowed(self):
        assert _is_allowed("viewer", "POST", "/api/anything") is False

    def test_unknown_role_returns_false(self):
        assert _is_allowed("nobody", "GET", "/api/stuff") is False

    def test_prefix_collision_disallowed(self):
        """Security: Verify that /api/chat_secrets does not match /api/chat."""
        # operator has /api/chat but NOT /api/chat_secrets
        assert _is_allowed("operator", "POST", "/api/chat/stream") is True
        assert _is_allowed("operator", "POST", "/api/chat_secrets") is False


# ---------------------------------------------------------------------------
# 7. TestRequireRole
# ---------------------------------------------------------------------------

class TestRequireRole:
    """Tests for the require_role FastAPI dependency factory."""

    @pytest.mark.asyncio
    async def test_allows_matching_role(self):
        dep = require_role("admin", "operator")
        user = {"user_id": "u1", "role": "operator", "key_id": "k1"}
        with patch("backend.security.rbac.get_current_user", new_callable=AsyncMock, return_value=user):
            result = await dep(user=user)
        assert result["role"] == "operator"

    @pytest.mark.asyncio
    async def test_denies_non_matching_role(self):
        dep = require_role("admin")
        user = {"user_id": "u1", "role": "viewer", "key_id": "k1"}
        with pytest.raises(HTTPException) as exc_info:
            await dep(user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_admin_allows_admin(self):
        user = {"user_id": "u1", "role": "admin", "key_id": "k1"}
        with patch("backend.security.rbac.get_current_user", new_callable=AsyncMock, return_value=user):
            result = await require_admin(user=user)
        assert result["role"] == "admin"

    @pytest.mark.asyncio
    async def test_require_admin_denies_operator(self):
        user = {"user_id": "u1", "role": "operator", "key_id": "k1"}
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_admin_denies_viewer(self):
        user = {"user_id": "u1", "role": "viewer", "key_id": "k1"}
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_role_multiple_roles(self):
        dep = require_role("operator", "viewer")
        user = {"user_id": "u1", "role": "viewer", "key_id": "k1"}
        result = await dep(user=user)
        assert result["role"] == "viewer"

    @pytest.mark.asyncio
    async def test_require_role_error_message_contains_roles(self):
        dep = require_role("admin", "operator")
        user = {"user_id": "u1", "role": "viewer", "key_id": "k1"}
        with pytest.raises(HTTPException) as exc_info:
            await dep(user=user)
        assert "admin" in exc_info.value.detail
        assert "operator" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 8. TestRateLimitExceeded
# ---------------------------------------------------------------------------

class TestRateLimitExceeded:
    """Tests for the RateLimitExceeded exception."""

    def test_default_status_code(self):
        exc = RateLimitExceeded()
        assert exc.status_code == 429

    def test_default_detail(self):
        exc = RateLimitExceeded()
        assert "Rate limit exceeded" in exc.detail

    def test_custom_detail(self):
        exc = RateLimitExceeded(detail="Too many requests, slow down")
        assert exc.detail == "Too many requests, slow down"

    def test_is_http_exception(self):
        exc = RateLimitExceeded()
        assert isinstance(exc, HTTPException)


# ---------------------------------------------------------------------------
# 9. TestHashKey
# ---------------------------------------------------------------------------

class TestHashKey:
    """Tests for the _hash_key helper."""

    def test_returns_sha256_hex(self):
        result = _hash_key("lm_testkey")
        expected = hashlib.sha256(b"lm_testkey").hexdigest()
        assert result == expected

    def test_consistent_output(self):
        assert _hash_key("abc") == _hash_key("abc")

    def test_different_input_different_hash(self):
        assert _hash_key("key1") != _hash_key("key2")

    def test_returns_64_char_hex_string(self):
        result = _hash_key("anything")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# 10. TestRolePermissionsStructure
# ---------------------------------------------------------------------------

class TestRolePermissionsStructure:
    """Verify the ROLE_PERMISSIONS dict structure is correct."""

    def test_all_roles_present(self):
        assert "admin" in ROLE_PERMISSIONS
        assert "operator" in ROLE_PERMISSIONS
        assert "viewer" in ROLE_PERMISSIONS

    def test_admin_has_wildcard(self):
        perms = ROLE_PERMISSIONS["admin"]
        assert ("*", "/api/") in perms

    def test_viewer_is_read_only(self):
        perms = ROLE_PERMISSIONS["viewer"]
        assert len(perms) == 1
        assert perms[0] == ("GET", "/api/")

    def test_operator_has_write_access_to_jobs(self):
        perms = ROLE_PERMISSIONS["operator"]
        methods = {m for m, p in perms if p == "/api/jobs"}
        assert "POST" in methods
        assert "PUT" in methods
        assert "DELETE" in methods

    def test_operator_no_admin_write_access(self):
        perms = ROLE_PERMISSIONS["operator"]
        admin_writes = [(m, p) for m, p in perms if "/api/admin" in p and m != "GET"]
        assert admin_writes == []


# ---------------------------------------------------------------------------
# 11. TestAdminRoutes
# ---------------------------------------------------------------------------

class TestAdminRoutes:
    """Tests for admin route handlers (mocking DB and auth dependencies)."""

    @pytest.mark.asyncio
    @patch("backend.routes.admin.APIKeyManager.list_keys")
    async def test_list_keys_returns_keys(self, mock_list):
        mock_list.return_value = [
            {"id": "k1", "user_id": "u1", "role": "admin", "description": "test",
             "rate_limit": 60, "created_at": "t", "last_used_at": None, "revoked_at": None},
        ]
        from backend.routes.admin import list_keys
        result = await list_keys()
        assert "keys" in result
        assert len(result["keys"]) == 1
        assert result["keys"][0]["id"] == "k1"

    @pytest.mark.asyncio
    @patch("backend.routes.admin.APIKeyManager.list_keys")
    async def test_list_keys_returns_empty_list(self, mock_list):
        mock_list.return_value = []
        from backend.routes.admin import list_keys
        result = await list_keys()
        assert result["keys"] == []

    @pytest.mark.asyncio
    @patch("backend.routes.admin.APIKeyManager.create_key", return_value="lm_newkey123")
    @patch("backend.routes.admin._get_conn")
    async def test_create_key_returns_raw_key(self, mock_conn_fn, mock_create):
        # Mock: user exists
        conn = _make_mock_conn(fetchone_val={"id": "user1"})
        mock_conn_fn.return_value = conn
        from backend.routes.admin import create_key, CreateKeyRequest
        body = CreateKeyRequest(user_id="user1", role="operator", description="test key")
        result = await create_key(body)
        assert result.raw_key == "lm_newkey123"
        assert "securely" in result.message.lower() or "Store" in result.message

    @pytest.mark.asyncio
    async def test_create_key_invalid_role_returns_400(self):
        from backend.routes.admin import create_key, CreateKeyRequest
        body = CreateKeyRequest(user_id="user1", role="superadmin", description="bad")
        with pytest.raises(HTTPException) as exc_info:
            await create_key(body)
        assert exc_info.value.status_code == 400
        assert "Invalid role" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("backend.routes.admin._get_conn")
    async def test_create_key_nonexistent_user_returns_404(self, mock_conn_fn):
        conn = _make_mock_conn(fetchone_val=None)
        mock_conn_fn.return_value = conn
        from backend.routes.admin import create_key, CreateKeyRequest
        body = CreateKeyRequest(user_id="ghost", role="viewer", description="")
        with pytest.raises(HTTPException) as exc_info:
            await create_key(body)
        assert exc_info.value.status_code == 404
        assert "ghost" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("backend.routes.admin.APIKeyManager.revoke_key")
    @patch("backend.routes.admin._get_conn")
    async def test_revoke_key_success(self, mock_conn_fn, mock_revoke):
        conn = _make_mock_conn(fetchone_val={"id": "key-abc"})
        mock_conn_fn.return_value = conn
        from backend.routes.admin import revoke_key
        result = await revoke_key("key-abc")
        assert result["status"] == "revoked"
        assert result["key_id"] == "key-abc"
        mock_revoke.assert_called_once_with("key-abc")

    @pytest.mark.asyncio
    @patch("backend.routes.admin._get_conn")
    async def test_revoke_nonexistent_key_returns_404(self, mock_conn_fn):
        conn = _make_mock_conn(fetchone_val=None)
        mock_conn_fn.return_value = conn
        from backend.routes.admin import revoke_key
        with pytest.raises(HTTPException) as exc_info:
            await revoke_key("no-such-key")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("backend.routes.admin.get_audit_logger")
    async def test_query_audit_log_returns_entries(self, mock_get_al):
        mock_al = MagicMock()
        mock_al.query.return_value = [
            {"id": "a1", "job_id": "j1", "node_id": "n1",
             "action": "start", "detail": "started", "actor": "system",
             "timestamp": "2025-01-01T00:00:00"},
        ]
        mock_get_al.return_value = mock_al
        from backend.routes.admin import query_audit_log
        result = await query_audit_log(action=None, actor=None, job_id=None, since=None, limit=100)
        assert result["count"] == 1
        assert result["entries"][0]["action"] == "start"

    @pytest.mark.asyncio
    @patch("backend.routes.admin.get_audit_logger")
    async def test_query_audit_log_empty(self, mock_get_al):
        mock_al = MagicMock()
        mock_al.query.return_value = []
        mock_get_al.return_value = mock_al
        from backend.routes.admin import query_audit_log
        result = await query_audit_log(action=None, actor=None, job_id=None, since=None, limit=100)
        assert result["count"] == 0
        assert result["entries"] == []

    @pytest.mark.asyncio
    @patch("backend.routes.admin.get_audit_logger")
    async def test_query_audit_log_with_action_filter(self, mock_get_al):
        mock_al = MagicMock()
        mock_al.query.return_value = []
        mock_get_al.return_value = mock_al
        from backend.routes.admin import query_audit_log
        await query_audit_log(action="cancel", actor=None, job_id=None, since=None, limit=50)
        mock_al.query.assert_called_once_with(
            action="cancel", actor=None, since=None, job_id=None, limit=50,
        )

    @pytest.mark.asyncio
    @patch("backend.routes.admin.get_audit_logger")
    async def test_query_audit_log_with_since_filter(self, mock_get_al):
        mock_al = MagicMock()
        mock_al.query.return_value = []
        mock_get_al.return_value = mock_al
        from backend.routes.admin import query_audit_log
        await query_audit_log(action=None, actor=None, job_id=None, since="2025-06-01T00:00:00", limit=100)
        mock_al.query.assert_called_once_with(
            action=None, actor=None, since="2025-06-01T00:00:00", job_id=None, limit=100,
        )

    @pytest.mark.asyncio
    @patch("backend.routes.admin.get_audit_logger")
    async def test_query_audit_log_with_all_filters(self, mock_get_al):
        mock_al = MagicMock()
        mock_al.query.return_value = []
        mock_get_al.return_value = mock_al
        from backend.routes.admin import query_audit_log
        await query_audit_log(action="complete", actor="bot", job_id="j99", since="2025-06-01", limit=10)
        mock_al.query.assert_called_once_with(
            action="complete", actor="bot", since="2025-06-01", job_id="j99", limit=10,
        )


# ---------------------------------------------------------------------------
# 12. TestBootstrapUser
# ---------------------------------------------------------------------------

class TestBootstrapUser:
    """Tests for the _BOOTSTRAP_USER constant."""

    def test_bootstrap_user_is_admin(self):
        assert _BOOTSTRAP_USER["role"] == "admin"

    def test_bootstrap_user_has_user_id(self):
        assert _BOOTSTRAP_USER["user_id"] == "bootstrap"

    def test_bootstrap_user_has_key_id(self):
        assert _BOOTSTRAP_USER["key_id"] == "bootstrap"


# ---------------------------------------------------------------------------
# 13. TestAdminRouteValidRoles
# ---------------------------------------------------------------------------

class TestAdminCreateKeyValidation:
    """Edge-case tests for the create_key route validation."""

    @pytest.mark.asyncio
    @patch("backend.routes.admin.APIKeyManager.create_key", return_value="lm_x")
    @patch("backend.routes.admin._get_conn")
    async def test_create_key_admin_role_accepted(self, mock_conn_fn, mock_create):
        conn = _make_mock_conn(fetchone_val={"id": "u1"})
        mock_conn_fn.return_value = conn
        from backend.routes.admin import create_key, CreateKeyRequest
        body = CreateKeyRequest(user_id="u1", role="admin", description="")
        result = await create_key(body)
        assert result.raw_key == "lm_x"

    @pytest.mark.asyncio
    @patch("backend.routes.admin.APIKeyManager.create_key", return_value="lm_y")
    @patch("backend.routes.admin._get_conn")
    async def test_create_key_viewer_role_accepted(self, mock_conn_fn, mock_create):
        conn = _make_mock_conn(fetchone_val={"id": "u1"})
        mock_conn_fn.return_value = conn
        from backend.routes.admin import create_key, CreateKeyRequest
        body = CreateKeyRequest(user_id="u1", role="viewer", description="readonly")
        result = await create_key(body)
        assert result.raw_key == "lm_y"

    @pytest.mark.asyncio
    async def test_create_key_empty_role_rejected(self):
        from backend.routes.admin import create_key, CreateKeyRequest
        body = CreateKeyRequest(user_id="u1", role="", description="")
        with pytest.raises(HTTPException) as exc_info:
            await create_key(body)
        assert exc_info.value.status_code == 400
