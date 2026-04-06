"""
rate_limit.py — Lightweight in-memory rate limiting middleware for FastAPI.

Uses a sliding-window counter per client IP. Different rate tiers apply
depending on the request path:

  - Chat endpoints (/api/chat):  stricter limit (LLM calls are expensive)
  - Health/status endpoints:     exempt (monitoring should always work)
  - SSE/streaming endpoints:     exempt (long-lived connections)
  - Everything else:             general API limit

This is intentionally simple — no Redis, no persistence.  It exists to
guard against accidental abuse (e.g., a frontend bug hammering the API),
not to enforce billing quotas.
"""

import logging
import time
from collections import defaultdict
from typing import Dict, List, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.config import (
    RATE_LIMIT_CHAT_RPM,
    RATE_LIMIT_GENERAL_RPM,
    RATE_LIMIT_WINDOW_SECONDS,
    RATE_LIMIT_EXEMPT_PATHS,
    RATE_LIMIT_CHAT_PATHS,
    RATE_LIMIT_ENABLED,
)

logger = logging.getLogger("localmind.middleware.rate_limit")


class _SlidingWindow:
    """Per-IP sliding-window request counter.

    Stores a list of request timestamps and prunes entries older than
    the window on every check. Memory-safe because the list length is
    bounded by the rate limit itself — once a client is throttled they
    stop accumulating entries.
    """

    def __init__(self) -> None:
        # ip -> list of request timestamps (epoch seconds)
        self._hits: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window: int) -> Tuple[bool, int, float]:
        """Check whether *key* may proceed.

        Returns:
            (allowed, remaining, retry_after)
            - allowed:     True if the request should proceed
            - remaining:   how many requests are left in the current window
            - retry_after: seconds until the oldest entry expires (0 if allowed)
        """
        now = time.monotonic()
        cutoff = now - window
        timestamps = self._hits[key]

        # Prune expired entries
        self._hits[key] = timestamps = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= max_requests:
            # The oldest entry still in the window determines when a slot opens
            retry_after = round(timestamps[0] - cutoff, 1)
            return False, 0, max(retry_after, 0.1)

        timestamps.append(now)
        remaining = max_requests - len(timestamps)
        return True, remaining, 0.0

    def cleanup(self, window: int) -> None:
        """Remove IPs with no recent activity to prevent unbounded growth."""
        now = time.monotonic()
        cutoff = now - window
        stale = [ip for ip, ts in self._hits.items() if not ts or ts[-1] <= cutoff]
        for ip in stale:
            del self._hits[ip]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces per-IP rate limits."""

    def __init__(self, app, **kwargs):
        super().__init__(app)
        self._window = _SlidingWindow()
        self._cleanup_counter = 0
        logger.info(
            "Rate limiter active  chat=%d/min  general=%d/min  window=%ds",
            RATE_LIMIT_CHAT_RPM,
            RATE_LIMIT_GENERAL_RPM,
            RATE_LIMIT_WINDOW_SECONDS,
        )

    # ------------------------------------------------------------------
    # Path classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _client_ip(request: Request) -> str:
        """Best-effort client IP extraction."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    @staticmethod
    def _is_exempt(path: str) -> bool:
        """Return True for paths that should never be rate-limited."""
        for exempt in RATE_LIMIT_EXEMPT_PATHS:
            if path == exempt or path.startswith(exempt + "/"):
                return True
        return False

    @staticmethod
    def _is_chat(path: str) -> bool:
        """Return True for chat/LLM paths that get the stricter limit."""
        for chat_path in RATE_LIMIT_CHAT_PATHS:
            if path == chat_path or path.startswith(chat_path + "/"):
                return True
        return False

    # ------------------------------------------------------------------
    # Middleware entry point
    # ------------------------------------------------------------------

    async def dispatch(self, request: Request, call_next):
        if not RATE_LIMIT_ENABLED:
            return await call_next(request)

        path = request.url.path

        # Skip non-API paths (static files, frontend HTML)
        if not path.startswith("/api"):
            return await call_next(request)

        # Exempt paths pass through unconditionally
        if self._is_exempt(path):
            return await call_next(request)

        ip = self._client_ip(request)
        window = RATE_LIMIT_WINDOW_SECONDS

        if self._is_chat(path):
            limit = RATE_LIMIT_CHAT_RPM
            tier = "chat"
        else:
            limit = RATE_LIMIT_GENERAL_RPM
            tier = "general"

        # Use a composite key so chat and general limits are independent
        key = f"{ip}:{tier}"
        allowed, remaining, retry_after = self._window.is_allowed(key, limit, window)

        if not allowed:
            logger.warning(
                "Rate limit hit  ip=%s  tier=%s  path=%s  retry_after=%.1fs",
                ip, tier, path, retry_after,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too many requests",
                    "detail": f"Rate limit exceeded for {tier} tier ({limit} req/{window}s). "
                              f"Retry after {retry_after}s.",
                },
                headers={
                    "Retry-After": str(int(retry_after) + 1),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + int(retry_after) + 1),
                },
            )

        # Periodically clean up stale entries (every ~100 requests)
        self._cleanup_counter += 1
        if self._cleanup_counter >= 100:
            self._cleanup_counter = 0
            self._window.cleanup(window)

        response = await call_next(request)

        # Attach informational rate-limit headers to successful responses
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
