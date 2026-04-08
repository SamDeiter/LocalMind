"""
Google OAuth2 routes for LocalMind — Google Workspace integration.

Supports OAuth flows for Gmail, Google Drive, Sheets, and Slides.

Flow:
1. User clicks "Connect Google" in Settings
2. GET /api/google/auth -> redirects to Google consent screen with all scopes
3. Google redirects back to /api/google/callback
4. Tokens are encrypted and stored in DB (with file fallback)
5. Google tools (Gmail, Drive, Sheets, Slides) use get_credentials() automatically

Setup: Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars,
       OR place credentials.json from Google Cloud Console in ~/.localmind/
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger("localmind.routes.google_auth")

# ---------------------------------------------------------------------------
# Google auth library availability
# ---------------------------------------------------------------------------

try:
    from google.oauth2.credentials import Credentials as GoogleCredentials
    from google.auth.transport.requests import Request as GoogleAuthRequest
    _GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    _GOOGLE_AUTH_AVAILABLE = False
    GoogleCredentials = None  # type: ignore[assignment,misc]
    GoogleAuthRequest = None  # type: ignore[assignment,misc]
    logger.warning(
        "google-auth not installed. Google OAuth will be unavailable. "
        "Install with: pip install google-auth google-auth-oauthlib"
    )

try:
    from google_auth_oauthlib.flow import Flow as OAuthFlow
    _OAUTHLIB_AVAILABLE = True
except ImportError:
    _OAUTHLIB_AVAILABLE = False
    OAuthFlow = None  # type: ignore[assignment,misc]
    if _GOOGLE_AUTH_AVAILABLE:
        logger.warning(
            "google-auth-oauthlib not installed. OAuth flow endpoints will be "
            "unavailable. Install with: pip install google-auth-oauthlib"
        )

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/google", tags=["google"])

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "http://localhost:8000/api/google/callback",
)

CONFIG_DIR = Path.home() / ".localmind"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "google_token.json"

# ---------------------------------------------------------------------------
# Scopes — Google Workspace (Drive, Slides, Sheets, Gmail)
# ---------------------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",       # Files created/opened by app
    "https://www.googleapis.com/auth/presentations",     # Google Slides
    "https://www.googleapis.com/auth/spreadsheets",      # Google Sheets
    "https://www.googleapis.com/auth/gmail.readonly",    # Gmail read
    "https://www.googleapis.com/auth/gmail.send",        # Gmail send
    "https://www.googleapis.com/auth/gmail.modify",      # Gmail modify
]


# ---------------------------------------------------------------------------
# Internal: client config resolution
# ---------------------------------------------------------------------------

def _get_client_config() -> dict:
    """Load OAuth client config from env vars or credentials.json file.

    Returns a dict in Google's "web" client config format, or empty dict
    if no credentials are available.
    """
    # Prefer env vars
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        return {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI],
            }
        }

    # Fall back to credentials.json
    if CREDENTIALS_FILE.exists():
        try:
            data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read credentials.json: %s", exc)
            return {}

        # Google exports as "installed" for desktop apps — convert to "web"
        if "installed" in data:
            installed = data["installed"]
            return {
                "web": {
                    "client_id": installed["client_id"],
                    "client_secret": installed["client_secret"],
                    "auth_uri": installed.get(
                        "auth_uri", "https://accounts.google.com/o/oauth2/auth"
                    ),
                    "token_uri": installed.get(
                        "token_uri", "https://oauth2.googleapis.com/token"
                    ),
                    "redirect_uris": [GOOGLE_REDIRECT_URI],
                }
            }
        return data

    return {}


def _require_google_libs() -> None:
    """Raise HTTPException if google auth libraries are not installed."""
    if not _GOOGLE_AUTH_AVAILABLE or not _OAUTHLIB_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail=(
                "Google auth libraries not installed. Run: "
                "pip install google-auth google-auth-oauthlib"
            ),
        )


# ---------------------------------------------------------------------------
# Token management — DB-backed with file fallback
# ---------------------------------------------------------------------------

def _get_db_provider_manager():
    """Lazily import and return a ProviderConfigManager, or None."""
    try:
        from backend.core.providers import ProviderConfigManager
        return ProviderConfigManager()
    except Exception:
        return None


def _default_workspace_id() -> str:
    """Return the default workspace ID for single-user/local mode."""
    return "default"


def store_credentials(
    credentials: "GoogleCredentials",
    user_id: Optional[str] = None,
) -> None:
    """Encrypt and store Google OAuth credentials.

    Storage strategy:
    1. Always write to the file-based token (backwards compat, single-user).
    2. Also persist to DB if the provider manager is available.

    Args:
        credentials: google.oauth2.credentials.Credentials object.
        user_id: Optional user ID. Defaults to "local" for single-user mode.
    """
    if not _GOOGLE_AUTH_AVAILABLE:
        logger.error("Cannot store credentials: google-auth not installed")
        return

    user_id = user_id or "local"
    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes or []),
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }

    # 1. File-based storage
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
        logger.info("Google OAuth token saved to %s", TOKEN_FILE)
    except OSError as exc:
        logger.error("Failed to write token file: %s", exc)

    # 2. DB-backed encrypted storage
    mgr = _get_db_provider_manager()
    if mgr is not None:
        try:
            expires_at = credentials.expiry.isoformat() if credentials.expiry else None
            mgr.store_oauth_token(
                workspace_id=_default_workspace_id(),
                user_id=user_id,
                provider="google",
                token_json=token_data,
                scopes=list(credentials.scopes or SCOPES),
                expires_at=expires_at,
            )
            logger.info("Google OAuth token saved to DB for user=%s", user_id)
        except Exception as exc:
            logger.warning("DB token store failed (file fallback OK): %s", exc)


def get_credentials(
    user_id: Optional[str] = None,
) -> Optional["GoogleCredentials"]:
    """Load stored Google OAuth credentials, auto-refreshing if expired.

    Resolution order:
    1. DB (encrypted) — preferred.
    2. File-based token — fallback for single-user/local.

    Returns None if no valid credentials are found.

    Args:
        user_id: Optional user ID. Defaults to "local" for single-user mode.
    """
    if not _GOOGLE_AUTH_AVAILABLE:
        return None

    user_id = user_id or "local"
    token_data: Optional[dict] = None

    # Try DB first
    mgr = _get_db_provider_manager()
    if mgr is not None:
        try:
            token_data = mgr.get_oauth_token(
                workspace_id=_default_workspace_id(),
                user_id=user_id,
                provider="google",
            )
        except Exception as exc:
            logger.debug("DB token lookup failed: %s", exc)

    # Fall back to file
    if token_data is None and TOKEN_FILE.exists():
        try:
            token_data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read token file: %s", exc)

    if token_data is None:
        return None

    # Build Credentials object
    try:
        creds = GoogleCredentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id", GOOGLE_CLIENT_ID),
            client_secret=token_data.get("client_secret", GOOGLE_CLIENT_SECRET),
            scopes=token_data.get("scopes", SCOPES),
        )
    except Exception as exc:
        logger.error("Failed to construct Credentials: %s", exc)
        return None

    # Auto-refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            store_credentials(creds, user_id=user_id)
            logger.info("Google OAuth token auto-refreshed for user=%s", user_id)
        except Exception as exc:
            logger.warning("Token refresh failed: %s", exc)
            return None

    return creds if (creds.valid or creds.refresh_token) else None


def has_scope(scope: str, user_id: Optional[str] = None) -> bool:
    """Check if stored credentials include a specific scope.

    Args:
        scope: Full scope URL, e.g. "https://www.googleapis.com/auth/gmail.send".
        user_id: Optional user ID. Defaults to "local".

    Returns:
        True if the scope is present in the stored credentials.
    """
    creds = get_credentials(user_id=user_id)
    if creds is None:
        return False
    granted = set(creds.scopes or [])
    return scope in granted


def needs_reauth(
    user_id: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """Check if the user needs to re-authenticate due to missing scopes.

    Compares the stored credential scopes against the full SCOPES list.

    Args:
        user_id: Optional user ID. Defaults to "local".

    Returns:
        Tuple of (needs_reauth: bool, missing_scopes: list[str]).
        If no credentials exist at all, returns (True, SCOPES).
    """
    creds = get_credentials(user_id=user_id)
    if creds is None:
        return True, list(SCOPES)

    granted = set(creds.scopes or [])
    required = set(SCOPES)
    missing = sorted(required - granted)
    return (len(missing) > 0, missing)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/auth")
async def google_auth_start():
    """Initiate Google OAuth flow.

    Generates an authorization URL with all SCOPES and redirects the user
    to Google's consent screen.
    """
    _require_google_libs()

    client_config = _get_client_config()
    if not client_config:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No Google credentials found. Set GOOGLE_CLIENT_ID and "
                f"GOOGLE_CLIENT_SECRET env vars, or place credentials.json "
                f"in {CONFIG_DIR}"
            ),
        )

    flow = OAuthFlow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    logger.info("Redirecting to Google OAuth consent: %s...", auth_url[:80])
    return RedirectResponse(auth_url)


@router.get("/callback")
async def google_auth_callback(request: Request):
    """Handle OAuth callback from Google.

    Exchanges the authorization code for tokens, encrypts them, and stores
    them in both the DB and the file system.
    """
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        logger.warning("Google OAuth returned error: %s", error)
        return HTMLResponse(
            content=_result_page(
                "Authorization Failed",
                f"Google returned an error: {error}",
                success=False,
            ),
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            content=_result_page(
                "Authorization Failed",
                "No authorization code received.",
                success=False,
            ),
            status_code=400,
        )

    _require_google_libs()

    client_config = _get_client_config()
    if not client_config:
        return HTMLResponse(
            content=_result_page(
                "Configuration Error",
                "Google credentials not found.",
                success=False,
            ),
            status_code=500,
        )

    try:
        flow = OAuthFlow.from_client_config(
            client_config, scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI
        )
        flow.fetch_token(code=code)
        creds = flow.credentials

        store_credentials(creds)

        granted_scopes = list(creds.scopes or [])
        logger.info(
            "Google OAuth completed. Granted scopes: %s",
            ", ".join(granted_scopes),
        )

        return HTMLResponse(
            content=_result_page(
                "Google Connected",
                "LocalMind can now access your Google Workspace (Gmail, Drive, "
                "Sheets, Slides). You can close this tab and return to LocalMind.",
                success=True,
            )
        )
    except Exception as exc:
        logger.exception("Google OAuth callback failed")
        return HTMLResponse(
            content=_result_page(
                "Authorization Failed",
                str(exc),
                success=False,
            ),
            status_code=500,
        )


@router.get("/status")
async def google_auth_status():
    """Check if user has valid Google OAuth tokens.

    Returns:
        JSON with authenticated status, granted scopes, and expiry info.
    """
    has_client_credentials = bool(_get_client_config())

    creds = get_credentials()
    if creds is None:
        return {
            "authenticated": False,
            "has_client_credentials": has_client_credentials,
            "scopes": [],
            "expires_at": None,
            "needs_reauth": True,
            "missing_scopes": list(SCOPES),
        }

    is_valid = creds.valid or (creds.expired and creds.refresh_token is not None)
    granted_scopes = list(creds.scopes or [])
    expires_at = creds.expiry.isoformat() if creds.expiry else None

    reauth_needed, missing = needs_reauth()

    return {
        "authenticated": is_valid,
        "has_client_credentials": has_client_credentials,
        "scopes": granted_scopes,
        "expires_at": expires_at,
        "needs_reauth": reauth_needed,
        "missing_scopes": missing,
    }


@router.post("/revoke")
async def google_revoke():
    """Revoke Google OAuth tokens and remove stored credentials.

    Calls Google's revoke endpoint if possible, then removes local storage.
    """
    creds = get_credentials()
    revoked_remote = False

    if creds is not None and creds.token:
        try:
            import httpx
            resp = httpx.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": creds.token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0,
            )
            revoked_remote = resp.status_code == 200
            if revoked_remote:
                logger.info("Google token revoked remotely")
            else:
                logger.warning(
                    "Google revoke returned status %d: %s",
                    resp.status_code, resp.text[:200],
                )
        except ImportError:
            # httpx not available, try urllib
            try:
                import urllib.request
                import urllib.parse
                data = urllib.parse.urlencode({"token": creds.token}).encode()
                req = urllib.request.Request(
                    "https://oauth2.googleapis.com/revoke",
                    data=data,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    revoked_remote = resp.status == 200
            except Exception as exc:
                logger.warning("Remote revoke via urllib failed: %s", exc)
        except Exception as exc:
            logger.warning("Remote token revoke failed: %s", exc)

    # Remove file-based token
    if TOKEN_FILE.exists():
        try:
            TOKEN_FILE.unlink()
            logger.info("Google token file removed")
        except OSError as exc:
            logger.error("Failed to remove token file: %s", exc)

    # Remove DB token
    mgr = _get_db_provider_manager()
    if mgr is not None:
        try:
            from backend.core.providers import _get_conn
            with _get_conn() as conn:
                conn.execute(
                    "DELETE FROM oauth_credentials WHERE provider = 'google' "
                    "AND workspace_id = ? AND user_id = ?",
                    (_default_workspace_id(), "local"),
                )
            logger.info("Google token removed from DB")
        except Exception as exc:
            logger.warning("DB token removal failed: %s", exc)

    return {
        "ok": True,
        "revoked_remote": revoked_remote,
        "message": "Google disconnected",
    }


@router.post("/refresh")
async def google_refresh():
    """Force-refresh Google OAuth tokens.

    Useful for obtaining a fresh access token without waiting for expiry.
    """
    if not _GOOGLE_AUTH_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="Google auth libraries not installed.",
        )

    creds = get_credentials()
    if creds is None:
        raise HTTPException(
            status_code=400,
            detail="No Google credentials found. Connect Google first.",
        )

    if not creds.refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh token available. Re-authorize Google to get one.",
        )

    try:
        creds.refresh(GoogleAuthRequest())
        store_credentials(creds)
        logger.info("Google OAuth token force-refreshed")
        return {
            "ok": True,
            "expires_at": creds.expiry.isoformat() if creds.expiry else None,
            "scopes": list(creds.scopes or []),
        }
    except Exception as exc:
        logger.exception("Token refresh failed")
        raise HTTPException(status_code=500, detail=f"Refresh failed: {exc}")


# ---------------------------------------------------------------------------
# Legacy compat: old /api/auth/google routes redirect to new /api/google/*
# ---------------------------------------------------------------------------

_legacy_router = APIRouter(prefix="/api/auth", tags=["google-legacy"])


@_legacy_router.get("/google")
async def legacy_google_auth_start():
    """Legacy redirect: /api/auth/google -> /api/google/auth."""
    return RedirectResponse("/api/google/auth", status_code=307)


@_legacy_router.get("/google/callback")
async def legacy_google_callback(request: Request):
    """Legacy redirect: /api/auth/google/callback -> /api/google/callback."""
    qs = str(request.query_params)
    url = f"/api/google/callback?{qs}" if qs else "/api/google/callback"
    return RedirectResponse(url, status_code=307)


@_legacy_router.get("/google/status")
async def legacy_google_status():
    """Legacy redirect: /api/auth/google/status -> /api/google/status."""
    return RedirectResponse("/api/google/status", status_code=307)


@_legacy_router.post("/google/disconnect")
async def legacy_google_disconnect():
    """Legacy redirect: /api/auth/google/disconnect -> /api/google/revoke."""
    return RedirectResponse("/api/google/revoke", status_code=307)


# ---------------------------------------------------------------------------
# HTML result page (shown to user after OAuth callback)
# ---------------------------------------------------------------------------

def _result_page(title: str, message: str, success: bool = True) -> str:
    """Generate a styled HTML result page for the OAuth callback redirect."""
    color = "#4ade80" if success else "#f87171"
    icon = "check_circle" if success else "error"
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>{title} — LocalMind</title>
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined" rel="stylesheet">
    <style>
        body {{ font-family: system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0;
               display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
        .card {{ background: #16213e; border-radius: 16px; padding: 48px; text-align: center;
                 max-width: 400px; border: 1px solid #2a2a4a; }}
        .icon {{ font-size: 64px; color: {color}; }}
        h1 {{ margin: 16px 0 8px; font-size: 24px; }}
        p {{ color: #a0a0b0; line-height: 1.6; }}
        .btn {{ display: inline-block; margin-top: 24px; padding: 12px 32px; background: #6366f1;
                color: white; border-radius: 8px; text-decoration: none; font-weight: 600; }}
        .btn:hover {{ background: #4f46e5; }}
    </style>
</head>
<body>
    <div class="card">
        <span class="material-symbols-outlined icon">{icon}</span>
        <h1>{title}</h1>
        <p>{message}</p>
        <a href="/" class="btn">Back to LocalMind</a>
    </div>
</body>
</html>"""
