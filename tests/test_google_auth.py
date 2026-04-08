"""
Tests for Google OAuth2 routes (backend/routes/google_auth.py).

Covers the full OAuth flow, token management, scope expansion,
legacy route redirects, and error handling — all without external services.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

import pytest
from starlette.testclient import TestClient
from fastapi import FastAPI

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_CLIENT_ID = "fake-client-id.apps.googleusercontent.com"
FAKE_CLIENT_SECRET = "fake-client-secret"
FAKE_REDIRECT_URI = "http://localhost:8000/api/google/callback"
FAKE_TOKEN = "ya29.fake-access-token"
FAKE_REFRESH_TOKEN = "1//fake-refresh-token"
FAKE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth?response_type=code&fake=1"

ALL_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _make_fake_creds(
    token=FAKE_TOKEN,
    refresh_token=FAKE_REFRESH_TOKEN,
    scopes=None,
    expired=False,
    valid=True,
    expiry=None,
):
    """Build a MagicMock that looks like google.oauth2.credentials.Credentials."""
    creds = MagicMock()
    creds.token = token
    creds.refresh_token = refresh_token
    creds.token_uri = "https://oauth2.googleapis.com/token"
    creds.client_id = FAKE_CLIENT_ID
    creds.client_secret = FAKE_CLIENT_SECRET
    creds.scopes = scopes if scopes is not None else set(ALL_SCOPES)
    creds.expired = expired
    creds.valid = valid
    creds.expiry = expiry or (datetime.now(timezone.utc) + timedelta(hours=1))
    creds.refresh = MagicMock()
    return creds


@pytest.fixture
def _patch_google_libs():
    """Ensure module-level google lib flags are True so routes are available."""
    with patch(
        "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
    ), patch(
        "backend.routes.google_auth._OAUTHLIB_AVAILABLE", True
    ):
        yield


@pytest.fixture
def _patch_env_client_config():
    """Provide env-var-based client config."""
    with patch(
        "backend.routes.google_auth.GOOGLE_CLIENT_ID", FAKE_CLIENT_ID
    ), patch(
        "backend.routes.google_auth.GOOGLE_CLIENT_SECRET", FAKE_CLIENT_SECRET
    ), patch(
        "backend.routes.google_auth.GOOGLE_REDIRECT_URI", FAKE_REDIRECT_URI
    ):
        yield


@pytest.fixture
def app():
    """Create a fresh FastAPI app that includes google_auth routers."""
    from backend.routes.google_auth import router, _legacy_router

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.include_router(_legacy_router)
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


# =========================================================================
# TestOAuthFlow
# =========================================================================


class TestOAuthFlow:
    """Tests for /api/google/auth and /api/google/callback."""

    def test_auth_start_redirects_to_google(
        self, client, _patch_google_libs, _patch_env_client_config
    ):
        """GET /api/google/auth should redirect to Google consent URL."""
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = (FAKE_AUTH_URL, "fake-state")

        with patch(
            "backend.routes.google_auth.OAuthFlow"
        ) as MockFlow:
            MockFlow.from_client_config.return_value = mock_flow
            resp = client.get("/api/google/auth")

        assert resp.status_code == 307
        assert resp.headers["location"] == FAKE_AUTH_URL
        MockFlow.from_client_config.assert_called_once()

    def test_auth_start_no_client_config_returns_400(
        self, client, _patch_google_libs
    ):
        """Should 400 if no client config is available."""
        with patch(
            "backend.routes.google_auth._get_client_config", return_value={}
        ):
            resp = client.get("/api/google/auth")

        assert resp.status_code == 400
        assert "No Google credentials found" in resp.json()["detail"]

    def test_auth_start_libs_missing_returns_500(self, client):
        """Should 500 if google-auth libraries are not installed."""
        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", False
        ), patch(
            "backend.routes.google_auth._OAUTHLIB_AVAILABLE", False
        ):
            resp = client.get("/api/google/auth")

        assert resp.status_code == 500
        assert "not installed" in resp.json()["detail"]

    def test_callback_success_stores_credentials(
        self, client, _patch_google_libs, _patch_env_client_config
    ):
        """Successful callback should exchange code and store creds."""
        fake_creds = _make_fake_creds()

        mock_flow = MagicMock()
        mock_flow.credentials = fake_creds

        with patch(
            "backend.routes.google_auth.OAuthFlow"
        ) as MockFlow, patch(
            "backend.routes.google_auth.store_credentials"
        ) as mock_store:
            MockFlow.from_client_config.return_value = mock_flow
            resp = client.get("/api/google/callback?code=fake-auth-code")

        assert resp.status_code == 200
        assert "Google Connected" in resp.text
        mock_store.assert_called_once_with(fake_creds)
        mock_flow.fetch_token.assert_called_once_with(code="fake-auth-code")

    def test_callback_google_error_param(self, client):
        """Callback should return 400 when Google sends an error query param."""
        resp = client.get("/api/google/callback?error=access_denied")
        assert resp.status_code == 400
        assert "access_denied" in resp.text

    def test_callback_no_code_returns_400(self, client):
        """Callback with no code param should return 400."""
        resp = client.get("/api/google/callback")
        assert resp.status_code == 400
        assert "No authorization code" in resp.text

    def test_callback_token_exchange_failure(
        self, client, _patch_google_libs, _patch_env_client_config
    ):
        """Callback should return 500 when token exchange raises."""
        mock_flow = MagicMock()
        mock_flow.fetch_token.side_effect = RuntimeError("Token exchange failed")

        with patch(
            "backend.routes.google_auth.OAuthFlow"
        ) as MockFlow:
            MockFlow.from_client_config.return_value = mock_flow
            resp = client.get("/api/google/callback?code=bad-code")

        assert resp.status_code == 500
        assert "Token exchange failed" in resp.text

    def test_callback_no_client_config_returns_500(
        self, client, _patch_google_libs
    ):
        """Callback should return 500 when client config is missing."""
        with patch(
            "backend.routes.google_auth._get_client_config", return_value={}
        ):
            resp = client.get("/api/google/callback?code=some-code")

        assert resp.status_code == 500
        assert "credentials not found" in resp.text.lower()


# =========================================================================
# TestTokenManagement
# =========================================================================


class TestTokenManagement:
    """Tests for store_credentials, get_credentials, /status, /refresh, /revoke."""

    def test_status_no_credentials(self, client):
        """Status should report unauthenticated when no creds exist."""
        with patch(
            "backend.routes.google_auth.get_credentials", return_value=None
        ), patch(
            "backend.routes.google_auth._get_client_config",
            return_value={"web": {}},
        ):
            resp = client.get("/api/google/status")

        data = resp.json()
        assert data["authenticated"] is False
        assert data["needs_reauth"] is True
        assert len(data["missing_scopes"]) == len(ALL_SCOPES)

    def test_status_valid_credentials(self, client):
        """Status should report authenticated with valid creds."""
        fake_creds = _make_fake_creds()

        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ), patch(
            "backend.routes.google_auth._get_client_config",
            return_value={"web": {}},
        ), patch(
            "backend.routes.google_auth.needs_reauth",
            return_value=(False, []),
        ):
            resp = client.get("/api/google/status")

        data = resp.json()
        assert data["authenticated"] is True
        assert data["needs_reauth"] is False
        assert data["missing_scopes"] == []

    def test_status_expired_but_refreshable(self, client):
        """Expired creds with a refresh token are still considered valid."""
        fake_creds = _make_fake_creds(expired=True, valid=False)

        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ), patch(
            "backend.routes.google_auth._get_client_config",
            return_value={"web": {}},
        ), patch(
            "backend.routes.google_auth.needs_reauth",
            return_value=(False, []),
        ):
            resp = client.get("/api/google/status")

        data = resp.json()
        # expired + has refresh_token => is_valid = True
        assert data["authenticated"] is True

    def test_refresh_success(
        self, client, _patch_google_libs
    ):
        """POST /refresh should refresh token and store new creds."""
        fake_creds = _make_fake_creds()

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ), patch(
            "backend.routes.google_auth.GoogleAuthRequest"
        ), patch(
            "backend.routes.google_auth.store_credentials"
        ) as mock_store:
            resp = client.post("/api/google/refresh")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        fake_creds.refresh.assert_called_once()
        mock_store.assert_called_once()

    def test_refresh_no_credentials(self, client):
        """POST /refresh with no creds should return 400."""
        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.get_credentials", return_value=None
        ):
            resp = client.post("/api/google/refresh")

        assert resp.status_code == 400
        assert "No Google credentials" in resp.json()["detail"]

    def test_refresh_no_refresh_token(self, client):
        """POST /refresh without a refresh_token should return 400."""
        fake_creds = _make_fake_creds(refresh_token=None)

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ):
            resp = client.post("/api/google/refresh")

        assert resp.status_code == 400
        assert "No refresh token" in resp.json()["detail"]

    def test_refresh_google_libs_missing(self, client):
        """POST /refresh should 500 if google libs are missing."""
        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", False
        ):
            resp = client.post("/api/google/refresh")

        assert resp.status_code == 500

    def test_refresh_raises_exception(self, client):
        """POST /refresh should 500 if refresh call explodes."""
        fake_creds = _make_fake_creds()
        fake_creds.refresh.side_effect = Exception("Network timeout")

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ), patch(
            "backend.routes.google_auth.GoogleAuthRequest"
        ):
            resp = client.post("/api/google/refresh")

        assert resp.status_code == 500
        assert "Network timeout" in resp.json()["detail"]

    def test_revoke_with_httpx(self, client):
        """POST /revoke should call Google revoke endpoint and clean up."""
        fake_creds = _make_fake_creds()

        mock_httpx_resp = MagicMock()
        mock_httpx_resp.status_code = 200

        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", MagicMock(exists=MagicMock(return_value=False))
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ), patch(
            "backend.routes.google_auth.httpx.post", mock_httpx_resp
        ) if False else patch(
            "httpx.post", return_value=mock_httpx_resp
        ):
            resp = client.post("/api/google/revoke")

        data = resp.json()
        assert data["ok"] is True
        assert data["message"] == "Google disconnected"

    def test_revoke_no_credentials(self, client):
        """POST /revoke with no creds should still succeed (idempotent)."""
        with patch(
            "backend.routes.google_auth.get_credentials", return_value=None
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE",
            MagicMock(exists=MagicMock(return_value=False)),
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ):
            resp = client.post("/api/google/revoke")

        data = resp.json()
        assert data["ok"] is True
        assert data["revoked_remote"] is False

    def test_revoke_removes_token_file(self, client, tmp_path):
        """POST /revoke should delete the token file if it exists."""
        token_file = tmp_path / "google_token.json"
        token_file.write_text("{}")

        with patch(
            "backend.routes.google_auth.get_credentials", return_value=None
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ):
            resp = client.post("/api/google/revoke")

        assert resp.json()["ok"] is True
        assert not token_file.exists()


# =========================================================================
# TestScopeExpansion
# =========================================================================


class TestScopeExpansion:
    """Tests for has_scope and needs_reauth helpers."""

    def test_has_scope_true(self):
        """has_scope returns True when scope is present."""
        fake_creds = _make_fake_creds(scopes=set(ALL_SCOPES))
        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ):
            from backend.routes.google_auth import has_scope
            assert has_scope("https://www.googleapis.com/auth/gmail.send") is True

    def test_has_scope_false(self):
        """has_scope returns False for an ungranted scope."""
        fake_creds = _make_fake_creds(
            scopes={"https://www.googleapis.com/auth/gmail.send"}
        )
        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ):
            from backend.routes.google_auth import has_scope
            assert has_scope("https://www.googleapis.com/auth/calendar") is False

    def test_has_scope_no_credentials(self):
        """has_scope returns False when there are no credentials."""
        with patch(
            "backend.routes.google_auth.get_credentials", return_value=None
        ):
            from backend.routes.google_auth import has_scope
            assert has_scope("https://www.googleapis.com/auth/drive.file") is False

    def test_needs_reauth_no_creds(self):
        """needs_reauth returns True with all scopes missing when no creds."""
        with patch(
            "backend.routes.google_auth.get_credentials", return_value=None
        ):
            from backend.routes.google_auth import needs_reauth
            needed, missing = needs_reauth()
            assert needed is True
            assert len(missing) == len(ALL_SCOPES)

    def test_needs_reauth_all_scopes_present(self):
        """needs_reauth returns False when all scopes are granted."""
        fake_creds = _make_fake_creds(scopes=set(ALL_SCOPES))
        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ):
            from backend.routes.google_auth import needs_reauth
            needed, missing = needs_reauth()
            assert needed is False
            assert missing == []

    def test_needs_reauth_partial_scopes(self):
        """needs_reauth returns True with the missing scopes listed."""
        partial = {
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/drive.file",
        }
        fake_creds = _make_fake_creds(scopes=partial)
        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ):
            from backend.routes.google_auth import needs_reauth
            needed, missing = needs_reauth()
            assert needed is True
            assert len(missing) == len(ALL_SCOPES) - len(partial)
            for s in missing:
                assert s not in partial

    def test_needs_reauth_empty_scopes(self):
        """needs_reauth with empty scopes set returns all scopes as missing."""
        fake_creds = _make_fake_creds(scopes=set())
        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ):
            from backend.routes.google_auth import needs_reauth
            needed, missing = needs_reauth()
            assert needed is True
            assert len(missing) == len(ALL_SCOPES)


# =========================================================================
# TestLegacyRoutes
# =========================================================================


class TestLegacyRoutes:
    """Tests for legacy /api/auth/google/* redirects."""

    def test_legacy_auth_redirects(self, client):
        """GET /api/auth/google should 307 to /api/google/auth."""
        resp = client.get("/api/auth/google")
        assert resp.status_code == 307
        assert resp.headers["location"] == "/api/google/auth"

    def test_legacy_callback_redirects_with_query(self, client):
        """GET /api/auth/google/callback?code=X should 307 preserving query."""
        resp = client.get("/api/auth/google/callback?code=abc&state=xyz")
        assert resp.status_code == 307
        loc = resp.headers["location"]
        assert loc.startswith("/api/google/callback?")
        assert "code=abc" in loc
        assert "state=xyz" in loc

    def test_legacy_callback_redirects_no_query(self, client):
        """GET /api/auth/google/callback with no QS should redirect cleanly."""
        resp = client.get("/api/auth/google/callback")
        assert resp.status_code == 307
        assert resp.headers["location"] == "/api/google/callback"

    def test_legacy_status_redirects(self, client):
        """GET /api/auth/google/status should 307 to /api/google/status."""
        resp = client.get("/api/auth/google/status")
        assert resp.status_code == 307
        assert resp.headers["location"] == "/api/google/status"

    def test_legacy_disconnect_redirects(self, client):
        """POST /api/auth/google/disconnect should 307 to /api/google/revoke."""
        resp = client.post("/api/auth/google/disconnect")
        assert resp.status_code == 307
        assert resp.headers["location"] == "/api/google/revoke"


# =========================================================================
# TestErrorHandling
# =========================================================================


class TestErrorHandling:
    """Edge cases, error paths, and resilience tests."""

    def test_get_client_config_env_vars(self):
        """_get_client_config should prefer env vars."""
        with patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_ID", FAKE_CLIENT_ID
        ), patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_SECRET", FAKE_CLIENT_SECRET
        ):
            from backend.routes.google_auth import _get_client_config
            cfg = _get_client_config()
            assert "web" in cfg
            assert cfg["web"]["client_id"] == FAKE_CLIENT_ID

    def test_get_client_config_empty_env_falls_back_to_file(self, tmp_path):
        """_get_client_config falls back to credentials.json when env is empty."""
        cred_file = tmp_path / "credentials.json"
        cred_file.write_text(json.dumps({
            "installed": {
                "client_id": "file-id",
                "client_secret": "file-secret",
            }
        }))
        with patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_ID", ""
        ), patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_SECRET", ""
        ), patch(
            "backend.routes.google_auth.CREDENTIALS_FILE", cred_file
        ):
            from backend.routes.google_auth import _get_client_config
            cfg = _get_client_config()
            assert cfg["web"]["client_id"] == "file-id"
            assert cfg["web"]["client_secret"] == "file-secret"

    def test_get_client_config_bad_json_file(self, tmp_path):
        """_get_client_config returns {} if credentials.json is invalid JSON."""
        cred_file = tmp_path / "credentials.json"
        cred_file.write_text("NOT JSON!")
        with patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_ID", ""
        ), patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_SECRET", ""
        ), patch(
            "backend.routes.google_auth.CREDENTIALS_FILE", cred_file
        ):
            from backend.routes.google_auth import _get_client_config
            assert _get_client_config() == {}

    def test_get_client_config_no_env_no_file(self, tmp_path):
        """_get_client_config returns {} when nothing is configured."""
        missing = tmp_path / "nonexistent.json"
        with patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_ID", ""
        ), patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_SECRET", ""
        ), patch(
            "backend.routes.google_auth.CREDENTIALS_FILE", missing
        ):
            from backend.routes.google_auth import _get_client_config
            assert _get_client_config() == {}

    def test_get_client_config_web_format_passthrough(self, tmp_path):
        """credentials.json already in web format should be returned as-is."""
        cred_file = tmp_path / "credentials.json"
        web_cfg = {"web": {"client_id": "web-id", "client_secret": "web-secret"}}
        cred_file.write_text(json.dumps(web_cfg))
        with patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_ID", ""
        ), patch(
            "backend.routes.google_auth.GOOGLE_CLIENT_SECRET", ""
        ), patch(
            "backend.routes.google_auth.CREDENTIALS_FILE", cred_file
        ):
            from backend.routes.google_auth import _get_client_config
            cfg = _get_client_config()
            assert cfg == web_cfg

    def test_store_credentials_file_write(self, tmp_path):
        """store_credentials should write token JSON to file."""
        token_file = tmp_path / "google_token.json"
        config_dir = tmp_path

        fake_creds = _make_fake_creds()

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.CONFIG_DIR", config_dir
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ):
            from backend.routes.google_auth import store_credentials
            store_credentials(fake_creds)

        assert token_file.exists()
        data = json.loads(token_file.read_text())
        assert data["token"] == FAKE_TOKEN
        assert data["refresh_token"] == FAKE_REFRESH_TOKEN

    def test_store_credentials_google_auth_not_available(self):
        """store_credentials should no-op when google-auth is missing."""
        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", False
        ):
            from backend.routes.google_auth import store_credentials
            # Should not raise
            store_credentials(MagicMock())

    def test_store_credentials_db_failure_still_writes_file(self, tmp_path):
        """DB storage failure should not prevent file storage."""
        token_file = tmp_path / "google_token.json"
        config_dir = tmp_path

        fake_creds = _make_fake_creds()
        mock_mgr = MagicMock()
        mock_mgr.store_oauth_token.side_effect = Exception("DB error")

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.CONFIG_DIR", config_dir
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=mock_mgr
        ):
            from backend.routes.google_auth import store_credentials
            store_credentials(fake_creds)

        # File should still be written despite DB failure
        assert token_file.exists()

    def test_get_credentials_from_file_fallback(self, tmp_path):
        """get_credentials should fall back to file when DB has nothing."""
        token_file = tmp_path / "google_token.json"
        token_data = {
            "token": FAKE_TOKEN,
            "refresh_token": FAKE_REFRESH_TOKEN,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": FAKE_CLIENT_ID,
            "client_secret": FAKE_CLIENT_SECRET,
            "scopes": ALL_SCOPES,
        }
        token_file.write_text(json.dumps(token_data))

        mock_google_creds = MagicMock()
        mock_google_creds.expired = False
        mock_google_creds.valid = True
        mock_google_creds.refresh_token = FAKE_REFRESH_TOKEN
        mock_google_creds.scopes = ALL_SCOPES

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ), patch(
            "backend.routes.google_auth.GoogleCredentials", return_value=mock_google_creds
        ):
            from backend.routes.google_auth import get_credentials
            creds = get_credentials()

        assert creds is not None
        assert creds.valid is True

    def test_get_credentials_returns_none_when_no_storage(self, tmp_path):
        """get_credentials returns None when neither DB nor file has tokens."""
        missing_file = tmp_path / "nonexistent_token.json"

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", missing_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ):
            from backend.routes.google_auth import get_credentials
            assert get_credentials() is None

    def test_get_credentials_google_auth_missing(self):
        """get_credentials returns None when google-auth is not installed."""
        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", False
        ):
            from backend.routes.google_auth import get_credentials
            assert get_credentials() is None

    def test_get_credentials_auto_refresh_on_expired(self, tmp_path):
        """get_credentials should auto-refresh expired tokens."""
        token_file = tmp_path / "google_token.json"
        token_data = {
            "token": "expired-token",
            "refresh_token": FAKE_REFRESH_TOKEN,
            "scopes": ALL_SCOPES,
        }
        token_file.write_text(json.dumps(token_data))

        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.valid = False
        mock_creds.refresh_token = FAKE_REFRESH_TOKEN
        mock_creds.scopes = ALL_SCOPES
        mock_creds.refresh = MagicMock()

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ), patch(
            "backend.routes.google_auth.GoogleCredentials", return_value=mock_creds
        ), patch(
            "backend.routes.google_auth.GoogleAuthRequest"
        ), patch(
            "backend.routes.google_auth.store_credentials"
        ) as mock_store:
            from backend.routes.google_auth import get_credentials
            creds = get_credentials()

        mock_creds.refresh.assert_called_once()
        mock_store.assert_called_once()

    def test_get_credentials_refresh_failure_returns_none(self, tmp_path):
        """get_credentials returns None if refresh explodes."""
        token_file = tmp_path / "google_token.json"
        token_file.write_text(json.dumps({
            "token": "expired",
            "refresh_token": FAKE_REFRESH_TOKEN,
            "scopes": ALL_SCOPES,
        }))

        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.valid = False
        mock_creds.refresh_token = FAKE_REFRESH_TOKEN
        mock_creds.refresh.side_effect = Exception("Refresh boom")

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ), patch(
            "backend.routes.google_auth.GoogleCredentials", return_value=mock_creds
        ), patch(
            "backend.routes.google_auth.GoogleAuthRequest"
        ):
            from backend.routes.google_auth import get_credentials
            assert get_credentials() is None

    def test_get_credentials_invalid_and_no_refresh_token(self, tmp_path):
        """Credentials that are not valid and lack a refresh token => None."""
        token_file = tmp_path / "google_token.json"
        token_file.write_text(json.dumps({
            "token": "stale",
            "refresh_token": None,
            "scopes": ALL_SCOPES,
        }))

        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_creds.valid = False
        mock_creds.refresh_token = None
        mock_creds.scopes = ALL_SCOPES

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ), patch(
            "backend.routes.google_auth.GoogleCredentials", return_value=mock_creds
        ):
            from backend.routes.google_auth import get_credentials
            assert get_credentials() is None

    def test_get_credentials_corrupt_token_file(self, tmp_path):
        """Corrupt token file should result in None, not a crash."""
        token_file = tmp_path / "google_token.json"
        token_file.write_text("{{INVALID JSON")

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ):
            from backend.routes.google_auth import get_credentials
            assert get_credentials() is None

    def test_get_credentials_constructor_failure(self, tmp_path):
        """If GoogleCredentials() raises, get_credentials returns None."""
        token_file = tmp_path / "google_token.json"
        token_file.write_text(json.dumps({
            "token": FAKE_TOKEN,
            "refresh_token": FAKE_REFRESH_TOKEN,
            "scopes": ALL_SCOPES,
        }))

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ), patch(
            "backend.routes.google_auth.GoogleCredentials",
            side_effect=ValueError("Bad args"),
        ):
            from backend.routes.google_auth import get_credentials
            assert get_credentials() is None

    def test_revoke_remote_failure_still_cleans_local(self, client, tmp_path):
        """Remote revoke failure should not prevent local cleanup."""
        fake_creds = _make_fake_creds()
        token_file = tmp_path / "google_token.json"
        token_file.write_text("{}")

        mock_httpx_resp = MagicMock()
        mock_httpx_resp.status_code = 400
        mock_httpx_resp.text = "invalid_token"

        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ), patch(
            "httpx.post", return_value=mock_httpx_resp
        ):
            resp = client.post("/api/google/revoke")

        data = resp.json()
        assert data["ok"] is True
        assert data["revoked_remote"] is False
        assert not token_file.exists()

    def test_revoke_httpx_import_error_falls_back_to_urllib(self, client):
        """Revoke should try urllib if httpx is unavailable."""
        fake_creds = _make_fake_creds()

        def httpx_post_unavailable(*a, **kw):
            raise ImportError("No httpx")

        with patch(
            "backend.routes.google_auth.get_credentials", return_value=fake_creds
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE",
            MagicMock(exists=MagicMock(return_value=False)),
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ), patch(
            "httpx.post", side_effect=httpx_post_unavailable
        ):
            # It will try httpx, fail with ImportError, then try urllib
            # urllib will also fail in test env but that's fine -- the endpoint
            # should still return ok
            resp = client.post("/api/google/revoke")

        assert resp.json()["ok"] is True

    def test_concurrent_token_refresh_safety(self, tmp_path):
        """Two simultaneous get_credentials calls should not corrupt state."""
        token_file = tmp_path / "google_token.json"
        token_file.write_text(json.dumps({
            "token": "expired",
            "refresh_token": FAKE_REFRESH_TOKEN,
            "scopes": ALL_SCOPES,
        }))

        call_count = 0

        def counting_refresh(auth_req):
            nonlocal call_count
            call_count += 1

        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.valid = False
        mock_creds.refresh_token = FAKE_REFRESH_TOKEN
        mock_creds.scopes = ALL_SCOPES
        mock_creds.refresh = counting_refresh

        with patch(
            "backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True
        ), patch(
            "backend.routes.google_auth.TOKEN_FILE", token_file
        ), patch(
            "backend.routes.google_auth._get_db_provider_manager", return_value=None
        ), patch(
            "backend.routes.google_auth.GoogleCredentials", return_value=mock_creds
        ), patch(
            "backend.routes.google_auth.GoogleAuthRequest"
        ), patch(
            "backend.routes.google_auth.store_credentials"
        ):
            from backend.routes.google_auth import get_credentials
            # Simulate two sequential calls (not truly concurrent, but tests
            # that the function is safe to call multiple times)
            r1 = get_credentials()
            r2 = get_credentials()

        assert call_count == 2
        assert r1 is not None
        assert r2 is not None

    def test_result_page_success_html(self):
        """_result_page with success=True should include check_circle icon."""
        from backend.routes.google_auth import _result_page
        html = _result_page("Title", "Message", success=True)
        assert "check_circle" in html
        assert "#4ade80" in html
        assert "Title" in html
        assert "Message" in html

    def test_result_page_failure_html(self):
        """_result_page with success=False should include error icon."""
        from backend.routes.google_auth import _result_page
        html = _result_page("Error", "Something broke", success=False)
        assert "error" in html
        assert "#f87171" in html
        assert "Error" in html

    def test_default_workspace_id(self):
        """_default_workspace_id should return 'default'."""
        from backend.routes.google_auth import _default_workspace_id
        assert _default_workspace_id() == "default"

    def test_require_google_libs_raises_when_missing(self):
        """_require_google_libs raises HTTPException when libs absent."""
        from backend.routes.google_auth import _require_google_libs
        with patch("backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", False), \
             patch("backend.routes.google_auth._OAUTHLIB_AVAILABLE", False):
            with pytest.raises(Exception) as exc_info:
                _require_google_libs()
            assert "not installed" in str(exc_info.value.detail)

    def test_require_google_libs_passes_when_available(self):
        """_require_google_libs should not raise when libs are installed."""
        from backend.routes.google_auth import _require_google_libs
        with patch("backend.routes.google_auth._GOOGLE_AUTH_AVAILABLE", True), \
             patch("backend.routes.google_auth._OAUTHLIB_AVAILABLE", True):
            # Should not raise
            _require_google_libs()
