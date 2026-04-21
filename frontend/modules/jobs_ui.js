/**
 * jobs_ui.js -- Full Job Dashboard
 * =================================
 * Enhanced CRUD + monitoring UI for the LocalMind Job Pipeline system.
 *
 * Features:
 *   - Progressive-disclosure creation bar (description, files, mode, priority)
 *   - Live job dashboard with real-time SSE updates + exponential backoff
 *   - Status filters + sort controls
 *   - Rich job detail: node timeline, progress, review gate, output files, audit
 *   - Full accessibility: ARIA live regions, keyboard nav, reduced-motion, high-contrast
 *
 * Exports:
 *   initJobsUI()    -- bootstrap: inject HTML into #jobsView, bind events
 *   showJobsView()  -- programmatically show the Jobs view + load data
 *   hideJobsView()  -- hide the view and stop background activity
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";
import { showCardSkeletons } from "./ui_components.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _jobs = [];
let _currentFilter = "all";
let _currentSort = "recent";       // "recent" | "priority" | "status"
let _selectedJobId = null;
let _pollTimer = null;
let _droppedFiles = [];
let _sseSource = null;
let _sseRetryDelay = 1000;         // exponential backoff start
const _SSE_MAX_DELAY = 60_000;
let _templates = [];                // cached for pipeline mode selector
let _creationExpanded = false;      // progressive disclosure state

// ---------------------------------------------------------------------------
// DOM helper
// ---------------------------------------------------------------------------

const el = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Status config -- icon + color + text label (accessibility: never color-only)
// ---------------------------------------------------------------------------

const STATUS_CFG = {
  pending:    { color: "slate",   icon: "schedule",     label: "Pending" },
  planning:   { color: "blue",    icon: "edit_note",    label: "Planning" },
  executing:  { color: "amber",   icon: "play_circle",  label: "Executing" },
  reviewing:  { color: "purple",  icon: "rate_review",  label: "Reviewing" },
  done:       { color: "emerald", icon: "check_circle", label: "Done" },
  failed:     { color: "red",     icon: "error",        label: "Failed" },
  cancelled:  { color: "slate",   icon: "cancel",       label: "Cancelled" },
  cancelling: { color: "amber",   icon: "pending",      label: "Cancelling" },
};

const NODE_STATUS_CFG = {
  pending:        { color: "slate",   icon: "schedule",      label: "Pending" },
  running:        { color: "amber",   icon: "play_circle",   label: "Running" },
  completed:      { color: "emerald", icon: "check_circle",  label: "Completed" },
  failed:         { color: "red",     icon: "error",         label: "Failed" },
  cancelled:      { color: "slate",   icon: "cancel",        label: "Cancelled" },
  awaiting_input: { color: "blue",    icon: "hourglass_top", label: "Awaiting Input" },
  skipped:        { color: "slate",   icon: "skip_next",     label: "Skipped" },
};

// Tailwind dynamic class safelist -- ensures JIT includes our status colours.
// These classes are referenced in template strings below; Tailwind CDN scans
// the page at runtime so they need to appear literally somewhere.
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
  "border-l-slate-500","border-l-blue-500","border-l-amber-500",
  "border-l-purple-500","border-l-emerald-500","border-l-red-500",
  "animate-pulse",
  "bg-slate-500","bg-blue-500","bg-amber-500",
  "bg-purple-500","bg-emerald-500","bg-red-500",
];
void _TW_SAFELIST; // suppress unused warning

// ---------------------------------------------------------------------------
// HTML Shell -- injected into the pre-existing #jobsView container
// ---------------------------------------------------------------------------

function buildShellHTML() {
  return `
  <!-- ============================================================ -->
  <!-- ARIA LIVE STATUS REGION (visually hidden)                     -->
  <!-- ============================================================ -->
  <div id="jobsLiveStatus" class="sr-only" aria-live="polite" aria-atomic="true" role="status"></div>

  <!-- ============================================================ -->
  <!-- LIST VIEW                                                     -->
  <!-- ============================================================ -->
  <div id="jobsListView" class="jobs-list-view flex-1 flex flex-col p-8 overflow-y-auto custom-scrollbar gap-6">

    <!-- Header -->
    <div class="flex items-center justify-between flex-wrap gap-4">
      <div class="flex items-center gap-3">
        <span class="material-symbols-outlined text-indigo-400 text-2xl" aria-hidden="true">work</span>
        <h2 class="text-xl font-headline font-bold tracking-tight text-slate-100">Jobs</h2>
        <span id="jobsTotalBadge" class="text-[11px] font-bold uppercase tracking-widest bg-indigo-500/20 text-indigo-400 px-2.5 py-1 rounded-full">0 jobs</span>
      </div>
      <button id="jobsRefreshBtn"
              class="bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 text-xs font-bold px-4 py-2 rounded-lg uppercase tracking-wider transition-colors border border-indigo-500/20 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              aria-label="Refresh job list">
        <span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">refresh</span> Refresh
      </button>
    </div>

    <!-- Creation Bar -->
    <div id="jobCreationBar" class="bg-slate-900/40 border border-slate-800/60 rounded-xl p-5 space-y-4 jobs-creation-bar">
      <label for="jobTitleInput" class="sr-only">Job description</label>
      <input
        id="jobTitleInput"
        type="text"
        placeholder="e.g. Update slides 3-7 with Q1 2026 revenue from the sales dashboard"
        aria-label="Job description"
        class="w-full bg-slate-800/60 border border-slate-700/50 rounded-lg px-4 py-3 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 font-body"
      />

      <!-- Description (progressive disclosure) -->
      <div id="jobDescSection" class="hidden">
        <label for="jobDescInput" class="block text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">Description (optional)</label>
        <textarea
          id="jobDescInput"
          rows="3"
          placeholder="Additional context, requirements, or constraints..."
          class="w-full bg-slate-800/60 border border-slate-700/50 rounded-lg px-4 py-2 text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 font-body resize-y min-h-[60px]"
          aria-label="Job description details"
        ></textarea>
      </div>

      <!-- Drop Zone -->
      <div
        id="jobDropZone"
        tabindex="0"
        role="button"
        aria-label="Drag and drop files here or click to select"
        class="border-2 border-dashed border-slate-700/50 rounded-lg p-4 text-center cursor-pointer hover:border-indigo-500/40 hover:bg-indigo-500/5 transition-all focus:outline-none focus:ring-2 focus:ring-indigo-500/50"
      >
        <span class="material-symbols-outlined text-slate-500 text-2xl block mb-1" aria-hidden="true">upload_file</span>
        <span class="text-xs text-slate-500">Drag files here or click to browse</span>
        <input id="jobFileInput" type="file" multiple class="hidden" aria-hidden="true" />
      </div>
      <div id="jobFilePreview" class="flex flex-wrap gap-2" aria-live="polite"></div>

      <!-- Mode + Priority Row (progressive disclosure) -->
      <div id="jobOptionsRow" class="hidden">
        <div class="flex gap-4 flex-wrap">
          <!-- Mode Selector -->
          <div class="flex-1 min-w-[140px]">
            <label for="jobModeSelect" class="block text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">Mode</label>
            <select id="jobModeSelect" class="w-full bg-slate-800/60 border border-slate-700/50 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 appearance-none cursor-pointer" aria-label="Job execution mode">
              <option value="quick">Quick (default)</option>
              <option value="pipeline">Pipeline (template)</option>
            </select>
          </div>
          <!-- Template Dropdown (shown when pipeline selected) -->
          <div id="jobTemplateGroup" class="flex-1 min-w-[180px] hidden">
            <label for="jobTemplateSelect" class="block text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">Template</label>
            <select id="jobTemplateSelect" class="w-full bg-slate-800/60 border border-slate-700/50 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 appearance-none cursor-pointer" aria-label="Pipeline template">
              <option value="">-- Select a template --</option>
            </select>
          </div>
          <!-- Priority Selector -->
          <div class="flex-1 min-w-[120px]">
            <label for="jobPrioritySelect" class="block text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">Priority</label>
            <select id="jobPrioritySelect" class="w-full bg-slate-800/60 border border-slate-700/50 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 appearance-none cursor-pointer" aria-label="Job priority">
              <option value="-1">Low</option>
              <option value="0" selected>Normal</option>
              <option value="1">High</option>
            </select>
          </div>
        </div>
      </div>

      <!-- Action Buttons -->
      <div class="flex items-center gap-3 flex-wrap">
        <button
          id="jobRunBtn"
          aria-label="Run job in quick mode"
          class="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold uppercase tracking-wider rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <span class="material-symbols-outlined text-sm" aria-hidden="true">bolt</span> Run
        </button>
        <button
          id="jobCustomizeBtn"
          aria-label="Show advanced options"
          class="flex items-center gap-2 px-5 py-2.5 bg-slate-800/60 hover:bg-slate-700/60 border border-slate-700/50 text-slate-300 text-xs font-bold uppercase tracking-wider rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <span class="material-symbols-outlined text-sm" aria-hidden="true">tune</span> <span id="customizeBtnLabel">Customize Plan</span>
        </button>
        <div id="jobSSEIndicator" class="ml-auto flex items-center gap-1.5 text-[11px] font-mono text-slate-600" title="SSE connection status">
          <span id="jobSSEDot" class="w-1.5 h-1.5 rounded-full bg-slate-600" aria-hidden="true"></span>
          <span id="jobSSELabel">Offline</span>
        </div>
      </div>
    </div>

    <!-- Filters + Sort Row -->
    <div class="flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-2 flex-wrap" role="tablist" aria-label="Filter jobs by status">
        <button data-filter="all"       role="tab" aria-selected="true"  class="jobs-filter-btn active">All</button>
        <button data-filter="running"    role="tab" aria-selected="false" class="jobs-filter-btn">Running</button>
        <button data-filter="completed"  role="tab" aria-selected="false" class="jobs-filter-btn">Completed</button>
        <button data-filter="failed"     role="tab" aria-selected="false" class="jobs-filter-btn">Failed</button>
      </div>
      <div class="flex items-center gap-2">
        <label for="jobsSortSelect" class="text-[11px] font-bold uppercase tracking-widest text-slate-600">Sort:</label>
        <select id="jobsSortSelect" class="bg-slate-900/40 border border-slate-800/60 rounded-lg px-2 py-1 text-xs text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 appearance-none cursor-pointer" aria-label="Sort jobs">
          <option value="recent">Most Recent</option>
          <option value="priority">Priority</option>
          <option value="status">Status</option>
        </select>
      </div>
    </div>

    <!-- Job Cards Grid -->
    <div id="jobsGrid" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4" role="list" aria-live="polite" aria-label="Job list">
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Injected CSS (non-Tailwind helpers + dashboard-specific styles)
// ---------------------------------------------------------------------------

function _injectStyles() {
  if (document.getElementById("jobs-ui-styles")) return;
  const style = document.createElement("style");
  style.id = "jobs-ui-styles";
  style.textContent = `
    /* ---- Filter Buttons ---- */
    .jobs-filter-btn {
      padding: 0.375rem 0.875rem;
      font-size: 0.625rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      border-radius: 0.5rem;
      border: 1px solid rgba(51,65,85,0.5);
      background: rgba(15,23,42,0.4);
      color: rgb(148,163,184);
      cursor: pointer;
      transition: all 0.15s;
    }
    .jobs-filter-btn:hover { background: rgba(30,41,59,0.6); color: rgb(226,232,240); }
    .jobs-filter-btn:focus-visible { outline: 2px solid rgba(99,102,241,0.5); outline-offset: 2px; }
    .jobs-filter-btn.active { background: rgba(99,102,241,0.15); color: rgb(129,140,248); border-color: rgba(99,102,241,0.3); }

    /* ---- Focus outlines ---- */
    .job-card-link:focus-visible,
    .node-card:focus-visible { outline: 2px solid rgba(99,102,241,0.5); outline-offset: 2px; }

    /* ---- Drop zone drag state ---- */
    #jobDropZone.drag-over { border-color: rgba(99,102,241,0.6); background: rgba(99,102,241,0.08); }

    /* ---- Progress bar animation ---- */
    .jobs-progress-bar {
      transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .jobs-progress-bar.jobs-progress-active {
      background-image: linear-gradient(
        -45deg,
        rgba(255,255,255,0.1) 25%,
        transparent 25%,
        transparent 50%,
        rgba(255,255,255,0.1) 50%,
        rgba(255,255,255,0.1) 75%,
        transparent 75%,
        transparent
      );
      background-size: 1rem 1rem;
      animation: jobs-progress-stripes 1s linear infinite;
    }
    @keyframes jobs-progress-stripes {
      from { background-position: 1rem 0; }
      to   { background-position: 0 0; }
    }

    /* ---- Job card enter animation ---- */
    .job-card-link {
      animation: jobs-card-enter 0.3s cubic-bezier(0.4, 0, 0.2, 1) both;
    }
    @keyframes jobs-card-enter {
      from { opacity: 0; transform: translateY(8px); }
      to   { opacity: 1; transform: translateY(0); }
    }

    /* ---- Node timeline card ---- */
    .jobs-node-card {
      border-left: 3px solid transparent;
      transition: all 0.2s ease;
    }
    .jobs-node-card[data-color="slate"]   { border-left-color: rgb(100,116,139); }
    .jobs-node-card[data-color="blue"]    { border-left-color: rgb(96,165,250); }
    .jobs-node-card[data-color="amber"]   { border-left-color: rgb(251,191,36); }
    .jobs-node-card[data-color="purple"]  { border-left-color: rgb(168,85,247); }
    .jobs-node-card[data-color="emerald"] { border-left-color: rgb(52,211,153); }
    .jobs-node-card[data-color="red"]     { border-left-color: rgb(248,113,113); }

    /* ---- Node detail slide-in ---- */
    .jobs-node-detail {
      animation: jobs-slide-in 0.25s cubic-bezier(0.4, 0, 0.2, 1) both;
    }
    @keyframes jobs-slide-in {
      from { opacity: 0; transform: translateY(12px); }
      to   { opacity: 1; transform: translateY(0); }
    }

    /* ---- Detail view slide-in ---- */
    .jobs-detail-view:not(.hidden) {
      animation: jobs-detail-enter 0.3s cubic-bezier(0.4, 0, 0.2, 1) both;
    }
    @keyframes jobs-detail-enter {
      from { opacity: 0; transform: translateX(16px); }
      to   { opacity: 1; transform: translateX(0); }
    }

    /* ---- SSE indicator ---- */
    .jobs-sse-connected { color: rgb(52,211,153); }
    .jobs-sse-connected .jobs-sse-dot { background: rgb(52,211,153); }
    .jobs-sse-reconnecting { color: rgb(251,191,36); }
    .jobs-sse-reconnecting .jobs-sse-dot { background: rgb(251,191,36); animation: pulse 1.5s infinite; }

    /* ---- Status update flash ---- */
    .jobs-status-flash {
      animation: jobs-flash 0.6s ease-out;
    }
    @keyframes jobs-flash {
      0%   { box-shadow: 0 0 0 0 rgba(99,102,241,0.5); }
      100% { box-shadow: 0 0 0 0 rgba(99,102,241,0); }
    }

    /* ---- Spinner for running status ---- */
    .jobs-spin {
      animation: jobs-spin-anim 1s linear infinite;
    }
    @keyframes jobs-spin-anim {
      from { transform: rotate(0deg); }
      to   { transform: rotate(360deg); }
    }

    /* ---- Priority badges ---- */
    .jobs-priority-high { color: #f87171; }
    .jobs-priority-low  { color: #64748b; }

    /* ---- Rendered node output (markdown / JSON) ---- */
    .node-output-rendered { word-break: break-word; }
    .node-output-rendered p { margin: 0.25em 0; }
    .node-output-rendered ul, .node-output-rendered ol { margin: 0.25em 0 0.25em 1.25em; }
    .node-output-rendered li { margin: 0.125em 0; }
    .node-output-rendered h1, .node-output-rendered h2, .node-output-rendered h3 {
      font-weight: 700; color: #e2e8f0; margin: 0.5em 0 0.25em;
    }
    .node-output-rendered h1 { font-size: 1.1em; }
    .node-output-rendered h2 { font-size: 1em; }
    .node-output-rendered h3 { font-size: 0.95em; }
    .node-output-rendered code {
      font-family: 'JetBrains Mono', monospace; font-size: 0.85em;
      background: rgba(30,41,59,0.6); border-radius: 3px; padding: 0.1em 0.35em;
    }
    .node-output-rendered pre {
      background: rgba(15,23,42,0.8); border: 1px solid rgba(51,65,85,0.4);
      border-radius: 0.5rem; padding: 0.75rem; margin: 0.5em 0;
      overflow-x: auto; font-size: 0.8em;
    }
    .node-output-rendered pre code { background: none; padding: 0; }
    .node-output-rendered a { color: rgb(129,140,248); text-decoration: underline; }
    .node-output-rendered blockquote {
      border-left: 3px solid rgba(99,102,241,0.4); padding-left: 0.75em;
      color: rgb(148,163,184); margin: 0.5em 0;
    }
    .node-output-rendered table { border-collapse: collapse; margin: 0.5em 0; font-size: 0.85em; }
    .node-output-rendered th, .node-output-rendered td {
      border: 1px solid rgba(51,65,85,0.5); padding: 0.3em 0.6em;
    }
    .node-output-rendered th { background: rgba(30,41,59,0.5); font-weight: 600; }
    .node-output-json { white-space: pre-wrap; font-family: 'JetBrains Mono', monospace; font-size: 0.8em; color: rgb(148,163,184); }
    .node-output-json .json-key { color: rgb(129,140,248); }
    .node-output-json .json-string { color: rgb(110,231,183); }
    .node-output-json .json-number { color: rgb(251,191,36); }
    .node-output-json .json-boolean { color: rgb(248,113,113); }
    .node-output-json .json-null { color: rgb(100,116,139); }

    /* ---- Responsive ---- */
    @media (max-width: 640px) {
      .jobs-list-view { padding: 1rem; gap: 1rem; }
      .jobs-detail-view { padding: 1rem; gap: 1rem; }
      .jobs-creation-bar { padding: 0.75rem; }
    }

    /* ---- prefers-reduced-motion ---- */
    @media (prefers-reduced-motion: reduce) {
      .job-card-link,
      .jobs-node-detail,
      .jobs-detail-view:not(.hidden),
      .jobs-progress-bar,
      .jobs-status-flash {
        animation: none !important;
        transition: none !important;
      }
      .jobs-progress-bar.jobs-progress-active {
        animation: none !important;
      }
      .jobs-spin { animation: none !important; }
    }

    /* ---- prefers-contrast: more (high contrast) ---- */
    @media (prefers-contrast: more) {
      .jobs-filter-btn {
        border-width: 2px;
        border-color: rgb(148,163,184);
      }
      .jobs-filter-btn.active {
        border-color: rgb(129,140,248);
        outline: 2px solid rgb(129,140,248);
      }
      .job-card-link {
        border-width: 2px;
      }
      .jobs-node-card {
        border-left-width: 4px;
      }
    }
  `;
  document.head.appendChild(style);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

/**
 * Bootstrap the Jobs view: populate #jobsView with HTML, bind all events.
 * Called once from app.js on DOMContentLoaded.
 */
export function initJobsUI() {
  const view = el("jobsView");
  if (!view) {
    console.warn("[jobs_ui] #jobsView not found in DOM");
    return;
  }

  _injectStyles();

  // Populate the container
  view.innerHTML = buildShellHTML();

  // Creation buttons
  el("jobRunBtn")?.addEventListener("click", () => _createJob());
  el("jobCustomizeBtn")?.addEventListener("click", () => {
    _creationExpanded = !_creationExpanded;
    _toggleCreationOptions(_creationExpanded);
  });

  // Mode selector -- show/hide template dropdown
  el("jobModeSelect")?.addEventListener("change", () => {
    const mode = el("jobModeSelect")?.value;
    const tplGroup = el("jobTemplateGroup");
    if (tplGroup) {
      if (mode === "pipeline") {
        tplGroup.classList.remove("hidden");
        _loadTemplatesForSelector();
      } else {
        tplGroup.classList.add("hidden");
      }
    }
  });

  // File drop zone
  _initDropZone();

  // Filter buttons
  view.querySelectorAll(".jobs-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      _currentFilter = btn.dataset.filter;
      view.querySelectorAll(".jobs-filter-btn").forEach((b) => {
        const isActive = b === btn;
        b.classList.toggle("active", isActive);
        b.setAttribute("aria-selected", String(isActive));
      });
      _renderJobCards();
    });
  });

  // Sort selector
  el("jobsSortSelect")?.addEventListener("change", () => {
    _currentSort = el("jobsSortSelect")?.value || "recent";
    _renderJobCards();
  });

  // Refresh button
  el("jobsRefreshBtn")?.addEventListener("click", async () => {
    const btn = el("jobsRefreshBtn");
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 jobs-spin" aria-hidden="true">progress_activity</span> Loading...';
    }
    await _loadJobs();
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">refresh</span> Refresh';
    }
  });

  // Enter key in title input triggers Run
  el("jobTitleInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      _createJob();
    }
  });

  // Title input -- auto-expand advanced options on long text
  el("jobTitleInput")?.addEventListener("input", () => {
    const val = el("jobTitleInput")?.value || "";
    // Show description hint for long inputs
    if (val.length > 80 && !_creationExpanded) {
      // subtle hint but don't force expand
    }
  });

  // SSE for real-time updates
  _connectSSE();
}

// ---------------------------------------------------------------------------
// Show / Hide
// ---------------------------------------------------------------------------

export function showJobsView() {
  // Show the hidden #jobsView shim (used for the full detail view)
  const view = el("jobsView");
  if (view) {
    view.classList.remove("hidden");
    view.style.display = "";
  }
  _showListView();
  const grid = el("jobsGrid");
  if (grid && _jobs.length === 0) showCardSkeletons(grid, 6);
  _loadJobs();
  _startPolling();
  _updateRunningBadge();
}

export function hideJobsView() {
  const view = el("jobsView");
  if (view) view.classList.add("hidden");
  _stopPolling();
}

function _showListView() {
  el("jobsListView")?.classList.remove("hidden");
  el("jobsDetailView")?.classList.add("hidden");
  el("jobNodeDetail")?.classList.add("hidden");
  _selectedJobId = null;
}

function _showDetailView(jobId) {
  // Delegate to the new v2 Job Detail overlay (3-pane: timeline / output / inspector).
  // The legacy inline view is retained in the DOM but no longer surfaced.
  _selectedJobId = jobId;
  import("./job_detail.js")
    .then((m) => m.openJobDetail?.(jobId))
    .catch((err) => {
      console.warn("[jobs_ui] Failed to open job_detail module:", err);
    });
}

// ---------------------------------------------------------------------------
// Progressive Disclosure
// ---------------------------------------------------------------------------

function _toggleCreationOptions(expanded) {
  const descSection = el("jobDescSection");
  const optionsRow = el("jobOptionsRow");
  const label = el("customizeBtnLabel");

  if (expanded) {
    descSection?.classList.remove("hidden");
    optionsRow?.classList.remove("hidden");
    if (label) label.textContent = "Simple Mode";
  } else {
    descSection?.classList.add("hidden");
    optionsRow?.classList.add("hidden");
    if (label) label.textContent = "Customize Plan";
  }
}

async function _loadTemplatesForSelector() {
  try {
    const res = await fetch(`${API}/api/jobs/templates`);
    if (!res.ok) return;
    const data = await res.json();
    _templates = data.templates || [];
    const select = el("jobTemplateSelect");
    if (!select) return;
    // Preserve any current selection
    const currentVal = select.value;
    select.innerHTML = '<option value="">-- Select a template --</option>';
    _templates.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = `${t.name} (${(t.nodes || []).length} nodes)`;
      select.appendChild(opt);
    });
    if (currentVal) select.value = currentVal;
  } catch {
    // silently fail
  }
}

// ---------------------------------------------------------------------------
// Polling (fallback when SSE is unavailable)
// ---------------------------------------------------------------------------

function _startPolling() {
  _stopPolling();
  _pollTimer = setInterval(() => {
    if (document.hidden) return; // Skip when tab is in background
    _loadJobs();
  }, 5000);
}

function _stopPolling() {
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
}

// ---------------------------------------------------------------------------
// SSE -- real-time push from /api/jobs/activity (exponential backoff)
// ---------------------------------------------------------------------------

function _connectSSE() {
  if (_sseSource) return;
  try {
    _sseSource = new EventSource(`${API}/api/jobs/activity`);

    _sseSource.onopen = () => {
      _sseRetryDelay = 1000; // reset backoff on success
      _updateSSEIndicator("connected");
    };

    const refreshList = () => {
      _loadJobs();
      _announceStatus("Job list updated");
    };

    const refreshDetail = (e) => {
      refreshList();
      try {
        const data = JSON.parse(e.data);
        // Flash the affected card in the grid
        if (data.job_id) {
          const card = document.querySelector(`[data-job-id="${data.job_id}"]`);
          if (card) {
            card.classList.add("jobs-status-flash");
            setTimeout(() => card.classList.remove("jobs-status-flash"), 700);
          }
        }
      } catch { /* ignore parse errors */ }
    };

    _sseSource.addEventListener("job_created", (e) => {
      refreshList();
      try {
        const data = JSON.parse(e.data);
        _announceStatus(`New job created: ${data.title || "Untitled"}`);
      } catch { /* ignore */ }
    });
    _sseSource.addEventListener("job_completed", () => {
      refreshList();
      _announceStatus("Job completed");
    });
    _sseSource.addEventListener("job_failed", () => {
      refreshList();
      _announceStatus("Job failed");
    });
    _sseSource.addEventListener("job_status_changed", refreshDetail);
    _sseSource.addEventListener("node_progress", refreshDetail);

    _sseSource.onerror = () => {
      _sseSource?.close();
      _sseSource = null;
      _updateSSEIndicator("reconnecting");
      // Exponential backoff
      const delay = Math.min(_sseRetryDelay, _SSE_MAX_DELAY);
      _sseRetryDelay = Math.min(_sseRetryDelay * 2, _SSE_MAX_DELAY);
      setTimeout(_connectSSE, delay);
    };
  } catch {
    _updateSSEIndicator("offline");
    // EventSource not supported or URL unreachable -- polling covers us.
  }
}

function _updateSSEIndicator(status) {
  const container = el("jobSSEIndicator");
  const dot = el("jobSSEDot");
  const label = el("jobSSELabel");
  if (!container || !dot || !label) return;

  container.className = "ml-auto flex items-center gap-1.5 text-[11px] font-mono";
  dot.className = "w-1.5 h-1.5 rounded-full jobs-sse-dot";

  if (status === "connected") {
    container.classList.add("jobs-sse-connected");
    dot.classList.add("bg-emerald-400");
    label.textContent = "Live";
  } else if (status === "reconnecting") {
    container.classList.add("jobs-sse-reconnecting");
    dot.classList.add("bg-amber-400");
    label.textContent = "Reconnecting...";
  } else {
    container.classList.add("text-slate-600");
    dot.classList.add("bg-slate-600");
    label.textContent = "Offline";
  }
}

// ---------------------------------------------------------------------------
// ARIA Live Announcements
// ---------------------------------------------------------------------------

function _announceStatus(message) {
  const region = el("jobsLiveStatus");
  if (region) {
    region.textContent = message;
    // Clear after a beat so the same message can be announced again
    setTimeout(() => { region.textContent = ""; }, 3000);
  }
}

// ---------------------------------------------------------------------------
// API: Load jobs list
// ---------------------------------------------------------------------------

async function _loadJobs() {
  try {
    const res = await fetch(`${API}/api/jobs`);
    if (!res.ok) return;
    const data = await res.json();
    _jobs = data.jobs || [];
    _sortJobs();
    _renderJobCards();
    _updateRunningBadge();
    _updateTotalBadge();
  } catch (err) {
    console.error("[jobs_ui] Failed to load jobs:", err);
  }
}

// ---------------------------------------------------------------------------
// API: Create job
// ---------------------------------------------------------------------------

async function _createJob() {
  const titleInput = el("jobTitleInput");
  const title = titleInput?.value?.trim();
  if (!title) {
    showToast("Please enter a job description", "error");
    titleInput?.focus();
    return;
  }

  // Gather options
  const descInput = el("jobDescInput");
  const description = _creationExpanded ? (descInput?.value?.trim() || title) : title;
  const mode = _creationExpanded ? (el("jobModeSelect")?.value || "quick") : "quick";
  const templateId = (mode === "pipeline" && _creationExpanded) ? (el("jobTemplateSelect")?.value || null) : null;
  const priority = _creationExpanded ? parseInt(el("jobPrioritySelect")?.value || "0", 10) : 0;

  const runBtn = el("jobRunBtn");
  const customBtn = el("jobCustomizeBtn");
  if (runBtn) runBtn.disabled = true;
  if (customBtn) customBtn.disabled = true;

  try {
    let res;
    const payload = { title, description, mode, priority };
    if (templateId) payload.template_id = templateId;

    if (_droppedFiles.length > 0) {
      const formData = new FormData();
      formData.append("metadata", JSON.stringify(payload));
      _droppedFiles.forEach((f) => formData.append("files", f));
      res = await fetch(`${API}/api/jobs`, { method: "POST", body: formData });
    } else {
      res = await fetch(`${API}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      showToast(errData.detail || `Failed to create job (${res.status})`, "error");
      return;
    }

    const job = await res.json();
    showToast(`Job created: ${escapeHtml(job.title || title)}`, "info");
    _announceStatus(`Job created: ${job.title || title}`);

    // Clear inputs
    if (titleInput) titleInput.value = "";
    if (descInput) descInput.value = "";
    _droppedFiles = [];
    const preview = el("jobFilePreview");
    if (preview) preview.innerHTML = "";

    // Reset creation bar
    _creationExpanded = false;
    _toggleCreationOptions(false);

    // Refresh & navigate
    await _loadJobs();
    if (job.id) _showDetailView(job.id);
  } catch (err) {
    console.error("[jobs_ui] Create job error:", err);
    showToast("Couldn't create the job. Check that the backend is running, then try again.", "error");
  } finally {
    if (runBtn) runBtn.disabled = false;
    if (customBtn) customBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// File Drop Zone
// ---------------------------------------------------------------------------

function _initDropZone() {
  const zone = el("jobDropZone");
  const fileInput = el("jobFileInput");
  if (!zone || !fileInput) return;

  zone.addEventListener("click", () => fileInput.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });

  fileInput.addEventListener("change", (e) => {
    _addFiles(Array.from(e.target.files || []));
    fileInput.value = "";
  });

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("drag-over");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    _addFiles(Array.from(e.dataTransfer?.files || []));
  });
}

