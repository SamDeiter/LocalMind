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
# Legacy single-profile token file. Treated as the "personal" profile if it
# exists at startup and no per-profile file has been written yet.
TOKEN_FILE = CONFIG_DIR / "google_token.json"
ACTIVE_PROFILE_FILE = CONFIG_DIR / "google_active_profile.txt"

VALID_PROFILES = ("personal", "work")
DEFAULT_PROFILE = "personal"


def _normalize_profile(profile: Optional[str]) -> str:
    """Coerce a profile string to one of the valid profiles, defaulting safely."""
    p = (profile or "").strip().lower()
    if p in VALID_PROFILES:
        return p
    return DEFAULT_PROFILE


def _token_file_for(profile: str) -> Path:
    """Per-profile token file path."""
    return CONFIG_DIR / f"google_token_{profile}.json"


def get_active_profile() -> str:
    """Return the currently active Google profile (personal | work)."""
    try:
        if ACTIVE_PROFILE_FILE.exists():
            v = ACTIVE_PROFILE_FILE.read_text(encoding="utf-8").strip().lower()
            if v in VALID_PROFILES:
                return v
    except OSError:
        pass
    return DEFAULT_PROFILE


def set_active_profile(profile: str) -> str:
    """Persist the active Google profile."""
    p = _normalize_profile(profile)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ACTIVE_PROFILE_FILE.write_text(p, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not persist active Google profile: %s", exc)
    return p

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
    profile: Optional[str] = None,
) -> None:
    """Encrypt and store Google OAuth credentials for a given profile.

    Storage strategy:
    1. Per-profile token file (backwards compat for the "personal" file).
    2. DB row keyed by profile name (mapped onto user_id for the providers table).

    Args:
        credentials: google.oauth2.credentials.Credentials object.
        user_id: Deprecated alias for `profile`. Ignored if profile is set.
        profile: "personal" or "work". Defaults to the currently active profile.
    """
    if not _GOOGLE_AUTH_AVAILABLE:
        logger.error("Cannot store credentials: google-auth not installed")
        return

    p = _normalize_profile(profile or user_id or get_active_profile())
    token_file = _token_file_for(p)

    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes or []),
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        "profile": p,
    }

    # 1. File-based storage (per profile).
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        token_file.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
        logger.info("Google OAuth token saved to %s (profile=%s)", token_file, p)
    except OSError as exc:
        logger.error("Failed to write token file %s: %s", token_file, exc)

    # 2. DB-backed encrypted storage — profile is the user_id key.
    mgr = _get_db_provider_manager()
    if mgr is not None:
        try:
            expires_at = credentials.expiry.isoformat() if credentials.expiry else None
            mgr.store_oauth_token(
                workspace_id=_default_workspace_id(),
                user_id=p,
                provider="google",
                token_json=token_data,
                scopes=list(credentials.scopes or SCOPES),
                expires_at=expires_at,
            )
            logger.info("Google OAuth token saved to DB for profile=%s", p)
        except Exception as exc:
            logger.warning("DB token store failed (file fallback OK): %s", exc)


