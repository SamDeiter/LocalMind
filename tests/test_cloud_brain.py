"""
Tests for the CloudBrainSupervisor -- cloud AI proposal review gate.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from backend.autonomy.cloud_brain import (
    CloudBrainSupervisor,
    CloudReview,
    get_cloud_brain,
    reset_cloud_brain,
    PROTECTED_FILES,
)


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def sample_proposal():
    """A realistic proposal for testing."""
    return {
        "id": "test123",
        "title": "Add retry logic to web search",
        "category": "performance",
        "description": "Wrap the web_search tool's httpx calls with exponential backoff.",
        "files_affected": ["backend/tools.py", "backend/web_search.py"],
        "effort": "small",
        "priority": "high",
    }


@pytest.fixture
def supervisor_enabled():
    """A CloudBrainSupervisor with a fake API key (enabled)."""
    return CloudBrainSupervisor(
        api_key="fake-test-key-for-testing",
        model="gemini-2.0-flash",
        max_reviews_per_hour=20,
        enabled=True,
    )


@pytest.fixture
def supervisor_disabled():
    """A CloudBrainSupervisor that is explicitly disabled."""
    return CloudBrainSupervisor(
        api_key="",
        model="gemini-2.0-flash",
        max_reviews_per_hour=20,
        enabled=True,
    )


# ── Availability Tests ─────────────────────────────────────────────────

class TestCloudBrainAvailability:
    def test_cloud_brain_disabled_by_default(self):
        """Cloud brain should be disabled when no API key is set."""
        supervisor = CloudBrainSupervisor(api_key="", enabled=True)
        assert not supervisor.is_available

    def test_cloud_brain_disabled_explicitly(self):
        """Cloud brain should be disabled when enabled=False even with a key."""
        supervisor = CloudBrainSupervisor(api_key="some-key", enabled=False)
        assert not supervisor.is_available

    def test_cloud_brain_enabled_with_key(self):
        """Cloud brain should be available when enabled with an API key."""
        supervisor = CloudBrainSupervisor(api_key="some-key", enabled=True)
        assert supervisor.is_available


# ── PII Scrubbing Tests ───────────────────────────────────────────────

class TestPIIScrubbing:
    def test_scrubs_windows_paths(self, supervisor_enabled):
        """Absolute Windows paths should be reduced to filenames."""
        proposal = {
            "title": "Fix config loading",
            "description": "The file C:\\Users\\Sam Deiter\\Documents\\project\\config.py has a bug.",
            "files_affected": ["backend/config.py"],
        }
        scrubbed = supervisor_enabled._scrub_pii(proposal)
        assert "Sam Deiter" not in scrubbed["description"]
        assert "C:\\Users" not in scrubbed["description"]

    def test_scrubs_unix_paths(self, supervisor_enabled):
        """Absolute Unix paths should be reduced to filenames."""
        proposal = {
            "title": "Fix permissions",
            "description": "Check /home/samdeiter/projects/localmind/server.py for issues.",
            "files_affected": ["server.py"],
        }
        scrubbed = supervisor_enabled._scrub_pii(proposal)
        assert "samdeiter" not in scrubbed["description"]
        assert "/home/" not in scrubbed["description"]

    def test_scrubs_email_addresses(self, supervisor_enabled):
        """Email addresses should be redacted."""
        proposal = {
            "title": "Update contact info",
            "description": "Send alerts to admin@localmind.dev when errors occur.",
            "files_affected": ["backend/alerts.py"],
        }
        scrubbed = supervisor_enabled._scrub_pii(proposal)
        assert "admin@localmind.dev" not in scrubbed["description"]
        assert "[EMAIL]" in scrubbed["description"]

    def test_scrubs_ip_addresses(self, supervisor_enabled):
        """IP addresses should be redacted."""
        proposal = {
            "title": "Fix CORS",
            "description": "Allow requests from 192.168.1.100 in dev mode.",
            "files_affected": ["backend/server.py"],
        }
        scrubbed = supervisor_enabled._scrub_pii(proposal)
        assert "192.168.1.100" not in scrubbed["description"]
        assert "[IP_ADDRESS]" in scrubbed["description"]

    def test_scrubs_nested_structures(self, supervisor_enabled):
        """PII scrubbing should work in nested dicts and lists."""
        proposal = {
            "title": "Multi-file fix",
            "description": "Normal description",
            "metadata": {
                "author": "user@example.com",
                "paths": ["C:\\Users\\Admin\\file.txt", "/home/admin/other.py"],
            },
        }
        scrubbed = supervisor_enabled._scrub_pii(proposal)
        assert "user@example.com" not in json.dumps(scrubbed)
        assert "Admin" not in json.dumps(scrubbed)

    def test_does_not_modify_original(self, supervisor_enabled):
        """Scrubbing should not modify the original proposal dict."""
        original_desc = "File at C:\\Users\\Sam\\project\\main.py"
        proposal = {
            "title": "Test",
            "description": original_desc,
            "files_affected": [],
        }
        supervisor_enabled._scrub_pii(proposal)
        assert proposal["description"] == original_desc


# ── Rate Limiting Tests ────────────────────────────────────────────────

class TestRateLimiting:
    def test_not_rate_limited_initially(self, supervisor_enabled):
        """A fresh supervisor should not be rate-limited."""
        assert not supervisor_enabled._is_rate_limited()

    def test_rate_limited_after_max_reviews(self, supervisor_enabled):
        """Should be rate-limited after max_reviews_per_hour reviews."""
        supervisor_enabled._max_reviews_per_hour = 5
        now = time.time()
        supervisor_enabled._review_timestamps = [now - i for i in range(5)]
        assert supervisor_enabled._is_rate_limited()

    def test_old_timestamps_pruned(self, supervisor_enabled):
        """Timestamps older than 1 hour should be pruned."""
        supervisor_enabled._max_reviews_per_hour = 5
        old_time = time.time() - 7200  # 2 hours ago
        supervisor_enabled._review_timestamps = [old_time] * 10
        assert not supervisor_enabled._is_rate_limited()
        # Old timestamps should have been pruned
        assert len(supervisor_enabled._review_timestamps) == 0

    @pytest.mark.asyncio
    async def test_rate_limited_returns_auto_approve(self, supervisor_enabled, sample_proposal):
        """When rate-limited, review should auto-approve with low confidence."""
        supervisor_enabled._max_reviews_per_hour = 0  # Always rate-limited
        review = await supervisor_enabled.review(sample_proposal)
        assert review.approved is True
        assert review.confidence <= 0.2
        assert "rate-limited" in review.reasoning.lower()


# ── Caching Tests ──────────────────────────────────────────────────────

class TestCaching:
    def test_proposal_hash_deterministic(self, supervisor_enabled, sample_proposal):
        """Same proposal should always produce the same hash."""
        h1 = supervisor_enabled._proposal_hash(sample_proposal)
        h2 = supervisor_enabled._proposal_hash(sample_proposal)
        assert h1 == h2

    def test_proposal_hash_different_proposals(self, supervisor_enabled, sample_proposal):
        """Different proposals should produce different hashes."""
        other = {**sample_proposal, "title": "Completely different title"}
        h1 = supervisor_enabled._proposal_hash(sample_proposal)
        h2 = supervisor_enabled._proposal_hash(other)
        assert h1 != h2

    def test_proposal_hash_ignores_non_core_fields(self, supervisor_enabled, sample_proposal):
        """Hash should be based on core fields only (title, category, description, files)."""
        p1 = {**sample_proposal}
        p2 = {**sample_proposal, "effort": "large", "priority": "low", "id": "different"}
        h1 = supervisor_enabled._proposal_hash(p1)
        h2 = supervisor_enabled._proposal_hash(p2)
        assert h1 == h2

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self, supervisor_enabled, sample_proposal):
        """Identical proposals should return the cached review without an API call."""
        cached_review = CloudReview(
            approved=True,
            confidence=0.9,
            reasoning="Cached review",
        )
        p_hash = supervisor_enabled._proposal_hash(sample_proposal)
        supervisor_enabled._cache[p_hash] = cached_review

        review = await supervisor_enabled.review(sample_proposal)
        assert review is cached_review
        assert review.reasoning == "Cached review"


# ── Review Behavior Tests ─────────────────────────────────────────────

class TestReviewBehavior:
    @pytest.mark.asyncio
    async def test_review_auto_approves_when_unavailable(self, supervisor_disabled, sample_proposal):
        """When cloud is unavailable, auto-approve with 0.0 confidence."""
        review = await supervisor_disabled.review(sample_proposal)
        assert review.approved is True
        assert review.confidence == 0.0
        assert "unavailable" in review.reasoning.lower()

    @pytest.mark.asyncio
    async def test_review_auto_approves_on_api_error(self, supervisor_enabled, sample_proposal):
        """When the API call fails, auto-approve with low confidence."""
        with patch.object(
            supervisor_enabled, "_call_gemini", side_effect=Exception("Connection refused")
        ):
            review = await supervisor_enabled.review(sample_proposal)
            assert review.approved is True
            assert review.confidence < 0.2
            assert "error" in review.reasoning.lower()

    @pytest.mark.asyncio
    async def test_successful_review(self, supervisor_enabled, sample_proposal):
        """A successful API call should return the parsed review."""
        mock_review = CloudReview(
            approved=True,
            confidence=0.85,
            reasoning="Well-scoped performance improvement.",
            suggestions=["Consider adding tests"],
        )
        with patch.object(
            supervisor_enabled, "_call_gemini", new_callable=AsyncMock, return_value=mock_review
        ):
            review = await supervisor_enabled.review(sample_proposal)
            assert review.approved is True
            assert review.confidence == 0.85
            assert "performance" in review.reasoning.lower()

    @pytest.mark.asyncio
    async def test_rejected_review(self, supervisor_enabled, sample_proposal):
        """A rejection from the cloud should be passed through."""
        mock_review = CloudReview(
            approved=False,
            confidence=0.9,
            reasoning="Too broad in scope, touches unrelated files.",
        )
        with patch.object(
            supervisor_enabled, "_call_gemini", new_callable=AsyncMock, return_value=mock_review
        ):
            review = await supervisor_enabled.review(sample_proposal)
            assert review.approved is False
            assert review.confidence == 0.9

    @pytest.mark.asyncio
    async def test_review_with_refinement(self, supervisor_enabled, sample_proposal):
        """Cloud can suggest a refined proposal."""
        refined = {**sample_proposal, "title": "Improved: Add retry with circuit breaker"}
        mock_review = CloudReview(
            approved=True,
            confidence=0.8,
            reasoning="Good idea but title could be more specific.",
            refinement=refined,
        )
        with patch.object(
            supervisor_enabled, "_call_gemini", new_callable=AsyncMock, return_value=mock_review
        ):
            review = await supervisor_enabled.review(sample_proposal)
            assert review.refinement is not None
            assert "circuit breaker" in review.refinement["title"]


# ── Response Parsing Tests ─────────────────────────────────────────────

class TestResponseParsing:
    def test_parse_clean_json(self, supervisor_enabled):
        """Parse a clean JSON response."""
        text = json.dumps({
            "approved": True,
            "confidence": 0.9,
            "reasoning": "Looks good.",
            "suggestions": [],
            "refinement": None,
        })
        review = supervisor_enabled._parse_review(text)
        assert review.approved is True
        assert review.confidence == 0.9

    def test_parse_json_with_code_fences(self, supervisor_enabled):
        """Parse JSON wrapped in markdown code fences."""
        text = '```json\n{"approved": false, "confidence": 0.3, "reasoning": "Bad idea."}\n```'
        review = supervisor_enabled._parse_review(text)
        assert review.approved is False
        assert review.confidence == 0.3

    def test_parse_invalid_json_raises(self, supervisor_enabled):
        """Non-JSON response should raise ValueError."""
        with pytest.raises(ValueError, match="Could not parse"):
            supervisor_enabled._parse_review("This is not JSON at all.")

    def test_parse_defaults_for_missing_fields(self, supervisor_enabled):
        """Missing optional fields should get sensible defaults."""
        text = json.dumps({"approved": True})
        review = supervisor_enabled._parse_review(text)
        assert review.approved is True
        assert review.confidence == 0.5  # default
        assert review.suggestions == []
        assert review.refinement is None


# ── Singleton Tests ────────────────────────────────────────────────────

class TestSingleton:
    def test_get_cloud_brain_returns_instance(self):
        """get_cloud_brain() should return a CloudBrainSupervisor."""
        reset_cloud_brain()
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False), \
             patch("backend.config.CLOUD_BRAIN_ENABLED", False), \
             patch("backend.config.CLOUD_BRAIN_MAX_REVIEWS_PER_HOUR", 20):
            brain = get_cloud_brain()
            assert isinstance(brain, CloudBrainSupervisor)
            reset_cloud_brain()

    def test_get_cloud_brain_singleton(self):
        """Repeated calls should return the same instance."""
        reset_cloud_brain()
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False), \
             patch("backend.config.CLOUD_BRAIN_ENABLED", False), \
             patch("backend.config.CLOUD_BRAIN_MAX_REVIEWS_PER_HOUR", 20):
            b1 = get_cloud_brain()
            b2 = get_cloud_brain()
            assert b1 is b2
            reset_cloud_brain()

    def test_reset_cloud_brain(self):
        """reset_cloud_brain() should clear the singleton."""
        reset_cloud_brain()
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False), \
             patch("backend.config.CLOUD_BRAIN_ENABLED", False), \
             patch("backend.config.CLOUD_BRAIN_MAX_REVIEWS_PER_HOUR", 20):
            b1 = get_cloud_brain()
            reset_cloud_brain()
            b2 = get_cloud_brain()
            assert b1 is not b2
            reset_cloud_brain()