function _addFiles(files) {
  _droppedFiles.push(...files);
  _renderFilePreview();
}

function _renderFilePreview() {
  const container = el("jobFilePreview");
  if (!container) return;

  container.innerHTML = _droppedFiles
    .map(
      (f, i) => `
      <div class="flex items-center gap-2 bg-slate-800/60 border border-slate-700/40 rounded-lg px-3 py-1.5 text-xs text-slate-300">
        <span class="material-symbols-outlined text-sm text-slate-500" aria-hidden="true">description</span>
        <span class="truncate max-w-[150px]">${escapeHtml(f.name)}</span>
        <span class="text-[11px] text-slate-500 font-mono">${_formatSize(f.size)}</span>
        <button
          data-file-idx="${i}"
          aria-label="Remove file ${escapeHtml(f.name)}"
          class="text-slate-500 hover:text-red-400 transition-colors ml-1 focus:outline-none focus:ring-1 focus:ring-red-400 rounded"
        >
          <span class="material-symbols-outlined text-sm" aria-hidden="true">close</span>
        </button>
      </div>`,
    )
    .join("");

  container.querySelectorAll("button[data-file-idx]").forEach((btn) => {
    btn.addEventListener("click", () => {
      _droppedFiles.splice(parseInt(btn.dataset.fileIdx, 10), 1);
      _renderFilePreview();
    });
  });
}

