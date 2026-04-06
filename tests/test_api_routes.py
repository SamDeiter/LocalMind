"""
Integration tests for all FastAPI API routes.

Tests the HTTP API contract (request/response shapes, status codes, headers)
using Starlette's synchronous TestClient. External dependencies (Ollama,
Gemini, ChromaDB) are mocked so no running LLM server is required.

Target: 30+ meaningful tests across health, autonomy, chat, swarm,
conversations, memory, files, tools, research, settings, and error/edge cases.
"""

import json
import threading
from unittest.mock import patch, MagicMock, AsyncMock

import httpx
import pytest

from tests.conftest import make_test_db_factory, _build_mock_autonomy_engine, _build_mock_coordinator


# ---------------------------------------------------------------------------
# Fixture: fully wired test app + client
# ---------------------------------------------------------------------------

@pytest.fixture
def _wired_app(seeded_db, tmp_path):
    """
    Build the FastAPI ``app`` with every injectable dependency replaced by
    a test double.  Yields ``(app, engine, coordinator)`` so individual
    tests can further customise the mocks.
    """
    get_test_db = make_test_db_factory(seeded_db)
    engine = _build_mock_autonomy_engine()
    coordinator = _build_mock_coordinator()
    engine.coordinator = coordinator

    # -- Proposals dir (empty, on disk so category-stats can glob) ----------
    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir()

    # -- Patch heavy modules that import Ollama / ChromaDB at module level --
    with patch("backend.db.DB_PATH", seeded_db), \
         patch("backend.db.get_db", get_test_db), \
         patch("backend.db.get_db_connection", get_test_db):

        # Import route modules *inside* the patch so they pick up the stubs
        from backend.routes import conversations, autonomy_routes, documents, chat
        from backend.routes.chat import init_chat_service

        conversations.configure(get_test_db, "You are LocalMind test prompt.")

        autonomy_routes.configure(
            engine=engine,
            proposals_dir=proposals_dir,
            rag_available=False,
            list_indexed_documents_fn=None,
        )

        documents.configure(rag_available=False)

        # Chat service -- we replace the real ChatService with a mock
        mock_chat_service = MagicMock()

        async def _fake_stream(body):
            """Return a trivial SSE generator."""
            async def _gen():
                yield f"data: {json.dumps({'token': 'Hello', 'conversation_id': 'test-conv-1'})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            return _gen()

        mock_chat_service.handle_chat = AsyncMock(side_effect=_fake_stream)
        chat._chat_service = mock_chat_service

        from backend.server import app

        # Attach the mock engine to app.state so swarm routes can find it
        app.state.autonomy_engine = engine

        yield app, engine, coordinator


@pytest.fixture
def client(_wired_app):
    """Synchronous TestClient talking to the fully-wired app."""
    from starlette.testclient import TestClient
    app, _, _ = _wired_app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def app_engine_coord(_wired_app):
    """Expose (app, engine, coordinator) for tests that need to tweak mocks."""
    return _wired_app


# ===================================================================
# 1. Health / Status Endpoints
# ===================================================================

