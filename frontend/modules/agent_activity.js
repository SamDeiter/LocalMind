/**
 * agent_activity.js — "What the AI just did" panel.
 *
 * Polls /api/agent/activity every 2s using since_id deltas so we don't
 * re-fetch entries we already have. Renders into #activityList. Filters
 * are pure client-side; the buffer is small (<= 200) so this is cheap.
 *
 * Open with the topbar button (#activityBtn). Close on scrim click or Esc.
 */

import { API } from "./state.js";
import { escapeHtml } from "./utils.js";

const POLL_MS = 2000;
const BUFFER_LIMIT = 200;

let _entries = [];          // newest first
let _lastSeenId = 0;
let _activeFilter = "all";
let _pollTimer = null;
let _isOpen = false;

export function initAgentActivity() {
  const btn = document.getElementById("activityBtn");
  const panel = document.getElementById("activityPanel");
  if (!btn || !panel) return;

  btn.addEventListener("click", openPanel);
  panel.querySelectorAll("[data-close-activity]").forEach((el) =>
    el.addEventListener("click", closePanel)
  );
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && _isOpen) closePanel();
  });

  panel.querySelectorAll("[data-kind]").forEach((el) => {
    el.addEventListener("click", () => {
      _activeFilter = el.dataset.kind;
      panel.querySelectorAll("[data-kind]").forEach((b) =>
        b.classList.toggle("lm-activity__filter--active", b.dataset.kind === _activeFilter)
      );
      _render();
    });
  });

  document.getElementById("activityClearBtn")?.addEventListener("click", async () => {
    if (!window.confirm("Clear the activity log? This is local-only and won't affect the bot.")) return;
    try {
      await fetch(`${API}/api/agent/activity`, { method: "DELETE" });
      _entries = [];
      _lastSeenId = 0;
      _render();
    } catch (_) { /* swallow */ }
  });

  // Start polling immediately so the badge can light up before the user opens the panel.
  _startPolling();
}

function openPanel() {
  const panel = document.getElementById("activityPanel");
  const btn = document.getElementById("activityBtn");
  if (!panel) return;
  panel.dataset.open = "true";
  panel.setAttribute("aria-hidden", "false");
  btn?.setAttribute("aria-expanded", "true");
  _isOpen = true;
  _hideBadge();
  _render();
}

function closePanel() {
  const panel = document.getElementById("activityPanel");
  const btn = document.getElementById("activityBtn");
  if (!panel) return;
  panel.dataset.open = "false";
  panel.setAttribute("aria-hidden", "true");
  btn?.setAttribute("aria-expanded", "false");
  _isOpen = false;
}

function _startPolling() {
  if (_pollTimer) return;
  _pollOnce();
  _pollTimer = setInterval(_pollOnce, POLL_MS);
}

async function _pollOnce() {
  try {
    const res = await fetch(`${API}/api/agent/activity?since_id=${_lastSeenId}&limit=${BUFFER_LIMIT}`);
    if (!res.ok) return;
    const data = await res.json();
    const fresh = Array.isArray(data?.entries) ? data.entries : [];
    if (fresh.length === 0) return;

    // Merge: server returns newest-first; prepend to our buffer (also newest-first).
    _entries = fresh.concat(_entries).slice(0, BUFFER_LIMIT);
    _lastSeenId = Math.max(_lastSeenId, ...fresh.map((e) => e.id || 0));

    if (!_isOpen) _showBadge();
    _render();
  } catch (_) { /* offline; keep going */ }
}

function _render() {
  const list = document.getElementById("activityList");
  if (!list) return;

  const filtered = _activeFilter === "all"
    ? _entries
    : _entries.filter((e) => e.kind === _activeFilter);

  if (filtered.length === 0) {
    list.innerHTML = `<div class="lm-activity__empty">No matching activity yet.</div>`;
    return;
  }

  list.innerHTML = filtered.map(_renderEntry).join("");
}

function _renderEntry(e) {
  const ago = _relativeAgo(e.at);
  const kindClass = `lm-activity__row--${e.kind}`;
  const successClass = e.success === false ? "lm-activity__row--failed" : "";
  const icon = _iconFor(e.kind);
  const dur = e.duration_ms != null ? `${Math.round(e.duration_ms)}ms` : "";
  const detail = _formatDetail(e.detail);

  return `
    <article class="lm-activity__row ${kindClass} ${successClass}">
      <div class="lm-activity__row-icon" aria-hidden="true">
        <span class="material-symbols-outlined">${icon}</span>
      </div>
      <div class="lm-activity__row-body">
        <div class="lm-activity__row-head">
          <span class="lm-activity__row-summary">${escapeHtml(e.summary || "")}</span>
          <span class="lm-activity__row-meta">
            ${e.actor === "bot" ? '<span class="lm-activity__chip lm-activity__chip--bot">bot</span>' : ""}
            ${e.actor === "user" ? '<span class="lm-activity__chip lm-activity__chip--user">you</span>' : ""}
            ${dur ? `<span class="lm-activity__chip lm-activity__chip--dur">${escapeHtml(dur)}</span>` : ""}
            ${e.success === false ? '<span class="lm-activity__chip lm-activity__chip--fail">failed</span>' : ""}
            <span class="lm-activity__row-time">${escapeHtml(ago)}</span>
          </span>
        </div>
        ${detail ? `<pre class="lm-activity__row-detail">${escapeHtml(detail)}</pre>` : ""}
      </div>
    </article>
  `;
}

function _formatDetail(d) {
  if (!d || typeof d !== "object") return "";
  const lines = [];
  for (const [k, v] of Object.entries(d).slice(0, 6)) {
    let val = v;
    if (typeof val === "object") {
      try { val = JSON.stringify(val); } catch (_) { val = String(val); }
    }
    if (val == null || val === "") continue;
    const s = String(val);
    lines.push(`${k}: ${s.length > 220 ? s.slice(0, 220) + "…" : s}`);
  }
  return lines.join("\n");
}

function _iconFor(kind) {
  switch (kind) {
    case "tool_call":       return "build";
    case "identity_change": return "smart_toy";
    case "fact_learned":    return "psychology";
    case "fact_forgotten":  return "delete";
    case "theme_change":    return "palette";
    case "memory_save":     return "save";
    default:                return "circle";
  }
}

function _relativeAgo(epoch) {
  if (!epoch) return "";
  const delta = Math.max(0, Date.now() / 1000 - epoch);
  if (delta < 5)    return "just now";
  if (delta < 60)   return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function _showBadge() {
  const dot = document.getElementById("activityBadge");
  if (dot) dot.hidden = false;
}
function _hideBadge() {
  const dot = document.getElementById("activityBadge");
  if (dot) dot.hidden = true;
}
