/**
 * Topbar Profile Mode toggle (Personal | Work).
 *
 * Owns the active Google profile state for the UI. Reads/writes the
 * server-side active profile via /api/google/active-profile so that the
 * job executor and tools all use the right account, and renders the
 * topbar segmented control accordingly.
 */

import { API } from "./state.js";
import { showToast } from "./utils.js";

const LS_KEY = "lm.mode";
const VALID = new Set(["personal", "work"]);

function _readLocal() {
  const v = localStorage.getItem(LS_KEY);
  return VALID.has(v) ? v : "personal";
}

function _writeLocal(mode) {
  localStorage.setItem(LS_KEY, mode);
}

export function getActiveMode() {
  return _readLocal();
}

async function _setServerProfile(mode) {
  try {
    const r = await fetch(`${API}/api/google/active-profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: mode }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  } catch (e) {
    console.warn("[mode_toggle] failed to sync active profile:", e);
  }
}

async function _fetchStatus() {
  try {
    const r = await fetch(`${API}/api/google/status/all`);
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

function _paint(mode, status) {
  const root = document.getElementById("modeToggle");
  if (!root) return;
  for (const btn of root.querySelectorAll("[data-mode]")) {
    const m = btn.dataset.mode;
    btn.setAttribute("aria-pressed", m === mode ? "true" : "false");
    const profStatus = status?.profiles?.[m];
    const connected = !!profStatus?.authenticated;
    btn.dataset.disconnected = connected ? "0" : "1";
    btn.title = connected
      ? `${m[0].toUpperCase() + m.slice(1)} · Google connected`
      : `${m[0].toUpperCase() + m.slice(1)} · Not connected — click then visit Settings`;
  }
}

async function _refreshStatus(mode) {
  const status = await _fetchStatus();
  _paint(mode, status);
  return status;
}

async function _switchTo(mode, opts = {}) {
  const m = VALID.has(mode) ? mode : "personal";
  _writeLocal(m);
  await _setServerProfile(m);
  const status = await _refreshStatus(m);

  // Notify other modules so they can re-pull anything mode-scoped.
  document.dispatchEvent(new CustomEvent("lm:mode-change", { detail: { mode: m, status } }));

  if (!opts.silent) {
    const connected = !!status?.profiles?.[m]?.authenticated;
    const label = m[0].toUpperCase() + m.slice(1);
    if (connected) {
      showToast(`Switched to ${label}.`, "info");
    } else {
      showToast(`Switched to ${label}. Connect a Google account in Settings.`, "info");
    }
  }
}

export function initModeToggle() {
  const root = document.getElementById("modeToggle");
  if (!root) return;

  // Boot: server is the source of truth for the active profile (so tools
  // and the executor agree). Reconcile localStorage with whatever the
  // server thinks is active.
  fetch(`${API}/api/google/active-profile`)
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      const serverMode = VALID.has(data?.profile) ? data.profile : null;
      const localMode = _readLocal();
      const mode = serverMode || localMode;
      if (mode !== localMode) _writeLocal(mode);
      // If localStorage already had a mode and server agrees, just paint.
      if (serverMode && serverMode === localMode) {
        _refreshStatus(mode);
      } else if (serverMode) {
        _refreshStatus(mode);
      } else {
        // First run: push our local default to the server.
        _switchTo(mode, { silent: true });
      }
    })
    .catch(() => {
      _refreshStatus(_readLocal());
    });

  root.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mode]");
    if (!btn) return;
    const m = btn.dataset.mode;
    if (!VALID.has(m)) return;
    if (m === _readLocal()) return; // already active
    _switchTo(m);
  });
}
