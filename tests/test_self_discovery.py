"""
Tests for the AI Self-Discovery service (backend/autonomy/self_discovery.py).

Mocks all external calls: Ollama LLM, web search, memory manager, database.
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build mock Ollama / httpx responses
# ---------------------------------------------------------------------------


def _ollama_response(content: str, status_code: int = 200):
    """Build a mock httpx response that looks like an Ollama /api/chat reply."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"message": {"content": content}}
    resp.text = content
    return resp


def _async_client_mock(response):
    """Return a mock httpx.AsyncClient whose .post always returns *response*."""
    client = AsyncMock()
    client.post.return_value = response
    return client


def _patch_httpx(response):
    """Context-manager patch that wires httpx.AsyncClient -> mock client."""
    client = _async_client_mock(response)
    patcher = patch("backend.autonomy.self_discovery.httpx.AsyncClient")
    mock_cls = patcher.start()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return patcher, client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_profile(tmp_path, monkeypatch):
    """Redirect PROFILE_PATH to a temp directory so tests don't touch disk."""
    profile_path = tmp_path / "ai_profile.json"
    monkeypatch.setattr("backend.autonomy.self_discovery.PROFILE_PATH", profile_path)
    return profile_path


@pytest.fixture
def svc(tmp_profile):
    """Return a fresh SelfDiscoveryService with isolated profile path."""
    from backend.autonomy.self_discovery import SelfDiscoveryService

    return SelfDiscoveryService(ollama_url="http://localhost:11434")


# ---------------------------------------------------------------------------
# TestSelfDiscoveryProfile
# ---------------------------------------------------------------------------


class TestSelfDiscoveryProfile:
    def test_profile_initially_none(self, svc):
        """Service starts with no profile when file does not exist."""
        assert svc.get_profile() is None

    def test_save_and_load_profile(self, svc, tmp_profile):
        """Profile round-trips through JSON file."""
        profile = {
            "identity": {"name": "LocalMind"},
            "capabilities": [],
            "personality_traits": ["helpful"],
            "last_updated": time.time(),
        }
        svc._save_profile(profile)

        assert tmp_profile.exists()
        loaded = json.loads(tmp_profile.read_text(encoding="utf-8"))
        assert loaded["identity"]["name"] == "LocalMind"
        assert loaded["personality_traits"] == ["helpful"]

    def test_load_profile_on_init(self, tmp_profile):
        """If a profile file already exists, it should be loaded at init."""
        profile = {"identity": {"name": "LocalMind"}, "last_updated": 1.0}
        tmp_profile.write_text(json.dumps(profile), encoding="utf-8")

        from backend.autonomy.self_discovery import SelfDiscoveryService

        svc = SelfDiscoveryService()
        assert svc.get_profile() is not None
        assert svc.get_profile()["identity"]["name"] == "LocalMind"

    def test_load_corrupt_profile_yields_none(self, tmp_profile):
        """Corrupt JSON on disk should not crash; profile stays None."""
        tmp_profile.write_text("{bad json!!", encoding="utf-8")

        from backend.autonomy.self_discovery import SelfDiscoveryService

        svc = SelfDiscoveryService()
        assert svc.get_profile() is None

    def test_profile_path_is_workspace(self):
        """PROFILE_PATH should be under LocalMind_Workspace."""
        from backend.autonomy.self_discovery import PROFILE_PATH

        assert "LocalMind_Workspace" in str(PROFILE_PATH)


# ---------------------------------------------------------------------------
# TestCapabilityIntrospection
# ---------------------------------------------------------------------------


class TestCapabilityIntrospection:
    @pytest.mark.asyncio
    async def test_introspect_returns_list(self, svc):
        """Introspection should return a list (possibly empty if import fails)."""
        caps = await svc._introspect_capabilities()
        assert isinstance(caps, list)

    @pytest.mark.asyncio
    async def test_introspect_finds_tools(self, svc):
        """Should find at least the built-in web_search tool."""
        caps = await svc._introspect_capabilities()
        names = [c["name"] for c in caps]
        # WebSearchTool is always present
        assert "web_search" in names

    @pytest.mark.asyncio
    async def test_introspect_tool_has_required_keys(self, svc):
        """Each capability should have name, description, and type keys."""
        caps = await svc._introspect_capabilities()
        if caps:
            for cap in caps:
                assert "name" in cap
                assert "description" in cap
                assert "type" in cap
                assert cap["type"] in ("built_in", "generated")


# ---------------------------------------------------------------------------
# TestPersonalityAnalysis
# ---------------------------------------------------------------------------