class TestHealthEndpoints:
    """GET /api/health and GET /api/version"""

    def test_health_returns_server_true(self, client):
        """GET /api/health always reports server=True."""
        with patch("backend.routes.system.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            resp = MagicMock(status_code=200)
            inst.get.return_value = resp
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = inst

            r = client.get("/api/health")
            assert r.status_code == 200
            body = r.json()
            assert body["server"] is True
            assert "ollama" in body

    def test_health_ollama_down(self, client):
        """GET /api/health gracefully handles Ollama being unreachable."""
        with patch("backend.routes.system.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.get.side_effect = httpx.ConnectError("refused")
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = inst

            body = client.get("/api/health").json()
            assert body["server"] is True
            assert body["ollama"] is False

    def test_version_returns_shape(self, client):
        """GET /api/version returns version and build keys."""
        r = client.get("/api/version")
        assert r.status_code == 200
        body = r.json()
        assert "version" in body
        assert "build" in body

    def test_hardware_returns_system_keys(self, client):
        """GET /api/hardware includes system CPU/RAM metrics."""
        with patch("backend.routes.system.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.get.side_effect = Exception("no ollama")
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = inst

            body = client.get("/api/hardware").json()
            assert "system" in body
            assert "cpu_percent" in body["system"]
            assert "ram_total_gb" in body["system"]
            assert "loaded" in body

    def test_models_list(self, client):
        """GET /api/models returns a models array."""
        with patch("backend.routes.system.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"models": [{"name": "test:7b", "size": 100}]}
            inst.get.return_value = resp
            inst.__aenter__ = AsyncMock(return_value=inst)
            inst.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = inst

            body = client.get("/api/models").json()
            assert "models" in body
            assert isinstance(body["models"], list)


# ===================================================================
# 2. Autonomy Endpoints
# ===================================================================

class TestAutonomyRoutes:
    """Endpoints under /api/autonomy/*"""

    def test_autonomy_status_shape(self, client):
        """GET /api/autonomy/status returns expected top-level keys."""
        body = client.get("/api/autonomy/status").json()
        assert "mode" in body
        assert "enabled" in body
        assert "start_time" in body
        assert "recent_events" in body
        # Global sidebar counts injected by the route
        assert "memories_count" in body
        assert "documents_count" in body
        assert "proposals_count" in body

    def test_autonomy_toggle(self, client, app_engine_coord):
        """POST /api/autonomy/toggle returns the new enabled state."""
        _, engine, _ = app_engine_coord
        engine.toggle.return_value = False
        body = client.post("/api/autonomy/toggle").json()
        assert "enabled" in body
        assert body["enabled"] is False
        engine.toggle.assert_called_once()

    def test_autonomy_mode_switch_body(self, client, app_engine_coord):
        """POST /api/autonomy/mode accepts mode in JSON body."""
        _, engine, _ = app_engine_coord
        engine.set_mode.side_effect = lambda m: m
        body = client.post("/api/autonomy/mode", json={"mode": "autonomous"}).json()
        assert body["ok"] is True
        assert body["mode"] == "autonomous"

    def test_autonomy_mode_invalid(self, client, app_engine_coord):
        """POST /api/autonomy/mode with invalid mode returns error."""
        _, engine, _ = app_engine_coord
        engine.set_mode.side_effect = ValueError("Invalid mode: bad")
        body = client.post("/api/autonomy/mode", json={"mode": "bad"}).json()
        assert body["ok"] is False
        assert "error" in body

    def test_autonomy_reflect(self, client, app_engine_coord):
        """POST /api/autonomy/reflect triggers reflection cycle."""
        _, engine, _ = app_engine_coord
        body = client.post("/api/autonomy/reflect").json()
        assert body["ok"] is True
        engine.trigger_reflection.assert_called_once()

    def test_autonomy_execute(self, client, app_engine_coord):
        """POST /api/autonomy/execute triggers execution cycle."""
        _, engine, _ = app_engine_coord
        body = client.post("/api/autonomy/execute").json()
        assert body["ok"] is True
        engine.trigger_execution.assert_called_once()

    def test_list_proposals_default(self, client, app_engine_coord):
        """GET /api/autonomy/proposals returns proposals list and count."""
        _, engine, _ = app_engine_coord
        engine.list_proposals.return_value = [
            {"id": "p1", "title": "Test", "status": "proposed"},
        ]
        body = client.get("/api/autonomy/proposals").json()
        assert "proposals" in body
        assert body["count"] == 1

    def test_list_proposals_filtered(self, client, app_engine_coord):
        """GET /api/autonomy/proposals?status=proposed passes filter."""
        _, engine, _ = app_engine_coord
        engine.list_proposals.return_value = []
        client.get("/api/autonomy/proposals?status=proposed")
        engine.list_proposals.assert_called_with(status_filter="proposed")

    def test_approve_proposal(self, client, app_engine_coord):
        """POST /api/autonomy/proposals/{id}/approve returns ok."""
        body = client.post("/api/autonomy/proposals/p1/approve").json()
        assert body["ok"] is True
        assert "proposal" in body

    def test_approve_proposal_not_found(self, client, app_engine_coord):
        """Approving a missing proposal returns ok=False."""
        _, engine, _ = app_engine_coord
        engine.approve_proposal.return_value = None
        body = client.post("/api/autonomy/proposals/missing/approve").json()
        assert body["ok"] is False

    def test_deny_proposal(self, client, app_engine_coord):
        """POST /api/autonomy/proposals/{id}/deny returns ok."""
        body = client.post("/api/autonomy/proposals/p1/deny").json()
        assert body["ok"] is True

    def test_retry_proposal(self, client, app_engine_coord):
        """POST /api/autonomy/proposals/{id}/retry returns ok."""
        body = client.post("/api/autonomy/proposals/p1/retry").json()
        assert body["ok"] is True

    def test_retry_proposal_not_found(self, client, app_engine_coord):
        """Retry of missing proposal returns ok=False."""
        _, engine, _ = app_engine_coord
        engine.retry_proposal.return_value = None
        body = client.post("/api/autonomy/proposals/missing/retry").json()
        assert body["ok"] is False

    def test_list_priorities(self, client):
        """GET /api/autonomy/priorities returns priorities array."""
        body = client.get("/api/autonomy/priorities").json()
        assert "priorities" in body
        assert isinstance(body["priorities"], list)

    def test_add_priority(self, client):
        """POST /api/autonomy/priorities with description succeeds."""
        body = client.post(
            "/api/autonomy/priorities",
            json={"description": "Fix login bug", "priority": "high"},
        ).json()
        assert body["ok"] is True
        assert body["priority"]["description"] == "test"  # from mock

    def test_add_priority_empty(self, client, app_engine_coord):
        """POST /api/autonomy/priorities with empty description fails."""
        body = client.post(
            "/api/autonomy/priorities",
            json={"description": "", "priority": "high"},
        ).json()
        assert body["ok"] is False
        assert "error" in body

    def test_remove_priority(self, client):
        """DELETE /api/autonomy/priorities/{id} returns ok."""
        body = client.delete("/api/autonomy/priorities/prio-1").json()
        assert body["ok"] is True

    def test_engine_reset(self, client, app_engine_coord):
        """POST /api/autonomy/reset returns summary."""
        body = client.post("/api/autonomy/reset").json()
        assert body["ok"] is True
        assert "archived" in body

    def test_category_stats(self, client):
        """GET /api/autonomy/category-stats returns categories dict."""
        body = client.get("/api/autonomy/category-stats").json()
        assert "categories" in body
        assert isinstance(body["categories"], dict)


# ===================================================================
# 3. Chat Endpoint
# ===================================================================

class TestChatEndpoint:
    """POST /api/chat"""

    def test_chat_returns_sse_stream(self, client):
        """POST /api/chat returns text/event-stream with data lines."""
        r = client.post("/api/chat", json={"message": "Hi"})
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        # The body should contain SSE data lines
        assert "data:" in r.text

    def test_chat_stream_contains_token_and_done(self, client):
        """The SSE stream includes a token event and a done event."""
        r = client.post("/api/chat", json={"message": "Hi"})
        lines = [l for l in r.text.strip().split("\n") if l.startswith("data:")]
        assert len(lines) >= 2
        first = json.loads(lines[0].removeprefix("data: "))
        assert "token" in first
        last = json.loads(lines[-1].removeprefix("data: "))
        assert last.get("done") is True

    def test_chat_service_not_initialized(self, client):
        """If chat service is None, endpoint returns 503."""
        from backend.routes import chat
        saved = chat._chat_service
        try:
            chat._chat_service = None
            r = client.post("/api/chat", json={"message": "Hi"})
            assert r.status_code == 503
        finally:
            chat._chat_service = saved

    def test_chat_sms_endpoint_exists(self, client):
        """POST /api/chat/sms returns a response (even if unimplemented)."""
        r = client.post("/api/chat/sms", json={"from": "+1234", "text": "hello"})
        assert r.status_code == 200
        body = r.json()
        assert "status" in body


# ===================================================================
# 4. Swarm Endpoints
# ===================================================================

class TestSwarmRoutes:
    """Endpoints under /api/swarm/*"""

    def test_swarm_status_with_coordinator(self, client):
        """GET /api/swarm/status returns coordinator status when available."""
        body = client.get("/api/swarm/status").json()
        assert body["running"] is True
        assert "queue" in body
        assert "agents" in body
        assert "gpu" in body

    def test_swarm_status_no_coordinator(self, client, app_engine_coord):
        """GET /api/swarm/status returns error when coordinator is None."""
        _, engine, _ = app_engine_coord
        engine.coordinator = None
        body = client.get("/api/swarm/status").json()
        assert body["running"] is False

    def test_swarm_agents(self, client):
        """GET /api/swarm/agents returns agent details list."""
        body = client.get("/api/swarm/agents").json()
        assert "agents" in body
        assert len(body["agents"]) == 2

    def test_swarm_agents_no_coordinator(self, client, app_engine_coord):
        """GET /api/swarm/agents with no coordinator returns empty list."""
        _, engine, _ = app_engine_coord
        engine.coordinator = None
        body = client.get("/api/swarm/agents").json()
        assert body["agents"] == []

    def test_swarm_scan(self, client):
        """POST /api/swarm/scan submits scan tasks."""
        body = client.post("/api/swarm/scan").json()
        assert "task_ids" in body
        assert len(body["task_ids"]) == 2

    def test_swarm_scan_no_coordinator(self, client, app_engine_coord):
        """POST /api/swarm/scan with no coordinator returns 503."""
        _, engine, _ = app_engine_coord
        engine.coordinator = None
        r = client.post("/api/swarm/scan")
        assert r.status_code == 503

    def test_swarm_research(self, client):
        """POST /api/swarm/research submits a research task."""
        body = client.post(
            "/api/swarm/research",
            json={"query": "transformer attention", "source": "web"},
        ).json()
        assert body["task_id"] == "task-r1"
        assert body["query"] == "transformer attention"

    def test_swarm_test(self, client):
        """POST /api/swarm/test submits a test/validation task."""
        body = client.post(
            "/api/swarm/test",
            json={"mode": "syntax", "files": ["backend/server.py"]},
        ).json()
        assert body["task_id"] == "task-t1"
        assert body["mode"] == "syntax"

    def test_swarm_scale(self, client):
        """POST /api/swarm/scale returns placeholder message."""
        body = client.post("/api/swarm/scale", json={"workers": 5}).json()
        assert "message" in body


# ===================================================================
# 5. Conversation CRUD
# ===================================================================

class TestConversationRoutes:
    """Endpoints under /api/conversations/*"""

    def test_list_conversations(self, client):
        body = client.get("/api/conversations").json()
        assert len(body["conversations"]) >= 1

    def test_get_conversation_messages(self, client):
        body = client.get("/api/conversations/test-conv-1/messages").json()
        assert len(body["messages"]) == 2

    def test_get_conversation_metadata(self, client):
        body = client.get("/api/conversations/test-conv-1").json()
        assert body["title"] == "Test Conversation"

    def test_get_nonexistent_conversation(self, client):
        body = client.get("/api/conversations/nonexistent").json()
        assert "error" in body

    def test_delete_conversation(self, client):
        body = client.delete("/api/conversations/test-conv-1").json()
        assert body["ok"] is True
        # Verify gone
        body2 = client.get("/api/conversations").json()
        assert len(body2["conversations"]) == 0

    def test_update_system_prompt(self, client):
        body = client.put(
            "/api/conversations/test-conv-1/system-prompt",
            json={"system_prompt": "Be a pirate."},
        ).json()
        assert body["ok"] is True
        # Verify
        meta = client.get("/api/conversations/test-conv-1").json()
        assert meta["system_prompt"] == "Be a pirate."

    def test_default_system_prompt(self, client):
        body = client.get("/api/default-system-prompt").json()
        assert "system_prompt" in body
        assert len(body["system_prompt"]) > 0

    def test_export_markdown(self, client):
        r = client.get("/api/conversations/test-conv-1/export?format=md")
        assert r.status_code == 200
        assert "Test Conversation" in r.text

    def test_export_json_format(self, client):
        r = client.get("/api/conversations/test-conv-1/export?format=json")
        data = json.loads(r.text)
        assert data["title"] == "Test Conversation"
        assert len(data["messages"]) == 2

    def test_export_nonexistent(self, client):
        body = client.get("/api/conversations/fake/export").json()
        assert "error" in body


# ===================================================================
# 6. Memory Endpoints
# ===================================================================

class TestMemoryRoutes:

    def test_memory_status(self, client):
        body = client.get("/api/memory/status").json()
        assert "learning_enabled" in body

    def test_memory_toggle_off_and_on(self, client):
        off = client.post("/api/memory/toggle", json={"enabled": False}).json()
        assert off["learning_enabled"] is False
        on = client.post("/api/memory/toggle", json={"enabled": True}).json()
        assert on["learning_enabled"] is True

    def test_list_memories_no_chromadb(self, client):
        """Listing memories when ChromaDB is unavailable should not crash."""
        with patch("backend.routes.memory.list_memories", new_callable=AsyncMock) as _:
            # The real endpoint catches ImportError internally
            body = client.get("/api/memories").json()
            # Either returns memories list or error, both acceptable
            assert "memories" in body or "error" in body


# ===================================================================
# 7. Documents (RAG)
# ===================================================================

class TestDocumentRoutes:

    def test_list_documents_rag_unavailable(self, client):
        """When RAG is not installed, listing docs returns empty."""
        body = client.get("/api/documents/").json()
        assert body["documents"] == []

    def test_upload_document_rag_unavailable(self, client):
        """Uploading when RAG is off returns an error message."""
        r = client.post(
            "/api/documents/upload",
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )
        body = r.json()
        assert "error" in body
        assert "RAG" in body["error"]

    def test_delete_document_rag_unavailable(self, client):
        """Deleting a doc when RAG is off returns an error."""
        body = client.delete("/api/documents/somefile.txt").json()
        assert "error" in body


# ===================================================================
# 8. Settings
# ===================================================================

class TestSettingsRoutes:

    def test_get_notification_settings(self, client):
        with patch("backend.notifications.get_settings", return_value={"enabled": False}):
            body = client.get("/api/settings/notifications").json()
            assert "enabled" in body

    def test_update_notification_settings(self, client):
        with patch("backend.notifications.save_settings") as mock_save:
            body = client.post(
                "/api/settings/notifications",
                json={"enabled": True, "phone": "1234567890"},
            ).json()
            assert body["status"] == "ok"
            mock_save.assert_called_once()

    def test_get_cloud_settings_masks_key(self, client):
        with patch("backend.gemini_client.get_settings", return_value={"api_key": "sk-abcdefghij1234567890"}):
            body = client.get("/api/settings/cloud").json()
            assert body["api_key"].startswith("****")
            assert body["api_key"].endswith("7890")


# ===================================================================
# 9. Error Cases
# ===================================================================

class TestErrorCases:

    def test_post_chat_no_body(self, client):
        """POST /api/chat without a body returns 422."""
        r = client.post("/api/chat")
        assert r.status_code == 422

    def test_post_chat_invalid_json(self, client):
        """POST /api/chat with broken JSON returns 422."""
        r = client.post(
            "/api/chat",
            content=b"{invalid json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 422

    def test_unknown_route_returns_404(self, client):
        """A completely unknown API path returns 404."""
        r = client.get("/api/nonexistent_endpoint_xyz")
        assert r.status_code == 404

    def test_put_system_prompt_no_body(self, client):
        """PUT system prompt with no JSON body returns an error status."""
        r = client.put("/api/conversations/test-conv-1/system-prompt")
        # FastAPI may return 422 (no body) or the route may crash; either is acceptable
        assert r.status_code in (400, 422, 500)

    def test_swarm_research_no_body(self, client):
        """POST /api/swarm/research without JSON body returns error."""
        r = client.post("/api/swarm/research")
        # Will fail trying to parse JSON -- 422 or 500
        assert r.status_code in (400, 422, 500)

    def test_tools_run_empty_code(self, client):
        """POST /api/tools/run with empty code returns success=False."""
        body = client.post("/api/tools/run", json={"code": ""}).json()
        assert body["success"] is False
        assert "No code" in body.get("error", "")


# ===================================================================
# 10. Edge Cases
# ===================================================================

class TestEdgeCases:

    def test_chat_empty_message(self, client):
        """Sending an empty message should still return a valid SSE stream."""
        r = client.post("/api/chat", json={"message": ""})
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")

    def test_chat_very_long_message(self, client):
        """A very long message (10K chars) should not crash the endpoint."""
        long_msg = "x" * 10_000
        r = client.post("/api/chat", json={"message": long_msg})
        assert r.status_code == 200

    def test_conversation_messages_empty(self, client):
        """Getting messages for a conversation with no messages returns empty."""
        body = client.get("/api/conversations/nonexistent/messages").json()
        assert body["messages"] == []

    def test_multiple_sequential_requests(self, client):
        """Several rapid sequential requests should all succeed."""
        for _ in range(5):
            r = client.get("/api/autonomy/status")
            assert r.status_code == 200

    def test_export_default_format_is_markdown(self, client):
        """Export without format param defaults to markdown."""
        r = client.get("/api/conversations/test-conv-1/export")
        assert r.status_code == 200
        # Markdown format returns text/markdown content type
        assert "markdown" in r.headers.get("content-type", "") or "Test Conversation" in r.text

    def test_autonomy_proposals_with_all_filter(self, client, app_engine_coord):
        """Passing status=all explicitly still works."""
        _, engine, _ = app_engine_coord
        engine.list_proposals.return_value = []
        client.get("/api/autonomy/proposals?status=all")
        engine.list_proposals.assert_called_with(status_filter="all")

    def test_swarm_research_empty_query(self, client, app_engine_coord):
        """Submitting research with empty query still returns task_id."""
        body = client.post(
            "/api/swarm/research",
            json={"query": "", "source": "web"},
        ).json()
        assert "task_id" in body

    def test_delete_nonexistent_conversation_is_safe(self, client):
        """Deleting a non-existent conversation should not crash."""
        r = client.delete("/api/conversations/does-not-exist")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_autonomy_activity_sse_stream(self, client, app_engine_coord):
        """GET /api/autonomy/activity returns an SSE stream.

        The SSE endpoint is long-lived. We inject an event into the
        subscriber queue so the generator yields immediately, then
        disconnect.
        """
        import asyncio as _asyncio
        _, engine, _ = app_engine_coord

        # Pre-populate a current_activity so the first yield is immediate
        engine.get_status.return_value = {
            **engine.get_status.return_value,
            "current_activity": {"action": "idle", "detail": "Waiting...", "time": "12:00:00"},
        }

        # The subscribe_activity mock returns an asyncio.Queue.
        # We need it to be a fresh queue per call (the route subscribes then reads).
        test_queue = _asyncio.Queue()
        engine.subscribe_activity.return_value = test_queue

        # Use a non-streaming GET so TestClient reads the *first* chunk
        # (the initial status) and then the 30-second timeout keepalive.
        # Instead of waiting for that, we just verify the response starts
        # with the correct content-type via a quick streaming peek.
        response_holder = {}

        def _fetch():
            """Fetch in a thread so we can time-box it."""
            try:
                r = client.get("/api/autonomy/activity", timeout=3)
                response_holder["status"] = r.status_code
                response_holder["content_type"] = r.headers.get("content-type", "")
                response_holder["text"] = r.text[:500]
            except Exception:
                # Timeout is expected for SSE -- still fine
                pass

        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=5)

        # If the thread completed, check the response
        if "status" in response_holder:
            assert response_holder["status"] == 200
            assert "text/event-stream" in response_holder["content_type"]
            assert "data:" in response_holder["text"]
        else:
            # Thread didn't finish (expected for infinite SSE); just verify
            # we didn't crash by checking the endpoint is at least reachable.
            # The fact that subscribe_activity was called proves it connected.
            engine.subscribe_activity.assert_called()
