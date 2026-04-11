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
  </div>

  <!-- ============================================================ -->
  <!-- DETAIL VIEW                                                   -->
  <!-- ============================================================ -->
  <div id="jobsDetailView" class="jobs-detail-view hidden flex-1 flex flex-col p-8 overflow-y-auto custom-scrollbar gap-6">

    <!-- Back button + Title -->
    <div class="flex items-center gap-4">
      <button
        id="jobBackBtn"
        aria-label="Back to job list"
        class="p-2 rounded-lg hover:bg-slate-800/60 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400"
      >
        <span class="material-symbols-outlined text-slate-400" aria-hidden="true">arrow_back</span>
      </button>
      <div class="flex-1 min-w-0">
        <h2 id="jobDetailTitle" class="text-lg font-headline font-bold text-slate-100 truncate"></h2>
        <div class="flex items-center gap-3 mt-1 flex-wrap">
          <span id="jobDetailStatus" class="inline-flex items-center gap-1 text-xs font-bold uppercase tracking-widest px-2.5 py-1 rounded-full"></span>
          <span id="jobDetailPriority" class="text-xs font-mono text-slate-500"></span>
          <span id="jobDetailCreated" class="text-xs font-mono text-slate-500"></span>
          <span id="jobDetailMode" class="text-xs font-mono text-slate-500 uppercase"></span>
        </div>
      </div>
      <div class="flex gap-2 shrink-0 flex-wrap">
        <!-- Review Gate Buttons -->
        <button
          id="jobApproveBtn"
          aria-label="Approve this job"
          class="hidden items-center gap-1.5 px-4 py-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-xs font-bold uppercase tracking-wider rounded-lg border border-emerald-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-400"
        >
          <span class="material-symbols-outlined text-sm" aria-hidden="true">check_circle</span><span>Approve</span>
        </button>
        <button
          id="jobRejectBtn"
          aria-label="Reject this job"
          class="hidden items-center gap-1.5 px-4 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 text-xs font-bold uppercase tracking-wider rounded-lg border border-red-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-red-400"
        >
          <span class="material-symbols-outlined text-sm" aria-hidden="true">cancel</span><span>Reject</span>
        </button>
        <button
          id="jobCancelBtn"
          aria-label="Cancel this job"
          class="hidden items-center gap-1.5 px-4 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 text-xs font-bold uppercase tracking-wider rounded-lg border border-red-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-red-400"
        >
          <span class="material-symbols-outlined text-sm" aria-hidden="true">cancel</span><span>Cancel</span>
        </button>
        <button
          id="jobDeleteBtn"
          aria-label="Delete this job"
          class="hidden items-center gap-1.5 px-4 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 text-xs font-bold uppercase tracking-wider rounded-lg border border-red-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-red-400"
        >
          <span class="material-symbols-outlined text-sm" aria-hidden="true">delete</span><span>Delete</span>
        </button>
        <button
          id="jobSaveTemplateBtn"
          aria-label="Save job as pipeline template"
          class="hidden items-center gap-1.5 px-4 py-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-xs font-bold uppercase tracking-wider rounded-lg border border-emerald-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-400"
        >
          <span class="material-symbols-outlined text-sm" aria-hidden="true">bookmark_add</span><span>Save as Template</span>
        </button>
      </div>
    </div>

    <!-- Description -->
    <div id="jobDetailDesc" class="text-xs text-slate-400 leading-relaxed hidden"></div>

    <!-- Progress Bar -->
    <div class="bg-slate-900/40 border border-slate-800/60 rounded-xl p-4">
      <div class="flex items-center justify-between mb-2">
        <span class="text-xs font-bold uppercase tracking-widest text-slate-500">Pipeline Progress</span>
        <span id="jobProgressLabel" class="text-xs font-mono text-slate-400">0/0 nodes</span>
      </div>
      <div class="w-full bg-slate-800/50 h-2 rounded-full overflow-hidden">
        <div
          id="jobProgressBar"
          role="progressbar"
          aria-valuenow="0"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-label="Job pipeline progress"
          class="jobs-progress-bar bg-indigo-500 h-full rounded-full"
          style="width: 0%"
        ></div>
      </div>
    </div>

    <!-- Job Cost Summary -->
    <div id="jobCostSummary" class="hidden bg-slate-900/40 border border-slate-800/60 rounded-xl p-4">
      <div class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-2">
        <span class="material-symbols-outlined text-xs align-middle text-emerald-400 mr-1" aria-hidden="true">payments</span>
        Job Cost
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3" role="region" aria-label="Job cost summary">
        <div class="text-center">
          <div class="text-sm font-bold text-emerald-400 font-mono" id="jobTotalCost">--</div>
          <div class="text-[10px] text-slate-600 uppercase tracking-widest">Total Cost</div>
        </div>
        <div class="text-center">
          <div class="text-sm font-bold text-cyan-400 font-mono" id="jobTotalTokensIn">--</div>
          <div class="text-[10px] text-slate-600 uppercase tracking-widest">Tokens In</div>
        </div>
        <div class="text-center">
          <div class="text-sm font-bold text-purple-400 font-mono" id="jobTotalTokensOut">--</div>
          <div class="text-[10px] text-slate-600 uppercase tracking-widest">Tokens Out</div>
        </div>
        <div class="text-center">
          <div class="text-sm font-bold text-amber-400 font-mono" id="jobTotalTokens">--</div>
          <div class="text-[10px] text-slate-600 uppercase tracking-widest">Total Tokens</div>
        </div>
      </div>
    </div>

    <!-- Node Pipeline Timeline -->
    <div>
      <div class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-3">Node Pipeline</div>
      <div id="jobNodesContainer" class="flex gap-3 overflow-x-auto pb-2 custom-scrollbar snap-x snap-mandatory" role="list" aria-label="Pipeline nodes"></div>
    </div>

    <!-- Expanded Node Detail -->
    <div id="jobNodeDetail" class="hidden jobs-node-detail bg-slate-900/40 border border-slate-800/60 rounded-xl p-5 space-y-3" aria-live="polite">
      <div class="flex items-center justify-between">
        <h3 id="nodeDetailTitle" class="text-sm font-headline font-bold text-slate-200"></h3>
        <button id="nodeDetailClose" aria-label="Close node detail" class="p-1 rounded hover:bg-slate-800/60 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400">
          <span class="material-symbols-outlined text-slate-500 text-sm" aria-hidden="true">close</span>
        </button>
      </div>
      <div class="flex items-center gap-3 flex-wrap">
        <div id="nodeDetailStatus" class="inline-flex items-center gap-1 text-xs font-bold uppercase tracking-widest px-2 py-0.5 rounded-full"></div>
        <div id="nodeDetailModel" class="text-[11px] font-mono text-slate-600"></div>
        <div id="nodeDetailElapsed" class="text-[11px] font-mono text-slate-600"></div>
      </div>
      <!-- Node Progress Bar (for running nodes) -->
      <div id="nodeProgressSection" class="hidden">
        <div class="w-full bg-slate-800/50 h-1.5 rounded-full overflow-hidden">
          <div id="nodeProgressBar" class="jobs-progress-bar bg-amber-500 h-full rounded-full" style="width: 0%"
               role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100" aria-label="Node progress"></div>
        </div>
        <div id="nodeProgressText" class="text-[11px] font-mono text-slate-600 mt-1"></div>
      </div>
      <!-- Node Cost/Token Metrics -->
      <div id="nodeDetailCostSection" class="hidden">
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2" role="region" aria-label="Node execution metrics">
          <div class="bg-slate-800/40 border border-slate-700/30 rounded-lg p-2.5 text-center">
            <div class="text-xs font-bold text-cyan-400 font-mono" id="nodeDetailTokensIn">--</div>
            <div class="text-[10px] text-slate-600 uppercase tracking-widest">Tokens In</div>
          </div>
          <div class="bg-slate-800/40 border border-slate-700/30 rounded-lg p-2.5 text-center">
            <div class="text-xs font-bold text-purple-400 font-mono" id="nodeDetailTokensOut">--</div>
            <div class="text-[10px] text-slate-600 uppercase tracking-widest">Tokens Out</div>
          </div>
          <div class="bg-slate-800/40 border border-slate-700/30 rounded-lg p-2.5 text-center">
            <div class="text-xs font-bold text-emerald-400 font-mono" id="nodeDetailCost">--</div>
            <div class="text-[10px] text-slate-600 uppercase tracking-widest">Cost</div>
          </div>
          <div class="bg-slate-800/40 border border-slate-700/30 rounded-lg p-2.5 text-center">
            <div class="text-xs font-bold text-amber-400 font-mono" id="nodeDetailDuration">--</div>
            <div class="text-[10px] text-slate-600 uppercase tracking-widest">Duration</div>
          </div>
        </div>
      </div>
      <div id="nodeDetailInstructions" class="text-xs text-slate-400 leading-relaxed"></div>
      <div id="nodeDetailTools" class="flex flex-wrap gap-1.5"></div>
      <div id="nodeDetailOutputSection" class="hidden">
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-[11px] font-bold uppercase tracking-widest text-slate-500">Output</span>
          <button id="nodeDetailCopyBtn" class="text-[11px] text-slate-600 hover:text-slate-400 transition-colors flex items-center gap-0.5 focus:outline-none" title="Copy output">
            <span class="material-symbols-outlined text-xs">content_copy</span> Copy
          </button>
        </div>
        <div id="nodeDetailOutput" class="bg-slate-950/50 border border-slate-800/40 rounded-lg p-4 text-sm text-slate-300 max-h-[480px] overflow-y-auto custom-scrollbar leading-relaxed node-output-rendered"></div>
      </div>
      <div id="nodeDetailFiles" class="hidden flex flex-wrap gap-2"></div>
    </div>

    <!-- Output Files -->
    <div id="jobFilesSection" class="hidden">
      <div class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-3">Output Files</div>
      <div id="jobFilesList" class="space-y-2" role="list" aria-label="Job output files"></div>
    </div>

    <!-- Artifacts (versioned outputs with provenance) -->
    <div id="jobArtifactsSection" class="hidden">
      <div class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-3 flex items-center gap-1">
        <span class="material-symbols-outlined text-xs text-violet-400" aria-hidden="true">inventory_2</span>
        Artifacts
      </div>
      <div id="jobArtifactsList" class="space-y-2" role="list" aria-label="Job artifacts with provenance"></div>
    </div>

    <!-- Audit Trail -->
    <details class="group">
      <summary class="text-xs font-bold uppercase tracking-widest text-slate-500 cursor-pointer hover:text-slate-400 transition-colors flex items-center gap-1 select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 rounded">
        <span class="material-symbols-outlined text-xs transition-transform group-open:rotate-90" aria-hidden="true">chevron_right</span>
        Audit Trail
      </summary>
      <div id="jobAuditList" class="mt-3 space-y-1 max-h-64 overflow-y-auto custom-scrollbar" role="log" aria-label="Job audit trail"></div>
    </details>

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

  // Detail view: back — close the overlay
  el("jobBackBtn")?.addEventListener("click", () => {
    const prevId = _selectedJobId;
    _showListView();
    // Restore focus to the job card that was clicked (if still present)
    if (prevId) {
      const card = document.querySelector(`[data-work-job-id="${prevId}"]`) ||
                   document.querySelector(`[data-job-id="${prevId}"]`);
      if (card) card.focus();
    }
  });

  // Detail view: close expanded node
  el("nodeDetailClose")?.addEventListener("click", () => {
    el("jobNodeDetail")?.classList.add("hidden");
  });

  // Detail view: cancel job
  el("jobCancelBtn")?.addEventListener("click", _cancelCurrentJob);

  // Detail view: delete job
  el("jobDeleteBtn")?.addEventListener("click", _deleteCurrentJob);

  // Detail view: save as template
  el("jobSaveTemplateBtn")?.addEventListener("click", _saveAsTemplate);

  // Detail view: review gate -- approve/reject
  el("jobApproveBtn")?.addEventListener("click", () => _reviewJob("approve"));
  el("jobRejectBtn")?.addEventListener("click", () => _reviewJob("reject"));

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

  // Keyboard: Escape from detail returns to list view
  view.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const detail = el("jobsDetailView");
      if (detail && !detail.classList.contains("hidden")) {
        e.preventDefault();
        _showListView();
      }
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
  // Ensure the overlay container is visible
  const view = el("jobsView");
  if (view) {
    view.classList.remove("hidden");
    view.style.display = "";
  }
  el("jobsListView")?.classList.add("hidden");
  const detail = el("jobsDetailView");
  if (detail) {
    detail.classList.remove("hidden");
  }
  el("jobNodeDetail")?.classList.add("hidden");
  _selectedJobId = jobId;
  _loadJobDetail(jobId);
  // Focus the back button for keyboard navigation
  el("jobBackBtn")?.focus();
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
    if (_selectedJobId) _loadJobDetail(_selectedJobId);
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
        if (_selectedJobId && data.job_id === _selectedJobId) {
          _loadJobDetail(_selectedJobId);
        }
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
    _sseSource.addEventListener("job_completed", (e) => {
      refreshList();
      try {
        const data = JSON.parse(e.data);
        if (_selectedJobId && data.job_id === _selectedJobId) {
          _loadJobDetail(_selectedJobId);
        }
        _announceStatus("Job completed");
      } catch { /* ignore */ }
    });
    _sseSource.addEventListener("job_failed", (e) => {
      refreshList();
      try {
        const data = JSON.parse(e.data);
        if (_selectedJobId && data.job_id === _selectedJobId) {
          _loadJobDetail(_selectedJobId);
        }
        _announceStatus("Job failed");
      } catch { /* ignore */ }
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
// API: Load single job detail
// ---------------------------------------------------------------------------

async function _loadJobDetail(jobId) {
  try {
    const res = await fetch(`${API}/api/jobs/${encodeURIComponent(jobId)}`);
    if (!res.ok) {
      if (res.status === 404) {
        showToast("Job not found", "error");
        _showListView();
        el("jobsView")?.classList.add("hidden");
      }
      return;
    }
    const job = await res.json();
    _renderJobDetail(job);
  } catch (err) {
    console.error("[jobs_ui] Failed to load job detail:", err);
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
// API: Cancel job
// ---------------------------------------------------------------------------

async function _cancelCurrentJob() {
  if (!_selectedJobId) return;
  const btn = el("jobCancelBtn");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(
      `${API}/api/jobs/${encodeURIComponent(_selectedJobId)}/cancel`,
      { method: "POST" },
    );
    if (res.ok) {
      showToast("Job cancellation requested", "info");
      _announceStatus("Job cancellation requested");
      _loadJobDetail(_selectedJobId);
      _loadJobs();
    } else {
      const errData = await res.json().catch(() => ({}));
      showToast(errData.detail || "Cancel failed", "error");
    }
  } catch {
    showToast("Couldn't cancel the job. Check your connection and try again.", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// API: Delete job
// ---------------------------------------------------------------------------

async function _deleteCurrentJob() {
  if (!_selectedJobId) return;
  if (!confirm("Delete this job? This cannot be undone.")) return;

  const btn = el("jobDeleteBtn");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(
      `${API}/api/jobs/${encodeURIComponent(_selectedJobId)}`,
      { method: "DELETE" },
    );
    if (res.ok) {
      showToast("Job deleted", "info");
      _announceStatus("Job deleted");
      _showListView();
      el("jobsView")?.classList.add("hidden");
      _loadJobs();
    } else {
      const errData = await res.json().catch(() => ({}));
      showToast(errData.detail || "Delete failed", "error");
    }
  } catch {
    showToast("Couldn't delete the job. Check your connection and try again.", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// API: Review job (approve / reject)
// ---------------------------------------------------------------------------

async function _reviewJob(action) {
  if (!_selectedJobId) return;
  const approveBtn = el("jobApproveBtn");
  const rejectBtn = el("jobRejectBtn");
  if (approveBtn) approveBtn.disabled = true;
  if (rejectBtn) rejectBtn.disabled = true;

  try {
    // The approve/reject endpoint. Try standard patterns.
    const endpoint = `${API}/api/jobs/${encodeURIComponent(_selectedJobId)}/${action}`;
    const res = await fetch(endpoint, { method: "POST" });
    if (res.ok) {
      showToast(`Job ${action === "approve" ? "approved" : "rejected"}`, "info");
      _announceStatus(`Job ${action === "approve" ? "approved" : "rejected"}`);
      _loadJobDetail(_selectedJobId);
      _loadJobs();
    } else {
      const errData = await res.json().catch(() => ({}));
      showToast(errData.detail || `${action} failed`, "error");
    }
  } catch {
    showToast(`Couldn't ${action} the job. Check your connection and try again.`, "error");
  } finally {
    if (approveBtn) approveBtn.disabled = false;
    if (rejectBtn) rejectBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// API: Save as template
// ---------------------------------------------------------------------------

async function _saveAsTemplate() {
  if (!_selectedJobId) return;
  const name = prompt("Template name:");
  if (!name || !name.trim()) return;

  const btn = el("jobSaveTemplateBtn");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(`${API}/api/jobs/templates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: _selectedJobId, name: name.trim() }),
    });
    if (res.ok) {
      showToast(`Template "${escapeHtml(name.trim())}" saved`, "info");
    } else {
      const errData = await res.json().catch(() => ({}));
      showToast(errData.detail || "Failed to save template", "error");
    }
  } catch {
    showToast("Couldn't save the template. Check your connection and try again.", "error");
  } finally {
    if (btn) btn.disabled = false;
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
// Render: Job Detail
// ---------------------------------------------------------------------------

function _renderJobDetail(job) {
  const cfg = STATUS_CFG[job.status] || STATUS_CFG.pending;

  // Title
  const titleEl = el("jobDetailTitle");
  if (titleEl) titleEl.textContent = job.title || "Untitled Job";

  // Status badge
  const statusEl = el("jobDetailStatus");
  if (statusEl) {
    const isRunning = ["executing", "planning"].includes(job.status);
    const spinClass = isRunning ? "jobs-spin" : "";
    statusEl.className = `inline-flex items-center gap-1 text-xs font-bold uppercase tracking-widest px-2.5 py-1 rounded-full bg-${cfg.color}-500/15 text-${cfg.color}-400 border border-${cfg.color}-500/20`;
    statusEl.innerHTML = `<span class="material-symbols-outlined text-xs ${spinClass}" aria-hidden="true">${isRunning ? "progress_activity" : cfg.icon}</span> ${cfg.label}`;
  }

  // Priority
  const priorityEl = el("jobDetailPriority");
  if (priorityEl) {
    const pl = _priorityLabel(job.priority);
    priorityEl.textContent = pl ? pl.text : "";
    priorityEl.className = `text-xs font-mono ${pl ? pl.cls : "text-slate-500"}`;
  }

  // Meta
  const createdEl = el("jobDetailCreated");
  if (createdEl) createdEl.textContent = job.created_at ? _formatDate(job.created_at) : "";

  const modeEl = el("jobDetailMode");
  if (modeEl) modeEl.textContent = job.mode ? `Mode: ${job.mode}` : "";

  // Description
  const descEl = el("jobDetailDesc");
  if (descEl) {
    if (job.description && job.description !== job.title) {
      descEl.textContent = job.description;
      descEl.classList.remove("hidden");
    } else {
      descEl.classList.add("hidden");
    }
  }

  // Cancel button -- only visible for non-terminal jobs
  const cancelBtn = el("jobCancelBtn");
  const terminalStates = ["done", "failed", "cancelled", "cancelling"];
  if (cancelBtn) {
    if (terminalStates.includes(job.status)) {
      cancelBtn.classList.add("hidden");
      cancelBtn.classList.remove("flex");
    } else {
      cancelBtn.classList.remove("hidden");
      cancelBtn.classList.add("flex");
    }
  }

  // Delete button -- only visible for terminal jobs
  const deleteBtn = el("jobDeleteBtn");
  if (deleteBtn) {
    if (terminalStates.includes(job.status)) {
      deleteBtn.classList.remove("hidden");
      deleteBtn.classList.add("flex");
    } else {
      deleteBtn.classList.add("hidden");
      deleteBtn.classList.remove("flex");
    }
  }

  // Review gate buttons -- visible only when reviewing
  const approveBtn = el("jobApproveBtn");
  const rejectBtn = el("jobRejectBtn");
  if (approveBtn && rejectBtn) {
    if (job.status === "reviewing") {
      approveBtn.classList.remove("hidden");
      approveBtn.classList.add("flex");
      rejectBtn.classList.remove("hidden");
      rejectBtn.classList.add("flex");
    } else {
      approveBtn.classList.add("hidden");
      approveBtn.classList.remove("flex");
      rejectBtn.classList.add("hidden");
      rejectBtn.classList.remove("flex");
    }
  }

  // Save-as-template button -- only for done jobs
  const templateBtn = el("jobSaveTemplateBtn");
  if (templateBtn) {
    if (job.status === "done") {
      templateBtn.classList.remove("hidden");
      templateBtn.classList.add("flex");
    } else {
      templateBtn.classList.add("hidden");
      templateBtn.classList.remove("flex");
    }
  }

  // Nodes
  const nodes = job.nodes || [];
  _renderNodes(nodes, job.status);

  // Job cost summary (uses job.cost_cents + audit trail token data)
  _renderJobCostSummary(job);

  // Progress bar
  const completedCount = nodes.filter((n) => n.status === "completed").length;
  const runningCount = nodes.filter((n) => n.status === "running").length;
  const totalCount = nodes.length;
  const pct = totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0;
  const isActive = ["executing", "planning"].includes(job.status);

  const bar = el("jobProgressBar");
  if (bar) {
    bar.style.width = `${pct}%`;
    bar.setAttribute("aria-valuenow", String(pct));
    bar.setAttribute("aria-valuemax", "100");
    if (isActive && runningCount > 0) {
      bar.classList.add("jobs-progress-active");
    } else {
      bar.classList.remove("jobs-progress-active");
    }
  }
  const progressLabel = el("jobProgressLabel");
  if (progressLabel) {
    let labelText = `${completedCount}/${totalCount} nodes (${pct}%)`;
    if (isActive && totalCount > 0) {
      const eta = _estimateETA(nodes);
      if (eta) labelText += ` \u2014 ${eta}`;
    }
    progressLabel.textContent = labelText;
  }

  // Output files
  const outputFiles = (job.files || []).filter((f) => f.file_type === "output");
  const filesSection = el("jobFilesSection");
  const filesList = el("jobFilesList");
  if (filesSection && filesList) {
    if (outputFiles.length > 0) {
      filesSection.classList.remove("hidden");
      filesList.innerHTML = outputFiles
        .map(
          (f) => `
          <a
            href="${API}/api/jobs/${encodeURIComponent(job.id)}/files/${encodeURIComponent(f.id)}"
            download="${escapeHtml(f.filename)}"
            class="flex items-center gap-3 bg-slate-800/40 border border-slate-700/40 rounded-lg px-4 py-3 hover:bg-slate-800/60 transition-colors group focus:outline-none focus:ring-2 focus:ring-indigo-400"
            role="listitem"
            aria-label="Download ${escapeHtml(f.filename)}"
          >
            <span class="material-symbols-outlined text-indigo-400 group-hover:text-indigo-300" aria-hidden="true">download</span>
            <div class="flex-1 min-w-0">
              <div class="text-xs font-medium text-slate-200 truncate">${escapeHtml(f.filename)}</div>
              <div class="text-[11px] font-mono text-slate-500">${f.mime_type || "unknown"}${f.size_bytes ? " - " + _formatSize(f.size_bytes) : ""}</div>
            </div>
          </a>`,
        )
        .join("");
    } else {
      filesSection.classList.add("hidden");
    }
  }

  // Artifacts section (versioned outputs with provenance)
  const artifacts = job.artifacts || [];
  const artifactsSection = el("jobArtifactsSection");
  const artifactsList = el("jobArtifactsList");
  if (artifactsSection && artifactsList) {
    if (artifacts.length > 0) {
      artifactsSection.classList.remove("hidden");
      artifactsList.innerHTML = artifacts
        .map((art) => {
          const ver = art.current_version;
          const sizeStr = ver && ver.file_size_bytes ? _formatSize(ver.file_size_bytes) : "";
          const mimeStr = ver ? (ver.mime_type || "unknown") : "";
          const nodeStr = ver && ver.node_attempt_id ? ver.node_attempt_id.substring(0, 12) : "";
          const hasLineage = ver && ver.parent_version_id;
          const downloadUrl = `${API}/api/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(art.id)}/download`;
          const lineageUrl = `${API}/api/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(art.id)}/lineage`;
          return `
          <div class="bg-slate-800/40 border border-slate-700/40 rounded-lg px-4 py-3" role="listitem">
            <div class="flex items-center gap-3">
              <span class="material-symbols-outlined text-violet-400 text-lg" aria-hidden="true">inventory_2</span>
              <div class="flex-1 min-w-0">
                <div class="text-xs font-medium text-slate-200 truncate">${escapeHtml(art.name)}.${escapeHtml(art.artifact_type)}</div>
                <div class="text-[11px] font-mono text-slate-500">${mimeStr}${sizeStr ? " - " + sizeStr : ""}${nodeStr ? " - node:" + escapeHtml(nodeStr) : ""}</div>
              </div>
              <a
                href="${downloadUrl}"
                download
                class="inline-flex items-center gap-1 text-xs font-medium text-indigo-400 hover:text-indigo-300 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 rounded px-2 py-1"
                aria-label="Download ${escapeHtml(art.name)}"
              >
                <span class="material-symbols-outlined text-sm" aria-hidden="true">download</span>
                Download
              </a>
              ${hasLineage ? `
              <button
                class="inline-flex items-center gap-1 text-xs font-medium text-violet-400 hover:text-violet-300 transition-colors focus:outline-none focus:ring-2 focus:ring-violet-400 rounded px-2 py-1 jobs-lineage-btn"
                data-lineage-url="${lineageUrl}"
                data-artifact-name="${escapeHtml(art.name)}"
                aria-label="View provenance for ${escapeHtml(art.name)}"
              >
                <span class="material-symbols-outlined text-sm" aria-hidden="true">account_tree</span>
                Provenance
              </button>` : ""}
            </div>
            ${ver ? `<div class="text-[10px] font-mono text-slate-600 mt-1 truncate" title="SHA-256: ${ver.sha256 || ""}">sha256: ${(ver.sha256 || "").substring(0, 16)}...</div>` : ""}
          </div>`;
        })
        .join("");

      // Wire provenance buttons
      artifactsList.querySelectorAll(".jobs-lineage-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const url = btn.dataset.lineageUrl;
          const name = btn.dataset.artifactName;
          try {
            const resp = await fetch(url);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            const chain = data.lineage || [];
            if (chain.length === 0) {
              alert("No lineage data available for this artifact.");
              return;
            }
            const lines = chain.map(
              (v, i) => `${i + 1}. Version ${v.version_number} (node: ${v.node_attempt_id || "unknown"}) - ${v.mime_type} - ${_formatSize(v.file_size_bytes)}`
            );
            alert(`Provenance chain for "${name}":\n\n${lines.join("\n")}`);
          } catch (err) {
            console.error("Failed to fetch lineage:", err);
            alert("Failed to load provenance data.");
          }
        });
      });
    } else {
      artifactsSection.classList.add("hidden");
    }
  }

  // Audit trail
  const auditList = el("jobAuditList");
  const audit = job.audit || [];
  if (auditList) {
    if (audit.length === 0) {
      auditList.innerHTML =
        '<div class="text-xs text-slate-600 italic">No audit entries</div>';
    } else {
      auditList.innerHTML = audit
        .map(
          (a) => `
          <div class="flex items-start gap-2 text-xs font-mono text-slate-500 py-1 border-b border-slate-800/30 last:border-0">
            <span class="text-slate-600 shrink-0">${_formatTime(a.timestamp)}</span>
            <span class="text-slate-400 font-bold">${escapeHtml(a.action)}</span>
            ${a.detail ? `<span class="text-slate-600 truncate flex-1">${escapeHtml(a.detail)}</span>` : ""}
            ${a.actor ? `<span class="text-slate-700 ml-auto shrink-0">${escapeHtml(a.actor)}</span>` : ""}
          </div>`,
        )
        .join("");
    }
  }
}

// ---------------------------------------------------------------------------
// Render: Nodes Pipeline (horizontal timeline cards with colored left border)
// ---------------------------------------------------------------------------

function _renderNodes(nodes, jobStatus) {
  const container = el("jobNodesContainer");
  if (!container) return;

  if (nodes.length === 0) {
    container.innerHTML =
      '<div class="text-xs text-slate-500 italic py-4" role="status">No pipeline nodes yet</div>';
    return;
  }

  container.innerHTML = nodes
    .map((node, i) => {
      const cfg = NODE_STATUS_CFG[node.status] || NODE_STATUS_CFG.pending;
      const isLast = i === nodes.length - 1;
      const isRunning = node.status === "running";
      const spinClass = isRunning ? "jobs-spin" : "";

      // Elapsed time (uses started_at/completed_at when available)
      const elapsedStr = _nodeElapsed(node);

      // Model info
      const modelStr = node.model || "";

      // Truncated output preview for completed nodes
      let outputPreview = "";
      if (node.status === "completed" && node.output_json) {
        try {
          const parsed = typeof node.output_json === "string"
            ? JSON.parse(node.output_json) : node.output_json;
          const raw = typeof parsed === "string" ? parsed : JSON.stringify(parsed);
          outputPreview = _truncate(raw, 200);
        } catch {
          outputPreview = _truncate(String(node.output_json), 200);
        }
      }

      // File links for nodes that produced output files
      const nodeFiles = node.output_files || [];

      return `
      <div class="flex items-center shrink-0 snap-start">
        <div
          class="jobs-node-card node-card bg-slate-900/60 border border-slate-800/50 rounded-xl p-4 min-w-[180px] max-w-[220px] cursor-pointer hover:bg-slate-800/50 transition-all"
          data-color="${cfg.color}"
          tabindex="0"
          role="listitem"
          data-node-idx="${i}"
          aria-label="Node ${i + 1}: ${escapeHtml(node.title || "Untitled")}, status ${cfg.label}"
        >
          <div class="flex items-center gap-2 mb-2">
            <span class="material-symbols-outlined text-${cfg.color}-400 text-sm ${spinClass}" aria-hidden="true">${isRunning ? "progress_activity" : cfg.icon}</span>
            <span class="text-[11px] font-bold uppercase tracking-widest text-${cfg.color}-400">${cfg.label}</span>
          </div>
          <div class="text-xs font-medium text-slate-200 truncate">${escapeHtml(node.title || "Untitled")}</div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-slate-600 mt-1.5 flex-wrap">
            <span>Step ${node.sequence ?? i + 1}</span>
            ${elapsedStr ? `<span class="text-slate-500" title="Elapsed time">${escapeHtml(elapsedStr)}</span>` : ""}
            ${modelStr ? `<span class="text-slate-600 truncate max-w-[80px]" title="${escapeHtml(modelStr)}">${escapeHtml(modelStr)}</span>` : ""}
          </div>
          ${isRunning && node.progress != null ? `
            <div class="mt-2 w-full bg-slate-800/50 h-1 rounded-full overflow-hidden">
              <div class="jobs-progress-bar jobs-progress-active bg-amber-500 h-full rounded-full" style="width: ${Math.min(100, Math.max(0, node.progress))}%" role="progressbar" aria-valuenow="${Math.min(100, Math.max(0, node.progress))}" aria-valuemin="0" aria-valuemax="100" aria-label="Node progress"></div>
            </div>
          ` : ""}
          ${outputPreview ? `
            <div class="mt-2 text-[11px] text-slate-500 leading-relaxed line-clamp-3 break-all" title="Node output preview">${escapeHtml(outputPreview)}</div>
          ` : ""}
          ${nodeFiles.length > 0 ? `
            <div class="mt-2 flex flex-wrap gap-1">
              ${nodeFiles.map((f) => `<span class="inline-flex items-center gap-0.5 text-xs font-mono text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 rounded px-1.5 py-0.5 truncate max-w-[120px]" title="${escapeHtml(f.filename || f.name || "file")}"><span class="material-symbols-outlined text-xs" aria-hidden="true">description</span>${escapeHtml(_truncate(f.filename || f.name || "file", 20))}</span>`).join("")}
            </div>
          ` : ""}
        </div>
        ${!isLast ? '<span class="material-symbols-outlined text-slate-700 text-sm mx-1 shrink-0" aria-hidden="true">chevron_right</span>' : ""}
      </div>`;
    })
    .join("");

  // Wire node click -> expand detail
  container.querySelectorAll("[data-node-idx]").forEach((card) => {
    const handler = () => {
      const idx = parseInt(card.dataset.nodeIdx, 10);
      _renderNodeDetail(nodes[idx]);
    };
    card.addEventListener("click", handler);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handler();
      }
      // Arrow keys navigate between nodes
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const cards = container.querySelectorAll("[data-node-idx]");
        const currentIdx = parseInt(card.dataset.nodeIdx, 10);
        const nextIdx = e.key === "ArrowRight" ? currentIdx + 1 : currentIdx - 1;
        if (nextIdx >= 0 && nextIdx < cards.length) {
          cards[nextIdx].focus();
        }
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Render: Expanded Node Detail
// ---------------------------------------------------------------------------

function _renderNodeDetail(node) {
  const panel = el("jobNodeDetail");
  if (!panel) return;
  panel.classList.remove("hidden");

  const cfg = NODE_STATUS_CFG[node.status] || NODE_STATUS_CFG.pending;
  const isRunning = node.status === "running";

  // Title
  const titleEl = el("nodeDetailTitle");
  if (titleEl) titleEl.textContent = node.title || "Untitled Node";

  // Status
  const statusEl = el("nodeDetailStatus");
  if (statusEl) {
    const spinClass = isRunning ? "jobs-spin" : "";
    statusEl.className = `inline-flex items-center gap-1 text-xs font-bold uppercase tracking-widest px-2 py-0.5 rounded-full bg-${cfg.color}-500/15 text-${cfg.color}-400 border border-${cfg.color}-500/20`;
    statusEl.innerHTML = `<span class="material-symbols-outlined text-xs ${spinClass}" aria-hidden="true">${isRunning ? "progress_activity" : cfg.icon}</span> ${cfg.label}`;
  }

  // Model
  const modelEl = el("nodeDetailModel");
  if (modelEl) {
    modelEl.textContent = node.model ? `Model: ${node.model}` : "";
  }

  // Elapsed time (prefer started_at/completed_at)
  const elapsedEl = el("nodeDetailElapsed");
  if (elapsedEl) {
    const elapsed = _nodeElapsed(node);
    elapsedEl.textContent = elapsed ? `Elapsed: ${elapsed}` : "";
  }

  // Node cost/token metrics
  _renderNodeCostMetrics(node);

  // Node progress bar (running nodes)
  const progressSection = el("nodeProgressSection");
  const progressBar = el("nodeProgressBar");
  const progressText = el("nodeProgressText");
  if (progressSection && progressBar) {
    if (isRunning && node.progress != null) {
      progressSection.classList.remove("hidden");
      const pct = Math.min(100, Math.max(0, node.progress));
      progressBar.style.width = `${pct}%`;
      progressBar.setAttribute("aria-valuenow", String(Math.round(pct)));
      if (progressText) {
        progressText.textContent = node.current_operation
          ? `${node.current_operation} (${Math.round(pct)}%)`
          : `${Math.round(pct)}% complete`;
      }
    } else {
      progressSection.classList.add("hidden");
    }
  }

  // Instructions
  const instrEl = el("nodeDetailInstructions");
  if (instrEl) instrEl.textContent = node.instructions || "No instructions specified";

  // Allowed tools
  const toolsEl = el("nodeDetailTools");
  if (toolsEl) {
    const tools = node.tools_allowed || [];
    toolsEl.innerHTML =
      tools.length > 0
        ? tools
            .map(
              (t) =>
                `<span class="text-[11px] font-mono bg-slate-800/60 border border-slate-700/40 text-slate-400 px-2 py-0.5 rounded">${escapeHtml(t)}</span>`,
            )
            .join("")
        : '<span class="text-[11px] text-slate-600 italic">No tool restrictions</span>';
  }

  // Output preview (rendered markdown / highlighted JSON)
  const outputSection = el("nodeDetailOutputSection");
  const outputEl = el("nodeDetailOutput");
  if (outputSection && outputEl) {
    if (node.output_json) {
      let parsed;
      try {
        parsed =
          typeof node.output_json === "string"
            ? JSON.parse(node.output_json)
            : node.output_json;
      } catch {
        parsed = String(node.output_json);
      }
      outputEl.innerHTML = _renderOutputHtml(parsed);
      outputSection.classList.remove("hidden");

      // Wire copy button
      const copyBtn = el("nodeDetailCopyBtn");
      if (copyBtn) {
        const newBtn = copyBtn.cloneNode(true);
        copyBtn.parentNode.replaceChild(newBtn, copyBtn);
        newBtn.id = "nodeDetailCopyBtn";
        newBtn.addEventListener("click", () => {
          const text = _outputPlainText(parsed);
          navigator.clipboard.writeText(text).then(() => {
            newBtn.innerHTML = '<span class="material-symbols-outlined text-xs">check</span> Copied';
            setTimeout(() => {
              newBtn.innerHTML = '<span class="material-symbols-outlined text-xs">content_copy</span> Copy';
            }, 1500);
          });
        });
      }
    } else {
      outputSection.classList.add("hidden");
    }
  }

  // Node output files
  const nodeFilesEl = el("nodeDetailFiles");
  if (nodeFilesEl) {
    const nFiles = node.output_files || [];
    if (nFiles.length > 0) {
      nodeFilesEl.classList.remove("hidden");
      nodeFilesEl.innerHTML =
        `<div class="w-full text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">Output Files</div>` +
        nFiles
          .map(
            (f) => `
            <a
              href="${f.url || "#"}"
              ${f.url ? `download="${escapeHtml(f.filename || f.name || "file")}"` : ""}
              class="inline-flex items-center gap-1.5 text-xs font-mono text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 rounded-lg px-2.5 py-1.5 hover:bg-indigo-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400"
              title="Download ${escapeHtml(f.filename || f.name || "file")}"
            >
              <span class="material-symbols-outlined text-xs" aria-hidden="true">download</span>
              ${escapeHtml(f.filename || f.name || "file")}
              ${f.size_bytes ? `<span class="text-slate-600">(${_formatSize(f.size_bytes)})</span>` : ""}
            </a>`,
          )
          .join("");
    } else {
      nodeFilesEl.classList.add("hidden");
      nodeFilesEl.innerHTML = "";
    }
  }

  // Focus the close button for keyboard accessibility
  el("nodeDetailClose")?.focus();
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
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

function _formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function _formatTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
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

/**
 * Get elapsed time string for a node, using started_at/completed_at when
 * available, falling back to created_at/updated_at.
 */
function _nodeElapsed(node) {
  const start = node.started_at || node.created_at;
  const end = node.completed_at || node.updated_at;
  if (!start || node.status === "pending") return "";
  // For running nodes, measure from start to now
  const startMs = new Date(start).getTime();
  const endMs = node.status === "running" ? Date.now() : (end ? new Date(end).getTime() : Date.now());
  const diffSec = Math.max(0, Math.round((endMs - startMs) / 1000));
  return _formatDuration(diffSec);
}

/**
 * Truncate a string to maxLen characters, adding ellipsis if needed.
 */
function _truncate(str, maxLen) {
  if (!str) return "";
  if (str.length <= maxLen) return str;
  return str.substring(0, maxLen) + "\u2026";
}

function _formatSize(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

// ---------------------------------------------------------------------------
// Cost / token formatting helpers
// ---------------------------------------------------------------------------

function _formatCents(cents) {
  if (cents == null || cents === 0) return "$0.00";
  if (cents < 1) return `${cents.toFixed(3)}\u00a2`;
  if (cents < 100) return `${cents.toFixed(2)}\u00a2`;
  return `$${(cents / 100).toFixed(2)}`;
}

function _formatTokenCount(n) {
  if (n == null) return "--";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function _formatMs(ms) {
  if (ms == null || ms === 0) return "--";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

/**
 * Render the job-level cost summary panel.
 * Called from _renderJobDetail after nodes are rendered.
 */
function _renderJobCostSummary(job) {
  const section = el("jobCostSummary");
  if (!section) return;

  const costCents = job.cost_cents;

  // If the job has cost data, show the summary
  if (costCents != null && costCents > 0) {
    section.classList.remove("hidden");

    const totalCostEl = el("jobTotalCost");
    if (totalCostEl) totalCostEl.textContent = _formatCents(costCents);

    // Extract token counts from audit trail detail strings
    let totalIn = 0;
    let totalOut = 0;
    const audit = job.audit || [];
    for (const a of audit) {
      if (a.action === "node_completed" && a.detail) {
        const inMatch = a.detail.match(/tokens_in=(\d+)/);
        const outMatch = a.detail.match(/tokens_out=(\d+)/);
        if (inMatch) totalIn += parseInt(inMatch[1], 10);
        if (outMatch) totalOut += parseInt(outMatch[1], 10);
      }
    }

    const tokInEl = el("jobTotalTokensIn");
    const tokOutEl = el("jobTotalTokensOut");
    const tokTotalEl = el("jobTotalTokens");
    if (tokInEl) tokInEl.textContent = totalIn > 0 ? _formatTokenCount(totalIn) : "--";
    if (tokOutEl) tokOutEl.textContent = totalOut > 0 ? _formatTokenCount(totalOut) : "--";
    if (tokTotalEl) tokTotalEl.textContent = (totalIn + totalOut) > 0 ? _formatTokenCount(totalIn + totalOut) : "--";
  } else {
    section.classList.add("hidden");
  }
}

/**
 * Render cost/token metrics in the expanded node detail panel.
 */
function _renderNodeCostMetrics(node) {
  const section = el("nodeDetailCostSection");
  if (!section) return;

  const hasData = (node.tokens_in != null && node.tokens_in > 0) ||
                  (node.tokens_out != null && node.tokens_out > 0) ||
                  (node.cost_cents != null && node.cost_cents > 0) ||
                  (node.duration_ms != null && node.duration_ms > 0);

  if (hasData) {
    section.classList.remove("hidden");
    const tokInEl = el("nodeDetailTokensIn");
    const tokOutEl = el("nodeDetailTokensOut");
    const costEl = el("nodeDetailCost");
    const durEl = el("nodeDetailDuration");
    if (tokInEl) tokInEl.textContent = _formatTokenCount(node.tokens_in);
    if (tokOutEl) tokOutEl.textContent = _formatTokenCount(node.tokens_out);
    if (costEl) costEl.textContent = _formatCents(node.cost_cents);
    if (durEl) durEl.textContent = _formatMs(node.duration_ms);
  } else {
    section.classList.add("hidden");
  }
}

// ---------------------------------------------------------------------------
// Output formatting helpers
// ---------------------------------------------------------------------------

/**
 * Syntax-highlight a JSON value as HTML with colored spans.
 */
function _highlightJson(obj, indent = 0) {
  const pad = "  ".repeat(indent);
  const pad1 = "  ".repeat(indent + 1);

  if (obj === null) return '<span class="json-null">null</span>';
  if (typeof obj === "boolean") return `<span class="json-boolean">${obj}</span>`;
  if (typeof obj === "number") return `<span class="json-number">${obj}</span>`;
  if (typeof obj === "string") {
    const escaped = escapeHtml(JSON.stringify(obj));
    return `<span class="json-string">${escaped}</span>`;
  }

  if (Array.isArray(obj)) {
    if (obj.length === 0) return "[]";
    const items = obj.map((v) => pad1 + _highlightJson(v, indent + 1));
    return `[\n${items.join(",\n")}\n${pad}]`;
  }

  const keys = Object.keys(obj);
  if (keys.length === 0) return "{}";
  const entries = keys.map(
    (k) =>
      `${pad1}<span class="json-key">${escapeHtml(JSON.stringify(k))}</span>: ${_highlightJson(obj[k], indent + 1)}`,
  );
  return `{\n${entries.join(",\n")}\n${pad}}`;
}

/**
 * Render parsed node output as rich HTML.
 *
 * Strategy:
 * 1. If the object is { result: <string> }, treat the string as markdown.
 * 2. If the object has a single string value under any key, render it as markdown.
 * 3. For objects with multiple keys, render each key as a labeled section:
 *    - string values → markdown
 *    - other values → syntax-highlighted JSON
 */
function _renderOutputHtml(parsed) {
  // Handle non-object (string, number, etc.)
  if (typeof parsed !== "object" || parsed === null) {
    const text = String(parsed);
    if (typeof marked !== "undefined") {
      return marked.parse(text, { breaks: true, gfm: true });
    }
    return `<div class="whitespace-pre-wrap">${escapeHtml(text)}</div>`;
  }

  const keys = Object.keys(parsed);

  // Single-key object with a string value → render as rich text
  if (keys.length === 1 && typeof parsed[keys[0]] === "string") {
    const text = parsed[keys[0]];
    if (typeof marked !== "undefined") {
      return marked.parse(text, { breaks: true, gfm: true });
    }
    return `<div class="whitespace-pre-wrap">${escapeHtml(text)}</div>`;
  }

  // Multi-key object → render each key as a section
  const sections = keys.map((key) => {
    const val = parsed[key];
    let body;
    if (typeof val === "string") {
      body =
        typeof marked !== "undefined"
          ? marked.parse(val, { breaks: true, gfm: true })
          : `<div class="whitespace-pre-wrap">${escapeHtml(val)}</div>`;
    } else {
      body = `<div class="node-output-json">${_highlightJson(val)}</div>`;
    }
    return `<div class="mb-3"><div class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">${escapeHtml(key)}</div>${body}</div>`;
  });

  return sections.join("");
}

/**
 * Get the raw text content of parsed output for clipboard copy.
 */
function _outputPlainText(parsed) {
  if (typeof parsed !== "object" || parsed === null) return String(parsed);
  const keys = Object.keys(parsed);
  if (keys.length === 1 && typeof parsed[keys[0]] === "string") return parsed[keys[0]];
  return JSON.stringify(parsed, null, 2);
}
