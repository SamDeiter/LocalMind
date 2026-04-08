/**
 * AI Time Machine — Timeline Slider UI
 *
 * Renders a horizontal scrollable timeline of all AI actions with
 * inspect / restore capabilities.  Pure vanilla JS, no frameworks.
 */

import { API } from "./state.js";

// ── Constants ───────────────────────────────────────────────────
const POLL_MS = 30_000;

const ACTION_ICONS = {
  file_edit: "\u270F\uFE0F",        // pencil
  proposal_execute: "\uD83D\uDE80",  // rocket
  tool_call: "\uD83D\uDD27",        // wrench
  config_change: "\u2699\uFE0F",    // gear
};

const ACTION_COLORS = {
  file_edit: "#818cf8",
  proposal_execute: "#34d399",
  tool_call: "#f59e0b",
  config_change: "#a78bfa",
};

const DEFAULT_COLOR = "#64748b";

// ── Public API ──────────────────────────────────────────────────
export function initTimeMachine() {
  _injectStyles();
  loadTimeline();
  setInterval(loadTimeline, POLL_MS);
}

export async function loadTimeline() {
  const container = document.getElementById("timeMachineTimeline");
  if (!container) return;

  try {
    const res = await fetch(`${API}/api/time-machine/timeline`);
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    _renderTimeline(container, data.timeline || []);
  } catch {
    container.innerHTML = `<p class="tm-empty">Unable to load timeline</p>`;
  }
}

// ── Render: horizontal timeline ─────────────────────────────────
function _renderTimeline(container, actions) {
  if (!actions.length) {
    container.innerHTML = `<p class="tm-empty">No actions recorded yet</p>`;
    return;
  }

  container.innerHTML = "";
  const track = document.createElement("div");
  track.className = "tm-track";

  // Horizontal line
  const line = document.createElement("div");
  line.className = "tm-line";
  track.appendChild(line);

  actions.forEach((a) => {
    const color = ACTION_COLORS[a.action_type] || DEFAULT_COLOR;
    const icon = ACTION_ICONS[a.action_type] || "\u2022";
    const ts = new Date(a.timestamp * 1000).toLocaleString();

    const node = document.createElement("button");
    node.className = "tm-node";
    node.style.borderColor = color;
    node.style.opacity = a.reverted ? "0.4" : "1";
    node.innerHTML = `<span class="tm-icon">${icon}</span>`;
    node.title = `${a.entity_id || a.entity_type}\n${ts}`;
    node.addEventListener("click", () => _showActionDetail(a.id));

    // Tooltip on hover (custom)
    const tip = document.createElement("span");
    tip.className = "tm-tip";
    tip.textContent = `${a.entity_id || a.entity_type} \u2014 ${ts}`;
    node.appendChild(tip);

    track.appendChild(node);
  });

  container.appendChild(track);
}

// ── Render: action detail modal ─────────────────────────────────
async function _showActionDetail(actionId) {
  // Remove any existing detail panel
  document.querySelectorAll(".tm-modal-overlay").forEach((e) => e.remove());

  const overlay = document.createElement("div");
  overlay.className = "tm-modal-overlay";
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });

  const modal = document.createElement("div");
  modal.className = "tm-modal";
  modal.innerHTML = `<p class="tm-empty">Loading...</p>`;
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  try {
    const res = await fetch(`${API}/api/time-machine/actions/${actionId}`);
    if (!res.ok) throw new Error(res.statusText);
    const action = await res.json();
    const color = ACTION_COLORS[action.action_type] || DEFAULT_COLOR;
    const icon = ACTION_ICONS[action.action_type] || "\u2022";
    const ts = new Date(action.timestamp * 1000).toLocaleString();

    let html = `<div class="tm-modal-header"><span style="color:${color};font-size:18px">${icon}</span><strong>${action.action_type}</strong><span class="tm-meta">${action.entity_type} / ${action.entity_id}</span><span class="tm-meta">${ts}</span><button class="tm-close">\u2715</button></div>`;

    if (action.before_state != null || action.after_state != null) {
      const fmt = (v) => typeof v === "string" ? v : JSON.stringify(v, null, 2);
      const before = fmt(action.before_state) || "(empty)";
      const after = fmt(action.after_state) || "(empty)";
      html += `<div class="tm-diff-grid"><div><h4 class="tm-diff-label">Before</h4><pre class="tm-pre tm-pre-before">${_esc(before)}</pre></div><div><h4 class="tm-diff-label">After</h4><pre class="tm-pre tm-pre-after">${_esc(after)}</pre></div></div>`;
    }

    // Unified diff
    if (action.diff) {
      html += `<div class="tm-diff-section"><h4 class="tm-diff-label">Diff</h4><div id="tmDiffBlock"></div></div>`;
    }

    // Restore button
    if (!action.reverted) {
      html += `<button class="tm-restore-btn" id="tmRestoreBtn">Restore to before-state</button>`;
    } else {
      html += `<p class="tm-meta" style="margin-top:12px">This action has been reverted.</p>`;
    }

    modal.innerHTML = html;

    // Render diff lines
    if (action.diff) {
      _renderDiff(document.getElementById("tmDiffBlock"), action.diff);
    }

    // Close button
    modal.querySelector(".tm-close")?.addEventListener("click", () => overlay.remove());

    // Restore handler
    const restoreBtn = document.getElementById("tmRestoreBtn");
    if (restoreBtn) {
      restoreBtn.addEventListener("click", async () => {
        restoreBtn.disabled = true;
        restoreBtn.textContent = "Restoring\u2026";
        try {
          const r = await fetch(`${API}/api/time-machine/restore/${actionId}`, { method: "POST" });
          const result = await r.json();
          if (result.ok) {
            restoreBtn.textContent = "\u2713 Restored";
            restoreBtn.style.background = "#059669";
            loadTimeline();
          } else {
            restoreBtn.textContent = result.message || "Restore failed";
            restoreBtn.style.background = "#dc2626";
          }
        } catch {
          restoreBtn.textContent = "Network error";
          restoreBtn.style.background = "#dc2626";
        }
      });
    }
  } catch {
    modal.innerHTML = `<p class="tm-empty">Failed to load action details</p>`;
  }
}

