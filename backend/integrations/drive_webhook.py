"""
Google Drive push-notification subscription manager + dispatcher.

Replaces Report Forge's 5-minute polling with ambient, event-driven capture
by subscribing to Drive ``changes.watch`` and routing incoming notifications
to registered async handlers.

Local dev requirement
---------------------
Google requires the webhook URL be HTTPS and publicly reachable. For local
development, expose the FastAPI server via a tunnel (e.g. ngrok, cloudflared,
or tailscale funnel) and pass the resulting HTTPS URL as ``webhook_url`` to
``watch_folder``.

Channel expiration
------------------
Drive channel subscriptions expire after at most 7 days (commonly 24h). The
``watch_folder`` return value contains ``expiration`` (epoch ms) — callers are
responsible for scheduling renewal (re-calling ``watch_folder``) and stopping
the old channel via ``stop_watch``.

Scopes
------
Requires ``https://www.googleapis.com/auth/drive`` (full Drive). The existing
credential store in ``backend.routes.google_auth`` currently requests
``drive.file`` (per-file scope), which suffices to watch files/folders the app
has explicitly accessed. To watch arbitrary folder IDs (e.g. a user's "Reports"
folder that wasn't created by LocalMind), expand SCOPES in ``google_auth.py``
to include the full ``drive`` scope and re-authorize.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable

logger = logging.getLogger("localmind.integrations.drive_webhook")

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Handler = Callable[[dict], Awaitable[None]]


# ---------------------------------------------------------------------------
# Lazy google api import
# ---------------------------------------------------------------------------


def _require_google_api() -> None:
    """Raise RuntimeError if google-api-python-client isn't installed."""
    try:
        import googleapiclient.discovery  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "google-api-python-client is not installed. "
            "Run: pip install google-api-python-client google-auth"
        ) from exc


def _get_credentials():
    """Load OAuth creds from the centralized google_auth store, or raise."""
    try:
        from backend.routes.google_auth import get_credentials
    except Exception as exc:
        raise RuntimeError(f"google_auth module unavailable: {exc}") from exc

    creds = get_credentials()
    if creds is None:
        raise RuntimeError(
            "No valid Google credentials found. Connect Google via Settings "
            "or run the OAuth flow at /api/google/auth"
        )
    return creds


def _build_drive_service(credentials):
    """Build a Drive v3 service client."""
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


# ---------------------------------------------------------------------------
# In-memory handler registry
# ---------------------------------------------------------------------------

# callback_id -> async handler(payload: dict) -> None
_HANDLERS: dict[str, Handler] = {}

# channel_id -> callback_id   (so dispatcher can resolve handler by channel id)
_CHANNEL_TO_CALLBACK: dict[str, str] = {}


def register_handler(callback_id: str, handler: Handler) -> None:
    """Register an async handler for a given callback_id.

    The handler will be invoked with the Drive notification payload (a dict
    combining the parsed headers and body) whenever a push notification
    arrives for any channel whose channel-id has been associated with this
    callback_id via ``watch_folder``.
    """
    if not asyncio.iscoroutinefunction(handler):
        raise TypeError(
            f"handler for callback_id={callback_id!r} must be an async function"
        )
    _HANDLERS[callback_id] = handler
    logger.info("Registered drive webhook handler: callback_id=%s", callback_id)


def unregister_handler(callback_id: str) -> None:
    """Remove a handler registration (no-op if not present)."""
    _HANDLERS.pop(callback_id, None)
    # Also drop any channel mappings pointing at this callback_id
    stale = [cid for cid, cb in _CHANNEL_TO_CALLBACK.items() if cb == callback_id]
    for cid in stale:
        _CHANNEL_TO_CALLBACK.pop(cid, None)


# ---------------------------------------------------------------------------
# Subscription management
# ---------------------------------------------------------------------------


def _do_watch_folder(
    credentials,
    folder_id: str,
    webhook_url: str,
    channel_id: str,
    token: str | None,
) -> dict:
    """Synchronous: call Drive files.watch on a folder.

    Drive exposes two watch APIs:
      - ``changes.watch`` — fires for all changes in the user's Drive.
      - ``files.watch``   — fires for changes to a specific file/folder.

    We use ``files.watch`` so the caller can scope notifications to one folder.
    """
    service = _build_drive_service(credentials)

    body: dict[str, Any] = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
    }
    if token:
        body["token"] = token

    response = service.files().watch(
        fileId=folder_id,
        body=body,
        supportsAllDrives=True,
    ).execute()

    return response


