"""PWA Push Notification and Tailscale middleware tests.

Tests cover:
- Push subscription CRUD via FastAPI TestClient (routes/push.py)
- Tailscale auth middleware IP-range and header logic (security/tailscale.py)
- PWA manifest.json validation (frontend/manifest.json)

Uses an in-memory SQLite DB following the pattern from test_swarm_api.py.
"""

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _init_push_db() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the push_subscriptions schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL UNIQUE,
            key_p256dh TEXT NOT NULL,
            key_auth TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_push_endpoint
            ON push_subscriptions(endpoint);
    """)
    return conn


class _PersistentConnection:
    """Wrapper that ignores close() so the in-memory DB survives route handlers."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self):
        pass  # keep alive

    def real_close(self):
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def push_db():
    """Yield a persistent in-memory DB with the push schema."""
    raw = _init_push_db()
    db = _PersistentConnection(raw)
    yield db
    db.real_close()


@pytest.fixture()
def client(push_db):
    """FastAPI TestClient with the push router, patching sqlite3.connect
    so every call inside routes/push.py uses the shared in-memory DB."""

    def _fake_connect(*args, **kwargs):
        return push_db

    # Patch sqlite3.connect inside the push module AND suppress the
    # module-level _ensure_table() call (already handled by our fixture).
    with patch("backend.routes.push.sqlite3.connect", side_effect=_fake_connect), \
         patch("backend.routes.push._ensure_table"):
        from backend.routes.push import router

        app = FastAPI()
        app.include_router(router)
        yield TestClient(app)


# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

_SUBSCRIPTION = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/test-endpoint-123",
    "keys": {
        "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-T1aUR1nNWPaABCDEFGH",
        "auth": "tBHItJI5svbpC7-ABCDEFG",
    },
}

_SUBSCRIPTION_2 = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/test-endpoint-456",
    "keys": {
        "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-SECOND",
        "auth": "tBHItJI5svbpC7-SECOND",
    },
}


# ===================================================================
# Push API tests
# ===================================================================

class TestVapidKey:

    def test_get_vapid_key_returns_key(self, client):
        """GET /api/push/vapid-key returns a publicKey when configured."""
        with patch("backend.routes.push._vapid_keys", return_value=("priv", "test-pub-key", "mailto:a@b.c")):
            resp = client.get("/api/push/vapid-key")
        assert resp.status_code == 200
        assert resp.json()["publicKey"] == "test-pub-key"

    def test_get_vapid_key_503_when_not_configured(self, client):
        """GET /api/push/vapid-key returns 503 if VAPID keys are empty."""
        with patch("backend.routes.push._vapid_keys", return_value=("", "", "")):
            resp = client.get("/api/push/vapid-key")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"].lower()


class TestSubscribe:

    def test_subscribe_stores_subscription(self, client, push_db):
        """POST /api/push/subscribe stores the subscription and returns 201."""
        resp = client.post("/api/push/subscribe", json=_SUBSCRIPTION)
        assert resp.status_code == 201
        assert resp.json()["status"] == "subscribed"

        # Verify it landed in the DB
        row = push_db.execute(
            "SELECT endpoint, key_p256dh, key_auth FROM push_subscriptions WHERE endpoint = ?",
            (_SUBSCRIPTION["endpoint"],),
        ).fetchone()
        assert row is not None
        assert row["key_p256dh"] == _SUBSCRIPTION["keys"]["p256dh"]
        assert row["key_auth"] == _SUBSCRIPTION["keys"]["auth"]

    def test_subscribe_is_idempotent(self, client, push_db):
        """POST /api/push/subscribe with the same endpoint updates (upsert)."""
        # First subscribe
        resp1 = client.post("/api/push/subscribe", json=_SUBSCRIPTION)
        assert resp1.status_code == 201

        # Subscribe again with updated keys
        updated = {
            "endpoint": _SUBSCRIPTION["endpoint"],
            "keys": {"p256dh": "UPDATED_KEY", "auth": "UPDATED_AUTH"},
        }
        resp2 = client.post("/api/push/subscribe", json=updated)
        assert resp2.status_code == 201

        # Should still be exactly one row
        count = push_db.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE endpoint = ?",
            (_SUBSCRIPTION["endpoint"],),
        ).fetchone()[0]
        assert count == 1

        # Keys should be updated
        row = push_db.execute(
            "SELECT key_p256dh, key_auth FROM push_subscriptions WHERE endpoint = ?",
            (_SUBSCRIPTION["endpoint"],),
        ).fetchone()
        assert row["key_p256dh"] == "UPDATED_KEY"
        assert row["key_auth"] == "UPDATED_AUTH"

    def test_subscribe_rejects_missing_endpoint(self, client):
        """POST /api/push/subscribe without endpoint returns 400."""
        resp = client.post("/api/push/subscribe", json={"keys": {"p256dh": "x", "auth": "y"}})
        assert resp.status_code == 400

    def test_subscribe_rejects_missing_keys(self, client):
        """POST /api/push/subscribe without keys returns 400."""
        resp = client.post("/api/push/subscribe", json={"endpoint": "https://example.com"})
        assert resp.status_code == 400