// ── Render: unified diff ────────────────────────────────────────
function _renderDiff(container, diff) {
  if (!container) return;
  const lines = (typeof diff === "string" ? diff : JSON.stringify(diff, null, 2)).split("\n");
  const pre = document.createElement("pre");
  pre.className = "tm-pre";
  lines.forEach((line) => {
    const span = document.createElement("span");
    span.style.display = "block";
    if (line.startsWith("+")) {
      span.style.color = "#34d399";
      span.style.background = "rgba(52,211,153,0.08)";
    } else if (line.startsWith("-")) {
      span.style.color = "#f87171";
      span.style.background = "rgba(248,113,113,0.08)";
    } else if (line.startsWith("@@")) {
      span.style.color = "#818cf8";
    } else {
      span.style.color = "#94a3b8";
    }
    span.textContent = line;
    pre.appendChild(span);
  });
  container.appendChild(pre);
}

// ── Helpers ─────────────────────────────────────────────────────
function _esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function _injectStyles() {
  if (document.getElementById("tm-styles")) return;
  const style = document.createElement("style");
  style.id = "tm-styles";
  style.textContent = [
    ".tm-empty{color:#64748b;font-size:11px;text-align:center;padding:16px 0;font-family:monospace}",
    ".tm-track{display:flex;align-items:center;gap:8px;overflow-x:auto;padding:12px 8px;position:relative;min-height:56px}",
    ".tm-line{position:absolute;top:50%;left:0;right:0;height:2px;background:#334155;pointer-events:none}",
    ".tm-node{position:relative;z-index:1;flex-shrink:0;width:36px;height:36px;border-radius:50%;border:2px solid;background:#1e293b;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:transform .15s,box-shadow .15s}",
    ".tm-node:hover{transform:scale(1.25);box-shadow:0 0 12px rgba(99,102,241,.4)}",
    ".tm-icon{font-size:14px;line-height:1}",
    ".tm-tip{display:none;position:absolute;bottom:calc(100% + 8px);left:50%;transform:translateX(-50%);background:#0f172a;color:#e2e8f0;padding:4px 8px;border-radius:6px;font-size:10px;white-space:nowrap;border:1px solid #334155;pointer-events:none;z-index:10}",
    ".tm-node:hover .tm-tip{display:block}",
    ".tm-modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center}",
    ".tm-modal{background:#0f172a;border:1px solid #334155;border-radius:12px;padding:20px;max-width:720px;width:90%;max-height:80vh;overflow-y:auto;color:#e2e8f0;font-family:ui-monospace,monospace;font-size:12px}",
    ".tm-modal-header{display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap}",
    ".tm-modal-header strong{color:#e2e8f0;font-size:14px}",
    ".tm-meta{color:#64748b;font-size:10px}",
    ".tm-close{margin-left:auto;background:none;border:none;color:#64748b;font-size:18px;cursor:pointer;padding:0 4px}",
    ".tm-close:hover{color:#e2e8f0}",
    ".tm-diff-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}",
    ".tm-diff-label{color:#94a3b8;font-size:10px;text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px}",
    ".tm-diff-section{margin-bottom:12px}",
    ".tm-pre{background:#1e293b;padding:10px;border-radius:8px;overflow-x:auto;font-size:11px;line-height:1.5;color:#e2e8f0;margin:0;white-space:pre-wrap;word-break:break-all}",
    ".tm-pre-before{border-left:3px solid #f87171}",
    ".tm-pre-after{border-left:3px solid #34d399}",
    ".tm-restore-btn{margin-top:12px;padding:8px 20px;border:none;border-radius:8px;background:#818cf8;color:#fff;font-size:12px;font-weight:600;cursor:pointer;transition:background .15s}",
    ".tm-restore-btn:hover{background:#6366f1}",
    ".tm-restore-btn:disabled{opacity:.6;cursor:not-allowed}",
  ].join("\n");
  document.head.appendChild(style);
}
