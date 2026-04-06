"""
backend/routes/ws_routes.py -- WebSocket endpoint for real-time autonomy activity
==================================================================================
Provides a /ws/activity WebSocket endpoint as an upgrade path alongside the
existing SSE endpoint at /api/autonomy/activity.

Subscribes to the same AutonomyEngine activity queue and pushes events as JSON.
Supports ping/pong keepalive and graceful disconnect handling.
"""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("localmind.routes.ws")

router = APIRouter(tags=["websocket"])

# Dependency injection -- set by configure() from server.py
_engine = None


def configure(*, engine):
    """Inject the autonomy engine reference."""
    global _engine
    _engine = engine


@router.websocket("/ws/activity")
async def ws_activity(websocket: WebSocket):
    """WebSocket endpoint for real-time autonomy activity events.

    Protocol:
      - Server sends JSON activity events as they occur.
      - Server sends {"type": "ping"} every 25 seconds as keepalive.
      - Client may send {"type": "pong"} in response (optional).
      - Client may send {"type": "ping"} and server will reply with {"type": "pong"}.
      - On disconnect, the subscription is cleaned up automatically.
    """
    await websocket.accept()
    queue = _engine.subscribe_activity()
    logger.info("WebSocket client connected to /ws/activity")

    try:
        # Send current status as first event (mirrors SSE behavior)
        status = _engine.get_status()
        current = status.get("current_activity")
        if current:
            await websocket.send_json({"type": "activity", "data": current})
        else:
            await websocket.send_json({
                "type": "activity",
                "data": {
                    "action": "idle",
                    "detail": "Waiting...",
                    "time": time.strftime("%H:%M:%S"),
                },
            })

        # Run two tasks concurrently:
        # 1. Forward engine events to the client
        # 2. Listen for client messages (ping/pong, etc.)
        await asyncio.gather(
            _send_events(websocket, queue),
            _receive_messages(websocket),
        )
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from /ws/activity")
    except Exception as exc:
        logger.warning(f"WebSocket error: {exc}")
    finally:
        _engine.unsubscribe_activity(queue)
        # Ensure the socket is closed if it hasn't been already
        try:
            await websocket.close()
        except Exception:
            pass


async def _send_events(websocket: WebSocket, queue: asyncio.Queue):
    """Forward activity events from the engine queue to the WebSocket client.

    Sends a keepalive ping every 25 seconds if no events arrive.
    """
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=25.0)
            await websocket.send_json({"type": "activity", "data": event})
        except asyncio.TimeoutError:
            # Send keepalive ping
            await websocket.send_json({"type": "ping", "ts": time.time()})


async def _receive_messages(websocket: WebSocket):
    """Listen for incoming client messages (ping/pong protocol)."""
    while True:
        raw = await websocket.receive_text()
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        msg_type = msg.get("type", "")
        if msg_type == "ping":
            await websocket.send_json({"type": "pong", "ts": time.time()})
        elif msg_type == "pong":
            # Client acknowledged our ping -- nothing to do
            pass
