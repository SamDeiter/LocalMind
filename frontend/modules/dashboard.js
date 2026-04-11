/**
 * dashboard.js -- Enterprise Dashboard
 * ======================================
 * Dashboard-first layout with active jobs, recent completions, template
 * quick-launch, approval queue summary, and system health metrics.
 *
 * Renders inside `#mainDashboardView` which is moved into `#messagesContainer`
 * as the default landing view (replaces the old chatEmptyState).
 *
 * Exports:
 *   initDashboard()  -- bootstrap: build shell, start polling, connect SSE
 *   stopDashboard()  -- tear down: stop polling, close SSE
 *   updateDashboardMetrics()  -- refresh hardware metrics (called externally)
 *   updateStatusBar()         -- refresh connection status (called externally)
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _dashboardInterval = null;
let _sseSource = null;
let _sseRetryDelay = 1000;
const _SSE_MAX_DELAY = 60_000;
let _statusFailCount = 0;
const _OFFLINE_THRESHOLD = 3;
let _activeJobs = [];
let _recentCompletions = [];
let _templates = [];
let _pendingApprovals = [];
let _mounted = false;

// ---------------------------------------------------------------------------
// Status config (mirrored from jobs_ui.js for consistency)
// ---------------------------------------------------------------------------

const STATUS_CFG = {
  pending:    { color: "slate",   icon: "schedule",      label: "Pending" },
  planning:   { color: "blue",    icon: "edit_note",     label: "Planning" },
  executing:  { color: "amber",   icon: "play_circle",   label: "Executing" },
  reviewing:  { color: "purple",  icon: "rate_review",   label: "Reviewing" },
  done:       { color: "emerald", icon: "check_circle",  label: "Done" },
  failed:     { color: "red",     icon: "error",         label: "Failed" },
  cancelled:  { color: "slate",   icon: "cancel",        label: "Cancelled" },
  cancelling: { color: "amber",   icon: "pending",       label: "Cancelling" },
};

// Tailwind JIT safelist -- ensures CDN scanner finds these dynamic classes
// prettier-ignore
const _TW_SAFELIST = [
  "bg-slate-500/15","text-slate-400","border-slate-500/20",
  "bg-blue-500/15","text-blue-400","border-blue-500/20",
  "bg-amber-500/15","text-amber-400","border-amber-500/20",
  "bg-purple-500/15","text-purple-400","border-purple-500/20",
  "bg-emerald-500/15","text-emerald-400","border-emerald-500/20",
  "bg-red-500/15","text-red-400","border-red-500/20",
  "hover:border-slate-500/30","hover:border-blue-500/30",
  "hover:border-amber-500/30","hover:border-purple-500/30",
  "hover:border-emerald-500/30","hover:border-red-500/30",
  "bg-slate-500","bg-blue-500","bg-amber-500",
  "bg-purple-500","bg-emerald-500","bg-red-500",
  "text-blue-300","text-emerald-300","text-amber-300","text-red-300","text-purple-300",
  "border-blue-500/30","border-emerald-500/30","border-amber-500/30",
  "border-red-500/30","border-purple-500/30",
];
void _TW_SAFELIST;

// ---------------------------------------------------------------------------
// DOM helper
// ---------------------------------------------------------------------------

const _el = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Time helpers
// ---------------------------------------------------------------------------

function _timeAgo(iso) {
  if (!iso) return "just now";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 0) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function _formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

// ---------------------------------------------------------------------------
// ETA estimation (same logic as jobs_ui)
// ---------------------------------------------------------------------------

function _estimateETA(nodes) {
  if (!nodes || nodes.length === 0) return null;
  const completed = nodes.filter(
    (n) => n.status === "completed" && n.started_at && n.completed_at,
  );
  if (completed.length === 0) return null;

  const totalMs = completed.reduce((sum, n) => {
    return sum + (new Date(n.completed_at).getTime() - new Date(n.started_at).getTime());
  }, 0);
  const avgMs = totalMs / completed.length;
  const remaining = nodes.filter((n) => !["completed", "failed", "cancelled", "skipped"].includes(n.status)).length;
  const etaMs = remaining * avgMs;

  if (etaMs < 60_000) return `~${Math.ceil(etaMs / 1000)}s`;
  if (etaMs < 3_600_000) return `~${Math.ceil(etaMs / 60_000)}m`;
  return `~${(etaMs / 3_600_000).toFixed(1)}h`;
}

// ---------------------------------------------------------------------------
// Shell HTML
// ---------------------------------------------------------------------------

function _buildDashboardHTML() {
  return `
  <div id="dashboardLiveRegion" class="sr-only" aria-live="polite" aria-atomic="true" role="status"></div>

  <div class="flex flex-col gap-6 p-6 lg:p-8 w-full max-w-7xl mx-auto" role="main" aria-label="Dashboard">

    <!-- ── Header ────────────────────────────────────────────── -->
    <div class="flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
          <span class="material-symbols-outlined text-white text-lg" aria-hidden="true">dashboard</span>
        </div>
        <div>
          <h1 class="text-lg font-headline font-bold tracking-tight text-slate-100">Dashboard</h1>
          <p class="text-[11px] text-slate-500 font-mono uppercase tracking-widest">Mission Control</p>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <span id="dashSSEDot" class="w-2 h-2 rounded-full bg-slate-600 transition-colors" aria-hidden="true"></span>
        <span id="dashSSELabel" class="text-[11px] font-mono text-slate-500">Connecting...</span>
        <button id="dashRefreshBtn"
                class="ml-2 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 text-xs font-bold px-3 py-1.5 rounded-lg uppercase tracking-wider transition-colors border border-indigo-500/20 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                aria-label="Refresh dashboard">
          <span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">refresh</span>Refresh
        </button>
      </div>
    </div>

    <!-- ── System Health Strip ───────────────────────────────── -->
    <section aria-label="System health" class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <div class="bg-slate-900/50 border border-slate-800/40 rounded-xl p-4 flex flex-col gap-1" role="status" aria-label="CPU usage">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-indigo-400 text-base" aria-hidden="true">memory</span>
          <span class="text-[11px] text-slate-500 uppercase tracking-widest font-bold">CPU</span>
        </div>
        <span id="dashCpuVal" class="text-lg font-bold text-slate-200 font-mono">--%</span>
      </div>
      <div class="bg-slate-900/50 border border-slate-800/40 rounded-xl p-4 flex flex-col gap-1" role="status" aria-label="RAM usage">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-cyan-400 text-base" aria-hidden="true">storage</span>
          <span class="text-[11px] text-slate-500 uppercase tracking-widest font-bold">RAM</span>
        </div>
        <span id="dashRamVal" class="text-lg font-bold text-slate-200 font-mono">--</span>
      </div>
      <div class="bg-slate-900/50 border border-slate-800/40 rounded-xl p-4 flex flex-col gap-1" role="status" aria-label="GPU VRAM usage">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-amber-400 text-base" aria-hidden="true">developer_board</span>
          <span class="text-[11px] text-slate-500 uppercase tracking-widest font-bold">VRAM</span>
        </div>
        <span id="dashVramVal" class="text-lg font-bold text-slate-200 font-mono">--</span>
      </div>
      <div class="bg-slate-900/50 border border-slate-800/40 rounded-xl p-4 flex flex-col gap-1" role="status" aria-label="Active model">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-emerald-400 text-base" aria-hidden="true">smart_toy</span>
          <span class="text-[11px] text-slate-500 uppercase tracking-widest font-bold">Model</span>
        </div>
        <span id="dashModelVal" class="text-sm font-bold text-slate-200 truncate">No model</span>
        <span id="dashServerStatus" class="text-[11px] font-mono text-emerald-400">Online</span>
      </div>
    </section>

    <!-- ── Cost Overview ────────────────────────────────────── -->
    <section aria-labelledby="dashCostTitle">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-emerald-400 text-lg" aria-hidden="true">savings</span>
          <h2 id="dashCostTitle" class="text-sm font-headline font-bold tracking-tight text-slate-100">Cost Overview</h2>
        </div>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3" role="region" aria-label="Cost overview metrics">
        <div class="bg-slate-900/50 border border-emerald-800/30 rounded-xl p-4 flex flex-col gap-1" role="status" aria-label="Saved this week">
          <div class="text-[11px] text-slate-500 uppercase tracking-widest font-bold">Saved This Week</div>
          <span id="dashSavedWeek" class="text-lg font-bold text-emerald-400 font-mono">--</span>
          <span id="dashCloudWeek" class="text-[11px] font-mono text-slate-500"></span>
        </div>
        <div class="bg-slate-900/50 border border-emerald-800/30 rounded-xl p-4 flex flex-col gap-1" role="status" aria-label="Saved this month">
          <div class="text-[11px] text-slate-500 uppercase tracking-widest font-bold">Saved This Month</div>
          <span id="dashSavedMonth" class="text-lg font-bold text-emerald-400 font-mono">--</span>
          <span id="dashCloudMonth" class="text-[11px] font-mono text-slate-500"></span>
        </div>
        <div class="bg-slate-900/50 border border-slate-800/40 rounded-xl p-4 flex flex-col gap-1" role="status" aria-label="Total tokens processed">
          <div class="text-[11px] text-slate-500 uppercase tracking-widest font-bold">Tokens</div>
          <span id="dashTokensTotal" class="text-lg font-bold text-cyan-400 font-mono">--</span>
          <span id="dashTokensBreakdown" class="text-[11px] font-mono text-slate-500"></span>
        </div>
        <div class="bg-slate-900/50 border border-slate-800/40 rounded-xl p-4 flex flex-col gap-1" role="status" aria-label="All-time savings">
          <div class="text-[11px] text-slate-500 uppercase tracking-widest font-bold">All-Time Saved</div>
          <span id="dashSavedTotal" class="text-lg font-bold text-emerald-400 font-mono">--</span>
          <span id="dashJobsCount" class="text-[11px] font-mono text-slate-500"></span>
        </div>
      </div>
    </section>

    <!-- ── Active Jobs ───────────────────────────────────────── -->
    <section aria-labelledby="dashActiveJobsTitle">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-amber-400 text-lg" aria-hidden="true">play_circle</span>
          <h2 id="dashActiveJobsTitle" class="text-sm font-headline font-bold tracking-tight text-slate-100">Active Jobs</h2>
          <span id="dashActiveCount" class="text-[11px] font-bold uppercase tracking-widest bg-amber-500/15 text-amber-400 px-2 py-0.5 rounded-full">0</span>
        </div>
        <button id="dashViewAllJobs"
                class="text-[11px] font-bold uppercase tracking-widest text-indigo-400 hover:text-indigo-300 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 rounded px-2 py-1"
                aria-label="View all jobs">
          View all
          <span class="material-symbols-outlined text-[11px] align-middle" aria-hidden="true">arrow_forward</span>
        </button>
      </div>
      <div id="dashActiveJobsGrid" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3" role="list" aria-label="Active jobs">
        <div class="text-sm text-slate-600 italic col-span-full py-6 text-center" role="status">Loading...</div>
      </div>
    </section>

    <!-- ── Recent Completions ────────────────────────────────── -->
    <section aria-labelledby="dashRecentTitle">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-emerald-400 text-lg" aria-hidden="true">check_circle</span>
          <h2 id="dashRecentTitle" class="text-sm font-headline font-bold tracking-tight text-slate-100">Recent Completions</h2>
          <span id="dashRecentCount" class="text-[11px] font-bold uppercase tracking-widest bg-emerald-500/15 text-emerald-400 px-2 py-0.5 rounded-full">0</span>
        </div>
        <button id="dashViewAllCompleted"
                class="text-[11px] font-bold uppercase tracking-widest text-indigo-400 hover:text-indigo-300 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 rounded px-2 py-1"
                aria-label="View all completed jobs">
          View all
          <span class="material-symbols-outlined text-[11px] align-middle" aria-hidden="true">arrow_forward</span>
        </button>
      </div>
      <div id="dashRecentGrid" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3" role="list" aria-label="Recently completed jobs">
        <div class="text-sm text-slate-600 italic col-span-full py-6 text-center" role="status">Loading...</div>
      </div>
    </section>

    <!-- ── Quick Launch Templates ────────────────────────────── -->
    <section aria-labelledby="dashTemplatesTitle">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-indigo-400 text-lg" aria-hidden="true">dashboard_customize</span>
          <h2 id="dashTemplatesTitle" class="text-sm font-headline font-bold tracking-tight text-slate-100">Quick Launch</h2>
          <span id="dashTemplateCount" class="text-[11px] font-bold uppercase tracking-widest bg-indigo-500/15 text-indigo-400 px-2 py-0.5 rounded-full">0</span>
        </div>
        <button id="dashViewAllTemplates"
                class="text-[11px] font-bold uppercase tracking-widest text-indigo-400 hover:text-indigo-300 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 rounded px-2 py-1"
                aria-label="View all templates">
          View all templates
          <span class="material-symbols-outlined text-[11px] align-middle" aria-hidden="true">arrow_forward</span>
        </button>
      </div>
      <div id="dashTemplatesGrid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" role="list" aria-label="Pipeline templates">
        <div class="text-sm text-slate-600 italic col-span-full py-6 text-center" role="status">Loading...</div>
      </div>
    </section>

    <!-- ── Approval Queue ────────────────────────────────────── -->
    <section aria-labelledby="dashApprovalsTitle">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-purple-400 text-lg" aria-hidden="true">approval</span>
          <h2 id="dashApprovalsTitle" class="text-sm font-headline font-bold tracking-tight text-slate-100">Approval Queue</h2>
          <span id="dashApprovalCount" class="text-[11px] font-bold uppercase tracking-widest bg-purple-500/15 text-purple-400 px-2 py-0.5 rounded-full">0</span>
        </div>
        <button id="dashViewAllApprovals"
                class="text-[11px] font-bold uppercase tracking-widest text-indigo-400 hover:text-indigo-300 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 rounded px-2 py-1"
                aria-label="View approval queue">
          View all
          <span class="material-symbols-outlined text-[11px] align-middle" aria-hidden="true">arrow_forward</span>
        </button>
      </div>
      <div id="dashApprovalsGrid" class="space-y-2" role="list" aria-label="Pending approvals">
        <div class="text-sm text-slate-600 italic py-6 text-center" role="status">Loading...</div>
      </div>
    </section>

  </div>`;
}

// ---------------------------------------------------------------------------
// Mounting
// ---------------------------------------------------------------------------

function _mountDashboard() {
  const container = _el("mainDashboardView");
  if (!container) return;

  // Populate the dashboard
  container.innerHTML = _buildDashboardHTML();
  container.className = "w-full";
  container.removeAttribute("aria-hidden");

  // Move into the messagesContainer so it's visible in the chat panel
  const messagesArea = _el("messagesContainer");
  if (messagesArea) {
    // Insert at the top of messagesContainer, before any messages
    const chatEmpty = _el("chatEmptyState");
    if (chatEmpty) {
      chatEmpty.style.display = "none";
    }
    messagesArea.insertBefore(container, messagesArea.firstChild);
  }

  _mounted = true;
  _wireEvents();
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

function _wireEvents() {
  // Refresh button
  _el("dashRefreshBtn")?.addEventListener("click", () => {
    _refreshAll();
  });

  // View all buttons -- navigate to full views
  _el("dashViewAllJobs")?.addEventListener("click", () => {
    _navigateToJobsView();
  });

  _el("dashViewAllCompleted")?.addEventListener("click", () => {
    _navigateToJobsView();
  });

  _el("dashViewAllTemplates")?.addEventListener("click", () => {
    _navigateToTemplatesView();
  });

  _el("dashViewAllApprovals")?.addEventListener("click", () => {
    _navigateToApprovalsView();
  });
}

// ---------------------------------------------------------------------------
// Navigation helpers -- trigger other views via their exported functions
// ---------------------------------------------------------------------------

function _navigateToJobsView() {
  // Switch to pipeline tab and show jobs
  try {
    import("./nav_rail.js").then((m) => m.switchNav("pipeline")).catch(() => {});
    import("./jobs_ui.js").then((mod) => {
      if (mod.showJobsView) mod.showJobsView();
    }).catch(() => {});
  } catch {
    // Fallback
  }
}

function _navigateToTemplatesView() {
  try {
    import("./templates_ui.js").then((mod) => {
      if (mod.showTemplatesView) mod.showTemplatesView();
    }).catch(() => {});
  } catch {
    // Fallback
  }
}

function _navigateToApprovalsView() {
  try {
    import("./nav_rail.js").then((m) => m.switchNav("pipeline")).catch(() => {});
    import("./approvals_ui.js").then((mod) => {
      if (mod.showApprovalsView) mod.showApprovalsView();
    }).catch(() => {});
  } catch {
    // Fallback
  }
}

function _navigateToJobDetail(jobId) {
  try {
    import("./nav_rail.js").then((m) => m.switchNav("pipeline")).catch(() => {});
    import("./jobs_ui.js").then((mod) => {
      if (mod.showJobsView) mod.showJobsView();
      setTimeout(() => {
        const card = document.querySelector(`[data-job-id="${jobId}"]`);
        if (card) card.click();
      }, 300);
    }).catch(() => {});
  } catch {
    // Fallback
  }
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function _fetchActiveJobs() {
  try {
    const res = await fetch(`${API}/api/jobs?status=executing,planning,reviewing`);
    if (!res.ok) return;
    const data = await res.json();
    _activeJobs = data.jobs || (Array.isArray(data) ? data : []);
    // Sort most recent first
    _activeJobs.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  } catch (err) {
    console.warn("[dashboard] Failed to fetch active jobs:", err);
  }
}

async function _fetchRecentCompletions() {
  try {
    const res = await fetch(`${API}/api/jobs?status=done&limit=5`);
    if (!res.ok) return;
    const data = await res.json();
    _recentCompletions = data.jobs || (Array.isArray(data) ? data : []);
    // Sort most recent first
    _recentCompletions.sort((a, b) => (b.completed_at || b.created_at || "").localeCompare(a.completed_at || a.created_at || ""));
    _recentCompletions = _recentCompletions.slice(0, 5);
  } catch (err) {
    console.warn("[dashboard] Failed to fetch completions:", err);
  }
}

async function _fetchTemplates() {
  try {
    const res = await fetch(`${API}/api/jobs/templates`);
    if (!res.ok) {
      // Try alternate endpoint
      const res2 = await fetch(`${API}/api/templates?limit=6`);
      if (!res2.ok) return;
      const data2 = await res2.json();
      _templates = data2.templates || (Array.isArray(data2) ? data2 : []);
      return;
    }
    const data = await res.json();
    _templates = data.templates || (Array.isArray(data) ? data : []);
    _templates = _templates.slice(0, 6);
  } catch (err) {
    console.warn("[dashboard] Failed to fetch templates:", err);
  }
}

async function _fetchApprovals() {
  try {
    // Try the pending approvals endpoint first
    const res = await fetch(`${API}/api/approvals/pending`);
    if (res.ok) {
      const data = await res.json();
      _pendingApprovals = data.approvals || data.items || (Array.isArray(data) ? data : []);
      return;
    }
    // Fallback: fetch reviewing jobs
    const res2 = await fetch(`${API}/api/jobs?status=reviewing`);
    if (!res2.ok) return;
    const data2 = await res2.json();
    _pendingApprovals = data2.jobs || (Array.isArray(data2) ? data2 : []);
  } catch (err) {
    console.warn("[dashboard] Failed to fetch approvals:", err);
  }
}

// ---------------------------------------------------------------------------
// Cost Overview: fetch + render
// ---------------------------------------------------------------------------

let _costStats = null;

async function _fetchCostStats() {
  try {
    const res = await fetch(`${API}/api/jobs/stats`);
    if (!res.ok) return;
    _costStats = await res.json();
  } catch (err) {
    console.warn("[dashboard] Failed to fetch cost stats:", err);
  }
}

function _formatCents(cents) {
  if (cents == null || cents === 0) return "$0.00";
  if (cents < 100) return `${cents.toFixed(2)}\u00a2`;
  return `$${(cents / 100).toFixed(2)}`;
}

function _formatTokenCount(n) {
  if (n == null || n === 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function _renderCostOverview() {
  if (!_costStats) return;

  const s = _costStats;

  // Saved this week (cloud equivalent cost — what you'd pay on Gemini)
  const savedWeekEl = _el("dashSavedWeek");
  const cloudWeekEl = _el("dashCloudWeek");
  if (savedWeekEl) savedWeekEl.textContent = _formatCents(s.cloud_cost_this_week || 0);
  if (cloudWeekEl) cloudWeekEl.textContent = (s.cloud_cost_this_week || 0) > 0
    ? `vs Gemini API equivalent`
    : "run jobs to start tracking";

  // Saved this month
  const savedMonthEl = _el("dashSavedMonth");
  const cloudMonthEl = _el("dashCloudMonth");
  if (savedMonthEl) savedMonthEl.textContent = _formatCents(s.cloud_cost_this_month || 0);
  if (cloudMonthEl) cloudMonthEl.textContent = (s.cloud_cost_this_month || 0) > 0
    ? `vs Gemini API equivalent`
    : "run jobs to start tracking";

  // Tokens
  const tokensEl = _el("dashTokensTotal");
  const tokensBrkEl = _el("dashTokensBreakdown");
  const totalTokens = (s.total_tokens_in || 0) + (s.total_tokens_out || 0);
  if (tokensEl) tokensEl.textContent = _formatTokenCount(totalTokens);
  if (tokensBrkEl) tokensBrkEl.textContent = totalTokens > 0
    ? `${_formatTokenCount(s.total_tokens_in)} in / ${_formatTokenCount(s.total_tokens_out)} out`
    : "no usage yet";

  // All-time saved
  const savedTotalEl = _el("dashSavedTotal");
  const jobsCountEl = _el("dashJobsCount");
  if (savedTotalEl) savedTotalEl.textContent = _formatCents(s.total_cloud_cost_cents || 0);
  if (jobsCountEl) jobsCountEl.textContent = `across ${s.total_jobs || 0} jobs`;
}

// ---------------------------------------------------------------------------
// Rendering: Active Jobs
// ---------------------------------------------------------------------------

function _renderActiveJobs() {
  const grid = _el("dashActiveJobsGrid");
  const badge = _el("dashActiveCount");
  if (!grid) return;

  if (badge) badge.textContent = String(_activeJobs.length);

  if (_activeJobs.length === 0) {
    grid.innerHTML = `
      <div class="col-span-full py-8 text-center" role="status">
        <span class="material-symbols-outlined text-3xl text-slate-700 mb-2 block" aria-hidden="true">check_circle</span>
        <p class="text-sm text-slate-400">No active jobs</p>
        <p class="text-[11px] text-slate-600 mt-1">Create one from the chat, use the task form, or launch a template below.</p>
      </div>`;
    return;
  }

  grid.innerHTML = _activeJobs.map((job) => {
    const cfg = STATUS_CFG[job.status] || STATUS_CFG.pending;
    const isRunning = ["executing", "planning"].includes(job.status);
    const spinClass = isRunning ? "animate-spin" : "";

    // Progress calculation
    const nodes = job.nodes || [];
    const nodeTotal = job.node_count ?? nodes.length;
    const nodeCompleted = job.nodes_completed ?? nodes.filter((n) => n.status === "completed").length;
    const nodePct = nodeTotal > 0 ? Math.round((nodeCompleted / nodeTotal) * 100) : 0;

    // ETA
    let etaStr = "";
    if (isRunning && nodes.length > 0) {
      const eta = _estimateETA(nodes);
      if (eta) etaStr = eta;
    }

    return `
    <div class="bg-slate-900/50 border border-slate-800/50 rounded-xl p-4 space-y-3 cursor-pointer
                hover:bg-slate-800/40 hover:border-${cfg.color}-500/30 transition-all group"
         role="listitem" tabindex="0"
         data-dash-job-id="${escapeHtml(job.id)}"
         aria-label="Job: ${escapeHtml(job.title)}, status ${cfg.label}">
      <div class="flex items-start justify-between gap-2">
        <h3 class="text-sm font-semibold text-slate-200 group-hover:text-white transition-colors truncate flex-1">
          ${escapeHtml(job.title)}
        </h3>
        <span class="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full shrink-0
                     bg-${cfg.color}-500/15 text-${cfg.color}-400 border border-${cfg.color}-500/20">
          <span class="material-symbols-outlined text-[10px] ${spinClass}" aria-hidden="true">${isRunning ? "progress_activity" : cfg.icon}</span>
          ${cfg.label}
        </span>
      </div>
      ${nodeTotal > 0 ? `
      <div class="space-y-1">
        <div class="w-full bg-slate-800/60 h-1.5 rounded-full overflow-hidden">
          <div class="bg-${cfg.color}-500 h-full rounded-full transition-all duration-500"
               style="width: ${nodePct}%"
               role="progressbar" aria-valuenow="${nodePct}" aria-valuemin="0" aria-valuemax="100"
               aria-label="Progress: ${nodePct}%"></div>
        </div>
        <div class="flex items-center justify-between text-[11px] font-mono text-slate-600">
          <span>${nodeCompleted}/${nodeTotal} nodes</span>
          ${etaStr ? `<span class="text-amber-400/70">ETA ${escapeHtml(etaStr)}</span>` : ""}
        </div>
      </div>` : ""}
      <div class="text-[11px] font-mono text-slate-600 flex items-center gap-1">
        <span class="material-symbols-outlined text-[11px]" aria-hidden="true">schedule</span>
        ${escapeHtml(_timeAgo(job.created_at))}
      </div>
    </div>`;
  }).join("");

  _wireJobCardClicks(grid, "data-dash-job-id");
}

// ---------------------------------------------------------------------------
// Rendering: Recent Completions
// ---------------------------------------------------------------------------

function _renderRecentCompletions() {
  const grid = _el("dashRecentGrid");
  const badge = _el("dashRecentCount");
  if (!grid) return;

  if (badge) badge.textContent = String(_recentCompletions.length);

  if (_recentCompletions.length === 0) {
    grid.innerHTML = `
      <div class="col-span-full py-8 text-center" role="status">
        <span class="material-symbols-outlined text-3xl text-slate-700 mb-2 block" aria-hidden="true">inbox</span>
        <p class="text-sm text-slate-500">No completed jobs yet</p>
      </div>`;
    return;
  }

  grid.innerHTML = _recentCompletions.map((job) => {
    const fileCount = job.file_count ?? (job.files ? job.files.filter((f) => f.file_type === "output").length : 0);
    const completedTime = job.completed_at || job.updated_at || job.created_at;
    const outputFiles = (job.files || []).filter((f) => f.file_type === "output").slice(0, 3);

    return `
    <div class="bg-slate-900/50 border border-slate-800/50 rounded-xl p-4 space-y-3 cursor-pointer
                hover:bg-slate-800/40 hover:border-emerald-500/30 transition-all group"
         role="listitem" tabindex="0"
         data-dash-job-id="${escapeHtml(job.id)}"
         aria-label="Completed job: ${escapeHtml(job.title)}">
      <div class="flex items-start justify-between gap-2">
        <h3 class="text-sm font-semibold text-slate-200 group-hover:text-white transition-colors truncate flex-1">
          ${escapeHtml(job.title)}
        </h3>
        <span class="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full shrink-0
                     bg-emerald-500/15 text-emerald-400">
          <span class="material-symbols-outlined text-[10px]" aria-hidden="true">check_circle</span>
          Done
        </span>
      </div>
      ${outputFiles.length > 0 ? `
      <div class="flex flex-wrap gap-1.5">
        ${outputFiles.map((f) => `
          <a href="${API}/api/jobs/${encodeURIComponent(job.id)}/files/${encodeURIComponent(f.id || f.file_id)}"
             class="inline-flex items-center gap-1 text-[11px] bg-slate-800/60 hover:bg-slate-700/60 text-slate-300 hover:text-indigo-400 px-2 py-1 rounded-lg border border-slate-700/40 transition-colors"
             download="${escapeHtml(f.filename || f.name || 'output')}"
             title="Download ${escapeHtml(f.filename || f.name || 'output')}"
             aria-label="Download file: ${escapeHtml(f.filename || f.name || 'output')}"
             onclick="event.stopPropagation()">
            <span class="material-symbols-outlined text-[11px]" aria-hidden="true">download</span>
            <span class="truncate max-w-[100px]">${escapeHtml(f.filename || f.name || "output")}</span>
          </a>
        `).join("")}
        ${fileCount > 3 ? `<span class="text-[11px] text-slate-500 self-center">+${fileCount - 3} more</span>` : ""}
      </div>` : ""}
      <div class="flex items-center gap-3 text-[11px] font-mono text-slate-600">
        <span class="flex items-center gap-1">
          <span class="material-symbols-outlined text-[11px]" aria-hidden="true">schedule</span>
          ${escapeHtml(_timeAgo(completedTime))}
        </span>
        ${fileCount > 0 ? `
        <span class="flex items-center gap-1">
          <span class="material-symbols-outlined text-[11px]" aria-hidden="true">attach_file</span>
          ${fileCount} file${fileCount !== 1 ? "s" : ""}
        </span>` : ""}
      </div>
    </div>`;
  }).join("");

  _wireJobCardClicks(grid, "data-dash-job-id");
}

// ---------------------------------------------------------------------------
// Rendering: Quick Launch Templates
// ---------------------------------------------------------------------------

function _renderTemplates() {
  const grid = _el("dashTemplatesGrid");
  const badge = _el("dashTemplateCount");
  if (!grid) return;

  if (badge) badge.textContent = String(_templates.length);

  if (_templates.length === 0) {
    grid.innerHTML = `
      <div class="col-span-full py-8 text-center" role="status">
        <span class="material-symbols-outlined text-3xl text-slate-700 mb-2 block" aria-hidden="true">layers</span>
        <p class="text-sm text-slate-400">No templates yet</p>
        <p class="text-[11px] text-slate-600 mt-1">Complete a pipeline job, then save it as a reusable template from the job detail view.</p>
      </div>`;
    return;
  }

  grid.innerHTML = _templates.map((tmpl) => {
    const nodes = tmpl.nodes || [];
    const nodeCount = tmpl.node_count ?? nodes.length;
    const usageCount = tmpl.usage_count ?? tmpl.run_count ?? 0;

    // Mini pipeline preview (max 4 node pills)
    let miniPipelineHtml = "";
    if (nodes.length > 0) {
      const maxShow = 4;
      const visible = nodes.slice(0, maxShow);
      const overflow = nodes.length - maxShow;
      const pills = visible.map((n, i) => {
        const title = n.title || `Step ${i + 1}`;
        const short = title.length > 10 ? title.slice(0, 9) + "\u2026" : title;
        return `<span class="inline-flex items-center bg-indigo-500/10 text-indigo-400/70 text-[10px] font-mono px-1.5 py-0.5 rounded border border-indigo-500/15 whitespace-nowrap" title="${escapeHtml(title)}">${escapeHtml(short)}</span>`;
      });
      const parts = pills.reduce((acc, pill, i) => {
        acc.push(pill);
        if (i < pills.length - 1) acc.push('<span class="text-slate-700 text-[10px]" aria-hidden="true">\u2192</span>');
        return acc;
      }, []);
      if (overflow > 0) parts.push(`<span class="text-[10px] text-slate-600 font-mono">+${overflow}</span>`);
      miniPipelineHtml = `<div class="flex items-center gap-1 overflow-x-auto custom-scrollbar pb-0.5" aria-label="Pipeline flow">${parts.join("")}</div>`;
    }

    return `
    <div class="bg-slate-900/50 border border-slate-800/50 rounded-xl p-4 space-y-3
                hover:bg-slate-800/40 hover:border-indigo-500/30 transition-all group"
         role="listitem" tabindex="0"
         data-dash-template-id="${escapeHtml(tmpl.id)}"
         aria-label="Template: ${escapeHtml(tmpl.name)}">
      <div class="flex items-start justify-between gap-2">
        <div class="flex-1 min-w-0">
          <h3 class="text-sm font-semibold text-slate-200 group-hover:text-white transition-colors truncate">
            ${escapeHtml(tmpl.name)}
          </h3>
          ${tmpl.description ? `<p class="text-[11px] text-slate-500 mt-0.5 line-clamp-2">${escapeHtml(tmpl.description.substring(0, 80))}</p>` : ""}
        </div>
        <span class="material-symbols-outlined text-indigo-400/50 group-hover:text-indigo-400 text-lg transition-colors shrink-0" aria-hidden="true">
          play_arrow
        </span>
      </div>
      ${miniPipelineHtml}
      <div class="flex items-center gap-3 text-[11px] font-mono text-slate-600">
        ${nodeCount > 0 ? `
        <span class="flex items-center gap-1">
          <span class="material-symbols-outlined text-[11px]" aria-hidden="true">account_tree</span>
          ${nodeCount} node${nodeCount !== 1 ? "s" : ""}
        </span>` : ""}
        ${usageCount > 0 ? `
        <span class="flex items-center gap-1">
          <span class="material-symbols-outlined text-[11px]" aria-hidden="true">replay</span>
          ${usageCount} run${usageCount !== 1 ? "s" : ""}
        </span>` : ""}
      </div>
      <button class="dash-launch-template w-full bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 text-[11px] font-bold uppercase tracking-widest py-1.5 rounded-lg transition-colors border border-indigo-500/20
                     focus:outline-none focus:ring-2 focus:ring-indigo-400"
              data-template-id="${escapeHtml(tmpl.id)}"
              data-template-name="${escapeHtml(tmpl.name)}"
              aria-label="Launch template: ${escapeHtml(tmpl.name)}">
        <span class="material-symbols-outlined text-[11px] align-middle mr-1" aria-hidden="true">rocket_launch</span>
        Launch
      </button>
    </div>`;
  }).join("");

  // Wire launch buttons
  grid.querySelectorAll(".dash-launch-template").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      _launchTemplate(btn.dataset.templateId, btn.dataset.templateName);
    });
    btn.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        _launchTemplate(btn.dataset.templateId, btn.dataset.templateName);
      }
    });
  });

  // Wire card clicks to navigate to templates view
  grid.querySelectorAll("[data-dash-template-id]").forEach((card) => {
    card.addEventListener("click", () => {
      _navigateToTemplatesView();
    });
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        _navigateToTemplatesView();
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Rendering: Approval Queue
// ---------------------------------------------------------------------------

function _renderApprovals() {
  const grid = _el("dashApprovalsGrid");
  const badge = _el("dashApprovalCount");
  if (!grid) return;

  if (badge) badge.textContent = String(_pendingApprovals.length);

  // Update the sidebar approval badges as well
  const sidebarBadge = _el("approvalsBadge");
  if (sidebarBadge) {
    sidebarBadge.textContent = String(_pendingApprovals.length);
    sidebarBadge.setAttribute("aria-label", `${_pendingApprovals.length} approvals pending`);
  }
  const workBadge = _el("workApprovalBadge");
  if (workBadge) {
    workBadge.textContent = String(_pendingApprovals.length);
    if (_pendingApprovals.length > 0) {
      workBadge.classList.remove("hidden");
    } else {
      workBadge.classList.add("hidden");
    }
  }

  if (_pendingApprovals.length === 0) {
    grid.innerHTML = `
      <div class="py-6 text-center" role="status">
        <span class="material-symbols-outlined text-2xl text-slate-700 mb-1 block" aria-hidden="true">verified</span>
        <p class="text-sm text-slate-400">No pending approvals</p>
        <p class="text-[11px] text-slate-600 mt-1">Approvals appear here when a job needs your review.</p>
      </div>`;
    return;
  }

  grid.innerHTML = _pendingApprovals.map((item) => {
    const title = item.title || item.job_title || item.description || "Untitled";
    const jobId = item.job_id || item.id;
    const created = item.created_at || item.timestamp;
    const reviewType = item.review_type || item.type || "approval_gate";
    const typeLabel = reviewType === "output_review" ? "Output Review" : "Approval Gate";
    const typeCls = reviewType === "output_review"
      ? "bg-violet-500/15 text-violet-400"
      : "bg-amber-500/15 text-amber-400";

    return `
    <div class="bg-slate-900/50 border border-slate-800/50 rounded-xl p-4 flex items-center gap-4 cursor-pointer
                hover:bg-slate-800/40 hover:border-purple-500/30 transition-all group"
         role="listitem" tabindex="0"
         data-dash-approval-id="${escapeHtml(jobId)}"
         aria-label="Pending approval: ${escapeHtml(title)}">
      <div class="w-8 h-8 rounded-lg bg-purple-500/15 flex items-center justify-center shrink-0">
        <span class="material-symbols-outlined text-purple-400 text-base" aria-hidden="true">rate_review</span>
      </div>
      <div class="flex-1 min-w-0">
        <h3 class="text-sm font-semibold text-slate-200 group-hover:text-white transition-colors truncate">
          ${escapeHtml(title)}
        </h3>
        <div class="flex items-center gap-2 mt-0.5">
          <span class="text-[10px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded-full ${typeCls}">
            ${escapeHtml(typeLabel)}
          </span>
          ${created ? `<span class="text-[11px] font-mono text-slate-600">${escapeHtml(_timeAgo(created))}</span>` : ""}
        </div>
      </div>
      <span class="material-symbols-outlined text-slate-600 group-hover:text-purple-400 transition-colors" aria-hidden="true">
        arrow_forward
      </span>
    </div>`;
  }).join("");

  // Wire clicks
  grid.querySelectorAll("[data-dash-approval-id]").forEach((card) => {
    const handler = () => _navigateToApprovalsView();
    card.addEventListener("click", handler);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handler();
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Shared: wire job card clicks
// ---------------------------------------------------------------------------

function _wireJobCardClicks(container, attr) {
  container.querySelectorAll(`[${attr}]`).forEach((card) => {
    const jobId = card.getAttribute(attr);
    const handler = () => _navigateToJobDetail(jobId);
    card.addEventListener("click", handler);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handler();
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Template launch
// ---------------------------------------------------------------------------

async function _launchTemplate(templateId, templateName) {
  try {
    const res = await fetch(`${API}/api/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: templateName || "Template job",
        template_id: templateId,
        mode: "pipeline",
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const job = await res.json();
    showToast(`Job started: ${escapeHtml(job.title || templateName)}`, "info");
    _announce(`Job started from template: ${job.title || templateName}`);
    // Refresh to show the new active job
    await _refreshAll();
    // Navigate to the detail view
    if (job.id) _navigateToJobDetail(job.id);
  } catch (err) {
    console.error("[dashboard] Template launch failed:", err);
    showToast(`Couldn't launch the template. ${err.message || "Check that the backend is running and try again."}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Hardware / Status (existing functionality, expanded)
// ---------------------------------------------------------------------------

async function updateDashboardMetrics() {
  try {
    const r = await fetch(`${API}/api/hardware`);
    if (!r.ok) return;
    const hw = await r.json();
    const sys = hw.system || {};
    const models = hw.models || [];

    // ── CPU ──
    const cpuPct = sys.cpu_percent ?? 0;
    const cpuEl = _el("cpuVal");
    if (cpuEl) cpuEl.textContent = `${cpuPct}%`;
    const dashCpu = _el("dashCpuVal");
    if (dashCpu) dashCpu.textContent = `${cpuPct}%`;

    // Update brain pulse color based on load
    const brainPulse = _el("brainPulse");
    if (brainPulse) {
      if (cpuPct > 80) brainPulse.style.backgroundColor = "#ff6b98";
      else if (cpuPct > 50) brainPulse.style.backgroundColor = "#f59e0b";
      else brainPulse.style.backgroundColor = "#4fdbc8";
    }

    // ── RAM ──
    const ramUsed = sys.ram_used_gb ?? 0;
    const ramTotal = sys.ram_total_gb ?? 0;
    const ramEl = _el("ramVal");
    if (ramEl) ramEl.textContent = `${ramUsed.toFixed(1)} / ${ramTotal.toFixed(0)}GB`;
    const dashRam = _el("dashRamVal");
    if (dashRam) dashRam.textContent = `${ramUsed.toFixed(1)} / ${ramTotal.toFixed(0)}GB`;

    // ── VRAM (from loaded models) ──
    let vramUsed = 0;
    let modelName = "No model";
    if (models.length > 0) {
      vramUsed = models.reduce((sum, m) => sum + (m.vram_gb || 0), 0);
      modelName = models[0].name || "Unknown";
    }
    const vramEl = _el("vramVal");
    if (vramEl) vramEl.textContent = vramUsed > 0 ? `${vramUsed.toFixed(1)}GB` : "N/A";
    const dashVram = _el("dashVramVal");
    if (dashVram) dashVram.textContent = vramUsed > 0 ? `${vramUsed.toFixed(1)}GB` : "N/A";

    // Update dashboard model info
    const dashModel = _el("dashModelVal");
    if (dashModel) dashModel.textContent = modelName;

    // Update status badge with model
    const versionBadge = _el("versionBadge");
    if (versionBadge && modelName !== "No model") versionBadge.textContent = modelName;
  } catch {
    // Silently fail -- hardware API may not be available
  }
}

async function updateStatusBar() {
  const dot = _el("brainPulse");
  const connEl = _el("brainStatus");
  const dashStatus = _el("dashServerStatus");
  try {
    const vr = await fetch(`${API}/api/version`);
    if (vr.ok) {
      _statusFailCount = 0;
      const vd = await vr.json();
      if (dot) dot.style.backgroundColor = "#4fdbc8";
      if (connEl) connEl.textContent = "Online";
      if (dashStatus) {
        dashStatus.textContent = "Online";
        dashStatus.className = "text-[11px] font-mono text-emerald-400";
      }
      const versionEl = document.querySelector(".version-badge");
      if (versionEl) versionEl.textContent = `v${vd.version || "0.0.0"}`;
    } else {
      _statusFailCount++;
      if (_statusFailCount >= _OFFLINE_THRESHOLD) {
        if (dot) dot.style.backgroundColor = "#ff6b98";
        if (connEl) connEl.textContent = "Degraded";
        if (dashStatus) {
          dashStatus.textContent = "Degraded";
          dashStatus.className = "text-[11px] font-mono text-amber-400";
        }
      }
    }

    // Ideas count for reasoning grid
    try {
      const mr = await fetch(`${API}/api/memories`);
      if (mr.ok) {
        const memories = await mr.json();
        const ideasEl = _el("brainIdeas");
        if (ideasEl) ideasEl.textContent = Array.isArray(memories) ? memories.length : "0";
      }
    } catch { /* ignore */ }
  } catch {
    _statusFailCount++;
    if (_statusFailCount >= _OFFLINE_THRESHOLD) {
      if (dot) dot.style.backgroundColor = "#ff6b98";
      if (connEl) connEl.textContent = "Offline";
      if (dashStatus) {
        dashStatus.textContent = "Offline";
        dashStatus.className = "text-[11px] font-mono text-red-400";
      }
    }
  }
}

