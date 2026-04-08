"""
Cloud Brain Supervisor -- optional cloud AI gate in the reflection pipeline.

When enabled, sends validated proposals to a cloud model (Gemini) for
a second opinion before the local meta-critic. This implements the
"cloud AI guides local AI" pattern from the feature backlog.

Design:
  - Optional: gracefully skips if no API key or if disabled
  - PII-scrubbed: strips file paths, user names before sending to cloud
  - Rate-limited: max N cloud reviews per hour to control cost
  - Cached: identical proposals (by hash) skip re-review
"""

import copy
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from backend.gemini_client import scrub_pii

logger = logging.getLogger("localmind.autonomy.cloud_brain")

# Protected files that should never be modified by proposals
PROTECTED_FILES = frozenset({
    ".env", ".env.local", ".env.production",
    "backend/config.py",
    "backend/security/prompt_guard.py",
    "backend/security/redact.py",
    "backend/security/paths.py",
})

# Regex for absolute paths (Windows and Unix)
# Windows paths may contain spaces (e.g. C:\Users\Sam Deiter\...) so we
# match until a quote, period+space, or end of string.
_ABS_WIN_PATH = re.compile(r'[A-Z]:\\(?:[^"\'.\n]+|\.(?!\s))+', re.IGNORECASE)
_ABS_UNIX_PATH = re.compile(r'/(?:home|Users|opt|var|etc|tmp)/[^\s"\']+')
_USERNAME_IN_PATH = re.compile(r'(?<=\\Users\\)[^\\]+|(?<=/home/)[^/\s]+|(?<=/Users/)[^/\s]+')

# Gemini API endpoint for generateContent
_GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_REVIEW_SYSTEM_PROMPT = """\
You are a senior code reviewer evaluating an AI-generated improvement proposal \
for a local-first AI assistant called LocalMind. Evaluate the proposal on four axes:

1. **Safety**: Does the proposal modify any security-critical, config, or \
environment files? Would it introduce vulnerabilities or break existing security?
2. **Quality**: Is the proposed change well-reasoned? Does the description make \
technical sense? Are the affected files plausible?
3. **Scope**: Is the change appropriately scoped? Not too broad (touching many \
unrelated files) and not trivially small (busywork).
4. **Novelty**: Is this a genuine improvement, or is it redundant busywork that \
adds complexity without value?

Respond with ONLY valid JSON (no markdown fences):
{
  "approved": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "one paragraph explaining your decision",
  "suggestions": ["optional list of improvements"],
  "refinement": null or { ...refined proposal with same keys... }
}
"""


@dataclass
class CloudReview:
    """Result of a cloud brain review."""
    approved: bool
    confidence: float  # 0.0-1.0
    reasoning: str
    suggestions: list[str] = field(default_factory=list)
    refinement: Optional[dict] = None  # refined proposal if cloud suggests changes