class TestPersonalityAnalysis:
    @pytest.mark.asyncio
    async def test_extracts_traits_from_llm(self, svc):
        """Should extract personality traits when LLM returns a JSON array."""
        traits_json = '["warm and genuine", "direct communicator", "privacy-focused"]'
        patcher, _client = _patch_httpx(_ollama_response(traits_json))
        try:
            traits = await svc._analyze_personality()
            assert isinstance(traits, list)
            assert len(traits) >= 3
            assert "warm and genuine" in traits
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_fallback_traits_on_llm_failure(self, svc):
        """Should return fallback traits when LLM is unavailable."""
        patcher, _client = _patch_httpx(_ollama_response("", status_code=500))
        try:
            traits = await svc._analyze_personality()
            assert isinstance(traits, list)
            assert len(traits) > 0
            # Fallback includes these hardcoded traits
            assert "warm and genuine" in traits
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# TestParseJsonFromText
# ---------------------------------------------------------------------------


class TestParseJsonFromText:
    def test_parses_json_array(self, svc):
        text = 'Here are traits: ["a", "b", "c"]'
        result = svc._parse_json_from_text(text)
        assert result == ["a", "b", "c"]

    def test_parses_json_object(self, svc):
        text = 'Result: {"key": "value"}'
        result = svc._parse_json_from_text(text)
        assert result == {"key": "value"}

    def test_returns_fallback_on_empty(self, svc):
        result = svc._parse_json_from_text("", fallback=[])
        assert result == []

    def test_returns_fallback_on_no_json(self, svc):
        result = svc._parse_json_from_text("no json here", fallback="default")
        assert result == "default"


# ---------------------------------------------------------------------------
# TestWebPresence
# ---------------------------------------------------------------------------


class TestWebPresence:
    @pytest.mark.asyncio
    async def test_research_web_returns_results(self, svc):
        """Should search for LocalMind-related topics and return results."""
        mock_response = {
            "success": True,
            "results": [
                {"title": "LocalMind AI", "snippet": "A local-first assistant", "url": "https://example.com"}
            ],
        }

        with patch("backend.tools.web_search.WebSearchTool") as MockWST:
            instance = AsyncMock()
            instance.execute.return_value = mock_response
            MockWST.return_value = instance

            # Need to reimport or call directly to pick up the mock
            results = await svc._research_web_presence()
            assert isinstance(results, list)
            # At most 5 results (capped)
            assert len(results) <= 5

    @pytest.mark.asyncio
    async def test_research_web_handles_failure(self, svc):
        """Should gracefully handle search failures."""
        with patch("backend.tools.web_search.WebSearchTool") as MockWST:
            instance = AsyncMock()
            instance.execute.side_effect = RuntimeError("Network error")
            MockWST.return_value = instance

            results = await svc._research_web_presence()
            assert isinstance(results, list)
            # Should be empty on failure, not crash
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_research_web_handles_import_error(self, svc):
        """Should gracefully handle WebSearchTool not being importable."""
        with patch.dict("sys.modules", {"backend.tools.web_search": None}):
            results = await svc._research_web_presence()
            assert isinstance(results, list)


# ---------------------------------------------------------------------------
# TestAvatarGeneration
# ---------------------------------------------------------------------------


class TestAvatarGeneration:
    @pytest.mark.asyncio
    async def test_generates_avatar_from_llm(self, svc):
        """Should generate avatar with colors and description from LLM."""
        avatar_json = json.dumps({
            "description": "A glowing blue orb",
            "style": "minimalist",
            "dominant_colors": ["blue", "white"],
            "motifs": ["shield", "brain"],
        })
        patcher, _client = _patch_httpx(_ollama_response(avatar_json))
        try:
            avatar = await svc._generate_avatar_description()
            assert isinstance(avatar, dict)
            assert "description" in avatar
            assert "style" in avatar
            assert "dominant_colors" in avatar
            assert "motifs" in avatar
            assert avatar["description"] == "A glowing blue orb"
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_avatar_fallback_on_llm_failure(self, svc):
        """Should return a fallback avatar when LLM fails."""
        patcher, _client = _patch_httpx(_ollama_response("", status_code=500))
        try:
            avatar = await svc._generate_avatar_description()
            assert isinstance(avatar, dict)
            assert "description" in avatar
            assert "minimalist" in avatar["style"]
        finally:
            patcher.stop()


# ---------------------------------------------------------------------------
# TestDiscoveryCycle
# ---------------------------------------------------------------------------