// ---------------------------------------------------------------------------
// SSE -- live updates from /api/jobs/activity
// ---------------------------------------------------------------------------

function _connectSSE() {
  if (_sseSource) return;
  try {
    _sseSource = new EventSource(`${API}/api/jobs/activity`);

    _sseSource.onopen = () => {
      _sseRetryDelay = 1000;
      _updateSSEIndicator("connected");
    };

    const refreshDashboard = () => {
      _fetchActiveJobs().then(() => _renderActiveJobs());
      _fetchRecentCompletions().then(() => _renderRecentCompletions());
      _fetchApprovals().then(() => _renderApprovals());
      _fetchCostStats().then(() => _renderCostOverview());
    };

    _sseSource.addEventListener("job_created", () => refreshDashboard());
    _sseSource.addEventListener("job_completed", () => refreshDashboard());
    _sseSource.addEventListener("job_failed", () => refreshDashboard());
    _sseSource.addEventListener("job_status_changed", () => refreshDashboard());
    _sseSource.addEventListener("node_progress", () => {
      _fetchActiveJobs().then(() => _renderActiveJobs());
    });

    _sseSource.onerror = () => {
      _sseSource?.close();
      _sseSource = null;
      _updateSSEIndicator("reconnecting");
      const delay = Math.min(_sseRetryDelay, _SSE_MAX_DELAY);
      _sseRetryDelay = Math.min(_sseRetryDelay * 2, _SSE_MAX_DELAY);
      setTimeout(_connectSSE, delay);
    };
  } catch {
    _updateSSEIndicator("offline");
  }
}

