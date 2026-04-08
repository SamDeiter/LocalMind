/**
 * approvals_ui.js — Approval Queue & Corrections History
 * Human-in-the-loop review panel for jobs awaiting approval, output review,
 * or change requests. Includes a corrections ledger with promote-to-memory.
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

// ── State ──────────────────────────────────────────────────────
let _approvals = [];
let _corrections = [];
let _selectedApproval = null;
let _activeTab = "queue"; // "queue" | "history"
let _pollingInterval = null;
let _visible = false;

const POLL_MS = 10_000;

// ── Helpers ────────────────────────────────────────────────────
function timeAgo(ts) {
  if (!ts) return "--";
  const now = Date.now();
  const then = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : new Date(ts).getTime();
  const diff = Math.floor((now - then) / 1000);
  if (diff < 0) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function reviewTypeBadge(type) {
  const map = {
    output_review: { label: "Output Review", cls: "bg-violet-500/20 text-violet-400" },
    approval_gate: { label: "Approval Gate", cls: "bg-amber-500/20 text-amber-400" },
  };
  const info = map[type] || { label: type || "Review", cls: "bg-slate-500/20 text-slate-400" };
  return `<span class="text-[11px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full ${info.cls}">${escapeHtml(info.label)}</span>`;
}

function correctionStatusBadge(status) {
  const map = {
    pending: "bg-amber-500/20 text-amber-400",
    promoted: "bg-emerald-500/20 text-emerald-400",
    dismissed: "bg-slate-500/20 text-slate-500",
  };
  const cls = map[status] || "bg-slate-500/20 text-slate-400";
  return `<span class="text-[11px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full ${cls}">${escapeHtml(status || "pending")}</span>`;
}

function isImage(filename) {
  if (!filename) return false;
  const ext = filename.split(".").pop().toLowerCase();
  return ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext);
}

// ── Data fetching ──────────────────────────────────────────────
async function fetchApprovals() {
  try {
    const res = await fetch(`${API}/api/jobs?status=reviewing`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _approvals = Array.isArray(data) ? data : (data.jobs || data.items || []);
    // Sort oldest first so stale reviews surface
    _approvals.sort((a, b) => {
      const ta = a.created_at || a.timestamp || 0;
      const tb = b.created_at || b.timestamp || 0;
      return (typeof ta === "number" ? ta : new Date(ta).getTime()) -
             (typeof tb === "number" ? tb : new Date(tb).getTime());
    });
  } catch (err) {
    console.warn("[Approvals] fetch error:", err);
  }
}

async function fetchCorrections() {
  try {
    const res = await fetch(`${API}/api/corrections`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _corrections = Array.isArray(data) ? data : (data.corrections || data.items || []);
  } catch (err) {
    console.warn("[Approvals] corrections fetch error:", err);
  }
}

async function submitReview(jobId, action, feedback) {
  try {
    const res = await fetch(`${API}/api/jobs/${jobId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, feedback: feedback || undefined }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    showToast(
      action === "approve" ? "Approved -- pipeline continuing" :
      action === "reject" ? "Rejected -- job cancelled" :
      "Changes requested -- pipeline paused",
      action === "reject" ? "error" : "info",
    );
    return data;
  } catch (err) {
    console.error("[Approvals] review submit error:", err);
    showToast("Failed to submit review", "error");
    return null;
  }
}

async function submitCorrection(correction) {
  try {
    const res = await fetch(`${API}/api/corrections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(correction),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast("Correction submitted", "info");
    return await res.json();
  } catch (err) {
    console.error("[Approvals] correction submit error:", err);
    showToast("Failed to submit correction", "error");
    return null;
  }
}

async function promoteCorrection(correctionId) {
  try {
    const res = await fetch(`${API}/api/corrections/${correctionId}/promote`, { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast("Correction promoted to memory", "info");
    return await res.json();
  } catch (err) {
    console.error("[Approvals] promote error:", err);
    showToast("Failed to promote correction", "error");
    return null;
  }
}

// ── Badge for nav ──────────────────────────────────────────────
export function getApprovalCount() {
  return _approvals.length;
}

function updateNavBadge() {
  const badge = document.getElementById("approvalsBadge");
  if (!badge) return;
  const count = _approvals.length;
  badge.textContent = count;
  badge.setAttribute("aria-label", `${count} approval${count !== 1 ? "s" : ""} pending`);
  badge.classList.toggle("hidden", count === 0);
}

// ── Polling ────────────────────────────────────────────────────
function startPolling() {
  if (_pollingInterval) return;
  poll();
  _pollingInterval = setInterval(poll, POLL_MS);
}

function stopPolling() {
  if (_pollingInterval) {
    clearInterval(_pollingInterval);
    _pollingInterval = null;
  }
}

async function poll() {
  await fetchApprovals();
  updateNavBadge();
  if (_visible) renderQueue();
}

// ── Rendering: Container ───────────────────────────────────────
function getContainer() {
  return document.getElementById("approvalsView");
}

function buildContainer() {
  const existing = getContainer();
  if (existing && existing.querySelector("#approvalsList")) return existing;

  // If the element exists as a placeholder, re-use it; otherwise create it
  let view = existing;
  if (!view) {
    const parent = document.getElementById("mainDashboardView");
    if (!parent) { console.warn("[Approvals] mainDashboardView not found"); return null; }
    view = document.createElement("div");
    view.id = "approvalsView";
    parent.appendChild(view);
  }

  view.className = "hidden flex-1 flex flex-col overflow-hidden";
  view.setAttribute("role", "region");
  view.setAttribute("aria-label", "Approval queue and corrections history");
  view.innerHTML = buildShell();
  wireShellEvents(view);
  return view;
}

function buildShell() {
  return `
    <!-- Header -->
    <div class="flex items-center justify-between px-8 pt-8 pb-4 flex-shrink-0">
      <div class="flex items-center gap-3">
        <span class="material-symbols-outlined text-indigo-400 text-2xl">approval</span>
        <h2 class="text-xl font-headline font-bold tracking-tight text-slate-100">Approval Queue</h2>
        <span id="approvalsLiveCount" class="text-[11px] font-bold uppercase tracking-widest bg-indigo-500/20 text-indigo-400 px-2.5 py-1 rounded-full">0 pending</span>
      </div>
      <div class="flex items-center gap-2">
        <button id="approvalsRefreshBtn"
                class="bg-slate-800/60 hover:bg-slate-700/60 text-slate-300 text-xs font-bold px-4 py-2 rounded-lg uppercase tracking-wider transition-colors border border-slate-700/40"
                aria-label="Refresh approval queue">
          <span class="material-symbols-outlined text-xs align-middle mr-1">refresh</span> Refresh
        </button>
      </div>
    </div>

    <!-- Tabs -->
    <div class="flex items-center gap-1 px-8 mb-4 flex-shrink-0" role="tablist" aria-label="Approvals tabs">
      <button id="tabQueue" role="tab" aria-selected="true" aria-controls="panelQueue"
              class="approvals-tab text-xs font-bold uppercase tracking-widest px-4 py-2 rounded-lg transition-colors bg-indigo-500/15 text-indigo-400 border border-indigo-500/30">
        Queue
      </button>
      <button id="tabHistory" role="tab" aria-selected="false" aria-controls="panelHistory"
              class="approvals-tab text-xs font-bold uppercase tracking-widest px-4 py-2 rounded-lg transition-colors text-slate-500 hover:text-slate-300 border border-transparent">
        Corrections History
      </button>
    </div>

    <!-- Tab panels -->
    <div class="flex-1 overflow-hidden px-8 pb-8">
      <!-- Queue panel -->
      <div id="panelQueue" role="tabpanel" aria-labelledby="tabQueue" class="flex gap-6 h-full">
        <!-- Left: list -->
        <div class="w-[380px] flex-shrink-0 flex flex-col h-full">
          <div id="approvalsList" class="flex-1 overflow-y-auto custom-scrollbar space-y-3 pr-2" role="list" aria-label="Pending approvals"></div>
        </div>
        <!-- Right: review panel -->
        <div id="reviewPanel" class="flex-1 bg-slate-900/40 border border-slate-800/60 rounded-xl overflow-hidden flex flex-col">
          <div class="flex-1 flex items-center justify-center text-slate-500 text-sm">
            <div class="text-center">
              <span class="material-symbols-outlined text-4xl mb-3 block text-slate-600">rate_review</span>
              <p>Select an item to review</p>
            </div>
          </div>
        </div>
      </div>

      <!-- History panel -->
      <div id="panelHistory" role="tabpanel" aria-labelledby="tabHistory" class="hidden h-full overflow-y-auto custom-scrollbar">
        <div id="correctionsList" class="space-y-3" role="list" aria-label="Corrections history"></div>
      </div>
    </div>
  `;
}

function wireShellEvents(view) {
  // Refresh button
  const refreshBtn = view.querySelector("#approvalsRefreshBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.disabled = true;
      const icon = refreshBtn.querySelector(".material-symbols-outlined");
      if (icon) icon.classList.add("animate-spin");
      await poll();
      if (_activeTab === "history") {
        await fetchCorrections();
        renderHistory();
      }
      setTimeout(() => {
        refreshBtn.disabled = false;
        if (icon) icon.classList.remove("animate-spin");
      }, 600);
    });
  }

  // Tab switching
  const tabQueue = view.querySelector("#tabQueue");
  const tabHistory = view.querySelector("#tabHistory");
  const panelQueue = view.querySelector("#panelQueue");
  const panelHistory = view.querySelector("#panelHistory");

  const TAB_ACTIVE = "approvals-tab text-xs font-bold uppercase tracking-widest px-4 py-2 rounded-lg transition-colors bg-indigo-500/15 text-indigo-400 border border-indigo-500/30";
  const TAB_INACTIVE = "approvals-tab text-xs font-bold uppercase tracking-widest px-4 py-2 rounded-lg transition-colors text-slate-500 hover:text-slate-300 border border-transparent";

  if (tabQueue) {
    tabQueue.addEventListener("click", () => {
      _activeTab = "queue";
      tabQueue.setAttribute("aria-selected", "true");
      tabHistory.setAttribute("aria-selected", "false");
      tabQueue.className = TAB_ACTIVE;
      tabHistory.className = TAB_INACTIVE;
      panelQueue.classList.remove("hidden");
      panelQueue.classList.add("flex");
      panelHistory.classList.add("hidden");
      renderQueue();
    });
  }

  if (tabHistory) {
    tabHistory.addEventListener("click", async () => {
      _activeTab = "history";
      tabHistory.setAttribute("aria-selected", "true");
      tabQueue.setAttribute("aria-selected", "false");
      tabHistory.className = TAB_ACTIVE;
      tabQueue.className = TAB_INACTIVE;
      panelHistory.classList.remove("hidden");
      panelQueue.classList.add("hidden");
      panelQueue.classList.remove("flex");
      await fetchCorrections();
      renderHistory();
    });
  }
}

// ── Rendering: Queue list ──────────────────────────────────────
function renderQueue() {
  const listEl = document.getElementById("approvalsList");
  const countEl = document.getElementById("approvalsLiveCount");
  if (!listEl) return;

  if (countEl) countEl.textContent = `${_approvals.length} pending`;

  if (_approvals.length === 0) {
    listEl.innerHTML = `
      <div class="flex flex-col items-center justify-center py-16 text-slate-500">
        <span class="material-symbols-outlined text-4xl mb-3 text-slate-600">check_circle</span>
        <p class="text-sm font-medium">No pending reviews</p>
        <p class="text-xs text-slate-600 mt-1">All caught up</p>
      </div>`;
    return;
  }

  listEl.innerHTML = _approvals.map((item, idx) => {
    const id = item.id || item.job_id || idx;
    const isSelected = _selectedApproval && ((_selectedApproval.id || _selectedApproval.job_id) === id);
    const title = item.title || item.job_title || item.name || `Job #${id}`;
    const node = item.node || item.step || item.stage || "Unknown node";
    const reviewType = item.review_type || item.type || "output_review";
    const ts = item.created_at || item.timestamp;
    const requester = item.requester || item.requested_by || "system";

    return `
      <button
        class="w-full text-left p-4 rounded-xl border transition-all group focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50
          ${isSelected
            ? "bg-indigo-500/10 border-indigo-500/40 shadow-[0_0_20px_rgba(99,102,241,0.1)]"
            : "bg-slate-900/40 border-slate-800/60 hover:bg-slate-800/40 hover:border-slate-700/60"}"
        data-approval-id="${escapeHtml(String(id))}"
        data-approval-idx="${idx}"
        role="listitem"
        aria-label="Review ${escapeHtml(title)} at node ${escapeHtml(node)}"
      >
        <div class="flex items-start justify-between mb-2">
          <span class="text-xs font-bold text-slate-200 truncate max-w-[200px]">${escapeHtml(title)}</span>
          ${reviewTypeBadge(reviewType)}
        </div>
        <div class="text-[11px] text-slate-400 truncate mb-2">
          <span class="material-symbols-outlined text-[12px] align-middle mr-1">account_tree</span>
          ${escapeHtml(node)}
        </div>
        <div class="flex items-center justify-between text-xs text-slate-500">
          <span class="flex items-center gap-1">
            <span class="material-symbols-outlined text-[11px]">person</span>
            ${escapeHtml(requester)}
          </span>
          <span class="flex items-center gap-1" title="${ts ? new Date(typeof ts === "number" && ts < 1e12 ? ts * 1000 : ts).toLocaleString() : ""}">
            <span class="material-symbols-outlined text-[11px]">schedule</span>
            ${timeAgo(ts)}
          </span>
        </div>
      </button>`;
  }).join("");

  // Wire click handlers via event delegation
  listEl.querySelectorAll("[data-approval-idx]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.approvalIdx, 10);
      _selectedApproval = _approvals[idx] || null;
      renderQueue(); // re-render to update selection highlight
      renderReviewPanel();
    });
  });
}

// ── Rendering: Review Panel ────────────────────────────────────
function renderReviewPanel() {
  const panel = document.getElementById("reviewPanel");
  if (!panel) return;

  if (!_selectedApproval) {
    panel.innerHTML = `
      <div class="flex-1 flex items-center justify-center text-slate-500 text-sm">
        <div class="text-center">
          <span class="material-symbols-outlined text-4xl mb-3 block text-slate-600">rate_review</span>
          <p>Select an item to review</p>
        </div>
      </div>`;
    return;
  }

  const item = _selectedApproval;
  const id = item.id || item.job_id;
  const title = item.title || item.job_title || item.name || `Job #${id}`;
  const node = item.node || item.step || item.stage || "Unknown node";
  const reviewType = item.review_type || item.type || "output_review";
  const output = item.output || item.result || item.content || "";
  const filePath = item.file_path || item.output_file || null;
  const fileName = filePath ? filePath.split("/").pop().split("\\").pop() : null;
  const previousVersion = item.previous_version || item.before || null;
  const currentVersion = item.current_version || item.after || output || null;
  const hasDiff = previousVersion && currentVersion;

  panel.innerHTML = `
    <!-- Review header -->
    <div class="p-5 border-b border-slate-800/60 flex-shrink-0">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-bold text-slate-100 font-headline">${escapeHtml(title)}</h3>
        ${reviewTypeBadge(reviewType)}
      </div>
      <div class="flex items-center gap-4 text-xs text-slate-500">
        <span class="flex items-center gap-1">
          <span class="material-symbols-outlined text-[11px]">account_tree</span>
          ${escapeHtml(node)}
        </span>
        <span class="flex items-center gap-1">
          <span class="material-symbols-outlined text-[11px]">schedule</span>
          ${timeAgo(item.created_at || item.timestamp)}
        </span>
        <span class="flex items-center gap-1">
          <span class="material-symbols-outlined text-[11px]">person</span>
          ${escapeHtml(item.requester || item.requested_by || "system")}
        </span>
      </div>
    </div>

    <!-- Output preview -->
    <div class="flex-1 overflow-y-auto custom-scrollbar p-5 space-y-4">
      ${hasDiff ? renderDiffView(previousVersion, currentVersion) : ""}
      ${!hasDiff && output ? renderOutputPreview(output) : ""}
      ${fileName ? renderFileSection(filePath, fileName) : ""}
      ${!output && !fileName && !hasDiff ? `
        <div class="flex items-center justify-center py-8 text-slate-500 text-xs">
          <span class="material-symbols-outlined text-lg mr-2">info</span>
          No output content available for preview
        </div>` : ""}
    </div>

    <!-- Action buttons -->
    <div class="p-5 border-t border-slate-800/60 flex-shrink-0 space-y-4">
      <!-- Change request form (hidden by default) -->
      <div id="changeRequestForm" class="hidden space-y-3 p-4 bg-slate-950/60 border border-amber-500/20 rounded-xl" aria-label="Change request form">
        <div class="flex items-center justify-between">
          <span class="text-xs font-bold uppercase tracking-widest text-amber-400">Request Changes</span>
          <button id="closeChangeForm" class="text-slate-500 hover:text-slate-300 transition-colors" aria-label="Close change request form">
            <span class="material-symbols-outlined text-sm">close</span>
          </button>
        </div>
        <div>
          <label for="correctionType" class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1 block">What's wrong</label>
          <select id="correctionType"
                  class="w-full bg-slate-900 border border-slate-700/60 rounded-lg text-xs text-slate-300 px-3 py-2 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30 transition-colors">
            <option value="factual">Factual error</option>
            <option value="formatting">Formatting issue</option>
            <option value="structural">Structural problem</option>
            <option value="instruction_misunderstand">Instruction misunderstanding</option>
          </select>
        </div>
        <div>
          <label for="correctionTarget" class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1 block">Target location</label>
          <input id="correctionTarget" type="text" placeholder='e.g. slide 7, title'
                 class="w-full bg-slate-900 border border-slate-700/60 rounded-lg text-xs text-slate-300 px-3 py-2 placeholder:text-slate-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30 transition-colors" />
        </div>
        <div>
          <label for="correctionValue" class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1 block">Corrected value</label>
          <input id="correctionValue" type="text" placeholder="What it should say..."
                 class="w-full bg-slate-900 border border-slate-700/60 rounded-lg text-xs text-slate-300 px-3 py-2 placeholder:text-slate-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30 transition-colors" />
        </div>
        <div>
          <label for="correctionNotes" class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1 block">Additional notes</label>
          <textarea id="correctionNotes" rows="2" placeholder="Any extra context..."
                    class="w-full bg-slate-900 border border-slate-700/60 rounded-lg text-xs text-slate-300 px-3 py-2 placeholder:text-slate-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30 transition-colors resize-none"></textarea>
        </div>
        <button id="submitChangeRequest"
                class="w-full bg-amber-500/20 hover:bg-amber-500/30 text-amber-400 text-xs font-bold px-4 py-2.5 rounded-lg uppercase tracking-wider transition-colors border border-amber-500/30">
          <span class="material-symbols-outlined text-xs align-middle mr-1">send</span> Submit Change Request
        </button>
      </div>

      <!-- Primary actions -->
      <div id="reviewActions" class="flex items-center gap-3">
        <button id="btnApprove"
                class="flex-1 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 text-xs font-bold px-4 py-3 rounded-xl uppercase tracking-wider transition-all border border-emerald-500/30 hover:shadow-[0_0_20px_rgba(16,185,129,0.15)] focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                aria-label="Approve and continue pipeline">
          <span class="material-symbols-outlined text-sm align-middle mr-1">check_circle</span> Approve
        </button>
        <button id="btnRequestChanges"
                class="flex-1 bg-amber-500/15 hover:bg-amber-500/25 text-amber-400 text-xs font-bold px-4 py-3 rounded-xl uppercase tracking-wider transition-all border border-amber-500/30 hover:shadow-[0_0_20px_rgba(245,158,11,0.15)] focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50"
                aria-label="Request changes and pause pipeline">
          <span class="material-symbols-outlined text-sm align-middle mr-1">edit_note</span> Request Changes
        </button>
        <button id="btnReject"
                class="flex-1 bg-red-500/15 hover:bg-red-500/25 text-red-400 text-xs font-bold px-4 py-3 rounded-xl uppercase tracking-wider transition-all border border-red-500/30 hover:shadow-[0_0_20px_rgba(239,68,68,0.15)] focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50"
                aria-label="Reject and cancel the job">
          <span class="material-symbols-outlined text-sm align-middle mr-1">cancel</span> Reject
        </button>
      </div>
    </div>
  `;

  wireReviewPanelEvents(id);
}

function renderOutputPreview(output) {
  const text = typeof output === "string" ? output : JSON.stringify(output, null, 2);
  const truncated = text.length > 3000;
  const display = truncated ? text.slice(0, 3000) : text;
  return `
    <div>
      <div class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-2">Output Preview</div>
      <div class="bg-slate-950/60 border border-slate-800/40 rounded-lg p-4 max-h-[300px] overflow-y-auto custom-scrollbar">
        <pre class="text-xs text-slate-300 whitespace-pre-wrap break-words font-mono leading-relaxed">${escapeHtml(display)}</pre>
        ${truncated ? '<div class="text-xs text-slate-500 mt-2 italic">Content truncated -- full output available after approval</div>' : ""}
      </div>
    </div>`;
}

function renderFileSection(filePath, fileName) {
  const imgPreview = isImage(fileName)
    ? `<div class="mt-3"><img src="${API}/api/files/${encodeURIComponent(filePath)}" alt="Preview of ${escapeHtml(fileName)}" class="max-h-48 rounded-lg border border-slate-700/40" loading="lazy" /></div>`
    : "";

  return `
    <div>
      <div class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-2">File Output</div>
      <div class="bg-slate-950/60 border border-slate-800/40 rounded-lg p-4 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <span class="material-symbols-outlined text-indigo-400">description</span>
          <div>
            <div class="text-xs text-slate-200 font-medium">${escapeHtml(fileName)}</div>
            <div class="text-xs text-slate-500">${escapeHtml(filePath)}</div>
          </div>
        </div>
        <a href="${API}/api/files/${encodeURIComponent(filePath)}?download=1"
           download="${escapeHtml(fileName)}"
           class="bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-400 text-xs font-bold px-3 py-2 rounded-lg transition-colors border border-indigo-500/30"
           aria-label="Download ${escapeHtml(fileName)}">
          <span class="material-symbols-outlined text-xs align-middle mr-1">download</span> Download
        </a>
      </div>
      ${imgPreview}
    </div>`;
}

function renderDiffView(before, after) {
  const beforeLines = String(before).split("\n");
  const afterLines = String(after).split("\n");

  const maxLines = Math.max(beforeLines.length, afterLines.length);
  let beforeHtml = "";
  let afterHtml = "";

  for (let i = 0; i < maxLines; i++) {
    const bLine = i < beforeLines.length ? beforeLines[i] : "";
    const aLine = i < afterLines.length ? afterLines[i] : "";
    const changed = bLine !== aLine;

    const lineNum = `<span class="inline-block w-8 text-right mr-3 text-slate-600 select-none">${i + 1}</span>`;

    if (changed) {
      beforeHtml += `<div class="bg-red-500/10 border-l-2 border-red-500/40 px-2 py-0.5">${lineNum}<span class="text-red-300">${escapeHtml(bLine)}</span></div>`;
      afterHtml += `<div class="bg-emerald-500/10 border-l-2 border-emerald-500/40 px-2 py-0.5">${lineNum}<span class="text-emerald-300">${escapeHtml(aLine)}</span></div>`;
    } else {
      const row = `<div class="px-2 py-0.5">${lineNum}<span class="text-slate-400">${escapeHtml(bLine)}</span></div>`;
      beforeHtml += row;
      afterHtml += row;
    }
  }

  return `
    <div>
      <div class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-2">Changes (diff)</div>
      <div class="grid grid-cols-2 gap-2">
        <div class="bg-slate-950/60 border border-red-500/20 rounded-lg overflow-hidden">
          <div class="px-3 py-1.5 bg-red-500/10 border-b border-red-500/20 text-[11px] font-bold uppercase tracking-widest text-red-400">Before</div>
          <div class="p-2 max-h-[250px] overflow-y-auto custom-scrollbar font-mono text-[11px] leading-relaxed">${beforeHtml}</div>
        </div>
        <div class="bg-slate-950/60 border border-emerald-500/20 rounded-lg overflow-hidden">
          <div class="px-3 py-1.5 bg-emerald-500/10 border-b border-emerald-500/20 text-[11px] font-bold uppercase tracking-widest text-emerald-400">After</div>
          <div class="p-2 max-h-[250px] overflow-y-auto custom-scrollbar font-mono text-[11px] leading-relaxed">${afterHtml}</div>
        </div>
      </div>
    </div>`;
}

function wireReviewPanelEvents(jobId) {
  const btnApprove = document.getElementById("btnApprove");
  const btnRequestChanges = document.getElementById("btnRequestChanges");
  const btnReject = document.getElementById("btnReject");
  const changeForm = document.getElementById("changeRequestForm");
  const closeChangeForm = document.getElementById("closeChangeForm");
  const submitChangeBtn = document.getElementById("submitChangeRequest");
  const reviewActions = document.getElementById("reviewActions");

  if (btnApprove) {
    btnApprove.addEventListener("click", async () => {
      setActionsDisabled(true);
      btnApprove.innerHTML = '<span class="material-symbols-outlined text-sm align-middle mr-1 animate-spin">refresh</span> Approving...';
      const result = await submitReview(jobId, "approve");
      if (result) {
        _selectedApproval = null;
        await poll();
        renderReviewPanel();
      } else {
        setActionsDisabled(false);
        btnApprove.innerHTML = '<span class="material-symbols-outlined text-sm align-middle mr-1">check_circle</span> Approve';
      }
    });
  }

  if (btnReject) {
    btnReject.addEventListener("click", async () => {
      setActionsDisabled(true);
      btnReject.innerHTML = '<span class="material-symbols-outlined text-sm align-middle mr-1 animate-spin">refresh</span> Rejecting...';
      const result = await submitReview(jobId, "reject");
      if (result) {
        _selectedApproval = null;
        await poll();
        renderReviewPanel();
      } else {
        setActionsDisabled(false);
        btnReject.innerHTML = '<span class="material-symbols-outlined text-sm align-middle mr-1">cancel</span> Reject';
      }
    });
  }

  if (btnRequestChanges && changeForm && reviewActions) {
    btnRequestChanges.addEventListener("click", () => {
      changeForm.classList.remove("hidden");
      reviewActions.classList.add("hidden");
      // Focus the first field for accessibility
      const firstInput = changeForm.querySelector("select");
      if (firstInput) firstInput.focus();
    });
  }

  if (closeChangeForm && changeForm && reviewActions) {
    closeChangeForm.addEventListener("click", () => {
      changeForm.classList.add("hidden");
      reviewActions.classList.remove("hidden");
    });
  }

  if (submitChangeBtn) {
    submitChangeBtn.addEventListener("click", async () => {
      const correctionType = document.getElementById("correctionType")?.value || "factual";
      const target = document.getElementById("correctionTarget")?.value || "";
      const correctedValue = document.getElementById("correctionValue")?.value || "";
      const notes = document.getElementById("correctionNotes")?.value || "";

      const feedback = {
        correction_type: correctionType,
        target: target,
        corrected_value: correctedValue,
        notes: notes,
      };

      submitChangeBtn.disabled = true;
      submitChangeBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin">refresh</span> Submitting...';

      // Submit review with change request
      const reviewResult = await submitReview(jobId, "request_changes", feedback);

      // Also log as a correction
      if (reviewResult) {
        await submitCorrection({
          job_id: jobId,
          correction_type: correctionType,
          target: target,
          original_value: _selectedApproval?.output || _selectedApproval?.result || "",
          corrected_value: correctedValue,
          notes: notes,
        });
        _selectedApproval = null;
        await poll();
        renderReviewPanel();
      } else {
        submitChangeBtn.disabled = false;
        submitChangeBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1">send</span> Submit Change Request';
      }
    });
  }
}

function setActionsDisabled(disabled) {
  ["btnApprove", "btnRequestChanges", "btnReject"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = disabled;
  });
}

// ── Rendering: Corrections History ─────────────────────────────
function renderHistory() {
  const listEl = document.getElementById("correctionsList");
  if (!listEl) return;

  if (_corrections.length === 0) {
    listEl.innerHTML = `
      <div class="flex flex-col items-center justify-center py-16 text-slate-500">
        <span class="material-symbols-outlined text-4xl mb-3 text-slate-600">history</span>
        <p class="text-sm font-medium">No corrections yet</p>
        <p class="text-xs text-slate-600 mt-1">Corrections from change requests will appear here</p>
      </div>`;
    return;
  }

  const typeLabels = {
    factual: "Factual",
    formatting: "Formatting",
    structural: "Structural",
    instruction_misunderstand: "Instruction Misunderstanding",
  };

  listEl.innerHTML = _corrections.map((c, idx) => {
    const id = c.id || idx;
    const type = c.correction_type || c.type || "unknown";
    const target = c.target || c.target_location || "--";
    const original = c.original_value || c.original || "";
    const corrected = c.corrected_value || c.corrected || "";
    const status = c.status || "pending";
    const notes = c.notes || "";
    const ts = c.created_at || c.timestamp;

    return `
      <div class="bg-slate-900/40 border border-slate-800/60 rounded-xl p-5 transition-all hover:bg-slate-800/30" role="listitem">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-3">
            <span class="text-[11px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-400">${escapeHtml(typeLabels[type] || type)}</span>
            ${correctionStatusBadge(status)}
          </div>
          <span class="text-xs text-slate-500 flex items-center gap-1">
            <span class="material-symbols-outlined text-[11px]">schedule</span>
            ${timeAgo(ts)}
          </span>
        </div>

        <div class="text-xs text-slate-400 mb-3">
          <span class="text-xs font-bold uppercase tracking-widest text-slate-500">Target:</span>
          <span class="ml-2">${escapeHtml(target)}</span>
        </div>

        ${original || corrected ? `
          <div class="grid grid-cols-2 gap-3 mb-3">
            <div class="bg-slate-950/60 border border-red-500/10 rounded-lg p-3">
              <div class="text-[11px] font-bold uppercase tracking-widest text-red-400 mb-1">Original</div>
              <div class="text-xs text-slate-400 whitespace-pre-wrap break-words max-h-[100px] overflow-y-auto custom-scrollbar">${original ? escapeHtml(original.length > 500 ? original.slice(0, 500) + "..." : original) : '<span class="italic text-slate-600">--</span>'}</div>
            </div>
            <div class="bg-slate-950/60 border border-emerald-500/10 rounded-lg p-3">
              <div class="text-[11px] font-bold uppercase tracking-widest text-emerald-400 mb-1">Corrected</div>
              <div class="text-xs text-slate-300 whitespace-pre-wrap break-words max-h-[100px] overflow-y-auto custom-scrollbar">${corrected ? escapeHtml(corrected.length > 500 ? corrected.slice(0, 500) + "..." : corrected) : '<span class="italic text-slate-600">--</span>'}</div>
            </div>
          </div>` : ""}

        ${notes ? `
          <div class="text-xs text-slate-500 mb-3 italic">${escapeHtml(notes)}</div>` : ""}

        ${status === "pending" ? `
          <div class="flex items-center gap-2">
            <button class="promote-btn bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 text-xs font-bold px-3 py-1.5 rounded-lg transition-colors border border-emerald-500/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50"
                    data-correction-id="${escapeHtml(String(id))}"
                    aria-label="Promote correction to workspace memory">
              <span class="material-symbols-outlined text-xs align-middle mr-1">psychology</span> Promote to Memory
            </button>
          </div>` : ""}
      </div>`;
  }).join("");

  // Wire promote buttons
  listEl.querySelectorAll(".promote-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const cid = btn.dataset.correctionId;
      btn.disabled = true;
      btn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin">refresh</span> Promoting...';
      const result = await promoteCorrection(cid);
      if (result) {
        await fetchCorrections();
        renderHistory();
      } else {
        btn.disabled = false;
        btn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1">psychology</span> Promote to Memory';
      }
    });
  });
}

// ── Show / Hide ────────────────────────────────────────────────
export function showApprovalsView() {
  const view = getContainer() || buildContainer();
  if (!view) return;

  // Hide other views
  const mainScroll = document.getElementById("mainScrollArea");
  const swarmView = document.getElementById("swarmDashboardView");
  if (mainScroll) mainScroll.classList.add("hidden");
  if (swarmView) swarmView.classList.add("hidden");

  view.classList.remove("hidden");
  view.style.display = "";
  _visible = true;

  // Ensure we have current data
  poll();
  if (_activeTab === "history") {
    fetchCorrections().then(renderHistory);
  }
}

function hideApprovalsView() {
  const view = getContainer();
  if (view) view.classList.add("hidden");
  _visible = false;
}

// ── Init ───────────────────────────────────────────────────────
export function initApprovalsUI() {
  buildContainer();

  // Wire existing nav button in sidebar
  wireNavButton();

  // Start background polling for badge count regardless of visibility
  startPolling();

  // Wire other nav buttons to hide approvals view to avoid blank screen overlaps
  const otherNavIds = ["overviewBtn", "editorToggle", "swarmDashBtn", "newChatBtn", "systemPromptBtn"];
  otherNavIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("click", () => {
        hideApprovalsView();
      });
    }
  });
}

function wireNavButton() {
  // Use existing #approvalsBtn in the sidebar; fall back to injecting one
  const btn = document.getElementById("approvalsBtn") || document.getElementById("approvalsNavBtn");
  if (!btn) return;

  // Ensure the badge has aria-live for screen readers
  const badge = document.getElementById("approvalsBadge");
  if (badge) {
    badge.setAttribute("aria-live", "polite");
    badge.setAttribute("aria-label", "Pending approvals count");
  }

  btn.addEventListener("click", () => {
    showApprovalsView();
    // Update active state
    btn.classList.add("bg-indigo-500/10", "text-indigo-400", "border-indigo-500/20");
    btn.classList.remove("text-slate-400");
  });

  // Clear active state when other nav buttons are clicked
  const otherNavIds = ["overviewBtn", "editorToggle", "swarmDashBtn", "newChatBtn", "systemPromptBtn"];
  otherNavIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("click", () => {
        btn.classList.remove("bg-indigo-500/10", "text-indigo-400", "border-indigo-500/20");
        btn.classList.add("text-slate-400");
      });
    }
  });
}