// ---------------------------------------------------------------------------
// Sorting
// ---------------------------------------------------------------------------

function _sortJobs() {
  if (_currentSort === "recent") {
    _jobs.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  } else if (_currentSort === "priority") {
    _jobs.sort((a, b) => {
      const pa = typeof a.priority === "number" ? a.priority : 0;
      const pb = typeof b.priority === "number" ? b.priority : 0;
      if (pb !== pa) return pb - pa; // highest first
      return (b.created_at || "").localeCompare(a.created_at || "");
    });
  } else if (_currentSort === "status") {
    const statusOrder = { executing: 0, planning: 1, reviewing: 2, pending: 3, cancelling: 4, done: 5, failed: 6, cancelled: 7 };
    _jobs.sort((a, b) => {
      const sa = statusOrder[a.status] ?? 99;
      const sb = statusOrder[b.status] ?? 99;
      if (sa !== sb) return sa - sb;
      return (b.created_at || "").localeCompare(a.created_at || "");
    });
  }
}

// ---------------------------------------------------------------------------
// Render: Job Cards Grid
// ---------------------------------------------------------------------------

function _renderJobCards() {
  const grid = el("jobsGrid");
  if (!grid) return;

  let filtered = _jobs;
  if (_currentFilter === "running") {
    filtered = _jobs.filter((j) =>
      ["pending", "planning", "executing", "reviewing", "cancelling"].includes(j.status),
    );
  } else if (_currentFilter === "completed") {
    filtered = _jobs.filter((j) => j.status === "done");
  } else if (_currentFilter === "failed") {
    filtered = _jobs.filter((j) => ["failed", "cancelled"].includes(j.status));
  }

  if (filtered.length === 0) {
    const filterMsg = _currentFilter === "all"
      ? "No active jobs — create one from the chat or use the task creation form above."
      : _currentFilter === "running"
        ? "No running jobs right now. Submit a new task to get started."
        : _currentFilter === "completed"
          ? "No completed jobs yet. Jobs will appear here once they finish."
          : "No failed jobs — that's a good thing!";
    grid.innerHTML = `
      <div class="col-span-full flex flex-col items-center justify-center py-12 text-center" role="status" aria-label="No jobs found">
        <span class="material-symbols-outlined text-3xl text-slate-700 mb-3" aria-hidden="true">work_history</span>
        <p class="text-sm text-slate-400 font-medium mb-1">No jobs found</p>
        <p class="text-xs text-slate-600 max-w-xs">${escapeHtml(filterMsg)}</p>
      </div>`;
    return;
  }

  grid.innerHTML = filtered
    .map((job, index) => {
      const cfg = STATUS_CFG[job.status] || STATUS_CFG.pending;
      const created = _timeAgo(job.created_at);
      const fileCount = job.file_count ?? (job.files ? job.files.filter((f) => f.file_type === "output").length : 0);
      const isRunning = ["executing", "planning"].includes(job.status);
      const spinClass = isRunning ? "jobs-spin" : "";
      const priorityLabel = _priorityLabel(job.priority);

      // Node progress for mini progress bar
      const nodes = job.nodes || [];
      const nodeTotal = job.node_count ?? nodes.length;
      const nodeCompleted = job.nodes_completed ?? nodes.filter((n) => n.status === "completed").length;
      const nodePct = nodeTotal > 0 ? Math.round((nodeCompleted / nodeTotal) * 100) : 0;
      const showMiniProgress = nodeTotal > 0;

      // ETA for actively running jobs
      let etaStr = "";
      if (isRunning && nodes.length > 0) {
        const eta = _estimateETA(nodes);
        if (eta) etaStr = eta;
      }

      return `
      <div
        class="job-card-link bg-slate-900/40 border border-slate-800/50 rounded-xl p-5 space-y-3 cursor-pointer hover:bg-slate-800/40 hover:border-${cfg.color}-500/30 transition-all group"
        role="listitem"
        tabindex="0"
        data-job-id="${escapeHtml(job.id)}"
        aria-label="Job: ${escapeHtml(job.title)}, status ${cfg.label}"
        style="animation-delay: ${index * 30}ms"
      >
        <div class="flex items-start justify-between gap-2">
          <h3 class="text-sm font-bold text-slate-200 group-hover:text-white transition-colors truncate flex-1">${escapeHtml(job.title)}</h3>
          <span class="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full shrink-0 bg-${cfg.color}-500/15 text-${cfg.color}-400 border border-${cfg.color}-500/20">
            <span class="material-symbols-outlined text-[11px] ${spinClass}" aria-hidden="true">${isRunning ? "progress_activity" : cfg.icon}</span>
            ${cfg.label}
          </span>
        </div>
        ${
          job.description && job.description !== job.title
            ? `<p class="text-[11px] text-slate-500 leading-relaxed line-clamp-2">${escapeHtml(job.description.substring(0, 120))}</p>`
            : ""
        }
        ${showMiniProgress ? `
        <div class="space-y-1" title="${nodeCompleted}/${nodeTotal} nodes complete (${nodePct}%)">
          <div class="w-full bg-slate-800/50 h-1.5 rounded-full overflow-hidden">
            <div class="jobs-progress-bar ${isRunning ? "jobs-progress-active" : ""} bg-${cfg.color}-500 h-full rounded-full" style="width: ${nodePct}%" role="progressbar" aria-valuenow="${nodePct}" aria-valuemin="0" aria-valuemax="100" aria-label="Job progress: ${nodePct}%"></div>
          </div>
          <div class="flex items-center justify-between text-xs font-mono text-slate-600">
            <span>${nodeCompleted}/${nodeTotal} nodes</span>
            ${etaStr ? `<span class="text-amber-400/70">${escapeHtml(etaStr)}</span>` : ""}
          </div>
        </div>
        ` : ""}
        <div class="flex items-center gap-3 text-[11px] font-mono text-slate-500 flex-wrap">
          <span class="flex items-center gap-1">
            <span class="material-symbols-outlined text-[11px]" aria-hidden="true">schedule</span>
            ${escapeHtml(created)}
          </span>
          ${job.mode ? `<span class="uppercase">${escapeHtml(job.mode)}</span>` : ""}
          ${priorityLabel ? `<span class="${priorityLabel.cls}">${escapeHtml(priorityLabel.text)}</span>` : ""}
          ${
            fileCount > 0
              ? `<span class="flex items-center gap-0.5" title="${fileCount} output file${fileCount !== 1 ? "s" : ""}"><span class="material-symbols-outlined text-[11px]" aria-hidden="true">attach_file</span><span class="bg-indigo-500/15 text-indigo-400 px-1 rounded">${fileCount}</span></span>`
              : ""
          }
        </div>
      </div>`;
    })
    .join("");

  // Wire click handlers
  grid.querySelectorAll("[data-job-id]").forEach((card) => {
    const handler = () => _showDetailView(card.dataset.jobId);
    card.addEventListener("click", handler);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handler();
      }
    });
  });

  // ── Work Tab adapter: mirror cards into #taskPipelineBody ──────────────
  const workGrid = document.getElementById("taskPipelineBody");
  if (workGrid) {
    if (filtered.length === 0) {
      workGrid.innerHTML = '<div class="col-span-full py-6 text-center text-xs text-slate-600 italic">No active jobs</div>';
    } else {
      workGrid.innerHTML = filtered.slice(0, 6).map((job) => {
        const cfg = STATUS_CFG[job.status] || STATUS_CFG.pending;
        const isRunning = ["executing", "planning"].includes(job.status);
        const spinClass = isRunning ? "jobs-spin" : "";
        return `
        <div class="bg-slate-900/40 border border-slate-800/50 rounded-xl p-4 space-y-2 cursor-pointer hover:bg-slate-800/40 hover:border-${cfg.color}-500/30 transition-all group"
             data-work-job-id="${escapeHtml(job.id)}" tabindex="0" role="listitem"
             aria-label="Job: ${escapeHtml(job.title)}, ${cfg.label}">
          <div class="flex items-start justify-between gap-2">
            <span class="text-xs font-semibold text-slate-200 truncate flex-1">${escapeHtml(job.title)}</span>
            <span class="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full shrink-0 bg-${cfg.color}-500/15 text-${cfg.color}-400">
              <span class="material-symbols-outlined text-[10px] ${spinClass}">${isRunning ? "progress_activity" : cfg.icon}</span>
              ${cfg.label}
            </span>
          </div>
          <div class="text-[11px] font-mono text-slate-600">${_timeAgo(job.created_at)}</div>
        </div>`;
      }).join("");
      // Wire work grid click → full detail view
      workGrid.querySelectorAll("[data-work-job-id]").forEach((card) => {
        const handler = () => _showDetailView(card.dataset.workJobId);
        card.addEventListener("click", handler);
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handler(); }
        });
      });
    }
  }
}