function _disconnectSSE() {
  if (_sseSource) {
    _sseSource.close();
    _sseSource = null;
  }
}

function _updateSSEIndicator(status) {
  const dot = _el("dashSSEDot");
  const label = _el("dashSSELabel");
  if (!dot || !label) return;

  if (status === "connected") {
    dot.className = "w-2 h-2 rounded-full bg-emerald-400 transition-colors";
    label.textContent = "Live";
    label.className = "text-[11px] font-mono text-emerald-400";
  } else if (status === "reconnecting") {
    dot.className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse transition-colors";
    label.textContent = "Reconnecting...";
    label.className = "text-[11px] font-mono text-amber-400";
  } else {
    dot.className = "w-2 h-2 rounded-full bg-slate-600 transition-colors";
    label.textContent = "Offline";
    label.className = "text-[11px] font-mono text-slate-500";
  }
}

// ---------------------------------------------------------------------------
// ARIA announcements
// ---------------------------------------------------------------------------

function _announce(message) {
  const region = _el("dashboardLiveRegion");
  if (region) {
    region.textContent = message;
    setTimeout(() => { region.textContent = ""; }, 3000);
  }
}

// ---------------------------------------------------------------------------
// Refresh all sections
// ---------------------------------------------------------------------------

async function _refreshAll() {
  await Promise.all([
    _fetchActiveJobs(),
    _fetchRecentCompletions(),
    _fetchTemplates(),
    _fetchApprovals(),
    _fetchCostStats(),
  ]);
  _renderActiveJobs();
  _renderRecentCompletions();
  _renderTemplates();
  _renderApprovals();
  _renderCostOverview();
  _announce(`Dashboard updated: ${_activeJobs.length} active jobs, ${_pendingApprovals.length} pending approvals`);
}

