"""
routes/drive_webhook.py — Google Drive push-notification receiver.
==================================================================

FastAPI route that receives Google Drive change notifications and hands them
off to the in-process dispatcher in ``backend.integrations.drive_webhook``.

Endpoint
--------
POST /api/drive/webhook  — Google POSTs here for every watched change.

Contract with Google (per https://developers.google.com/drive/api/guides/push)
-----------------------------------------------------------------------------
- Must respond 200 within 30 seconds, otherwise Google retries with
  exponential backoff and eventually disables the channel.
- Metadata is carried in HTTP headers (X-Goog-*), NOT the body. The body is
  typically empty.
- The first notification on any new channel is a ``sync`` handshake — just
  acknowledge it with 200 and do not dispatch.
- All actual work is deferred via ``asyncio.create_task`` inside the
  dispatcher so the handler never blocks the HTTP 200.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response

from backend.integrations import drive_webhook as drive_webhook_integration

logger = logging.getLogger("localmind.routes.drive_webhook")

router = APIRouter(prefix="/api/drive", tags=["drive"])


# The non-``sync`` resource-states Google may send. We dispatch on any of
# these; anything else (including future/unknown states) falls through to
# a debug log and a 200 to keep Google from retrying.
_DISPATCHED_STATES = frozenset({
    "update", "add", "remove", "trash", "untrash", "change", "exists",
})


@router.post("/webhook")
async def drive_webhook(request: Request) -> Response:
    """Receive a Google Drive push notification.

    Google carries all metadata in headers:
      - ``X-Goog-Channel-Id``        : our channel UUID (set by watch_folder).
      - ``X-Goog-Channel-Token``     : opaque token (if we configured one).
      - ``X-Goog-Channel-Expiration``: RFC1123 channel expiration (optional).
      - ``X-Goog-Resource-Id``       : opaque resource id.
      - ``X-Goog-Resource-Uri``      : URI of the watched resource.
      - ``X-Goog-Resource-State``    : ``sync`` (handshake) or change state.
      - ``X-Goog-Message-Number``    : monotonic per channel.
      - ``X-Goog-Changed``           : comma list of change kinds (optional).

    Returns:
        200 no-content as fast as possible. Any processing is done
        fire-and-forget inside ``drive_webhook.dispatch``.
    """
    headers = dict(request.headers)

    channel_id = headers.get("x-goog-channel-id")
    resource_state = headers.get("x-goog-resource-state")
    message_number = headers.get("x-goog-message-number")

    # Short-circuit: sync handshake fires once per new channel. No work to do.
    if resource_state == "sync":
        logger.info(
            "Drive webhook sync handshake: channel_id=%s msg#=%s",
            channel_id, message_number,
        )
        return Response(status_code=200)

    # Body is usually empty; parse defensively so a stray payload doesn't 500
    # the endpoint (which would make Google retry).
    body: dict = {}
    try:
        raw = await request.body()
        if raw:
            import json
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    body = parsed
                else:
                    body = {"_raw": parsed}
            except json.JSONDecodeError:
                body = {"_raw_bytes_len": len(raw)}
    except Exception as exc:
        # Never let body parsing failures propagate — we must return 200.
        logger.debug("Drive webhook body read failed (non-fatal): %s", exc)

    if resource_state and resource_state not in _DISPATCHED_STATES:
        logger.debug(
            "Drive webhook unknown state=%s channel_id=%s (acknowledging)",
            resource_state, channel_id,
        )
        # Still ack — Google should not retry. Do not dispatch unknown states.
        return Response(status_code=200)

    # Fire-and-forget: dispatch schedules the handler via asyncio.create_task
    # internally. This call itself is cheap (registry lookup + task creation).
    try:
        await drive_webhook_integration.dispatch(headers, body)
    except Exception:
        # Never fail the webhook — log and move on.
        logger.exception(
            "Drive webhook dispatch raised (swallowed to keep 200): channel_id=%s state=%s",
            channel_id, resource_state,
        )

    return Response(status_code=200)