class CloudBrainSupervisor:
    """Cloud-based proposal review gate.

    Sends validated proposals to a cloud LLM (Gemini) for a second opinion.
    Fully optional -- if no API key is configured or the feature is disabled,
    all reviews auto-approve with low confidence so the local meta-critic
    still has the final say.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "gemini-2.0-flash",
        max_reviews_per_hour: int = 20,
        enabled: bool = True,
    ):
        self._api_key = api_key
        self._model = model
        self._max_reviews_per_hour = max_reviews_per_hour
        self._enabled = enabled and bool(api_key)
        self._review_timestamps: list[float] = []
        self._cache: dict[str, CloudReview] = {}  # hash -> review

    @property
    def is_available(self) -> bool:
        """Check if cloud brain is configured and available."""
        return self._enabled and bool(self._api_key)

    # ── PII Scrubbing ──────────────────────────────────────────────

    def _scrub_pii(self, proposal: dict) -> dict:
        """Remove PII before sending to cloud.

        Deep-copies the proposal and sanitizes:
        - Absolute file paths -> relative paths
        - User names embedded in paths
        - Emails, phone numbers, IPs, API keys (via gemini_client.scrub_pii)
        """
        scrubbed = copy.deepcopy(proposal)

        def _clean_value(value):
            if isinstance(value, str):
                # First, replace absolute paths with relative
                cleaned = _ABS_WIN_PATH.sub(
                    lambda m: m.group(0).split("\\")[-1], value
                )
                cleaned = _ABS_UNIX_PATH.sub(
                    lambda m: m.group(0).split("/")[-1], cleaned
                )
                # Strip usernames that might remain
                cleaned = _USERNAME_IN_PATH.sub("[USER]", cleaned)
                # Delegate to the existing PII scrubber for emails, IPs, etc.
                cleaned = scrub_pii(cleaned)
                return cleaned
            elif isinstance(value, list):
                return [_clean_value(item) for item in value]
            elif isinstance(value, dict):
                return {k: _clean_value(v) for k, v in value.items()}
            return value

        return _clean_value(scrubbed)

    # ── Rate Limiting ──────────────────────────────────────────────

    def _is_rate_limited(self) -> bool:
        """Check if we have exceeded the hourly review limit."""
        now = time.time()
        cutoff = now - 3600  # one hour ago
        # Prune old timestamps
        self._review_timestamps = [
            ts for ts in self._review_timestamps if ts > cutoff
        ]
        return len(self._review_timestamps) >= self._max_reviews_per_hour

    def _record_review(self) -> None:
        """Record that a review was performed (for rate limiting)."""
        self._review_timestamps.append(time.time())

    # ── Caching ────────────────────────────────────────────────────

    def _proposal_hash(self, proposal: dict) -> str:
        """Deterministic hash for cache lookup.

        Uses a canonical JSON serialization of the proposal's core fields
        so that cosmetic changes (whitespace, key order) don't bust the cache.
        """
        key_fields = {
            "title": proposal.get("title", ""),
            "category": proposal.get("category", ""),
            "description": proposal.get("description", ""),
            "files_affected": sorted(proposal.get("files_affected", [])),
        }
        canonical = json.dumps(key_fields, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    # ── Core Review Logic ──────────────────────────────────────────

    async def review(
        self, proposal: dict, context: dict | None = None
    ) -> CloudReview:
        """Review a proposal via cloud AI.

        Returns CloudReview with approval decision. If cloud is unavailable
        or rate-limited, returns an auto-approved review with low confidence
        so the local meta-critic still runs.
        """
        # Guard: not available
        if not self.is_available:
            return CloudReview(
                approved=True,
                confidence=0.0,
                reasoning="Cloud brain unavailable (no API key or disabled). Auto-approved.",
            )

        # Guard: rate limited
        if self._is_rate_limited():
            logger.info(
                "Cloud brain rate-limited (%d/%d reviews this hour). Auto-approving.",
                len(self._review_timestamps),
                self._max_reviews_per_hour,
            )
            return CloudReview(
                approved=True,
                confidence=0.1,
                reasoning="Cloud brain rate-limited. Auto-approved to avoid blocking.",
            )

        # Check cache
        p_hash = self._proposal_hash(proposal)
        if p_hash in self._cache:
            logger.debug("Cloud brain cache hit for %s", p_hash)
            return self._cache[p_hash]

        # Scrub PII
        clean_proposal = self._scrub_pii(proposal)

        # Build the prompt
        files_context = ""
        if context and context.get("files"):
            files_context = (
                "\n\nAvailable project files (subset):\n"
                + "\n".join(f"  - {f}" for f in sorted(context["files"])[:30])
            )

        # Check for protected files in proposal
        affected = set(proposal.get("files_affected", []))
        protected_hit = affected & PROTECTED_FILES
        safety_warning = ""
        if protected_hit:
            safety_warning = (
                f"\n\nWARNING: This proposal modifies protected files: "
                f"{', '.join(protected_hit)}. Treat with extra scrutiny."
            )

        user_prompt = (
            f"Evaluate this proposal:{safety_warning}\n\n"
            f"```json\n{json.dumps(clean_proposal, indent=2)}\n```"
            f"{files_context}"
        )

        # Call Gemini API via httpx
        try:
            review = await self._call_gemini(user_prompt)
            self._record_review()
            self._cache[p_hash] = review
            return review
        except Exception as exc:
            logger.warning("Cloud brain API call failed: %s. Auto-approving.", exc)
            return CloudReview(
                approved=True,
                confidence=0.05,
                reasoning=f"Cloud brain API error ({type(exc).__name__}). Auto-approved.",
            )

    async def _call_gemini(self, user_prompt: str) -> CloudReview:
        """Make the actual Gemini API call and parse the response."""
        url = _GEMINI_API_URL.format(model=self._model)

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": _REVIEW_SYSTEM_PROMPT + "\n\n" + user_prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1024,
            },
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            resp = await client.post(
                url,
                params={"key": self._api_key},
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()

        data = resp.json()

        # Extract text from Gemini response
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Unexpected Gemini response structure: {exc}") from exc

        # Parse the JSON review
        return self._parse_review(text)

    def _parse_review(self, text: str) -> CloudReview:
        """Parse the cloud model's JSON response into a CloudReview."""
        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (with optional language tag)
            cleaned = re.sub(r'^```[a-zA-Z]*\n?', '', cleaned)
            cleaned = re.sub(r'\n?```\s*$', '', cleaned)
            cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning("Cloud brain returned non-JSON: %s...", text[:200])
            raise ValueError(f"Could not parse cloud review JSON: {exc}") from exc

        return CloudReview(
            approved=bool(parsed.get("approved", True)),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=str(parsed.get("reasoning", "No reasoning provided.")),
            suggestions=list(parsed.get("suggestions", [])),
            refinement=parsed.get("refinement"),
        )


# ── Module-level singleton ─────────────────────────────────────────────

_cloud_brain: Optional[CloudBrainSupervisor] = None


def get_cloud_brain() -> CloudBrainSupervisor:
    """Return the module-level CloudBrainSupervisor singleton.

    Lazily initialized from environment/config on first call.
    """
    global _cloud_brain
    if _cloud_brain is None:
        import os
        from backend.config import (
            CLOUD_BRAIN_ENABLED,
            CLOUD_BRAIN_MAX_REVIEWS_PER_HOUR,
        )

        api_key = os.environ.get("GEMINI_API_KEY", "")
        _cloud_brain = CloudBrainSupervisor(
            api_key=api_key,
            model="gemini-2.0-flash",
            max_reviews_per_hour=CLOUD_BRAIN_MAX_REVIEWS_PER_HOUR,
            enabled=CLOUD_BRAIN_ENABLED,
        )
    return _cloud_brain


def reset_cloud_brain() -> None:
    """Reset the singleton (useful for testing)."""
    global _cloud_brain
    _cloud_brain = None