// ---------------------------------------------------------------------------
// Visibility management
// ---------------------------------------------------------------------------

/**
 * Show the dashboard (called when messagesContainer has no messages).
 * The dashboard is hidden when chat messages appear.
 */
function _showDashboard() {
  const container = _el("mainDashboardView");
  if (container) {
    container.style.display = "";
    container.classList.remove("hidden");
  }
}

/**
 * Hide the dashboard (called when messages appear in chat).
 * Other code (chat.js) hides chatEmptyState -- we also hide the dashboard.
 */
function _hideDashboard() {
  const container = _el("mainDashboardView");
  if (container) {
    container.style.display = "none";
  }
}

// ---------------------------------------------------------------------------
// Observe message additions to auto-hide dashboard
// ---------------------------------------------------------------------------

function _observeMessages() {
  const messagesArea = _el("messagesContainer");
  if (!messagesArea) return;

  const observer = new MutationObserver(() => {
    // If there are user/assistant messages (not just the dashboard), hide it
    const messages = messagesArea.querySelectorAll(".message");
    if (messages.length > 0) {
      _hideDashboard();
    }
  });

  observer.observe(messagesArea, { childList: true });
}

// ---------------------------------------------------------------------------
// Public: init / stop
// ---------------------------------------------------------------------------

let _dataInterval = null;

