"""
LocalMind Security — Tailscale Authentication Middleware
=========================================================
Trusts the ``Tailscale-User-Login`` header as an authenticated identity
**only** when the request originates from the Tailscale CGNAT range
(100.64.0.0/10).  This prevents header-spoofing from non-Tailscale clients.

Security model
--------------
- **Default: DENY.**  TAILSCALE_AUTH_ENABLED must be explicitly set to
  ``true`` for this middleware to activate.  When disabled the middleware
  is a transparent pass-through.
- Both conditions must hold for Tailscale auth to apply:
    1. Source IP is within 100.64.0.0/10 (Tailscale CGNAT).
    2. The ``Tailscale-User-Login`` header is present and non-empty.
- If only one condition is met (e.g. header present but IP outside CGNAT),
  the request is **not** treated as Tailscale-authenticated and falls through
  to normal API-key auth.
- Authenticated Tailscale users are assigned the ``operator`` role by default.
  Override with TAILSCALE_DEFAULT_ROLE.

Environment variables
---------------------
TAILSCALE_AUTH_ENABLED   Enable Tailscale auth (default ``false``).
TAILSCALE_DEFAULT_ROLE   Role for Tailscale-authed users (default ``operator``).
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger("localmind.security.tailscale")

# ---------------------------------------------------------------------------
# Config — opt-in only
# ---------------------------------------------------------------------------

TAILSCALE_AUTH_ENABLED: bool = (
    os.getenv("TAILSCALE_AUTH_ENABLED", "false").lower() == "true"
)
TAILSCALE_DEFAULT_ROLE: str = os.getenv("TAILSCALE_DEFAULT_ROLE", "operator")

# ---------------------------------------------------------------------------
# Tailscale CGNAT network (100.64.0.0/10)
#
# Every Tailscale node is assigned an IP in this range.  If the source IP
# falls within it we can reasonably trust that the request transited the
# Tailscale mesh and the identity headers were injected by tailscaled.
# ---------------------------------------------------------------------------

_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")

# Header injected by Tailscale when running in HTTPS / proxy mode.
_TS_USER_HEADER = "Tailscale-User-Login"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _ip_in_tailscale_range(ip_str: str) -> bool:
    """Return ``True`` if *ip_str* is inside the Tailscale CGNAT block.

    Returns ``False`` for any unparseable address rather than raising.
    """
    try:
        return ipaddress.ip_address(ip_str) in _TAILSCALE_CGNAT
    except (ValueError, TypeError):
        return False


def is_tailscale_request(request: Request) -> bool:
    """Check whether *request* is a valid Tailscale-authenticated request.

    Both conditions must be true:
    1. The source IP is within 100.64.0.0/10.
    2. The ``Tailscale-User-Login`` header is present and non-empty.

    This function is safe to call regardless of whether Tailscale auth is
    enabled — it only inspects the request, it never mutates state.
    """
    client_ip = request.client.host if request.client else None
    if not client_ip or not _ip_in_tailscale_range(client_ip):
        return False
    ts_user = request.headers.get(_TS_USER_HEADER, "").strip()
    return bool(ts_user)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class TailscaleAuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that authenticates Tailscale-proxied requests.

    When enabled and a request satisfies both the CGNAT-IP and header checks,
    the middleware sets ``request.state.user`` with the Tailscale identity and
    marks ``request.state.tailscale_authenticated = True`` so that downstream
    auth middleware can skip API-key validation.

    When disabled (the default), every request passes through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        if TAILSCALE_AUTH_ENABLED:
            logger.info(
                "Tailscale auth middleware ENABLED (default role=%s)",
                TAILSCALE_DEFAULT_ROLE,
            )
        else:
            logger.debug("Tailscale auth middleware disabled (pass-through)")

    async def dispatch(self, request: Request, call_next):
        # Fast path — feature flag off, do nothing.
        if not TAILSCALE_AUTH_ENABLED:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # --- Check both conditions: CGNAT IP + header ---
        ip_ok = _ip_in_tailscale_range(client_ip)
        ts_user: Optional[str] = request.headers.get(_TS_USER_HEADER, "").strip() or None

        if ip_ok and ts_user:
            # Both conditions met — trust Tailscale identity.
            request.state.user = {
                "user_id": ts_user,
                "role": TAILSCALE_DEFAULT_ROLE,
                "key_id": f"tailscale:{ts_user}",
            }
            request.state.tailscale_authenticated = True
            logger.info(
                "Tailscale auth OK: user=%s role=%s ip=%s",
                ts_user, TAILSCALE_DEFAULT_ROLE, client_ip,
            )
        else:
            # At least one condition failed — fall through to normal auth.
            request.state.tailscale_authenticated = False

            # Log a warning if the header is present but the IP is wrong —
            # this could indicate a spoofing attempt.
            if ts_user and not ip_ok:
                logger.warning(
                    "DENIED: Tailscale-User-Login header present (%s) but "
                    "source IP %s is NOT in CGNAT range — possible spoofing",
                    ts_user, client_ip,
                )
            elif ip_ok and not ts_user:
                logger.debug(
                    "Tailscale CGNAT IP %s but no %s header — "
                    "falling through to normal auth",
                    client_ip, _TS_USER_HEADER,
                )

        return await call_next(request)
