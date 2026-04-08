"""
routes/push.py — Web Push Notifications API
=============================================
FastAPI routes for managing Web Push subscriptions and sending notifications.

Endpoints
---------
GET    /api/push/vapid-key         — Return the public VAPID key
POST   /api/push/subscribe         — Store a push subscription
DELETE /api/push/subscribe         — Remove a push subscription by endpoint
POST   /api/push/test              — Send a test notification to all subscribers

The ``send_push_to_all`` helper is importable by other modules (e.g. the job
completion handler) to fire notifications without going through HTTP.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.config import DB_PATH

logger = logging.getLogger("localmind.routes.push")

router = APIRouter(prefix="/api/push", tags=["push"])

# ---------------------------------------------------------------------------
# Conditional pywebpush import — graceful degradation if not installed
# ---------------------------------------------------------------------------

try:
    from pywebpush import webpush, WebPushException  # type: ignore[import-untyped]
    _WEBPUSH_AVAILABLE = True
except ImportError:
    _WEBPUSH_AVAILABLE = False
    logger.warning("pywebpush not installed — push notifications will be unavailable")

# ---------------------------------------------------------------------------
# Table bootstrap — CREATE TABLE IF NOT EXISTS (matches codebase pattern)
# ---------------------------------------------------------------------------


def _ensure_table() -> None:
    """Create the push_subscriptions table if it doesn't exist yet."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL UNIQUE,
            key_p256dh TEXT NOT NULL,
            key_auth TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_push_endpoint
            ON push_subscriptions(endpoint);
    """)
    conn.commit()
    conn.close()


# Run once at import time (idempotent)
_ensure_table()

# ---------------------------------------------------------------------------
# VAPID config helpers
# ---------------------------------------------------------------------------


def _vapid_keys() -> tuple[str, str, str]:
    """Return (private_key, public_key, claims_email) from config.

    Imported lazily to avoid circular imports if config grows.
    """
    from backend.config import VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_CLAIMS_EMAIL
    return VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_CLAIMS_EMAIL


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/vapid-key")
async def get_vapid_key() -> JSONResponse:
    """Return the public VAPID key so the frontend can subscribe."""
    _, public_key, _ = _vapid_keys()
    if not public_key:
        raise HTTPException(status_code=503, detail="VAPID keys not configured")
    return JSONResponse({"publicKey": public_key})


@router.post("/subscribe")
async def subscribe(request: Request) -> JSONResponse:
    """Store a Web Push subscription.

    Body: {
        "endpoint": "https://...",
        "keys": { "p256dh": "...", "auth": "..." }
    }
    """
    try:
        body: dict = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    endpoint: str | None = body.get("endpoint")
    keys: dict | None = body.get("keys")

    if not endpoint:
        raise HTTPException(status_code=400, detail="'endpoint' is required")
    if not keys or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(status_code=400, detail="'keys.p256dh' and 'keys.auth' are required")

    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.execute(
            """INSERT INTO push_subscriptions (endpoint, key_p256dh, key_auth, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET
                   key_p256dh = excluded.key_p256dh,
                   key_auth   = excluded.key_auth,
                   created_at = excluded.created_at""",
            (endpoint, keys["p256dh"], keys["auth"], now),
        )
        conn.commit()
    except Exception as exc:
        logger.exception("Failed to store push subscription")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        conn.close()

    logger.info("Push subscription stored: %s", endpoint[:80])
    return JSONResponse({"status": "subscribed"}, status_code=201)


@router.delete("/subscribe")
async def unsubscribe(request: Request) -> JSONResponse:
    """Remove a push subscription by endpoint.

    Body: { "endpoint": "https://..." }
    """
    try:
        body: dict = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    endpoint: str | None = body.get("endpoint")
    if not endpoint:
        raise HTTPException(status_code=400, detail="'endpoint' is required")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        cursor = conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        conn.commit()
        deleted = cursor.rowcount
    except Exception as exc:
        logger.exception("Failed to remove push subscription")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        conn.close()

    if deleted == 0:
        raise HTTPException(status_code=404, detail="Subscription not found")

    logger.info("Push subscription removed: %s", endpoint[:80])
    return JSONResponse({"status": "unsubscribed"})


@router.post("/test")
async def send_test() -> JSONResponse:
    """Send a test notification to all stored subscriptions."""
    result = send_push_to_all(
        title="LocalMind Test",
        body="Push notifications are working!",
        url="/",
    )
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Public helper — importable by other modules
# ---------------------------------------------------------------------------


def send_push_to_all(title: str, body: str, url: str | None = None) -> dict:
    """Send a Web Push notification to every stored subscription.

    Returns a summary dict: { "sent": N, "failed": N, "skipped": N }

    Safe to call even if pywebpush is not installed (returns skipped count).
    """
    private_key, public_key, claims_email = _vapid_keys()

    if not _WEBPUSH_AVAILABLE:
        logger.warning("send_push_to_all called but pywebpush is not installed")
        return {"sent": 0, "failed": 0, "skipped": 1, "reason": "pywebpush not installed"}

    if not private_key or not public_key:
        logger.warning("send_push_to_all called but VAPID keys are not configured")
        return {"sent": 0, "failed": 0, "skipped": 1, "reason": "VAPID keys not configured"}

    # Fetch all subscriptions
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    rows = conn.execute(
        "SELECT endpoint, key_p256dh, key_auth FROM push_subscriptions"
    ).fetchall()
    conn.close()

    if not rows:
        return {"sent": 0, "failed": 0, "skipped": 0, "reason": "no subscriptions"}

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    vapid_claims = {"sub": claims_email}
    sent = 0
    failed = 0
    stale_endpoints: list[str] = []

    for row in rows:
        subscription_info = {
            "endpoint": row["endpoint"],
            "keys": {
                "p256dh": row["key_p256dh"],
                "auth": row["key_auth"],
            },
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims=vapid_claims,
            )
            sent += 1
        except WebPushException as exc:
            # 404 or 410 means the subscription is stale — queue for removal
            status_code = getattr(exc, "response", None)
            if status_code is not None:
                status_code = getattr(status_code, "status_code", None)
            if status_code in (404, 410):
                stale_endpoints.append(row["endpoint"])
                logger.info("Removing stale push subscription: %s", row["endpoint"][:80])
            else:
                logger.warning("Push failed for %s: %s", row["endpoint"][:80], exc)
            failed += 1
        except Exception as exc:
            logger.warning("Unexpected push error for %s: %s", row["endpoint"][:80], exc)
            failed += 1

    # Clean up stale subscriptions
    if stale_endpoints:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA busy_timeout=5000")
        for ep in stale_endpoints:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (ep,))
        conn.commit()
        conn.close()
        logger.info("Cleaned up %d stale push subscriptions", len(stale_endpoints))

    logger.info("Push sent=%d failed=%d total=%d", sent, failed, len(rows))
    return {"sent": sent, "failed": failed, "total": len(rows)}
