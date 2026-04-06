/**
 * ws_client.js -- WebSocket client with SSE fallback for autonomy activity
 * =========================================================================
 * Tries WebSocket first. If it fails to connect, falls back to SSE.
 * Auto-reconnects with exponential backoff (1s -> 2s -> 4s -> ... -> 30s max).
 *
 * Public API:
 *   init()                  -- Start the connection (called once from app.js)
 *   onActivity(callback)    -- Register a listener for activity events
 *   offActivity(callback)   -- Remove a listener
 *   onStatus(callback)      -- Register a listener for connection status changes
 *   offStatus(callback)     -- Remove a status listener
 *   getConnectionStatus()   -- Returns current status: "connected" | "connecting" | "disconnected"
 *   getTransport()          -- Returns current transport: "websocket" | "sse" | null
 */

import { API } from "./state.js";

// -- Internal state ----------------------------------------------------------
const _activityListeners = new Set();
const _statusListeners = new Set();

let _status = "disconnected"; // "connected" | "connecting" | "disconnected"
let _transport = null;        // "websocket" | "sse" | null
let _ws = null;
let _sse = null;
let _backoff = 1000;
let _reconnectTimer = null;
let _stopped = false;

const BACKOFF_MAX = 30000;
const BACKOFF_BASE = 1000;

// -- Public API --------------------------------------------------------------

export function init() {
  _stopped = false;
  _tryWebSocket();
}

export function onActivity(cb) {
  _activityListeners.add(cb);
}

export function offActivity(cb) {
  _activityListeners.delete(cb);
}

export function onStatus(cb) {
  _statusListeners.add(cb);
  // Immediately notify with current status
  try { cb(_status, _transport); } catch (e) { console.error("ws_client status callback error:", e); }
}

export function offStatus(cb) {
  _statusListeners.delete(cb);
}

export function getConnectionStatus() {
  return _status;
}

export function getTransport() {
  return _transport;
}

// -- Connection status management --------------------------------------------

function _setStatus(newStatus, newTransport) {
  if (_status === newStatus && _transport === newTransport) return;
  _status = newStatus;
  _transport = newTransport;
  for (const cb of _statusListeners) {
    try { cb(_status, _transport); } catch (e) { console.error("ws_client status callback error:", e); }
  }
}

// -- Activity dispatch -------------------------------------------------------

function _dispatchActivity(event) {
  for (const cb of _activityListeners) {
    try { cb(event); } catch (e) { console.error("ws_client activity callback error:", e); }
  }
}

// -- Reconnect with exponential backoff --------------------------------------

function _scheduleReconnect() {
  if (_stopped) return;
  _setStatus("disconnected", null);
  if (_reconnectTimer) clearTimeout(_reconnectTimer);
  console.warn(`ws_client: reconnecting in ${_backoff}ms...`);
  _reconnectTimer = setTimeout(() => {
    _backoff = Math.min(_backoff * 2, BACKOFF_MAX);
    _tryWebSocket();
  }, _backoff);
}

function _resetBackoff() {
  _backoff = BACKOFF_BASE;
}

// -- WebSocket transport -----------------------------------------------------

function _tryWebSocket() {
  if (_stopped) return;
  _cleanup();
  _setStatus("connecting", null);

  // Derive WS URL from the HTTP API origin
  const wsProto = API.startsWith("https") ? "wss" : "ws";
  const host = API.replace(/^https?:\/\//, "");
  const url = `${wsProto}://${host}/ws/activity`;

  try {
    _ws = new WebSocket(url);
  } catch (err) {
    console.warn("ws_client: WebSocket constructor failed, falling back to SSE", err);
    _fallbackToSSE();
    return;
  }

  // If the socket doesn't open within 5 seconds, fall back to SSE
  const openTimeout = setTimeout(() => {
    if (_ws && _ws.readyState !== WebSocket.OPEN) {
      console.warn("ws_client: WebSocket open timeout, falling back to SSE");
      _ws.close();
      _fallbackToSSE();
    }
  }, 5000);

  _ws.onopen = () => {
    clearTimeout(openTimeout);
    _resetBackoff();
    _setStatus("connected", "websocket");
    console.info("ws_client: connected via WebSocket");
  };

  _ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "activity") {
        _dispatchActivity(msg.data);
      } else if (msg.type === "ping") {
        // Respond to server ping
        if (_ws && _ws.readyState === WebSocket.OPEN) {
          _ws.send(JSON.stringify({ type: "pong" }));
        }
      }
      // "pong" from server after our ping -- ignore
    } catch (err) {
      console.error("ws_client: failed to parse WS message:", err);
    }
  };

  _ws.onerror = () => {
    clearTimeout(openTimeout);
    // onerror is always followed by onclose, so let onclose handle reconnection
  };

  _ws.onclose = (e) => {
    clearTimeout(openTimeout);
    if (_transport === "websocket") {
      // We were connected via WS and lost it -- try WS again first
      console.warn("ws_client: WebSocket closed", e.code, e.reason);
      _ws = null;
      _scheduleReconnect();
    } else {
      // Never achieved WS connection -- fall back to SSE immediately
      console.warn("ws_client: WebSocket failed to connect, falling back to SSE");
      _ws = null;
      _fallbackToSSE();
    }
  };
}

// -- SSE fallback transport --------------------------------------------------

function _fallbackToSSE() {
  if (_stopped) return;
  _cleanup();
  _setStatus("connecting", null);

  const url = `${API}/api/autonomy/activity`;
  try {
    _sse = new EventSource(url);
  } catch (err) {
    console.error("ws_client: EventSource constructor failed:", err);
    _scheduleReconnect();
    return;
  }

  _sse.onopen = () => {
    _resetBackoff();
    _setStatus("connected", "sse");
    console.info("ws_client: connected via SSE fallback");
  };

  _sse.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      _dispatchActivity(event);
    } catch (err) {
      console.error("ws_client: failed to parse SSE event:", err);
    }
  };

  _sse.onerror = () => {
    console.warn("ws_client: SSE connection lost");
    _sse.close();
    _sse = null;
    _scheduleReconnect();
  };
}

// -- Cleanup -----------------------------------------------------------------

function _cleanup() {
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
  if (_ws) {
    try { _ws.close(); } catch (e) { /* ignore */ }
    _ws = null;
  }
  if (_sse) {
    try { _sse.close(); } catch (e) { /* ignore */ }
    _sse = null;
  }
}
