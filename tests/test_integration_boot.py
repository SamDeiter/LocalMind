"""
Integration boot tests for LocalMind server.

Verifies that the FastAPI app boots correctly with all components wired up:
- App creation and router registration
- Health endpoints respond correctly
- Static file serving works
- CORS middleware is configured
- No-cache middleware is active

All tests run WITHOUT external services (Ollama, Slack, real DB).
Heavy dependencies are mocked so the server can boot in isolation.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers — patch the lifespan so the app boots without real infra
# ---------------------------------------------------------------------------

def _noop(*args, **kwargs):
    """Synchronous no-op for DB init stubs."""
    pass


@asynccontextmanager
async def _mock_lifespan(app: FastAPI):
    """Lightweight lifespan that skips DB, workers, and external calls."""
    yield


def _make_test_app():
    """
    Import and return the real ``app`` object with its lifespan replaced
    by a lightweight mock so no external services are required.
    """
    with patch("backend.server.lifespan", _mock_lifespan):
        # Reimport to pick up the patched lifespan
        import importlib
        import backend.server as srv_mod
        # Swap the lifespan on the already-created app
        srv_mod.app.router.lifespan_context = _mock_lifespan
        return srv_mod.app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def boot_app():
    """Module-scoped FastAPI app with mocked lifespan."""
    return _make_test_app()


@pytest.fixture(scope="module")
def client(boot_app):
    """Module-scoped synchronous test client.

    Uses base_url=http://localhost so the no-cache middleware
    sees hostname='localhost' (the default TestClient uses 'testserver').
    """
    return TestClient(boot_app, base_url="http://localhost")


@pytest.fixture(scope="module")
def all_route_paths(boot_app) -> list[str]:
    """Collect every registered route path from the app."""
    return [route.path for route in boot_app.routes if hasattr(route, "path")]


# ═══════════════════════════════════════════════════════════════════════════
# TestAppCreation — verify the app object and router wiring
# ═══════════════════════════════════════════════════════════════════════════

class TestAppCreation:
    """Validate the FastAPI instance and registered routers."""

    def test_app_is_fastapi_instance(self, boot_app):
        assert isinstance(boot_app, FastAPI)

    def test_app_title(self, boot_app):
        assert boot_app.title == "LocalMind"

    def test_app_version(self, boot_app):
        assert boot_app.version == "1.0.0"

    def test_route_count_reasonable(self, all_route_paths):
        """There should be a significant number of routes (> 30)."""
        assert len(all_route_paths) > 30, (
            f"Expected > 30 routes, found {len(all_route_paths)}"
        )

    # -- Router prefix checks ------------------------------------------------

    def test_chat_routes_registered(self, all_route_paths):
        assert any("/api/chat" in p for p in all_route_paths)

    def test_conversations_routes_registered(self, all_route_paths):
        assert any("/api/conversations" in p for p in all_route_paths)

    def test_memory_routes_registered(self, all_route_paths):
        assert any("/api/memory" in p for p in all_route_paths)

    def test_files_routes_registered(self, all_route_paths):
        assert any("/api/files" in p for p in all_route_paths)

    def test_tools_routes_registered(self, all_route_paths):
        assert any("/api/tools" in p for p in all_route_paths)

    def test_documents_routes_registered(self, all_route_paths):
        assert any("/api/documents" in p for p in all_route_paths)

    def test_jobs_routes_registered(self, all_route_paths):
        assert any("/api/jobs" in p for p in all_route_paths)

    def test_health_routes_registered(self, all_route_paths):
        assert any(p == "/health" for p in all_route_paths)

    def test_health_ready_route_registered(self, all_route_paths):
        assert any(p == "/health/ready" for p in all_route_paths)

    def test_health_deep_route_registered(self, all_route_paths):
        assert any(p == "/health/deep" for p in all_route_paths)

    def test_autonomy_routes_registered(self, all_route_paths):
        assert any("/api/autonomy" in p or "/api/proposals" in p for p in all_route_paths)

    def test_settings_routes_registered(self, all_route_paths):
        assert any("/api/settings" in p for p in all_route_paths)

    def test_swarm_routes_registered(self, all_route_paths):
        assert any("/api/swarm" in p for p in all_route_paths)

    def test_validation_routes_registered(self, all_route_paths):
        assert any("/api/validation" in p for p in all_route_paths)

    def test_google_auth_routes_registered(self, all_route_paths):
        assert any("/api/google" in p for p in all_route_paths)

    def test_system_routes_registered(self, all_route_paths):
        # system router uses /api prefix and contains e.g. /api/models, /api/health
        assert any("/api/models" in p or "/api/system" in p for p in all_route_paths)

    def test_research_routes_registered(self, all_route_paths):
        assert any("/api/research" in p for p in all_route_paths)


# ═══════════════════════════════════════════════════════════════════════════
# TestHealthEndpoints — hit the three /health endpoints via TestClient
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthEndpoints:
    """Verify the health endpoints return 200 with the expected shape."""

    def test_health_liveness_status_200(self, client):
        with patch(
            "backend.server.health_checker.check_liveness",
            new_callable=AsyncMock,
            return_value=MagicMock(
                healthy=True,
                to_dict=lambda: {"healthy": True, "checks": {}},
            ),
        ):
            r = client.get("/health")
            assert r.status_code == 200

    def test_health_liveness_shape(self, client):
        with patch(
            "backend.server.health_checker.check_liveness",
            new_callable=AsyncMock,
            return_value=MagicMock(
                healthy=True,
                to_dict=lambda: {"healthy": True, "checks": {"db": "pass"}},
            ),
        ):
            data = client.get("/health").json()
            assert "healthy" in data

    def test_health_liveness_healthy_true(self, client):
        with patch(
            "backend.server.health_checker.check_liveness",
            new_callable=AsyncMock,
            return_value=MagicMock(
                healthy=True,
                to_dict=lambda: {"healthy": True, "checks": {}},
            ),
        ):
            data = client.get("/health").json()
            assert data["healthy"] is True

    def test_health_liveness_unhealthy(self, client):
        with patch(
            "backend.server.health_checker.check_liveness",
            new_callable=AsyncMock,
            return_value=MagicMock(
                healthy=False,
                to_dict=lambda: {"healthy": False, "checks": {}},
            ),
        ):
            data = client.get("/health").json()
            assert data["healthy"] is False

    def test_health_ready_status_200(self, client):
        with patch(
            "backend.server.health_checker.check_readiness",
            new_callable=AsyncMock,
            return_value=MagicMock(
                healthy=True,
                to_dict=lambda: {"healthy": True, "checks": {}},
            ),
        ):
            r = client.get("/health/ready")
            assert r.status_code == 200

    def test_health_ready_shape(self, client):
        with patch(
            "backend.server.health_checker.check_readiness",
            new_callable=AsyncMock,
            return_value=MagicMock(
                healthy=True,
                to_dict=lambda: {"healthy": True, "checks": {"ollama": "pass"}},
            ),
        ):
            data = client.get("/health/ready").json()
            assert "healthy" in data

    def test_health_deep_status_200(self, client):
        with patch(
            "backend.server.health_checker.check_deep",
            new_callable=AsyncMock,
            return_value=MagicMock(
                healthy=True,
                to_dict=lambda: {"healthy": True, "checks": {}},
            ),
        ):
            r = client.get("/health/deep")
            assert r.status_code == 200

    def test_health_deep_shape(self, client):
        with patch(
            "backend.server.health_checker.check_deep",
            new_callable=AsyncMock,
            return_value=MagicMock(
                healthy=True,
                to_dict=lambda: {
                    "healthy": True,
                    "checks": {"disk": "pass", "vram": "pass"},
                },
            ),
        ):
            data = client.get("/health/deep").json()
            assert "healthy" in data

    def test_health_liveness_fallback_no_to_dict(self, client):
        """When the result lacks to_dict(), the handler falls back to {healthy: ...}."""
        result = MagicMock(spec=[])  # no attributes at all
        result.healthy = True
        with patch(
            "backend.server.health_checker.check_liveness",
            new_callable=AsyncMock,
            return_value=result,
        ):
            data = client.get("/health").json()
            assert data == {"healthy": True}


# ═══════════════════════════════════════════════════════════════════════════
# TestStaticServing — verify the root serves the frontend
# ═══════════════════════════════════════════════════════════════════════════

class TestStaticServing:
    """Verify that GET / serves the frontend index.html."""

    def test_root_returns_200(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_root_is_html(self, client):
        r = client.get("/")
        content_type = r.headers.get("content-type", "")
        assert "text/html" in content_type

    def test_root_contains_known_content(self, client):
        """The served HTML should contain a recognisable marker."""
        r = client.get("/")
        body = r.text.lower()
        # index.html uses the brand name "Nexus" and references "LocalMind"
        assert "nexus" in body or "localmind" in body or "<!doctype html>" in body


# ═══════════════════════════════════════════════════════════════════════════
# TestCORSHeaders — verify CORS middleware is wired up
# ═══════════════════════════════════════════════════════════════════════════

class TestCORSHeaders:
    """Verify that CORS middleware adds the expected headers."""

    def test_cors_middleware_present(self, boot_app):
        """CORSMiddleware should be in the middleware stack."""
        middleware_classes = [
            type(m).__name__
            for m in getattr(boot_app, "user_middleware", [])
        ]
        assert "CORSMiddleware" in [
            m.cls.__name__ for m in boot_app.user_middleware
        ], f"CORS not found in middleware: {middleware_classes}"

    def test_options_returns_cors_headers(self, client):
        """An OPTIONS preflight with an allowed Origin should get CORS headers."""
        r = client.options(
            "/api/chat",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        # The middleware should return access-control-allow-origin
        assert "access-control-allow-origin" in r.headers

    def test_cors_allows_localhost_3000(self, client):
        r = client.options(
            "/api/chat",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_cors_allows_credentials(self, client):
        r = client.options(
            "/api/chat",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.headers.get("access-control-allow-credentials") == "true"

    def test_cors_rejects_unknown_origin(self, client):
        """An unknown origin should not get reflected back."""
        r = client.options(
            "/api/chat",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        allowed = r.headers.get("access-control-allow-origin", "")
        assert "evil.example.com" not in allowed


# ═══════════════════════════════════════════════════════════════════════════
# TestMiddleware — verify the no-cache middleware is active
# ═══════════════════════════════════════════════════════════════════════════

class TestMiddleware:
    """Verify custom HTTP middleware behaviour."""

    def test_no_cache_on_root_localhost(self, client):
        """GET / from localhost should set Cache-Control: no-cache."""
        r = client.get("/")
        cache_ctrl = r.headers.get("cache-control", "")
        assert "no-cache" in cache_ctrl

    def test_no_cache_on_js_path(self, client):
        """A .js request on localhost should also get no-cache."""
        # The test client connects as localhost by default.
        # Even if the file doesn't exist, the middleware still runs before 404.
        r = client.get("/static/app.js")
        cache_ctrl = r.headers.get("cache-control", "")
        # Middleware fires on .js regardless of status code
        assert "no-cache" in cache_ctrl

    def test_health_no_cache_not_set(self, client):
        """Non-static paths (/health) should NOT get the no-cache header."""
        with patch(
            "backend.server.health_checker.check_liveness",
            new_callable=AsyncMock,
            return_value=MagicMock(
                healthy=True,
                to_dict=lambda: {"healthy": True, "checks": {}},
            ),
        ):
            r = client.get("/health")
            cache_ctrl = r.headers.get("cache-control", "")
            assert "no-cache" not in cache_ctrl