def get_credentials(
    user_id: Optional[str] = None,
    profile: Optional[str] = None,
) -> Optional["GoogleCredentials"]:
    """Load stored Google OAuth credentials for a profile, auto-refreshing if expired.

    Resolution order:
    1. DB (encrypted) — preferred.
    2. Per-profile token file.
    3. Legacy single-profile token file (only if asking for "personal").

    Returns None if no valid credentials are found.

    Args:
        user_id: Deprecated alias for `profile`. Ignored if profile is set.
        profile: "personal" or "work". Defaults to the currently active profile.
    """
    if not _GOOGLE_AUTH_AVAILABLE:
        return None

    p = _normalize_profile(profile or user_id or get_active_profile())
    token_data: Optional[dict] = None

    # Try DB first.
    mgr = _get_db_provider_manager()
    if mgr is not None:
        try:
            token_data = mgr.get_oauth_token(
                workspace_id=_default_workspace_id(),
                user_id=p,
                provider="google",
            )
        except Exception as exc:
            logger.debug("DB token lookup failed: %s", exc)

    # Fall back to per-profile file.
    profile_file = _token_file_for(p)
    if token_data is None and profile_file.exists():
        try:
            token_data = json.loads(profile_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read token file %s: %s", profile_file, exc)

    # Legacy fallback: the v1 single-profile file maps to "personal".
    if token_data is None and p == DEFAULT_PROFILE and TOKEN_FILE.exists():
        try:
            token_data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read legacy token file: %s", exc)

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

    # Auto-refresh if expired.
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            store_credentials(creds, profile=p)
            logger.info("Google OAuth token auto-refreshed for profile=%s", p)
        except Exception as exc:
            logger.warning("Token refresh failed: %s", exc)
            return None

    return creds if (creds.valid or creds.refresh_token) else None


def has_scope(scope: str, profile: Optional[str] = None) -> bool:
    """Check if stored credentials for a profile include a specific scope."""
    creds = get_credentials(profile=profile)
    if creds is None:
        return False
    granted = set(creds.scopes or [])
    return scope in granted


def needs_reauth(
    profile: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """Check if the user needs to re-authenticate due to missing scopes."""
    creds = get_credentials(profile=profile)
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
async def google_auth_start(profile: str = DEFAULT_PROFILE):
    """Initiate Google OAuth flow for a given profile (personal | work).

    The profile name is encoded into the OAuth `state` param so the callback
    knows which slot to save the resulting tokens into.
    """
    _require_google_libs()

    p = _normalize_profile(profile)

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
    # Encode the profile in the state param so the callback can route the token.
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=f"profile:{p}",
    )

    logger.info("Redirecting to Google OAuth consent for profile=%s", p)
    return RedirectResponse(auth_url)


@router.get("/callback")
async def google_auth_callback(request: Request):
    """Handle OAuth callback from Google.

    Exchanges the authorization code for tokens and stores them under the
    profile encoded into the `state` param.
    """
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    state = request.query_params.get("state", "")
    callback_profile = DEFAULT_PROFILE
    if state.startswith("profile:"):
        callback_profile = _normalize_profile(state.split(":", 1)[1])

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

        store_credentials(creds, profile=callback_profile)
        # Make the just-connected profile the active one if no other profile
        # has been activated yet — otherwise leave the user's choice alone.
        if not ACTIVE_PROFILE_FILE.exists():
            set_active_profile(callback_profile)

        granted_scopes = list(creds.scopes or [])
        logger.info(
            "Google OAuth completed for profile=%s. Granted scopes: %s",
            callback_profile,
            ", ".join(granted_scopes),
        )

        label = callback_profile.capitalize()
        return HTMLResponse(
            content=_result_page(
                f"Google ({label}) Connected",
                f"LocalMind can now access your {label} Google Workspace "
                f"(Gmail, Drive, Sheets, Slides). You can close this tab and "
                f"return to LocalMind.",
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


def _status_for_profile(profile: str, has_client_credentials: bool) -> dict:
    """Build the JSON status payload for a single profile."""
    creds = get_credentials(profile=profile)
    if creds is None:
        return {
            "profile": profile,
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
    reauth_needed, missing = needs_reauth(profile=profile)

    return {
        "profile": profile,
        "authenticated": is_valid,
        "has_client_credentials": has_client_credentials,
        "scopes": granted_scopes,
        "expires_at": expires_at,
        "needs_reauth": reauth_needed,
        "missing_scopes": missing,
    }


@router.get("/status")
async def google_auth_status(profile: Optional[str] = None):
    """Status of one Google profile (defaults to the currently active one).

    The response shape stays backwards compatible (the same fields a v1
    single-profile caller would see), but with a `profile` field added.
    """
    has_client_credentials = bool(_get_client_config())
    p = _normalize_profile(profile or get_active_profile())
    return _status_for_profile(p, has_client_credentials)


@router.get("/status/all")
async def google_auth_status_all():
    """Combined status for all profiles plus the currently active one."""
    has_client_credentials = bool(_get_client_config())
    return {
        "active_profile": get_active_profile(),
        "has_client_credentials": has_client_credentials,
        "profiles": {
            p: _status_for_profile(p, has_client_credentials) for p in VALID_PROFILES
        },
    }


@router.get("/active-profile")
async def google_active_profile_get():
    """Return the currently active Google profile."""
    return {"profile": get_active_profile(), "valid": list(VALID_PROFILES)}


@router.post("/active-profile")
async def google_active_profile_set(request: Request):
    """Set the currently active Google profile.

    Body: { "profile": "personal" | "work" }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    requested = body.get("profile") if isinstance(body, dict) else None
    p = _normalize_profile(requested)
    if requested and requested.lower() not in VALID_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"profile must be one of {list(VALID_PROFILES)}",
        )
    set_active_profile(p)
    return {"profile": p, "ok": True}


@router.post("/revoke")
async def google_revoke(profile: Optional[str] = None):
    """Revoke a profile's Google OAuth tokens and remove stored credentials."""
    p = _normalize_profile(profile or get_active_profile())
    creds = get_credentials(profile=p)
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

    # Remove the per-profile token file.
    profile_file = _token_file_for(p)
    if profile_file.exists():
        try:
            profile_file.unlink()
            logger.info("Google token file removed for profile=%s", p)
        except OSError as exc:
            logger.error("Failed to remove token file: %s", exc)

    # Also remove the legacy single-profile file if we just revoked personal.
    if p == DEFAULT_PROFILE and TOKEN_FILE.exists():
        try:
            TOKEN_FILE.unlink()
            logger.info("Legacy Google token file removed")
        except OSError as exc:
            logger.error("Failed to remove legacy token file: %s", exc)

    # Remove DB token for this profile.
    mgr = _get_db_provider_manager()
    if mgr is not None:
        try:
            from backend.core.providers import _get_conn
            with _get_conn() as conn:
                conn.execute(
                    "DELETE FROM oauth_credentials WHERE provider = 'google' "
                    "AND workspace_id = ? AND user_id = ?",
                    (_default_workspace_id(), p),
                )
            logger.info("Google token removed from DB for profile=%s", p)
        except Exception as exc:
            logger.warning("DB token removal failed: %s", exc)

    return {
        "ok": True,
        "profile": p,
        "revoked_remote": revoked_remote,
        "message": f"Google ({p}) disconnected",
    }


@router.post("/refresh")
async def google_refresh(profile: Optional[str] = None):
    """Force-refresh a profile's Google OAuth tokens."""
    if not _GOOGLE_AUTH_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="Google auth libraries not installed.",
        )

    p = _normalize_profile(profile or get_active_profile())
    creds = get_credentials(profile=p)
    if creds is None:
        raise HTTPException(
            status_code=400,
            detail=f"No Google credentials found for {p}. Connect Google first.",
        )

    if not creds.refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh token available. Re-authorize Google to get one.",
        )

    try:
        creds.refresh(GoogleAuthRequest())
        store_credentials(creds, profile=p)
        logger.info("Google OAuth token force-refreshed for profile=%s", p)
        return {
            "ok": True,
            "profile": p,
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