class TestDiscoveryCycle:
    @pytest.mark.asyncio
    async def test_full_discovery_returns_profile(self, svc):
        """Full discovery should produce a complete profile dict."""
        traits_json = '["helpful", "private"]'
        avatar_json = json.dumps({
            "description": "A digital orb",
            "style": "geometric",
            "dominant_colors": ["blue"],
            "motifs": ["shield"],
        })
        facts_json = '["Fact one.", "Fact two."]'

        # Mock all LLM calls -- they happen through _llm_chat which uses httpx
        call_count = 0

        async def _fake_llm_chat(prompt, temperature=0.3):
            nonlocal call_count
            call_count += 1
            if "personality traits" in prompt.lower() or "traits" in prompt.lower():
                return traits_json
            if "avatar" in prompt.lower() or "visual" in prompt.lower():
                return avatar_json
            if "tagline" in prompt.lower():
                return "Your private AI, running locally"
            if "fun" in prompt.lower() or "facts" in prompt.lower():
                return facts_json
            return '["default"]'

        svc._llm_chat = _fake_llm_chat  # type: ignore[method-assign]

        # Mock web search
        with patch("backend.tools.web_search.WebSearchTool") as MockWST:
            ws_instance = AsyncMock()
            ws_instance.execute.return_value = {
                "success": True,
                "results": [{"title": "T", "snippet": "S", "url": "https://x.com"}],
            }
            MockWST.return_value = ws_instance

            # Mock memory/db calls that _gather_knowledge_areas uses
            with patch("backend.autonomy.self_discovery.importlib.import_module", side_effect=ImportError):
                profile = await svc.discover(force=True)

        assert isinstance(profile, dict)
        assert "identity" in profile
        assert "capabilities" in profile
        assert "personality_traits" in profile
        assert "avatar" in profile
        assert "fun_facts" in profile
        assert "last_updated" in profile

    @pytest.mark.asyncio
    async def test_discovery_skips_if_profile_exists(self, svc, tmp_profile):
        """Should skip discovery if profile already exists (unless force=True)."""
        existing = {"identity": {"name": "Cached"}, "last_updated": 1.0}
        svc._save_profile(existing)

        profile = await svc.discover(force=False)
        assert profile["identity"]["name"] == "Cached"

    @pytest.mark.asyncio
    async def test_force_rediscovery(self, svc, tmp_profile):
        """force=True should re-run even with existing profile."""
        existing = {"identity": {"name": "Old"}, "last_updated": 1.0}
        svc._save_profile(existing)

        async def _fake_llm_chat(prompt, temperature=0.3):
            if "tagline" in prompt.lower():
                return "New tagline"
            if "traits" in prompt.lower():
                return '["new trait"]'
            if "avatar" in prompt.lower():
                return json.dumps({"description": "new", "style": "new", "dominant_colors": [], "motifs": []})
            if "facts" in prompt.lower():
                return '["new fact"]'
            return "[]"

        svc._llm_chat = _fake_llm_chat  # type: ignore[method-assign]

        with patch("backend.tools.web_search.WebSearchTool") as MockWST:
            ws_instance = AsyncMock()
            ws_instance.execute.return_value = {"success": True, "results": []}
            MockWST.return_value = ws_instance

            profile = await svc.discover(force=True)

        # Should have been re-built, not the cached "Old" profile
        assert profile["identity"]["name"] == "LocalMind"
        assert profile["last_updated"] > 1.0


# ---------------------------------------------------------------------------
# TestFunFacts
# ---------------------------------------------------------------------------


class TestFunFacts:
    @pytest.mark.asyncio
    async def test_fun_facts_from_llm(self, svc):
        facts_json = '["LocalMind runs locally.", "No cloud needed."]'
        patcher, _client = _patch_httpx(_ollama_response(facts_json))
        try:
            facts = await svc._discover_fun_facts(web_results=[])
            assert isinstance(facts, list)
            assert "LocalMind runs locally." in facts
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_fun_facts_fallback(self, svc):
        patcher, _client = _patch_httpx(_ollama_response("garbage", status_code=500))
        try:
            facts = await svc._discover_fun_facts()
            assert isinstance(facts, list)
            assert len(facts) > 0
            # Should contain fallback facts
            assert any("LocalMind" in f for f in facts)
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_fun_facts_uses_web_context(self, svc):
        """Web results should be included in the LLM prompt."""
        web_results = [
            {"title": "Web Title", "snippet": "Web snippet text", "url": "https://example.com"}
        ]
        captured_prompts = []

        async def _capture_llm(prompt, temperature=0.3):
            captured_prompts.append(prompt)
            return '["fact"]'

        svc._llm_chat = _capture_llm  # type: ignore[method-assign]
        await svc._discover_fun_facts(web_results=web_results)

        assert len(captured_prompts) == 1
        assert "Web Title" in captured_prompts[0]
        assert "Web snippet text" in captured_prompts[0]


# ---------------------------------------------------------------------------
# TestSingleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_self_discovery_returns_instance(self):
        from backend.autonomy.self_discovery import get_self_discovery

        svc = get_self_discovery()
        assert svc is not None

    def test_get_self_discovery_returns_same_instance(self):
        from backend.autonomy.self_discovery import get_self_discovery

        svc1 = get_self_discovery()
        svc2 = get_self_discovery()
        assert svc1 is svc2
