"""
Google OAuth2 routes for LocalMind.

Flow:
1. User clicks "Connect Google" in Settings
2. GET /api/auth/google → redirects to Google consent screen
3. Google redirects back to /api/auth/google/callback
4. Token saved to ~/.localmind/gmail_token.json
5. Gmail tool (and future Google tools) use the token automatically

Setup: Place credentials.json from Google Cloud Console in ~/.localmind/
       OR set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars.
"""

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger("localmind.auth.google")

router = APIRouter(prefix="/api/auth")

CONFIG_DIR = Path.home() / ".localmind"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "gmail_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Port LocalMind runs on — used for the OAuth callback
CALLBACK_PORT = int(os.getenv("PORT", 8001))
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/api/auth/google/callback"


def _get_client_config() -> dict:
    """Load OAuth client config from credentials.json or env vars."""
    # Try env vars first
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if client_id and client_secret:
        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        }

    # Fall back to credentials.json
    if CREDENTIALS_FILE.exists():
        data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        # Google exports as "installed" for desktop apps — we need "web" for redirect flow
        if "installed" in data:
            installed = data["installed"]
            return {
                "web": {
                    "client_id": installed["client_id"],
                    "client_secret": installed["client_secret"],
                    "auth_uri": installed.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                    "token_uri": installed.get("token_uri", "https://oauth2.googleapis.com/token"),
                    "redirect_uris": [REDIRECT_URI],
                }
            }
        return data

    return {}


@router.get("/google/status")
async def google_auth_status():
    """Check if Google is connected."""
    has_credentials = CREDENTIALS_FILE.exists() or (
        os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET")
    )
    has_token = TOKEN_FILE.exists()

    # Validate token if it exists
    token_valid = False
    if has_token:
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            token_valid = creds.valid or (creds.expired and creds.refresh_token is not None)
        except Exception:
            token_valid = False

    return {
        "has_credentials": has_credentials,
        "connected": has_token and token_valid,
        "credentials_path": str(CREDENTIALS_FILE),
    }


@router.get("/google")
async def google_auth_start():
    """Start the Google OAuth flow — redirects to Google consent screen."""
    client_config = _get_client_config()
    if not client_config:
        raise HTTPException(
            status_code=400,
            detail=f"No Google credentials found. Place credentials.json in {CONFIG_DIR} "
                   "or set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars.",
        )

    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Google auth libraries not installed. Run: pip install google-auth-oauthlib",
        )

    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    logger.info(f"Redirecting to Google OAuth: {auth_url[:80]}...")
    return RedirectResponse(auth_url)


@router.get("/google/callback")
async def google_auth_callback(request: Request):
    """Handle the OAuth callback from Google."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(
            content=_result_page("Authorization Failed", f"Google returned an error: {error}", success=False),
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            content=_result_page("Authorization Failed", "No authorization code received.", success=False),
            status_code=400,
        )

    client_config = _get_client_config()
    if not client_config:
        return HTMLResponse(
            content=_result_page("Configuration Error", "Google credentials not found.", success=False),
            status_code=500,
        )

    try:
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Save token
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        logger.info("Google OAuth token saved successfully")

        return HTMLResponse(
            content=_result_page(
                "Google Connected",
                "LocalMind can now access your Gmail. You can close this tab and return to LocalMind.",
                success=True,
            )
        )
    except Exception as exc:
        logger.exception("Google OAuth callback failed")
        return HTMLResponse(
            content=_result_page("Authorization Failed", str(exc), success=False),
            status_code=500,
        )


@router.post("/google/disconnect")
async def google_disconnect():
    """Remove stored Google token."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        logger.info("Google token removed")
    return {"ok": True, "message": "Google disconnected"}


def _result_page(title: str, message: str, success: bool = True) -> str:
    """Generate a simple HTML result page for the OAuth callback."""
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