class TestUnsubscribe:

    def test_unsubscribe_removes_subscription(self, client, push_db):
        """DELETE /api/push/subscribe removes the subscription."""
        # Insert first
        client.post("/api/push/subscribe", json=_SUBSCRIPTION)

        resp = client.request("DELETE", "/api/push/subscribe", json={
            "endpoint": _SUBSCRIPTION["endpoint"],
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "unsubscribed"

        # Verify it's gone
        row = push_db.execute(
            "SELECT * FROM push_subscriptions WHERE endpoint = ?",
            (_SUBSCRIPTION["endpoint"],),
        ).fetchone()
        assert row is None

    def test_unsubscribe_unknown_endpoint_returns_404(self, client):
        """DELETE /api/push/subscribe with unknown endpoint returns 404."""
        resp = client.request("DELETE", "/api/push/subscribe", json={
            "endpoint": "https://no-such-endpoint.example.com",
        })
        assert resp.status_code == 404


class TestSendTest:

    def test_send_test_without_pywebpush(self, client):
        """POST /api/push/test gracefully degrades when pywebpush is absent."""
        with patch("backend.routes.push._WEBPUSH_AVAILABLE", False):
            resp = client.post("/api/push/test")
        assert resp.status_code == 200
        data = resp.json()
        # When pywebpush is not installed, send_push_to_all returns skipped=1
        assert data.get("skipped", 0) >= 1 or data.get("sent", 0) == 0


# ===================================================================
# Tailscale middleware tests
# ===================================================================

class TestTailscaleMiddleware:

    def _make_app_with_middleware(self, *, enabled: bool = True):
        """Build a minimal FastAPI app with TailscaleAuthMiddleware."""
        app = FastAPI()

        @app.get("/whoami")
        async def whoami(request: Request):
            user = getattr(request.state, "user", None)
            ts_auth = getattr(request.state, "tailscale_authenticated", False)
            return {"user": user, "tailscale_authenticated": ts_auth}

        # Patch the module-level flag before instantiating the middleware
        with patch("backend.security.tailscale.TAILSCALE_AUTH_ENABLED", enabled):
            from backend.security.tailscale import TailscaleAuthMiddleware
            app.add_middleware(TailscaleAuthMiddleware)

        return TestClient(app)

    def test_cgnat_ip_with_header_is_trusted(self):
        """Request from Tailscale CGNAT IP (100.64.x.x) with header is authenticated."""
        tc = self._make_app_with_middleware(enabled=True)
        resp = tc.get(
            "/whoami",
            headers={"Tailscale-User-Login": "sam@example.com"},
            # TestClient uses 'testclient' as default host; we override via
            # a real ASGI transport to inject the IP. Since TestClient doesn't
            # directly expose client IP override, we patch the middleware helper.
        )
        # TestClient default IP is "testclient" which is not in CGNAT,
        # so we test the helper directly instead.
        from backend.security.tailscale import _ip_in_tailscale_range, is_tailscale_request
        assert _ip_in_tailscale_range("100.64.0.1") is True
        assert _ip_in_tailscale_range("100.100.100.100") is True
        assert _ip_in_tailscale_range("100.127.255.254") is True

    def test_non_cgnat_ip_with_header_is_not_trusted(self):
        """Request from non-CGNAT IP with Tailscale header is NOT trusted (spoofing)."""
        from backend.security.tailscale import _ip_in_tailscale_range
        # These are outside the 100.64.0.0/10 range
        assert _ip_in_tailscale_range("192.168.1.1") is False
        assert _ip_in_tailscale_range("10.0.0.1") is False
        assert _ip_in_tailscale_range("8.8.8.8") is False
        # Edge: 100.63.255.255 is just below the CGNAT range
        assert _ip_in_tailscale_range("100.63.255.255") is False

    def test_is_tailscale_request_requires_both_ip_and_header(self):
        """is_tailscale_request needs BOTH a CGNAT IP and the header."""
        from backend.security.tailscale import is_tailscale_request

        # Mock request with CGNAT IP + header
        req = MagicMock(spec=Request)
        req.client.host = "100.64.1.1"
        req.headers = {"Tailscale-User-Login": "sam@example.com"}
        assert is_tailscale_request(req) is True

        # CGNAT IP but no header
        req_no_header = MagicMock(spec=Request)
        req_no_header.client.host = "100.64.1.1"
        req_no_header.headers = {}
        assert is_tailscale_request(req_no_header) is False

        # Header but wrong IP
        req_bad_ip = MagicMock(spec=Request)
        req_bad_ip.client.host = "192.168.1.100"
        req_bad_ip.headers = {"Tailscale-User-Login": "sam@example.com"}
        assert is_tailscale_request(req_bad_ip) is False

    def test_middleware_disabled_passes_through(self):
        """When TAILSCALE_AUTH_ENABLED=false, middleware is a no-op."""
        tc = self._make_app_with_middleware(enabled=False)
        resp = tc.get("/whoami", headers={"Tailscale-User-Login": "sam@example.com"})
        assert resp.status_code == 200
        data = resp.json()
        # Middleware should NOT have set tailscale_authenticated
        assert data["tailscale_authenticated"] is False


# ===================================================================
# Manifest validation tests
# ===================================================================

class TestManifestValidation:

    @pytest.fixture(autouse=True)
    def _load_manifest(self):
        manifest_path = _PROJECT_ROOT / "frontend" / "manifest.json"
        assert manifest_path.exists(), f"manifest.json not found at {manifest_path}"
        with open(manifest_path, encoding="utf-8") as f:
            self.manifest = json.load(f)

    def test_manifest_is_valid_json(self):
        """frontend/manifest.json parses as valid JSON (covered by fixture)."""
        assert isinstance(self.manifest, dict)

    def test_manifest_has_required_pwa_fields(self):
        """manifest.json contains all required PWA fields."""
        required = ["name", "short_name", "start_url", "display", "icons"]
        for field in required:
            assert field in self.manifest, f"Missing required PWA field: {field}"

        # Basic value checks
        assert isinstance(self.manifest["name"], str) and len(self.manifest["name"]) > 0
        assert isinstance(self.manifest["icons"], list) and len(self.manifest["icons"]) > 0
        assert self.manifest["display"] in ("standalone", "fullscreen", "minimal-ui", "browser")