function initDashboard() {
  // Mount the dashboard shell
  _mountDashboard();

  // Initial data load
  updateDashboardMetrics();
  updateStatusBar();
  _refreshAll();

  // Periodic polling for hardware metrics (every 3s)
  _dashboardInterval = setInterval(() => {
    if (document.hidden) return; // Skip when tab is in background
    updateDashboardMetrics();
    updateStatusBar();
  }, 3000);

  // Slower poll for job data (SSE covers real-time, this is fallback)
  _dataInterval = setInterval(() => {
    if (!_mounted || document.hidden) return; // Skip when unmounted or hidden
    _fetchActiveJobs().then(() => _renderActiveJobs());
    _fetchApprovals().then(() => _renderApprovals());
    _fetchCostStats().then(() => _renderCostOverview());
  }, 15_000);

  // Refresh immediately when user returns to the tab
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && _mounted) {
      updateDashboardMetrics();
      updateStatusBar();
      _fetchActiveJobs().then(() => _renderActiveJobs());
    }
  });

  // Connect SSE for live updates
  _connectSSE();

  // Watch for messages to auto-hide dashboard
  _observeMessages();
}

function stopDashboard() {
  if (_dashboardInterval) {
    clearInterval(_dashboardInterval);
    _dashboardInterval = null;
  }
  if (_dataInterval) {
    clearInterval(_dataInterval);
    _dataInterval = null;
  }
  _disconnectSSE();
}

export { initDashboard, stopDashboard, updateDashboardMetrics, updateStatusBar };