// ---------------------------------------------------------------------------
// Running-badge update (sidebar #jobsRunningBadge)
// ---------------------------------------------------------------------------

function _updateRunningBadge() {
  const badge = el("jobsRunningBadge");
  const running = _jobs.filter((j) =>
    ["pending", "planning", "executing", "reviewing", "cancelling"].includes(j.status),
  ).length;

  if (badge) {
    badge.textContent = String(running);
    badge.setAttribute("aria-label", `${running} job${running !== 1 ? "s" : ""} running`);
    if (running > 0) badge.classList.remove("hidden");
    else badge.classList.add("hidden");
  }

  // ── Work Tab adapter: update #taskPipelineCount chip ────────────────
  const workCount = document.getElementById("taskPipelineCount");
  if (workCount) {
    workCount.textContent = running > 0 ? `${running} active` : "No active jobs";
  }
}

function _updateTotalBadge() {
  const badge = el("jobsTotalBadge");
  if (badge) {
    badge.textContent = `${_jobs.length} job${_jobs.length !== 1 ? "s" : ""}`;
  }
}

// ---------------------------------------------------------------------------
// Priority label
// ---------------------------------------------------------------------------

function _priorityLabel(priority) {
  if (priority == null || priority === 0) return null;
  if (priority > 0) return { text: "High", cls: "jobs-priority-high font-bold" };
  if (priority < 0) return { text: "Low", cls: "jobs-priority-low" };
  return null;
}

// ---------------------------------------------------------------------------
// Formatting helpers
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

function _formatDuration(sec) {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

/**
 * Estimate remaining time based on average completed-node duration.
 * Returns a human-readable string like "~2 min remaining" or null.
 */
function _estimateETA(nodes) {
  const completed = nodes.filter(
    (n) => n.status === "completed" && n.started_at && n.completed_at,
  );
  if (completed.length === 0) return null;

  const totalMs = completed.reduce((sum, n) => {
    return sum + (new Date(n.completed_at).getTime() - new Date(n.started_at).getTime());
  }, 0);
  const avgMs = totalMs / completed.length;

  const remaining = nodes.filter(
    (n) => !["completed", "failed", "cancelled", "skipped"].includes(n.status),
  ).length;
  if (remaining === 0) return null;

  const etaSec = Math.round((avgMs * remaining) / 1000);
  if (etaSec <= 0) return null;
  return `~${_formatDuration(etaSec)} remaining`;
}

function _formatSize(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