async def watch_folder(
    folder_id: str,
    webhook_url: str,
    callback_id: str,
    token: str | None = None,
) -> dict:
    """Subscribe to changes for a Drive folder and associate them with a callback_id.

    Args:
        folder_id: Drive file ID of the folder to watch.
        webhook_url: Fully-qualified HTTPS URL where Google should POST
            notifications. Must be reachable from the public internet.
        callback_id: Logical identifier used to look up a handler registered
            via ``register_handler``. One callback_id may back many channels.
        token: Optional opaque token sent back in ``X-Goog-Channel-Token``
            headers so handlers can verify the notification's origin.

    Returns:
        Dict with at least ``id`` (channel id), ``resourceId`` (needed for stop),
        ``expiration`` (epoch ms string; None if unlimited), and ``kind``.
        Caller SHOULD persist this so they can renew before expiration and
        call ``stop_watch`` on shutdown.

    Raises:
        RuntimeError: if google-api libs are missing or creds are absent.
    """
    _require_google_api()
    creds = _get_credentials()

    # Drive requires a unique channel id per subscription. Always generate a
    # fresh UUID4 — reusing an id returns HTTP 400 "channelIdNotUnique".
    channel_id = f"{callback_id}:{uuid.uuid4()}"

    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            None,
            lambda: _do_watch_folder(
                creds, folder_id, webhook_url, channel_id, token
            ),
        )
    except Exception as exc:
        logger.exception(
            "drive watch_folder failed: folder_id=%s callback_id=%s",
            folder_id, callback_id,
        )
        raise

    # Associate this channel's id with the callback so dispatch() can route
    # notifications back to the correct handler.
    _CHANNEL_TO_CALLBACK[response.get("id", channel_id)] = callback_id

    logger.info(
        "Drive watch created: folder_id=%s callback_id=%s channel_id=%s expiration=%s",
        folder_id,
        callback_id,
        response.get("id"),
        response.get("expiration"),
    )
    return response


def _do_stop_watch(credentials, channel_id: str, resource_id: str) -> None:
    """Synchronous: call channels.stop."""
    service = _build_drive_service(credentials)
    service.channels().stop(
        body={"id": channel_id, "resourceId": resource_id}
    ).execute()


async def stop_watch(channel_id: str, resource_id: str) -> None:
    """Unsubscribe an active channel.

    Args:
        channel_id: The ``id`` value returned by ``watch_folder``.
        resource_id: The ``resourceId`` returned by ``watch_folder``.
    """
    _require_google_api()
    creds = _get_credentials()

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: _do_stop_watch(creds, channel_id, resource_id),
        )
    except Exception:
        logger.exception(
            "drive stop_watch failed: channel_id=%s resource_id=%s",
            channel_id, resource_id,
        )
        raise

    _CHANNEL_TO_CALLBACK.pop(channel_id, None)
    logger.info("Drive watch stopped: channel_id=%s", channel_id)


# ---------------------------------------------------------------------------
# Dispatcher — invoked by the route on incoming notifications
# ---------------------------------------------------------------------------


def _header(headers: dict, name: str) -> str | None:
    """Case-insensitive header lookup (FastAPI gives lowercased keys, but
    callers may hand us raw casing)."""
    if not headers:
        return None
    if name in headers:
        return headers[name]
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            return value
    return None


async def dispatch(headers: dict, body: dict | None) -> None:
    """Route a push notification to its registered handler.

    Called by the FastAPI route. Extracts ``X-Goog-Channel-Id`` from headers,
    looks up the associated callback_id, and fires the handler via
    ``asyncio.create_task`` so the route can return 200 immediately.

    Silently no-ops (with a warning log) if:
      - The channel id is missing.
      - No callback_id is associated with the channel (e.g. stale subscription
        that outlived a server restart).
      - No handler is registered for the callback_id.
    """
    channel_id = _header(headers, "X-Goog-Channel-Id")
    resource_state = _header(headers, "X-Goog-Resource-State")
    resource_id = _header(headers, "X-Goog-Resource-Id")
    message_number = _header(headers, "X-Goog-Message-Number")
    channel_token = _header(headers, "X-Goog-Channel-Token")
    changed = _header(headers, "X-Goog-Changed")

    if not channel_id:
        logger.warning(
            "Drive webhook dispatch: missing X-Goog-Channel-Id header; dropping"
        )
        return

    callback_id = _CHANNEL_TO_CALLBACK.get(channel_id)
    if not callback_id:
        logger.warning(
            "Drive webhook dispatch: no callback mapped for channel_id=%s "
            "(stale subscription after restart?)",
            channel_id,
        )
        return

    handler = _HANDLERS.get(callback_id)
    if handler is None:
        logger.warning(
            "Drive webhook dispatch: no handler registered for callback_id=%s",
            callback_id,
        )
        return

    payload: dict[str, Any] = {
        "channel_id": channel_id,
        "resource_state": resource_state,
        "resource_id": resource_id,
        "message_number": message_number,
        "channel_token": channel_token,
        "changed": changed,
        "callback_id": callback_id,
        "headers": dict(headers) if headers else {},
        "body": body or {},
    }

    # Fire-and-forget: never await the handler here, the route MUST return 200
    # within 30s. Handler errors are logged via the task done-callback.
    task = asyncio.create_task(handler(payload))
    task.add_done_callback(
        lambda t: _log_task_error(t, callback_id, channel_id)
    )
    logger.debug(
        "Drive webhook dispatched: callback_id=%s state=%s channel_id=%s",
        callback_id, resource_state, channel_id,
    )


def _log_task_error(task: asyncio.Task, callback_id: str, channel_id: str) -> None:
    """Surface exceptions from fire-and-forget handlers."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Drive webhook handler raised: callback_id=%s channel_id=%s error=%s",
            callback_id, channel_id, exc,
            exc_info=exc,
        )


# ---------------------------------------------------------------------------
# Introspection helpers (useful for debugging / admin endpoints)
# ---------------------------------------------------------------------------


def active_channels() -> dict[str, str]:
    """Return a snapshot of the current channel_id -> callback_id mapping."""
    return dict(_CHANNEL_TO_CALLBACK)


def registered_callbacks() -> list[str]:
    """Return the list of currently-registered callback_ids."""
    return list(_HANDLERS.keys())
