/**
 * jobs_ui.js — Jobs View
 * ======================
 * Full CRUD UI for the LocalMind Job Pipeline system.
 *
 * Exports:
 *   initJobsUI()    — bootstrap: inject HTML into #jobsView, bind events
 *   showJobsView()  — programmatically show the Jobs view + load data
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _jobs = [];
let _currentFilter = "all";
let _selectedJobId = null;
let _pollTimer = null;
let _droppedFiles = [];
let _sseSource = null;

// ---------------------------------------------------------------------------
// DOM helper
// ---------------------------------------------------------------------------

const el = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Status config — icon + color + text label (accessibility: never color-only)
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

// Tailwind dynamic class safelist — ensures JIT includes our status colours.
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
];
void _TW_SAFELIST; // suppress unused warning

// ---------------------------------------------------------------------------
// HTML Shell — injected into the pre-existing #jobsView container
// ---------------------------------------------------------------------------

function buildShellHTML() {
  return `
  <!-- ============================================================ -->
  <!-- LIST VIEW                                                     -->
  <!-- ============================================================ -->
  <div id="jobsListView" class="flex-1 flex flex-col p-8 overflow-y-auto custom-scrollbar gap-6">

    <!-- Header -->
    <div class="flex items-center justify-between flex-wrap gap-4">
      <div class="flex items-center gap-3">
        <span class="material-symbols-outlined text-indigo-400 text-2xl">work</span>
        <h2 class="text-xl font-headline font-bold tracking-tight text-slate-100">Jobs</h2>
      </div>
    </div>

    <!-- Creation Bar -->
    <div class="bg-slate-900/40 border border-slate-800/60 rounded-xl p-5 space-y-4">
      <label for="jobTitleInput" class="sr-only">Job description</label>
      <input
        id="jobTitleInput"
        type="text"
        placeholder="e.g. Update slides 3-7 with Q1 2026 revenue from the sales dashboard"
        aria-label="Job description"
        class="w-full bg-slate-800/60 border border-slate-700/50 rounded-lg px-4 py-3 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 font-body"
      />

      <!-- Drop Zone -->
      <div
        id="jobDropZone"
        tabindex="0"
        role="button"
        aria-label="Drag and drop files here or click to select"
        class="border-2 border-dashed border-slate-700/50 rounded-lg p-4 text-center cursor-pointer hover:border-indigo-500/40 hover:bg-indigo-500/5 transition-all focus:outline-none focus:ring-2 focus:ring-indigo-500/50"
      >
        <span class="material-symbols-outlined text-slate-500 text-2xl block mb-1">upload_file</span>
        <span class="text-xs text-slate-500">Drag files here or click to browse</span>
        <input id="jobFileInput" type="file" multiple class="hidden" aria-hidden="true" />
      </div>
      <div id="jobFilePreview" class="flex flex-wrap gap-2" aria-live="polite"></div>

      <!-- Action Buttons -->
      <div class="flex gap-3">
        <button
          id="jobRunBtn"
          aria-label="Run job in quick mode"
          class="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold uppercase tracking-wider rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <span class="material-symbols-outlined text-sm">bolt</span> Run
        </button>
        <button
          id="jobCustomizeBtn"
          aria-label="Customize plan before executing"
          class="flex items-center gap-2 px-5 py-2.5 bg-slate-800/60 hover:bg-slate-700/60 border border-slate-700/50 text-slate-300 text-xs font-bold uppercase tracking-wider rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <span class="material-symbols-outlined text-sm">tune</span> Customize Plan
        </button>
      </div>
    </div>

    <!-- Filters -->
    <div class="flex items-center gap-2 flex-wrap" role="tablist" aria-label="Filter jobs by status">
      <button data-filter="all"       role="tab" aria-selected="true"  class="jobs-filter-btn active">All</button>
      <button data-filter="running"    role="tab" aria-selected="false" class="jobs-filter-btn">Running</button>
      <button data-filter="completed"  role="tab" aria-selected="false" class="jobs-filter-btn">Completed</button>
      <button data-filter="failed"     role="tab" aria-selected="false" class="jobs-filter-btn">Failed</button>
    </div>

    <!-- Job Cards Grid -->
    <div id="jobsGrid" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4" role="list" aria-live="polite">
      <div class="text-sm text-slate-500 italic col-span-full py-8 text-center">Loading jobs...</div>
    </div>
  </div>

  <!-- ============================================================ -->
  <!-- DETAIL VIEW                                                   -->
  <!-- ============================================================ -->
  <div id="jobsDetailView" class="hidden flex-1 flex flex-col p-8 overflow-y-auto custom-scrollbar gap-6">

    <!-- Back button + Title -->
    <div class="flex items-center gap-4">
      <button
        id="jobBackBtn"
        aria-label="Back to job list"
        class="p-2 rounded-lg hover:bg-slate-800/60 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400"
      >
        <span class="material-symbols-outlined text-slate-400">arrow_back</span>
      </button>
      <div class="flex-1 min-w-0">
        <h2 id="jobDetailTitle" class="text-lg font-headline font-bold text-slate-100 truncate"></h2>
        <div class="flex items-center gap-3 mt-1 flex-wrap">
          <span id="jobDetailStatus" class="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2.5 py-1 rounded-full"></span>
          <span id="jobDetailCreated" class="text-[10px] font-mono text-slate-500"></span>
          <span id="jobDetailMode" class="text-[10px] font-mono text-slate-500 uppercase"></span>
        </div>
      </div>
      <div class="flex gap-2 shrink-0">
        <button
          id="jobCancelBtn"
          aria-label="Cancel this job"
          class="hidden items-center gap-1.5 px-4 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 text-[10px] font-bold uppercase tracking-wider rounded-lg border border-red-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-red-400"
        >
          <span class="material-symbols-outlined text-sm">cancel</span><span>Cancel</span>
        </button>
        <button
          id="jobSaveTemplateBtn"
          aria-label="Save job as pipeline template"
          class="hidden items-center gap-1.5 px-4 py-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-[10px] font-bold uppercase tracking-wider rounded-lg border border-emerald-500/20 transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-400"
        >
          <span class="material-symbols-outlined text-sm">bookmark_add</span><span>Save as Template</span>
        </button>
      </div>
    </div>

    <!-- Description -->
    <div id="jobDetailDesc" class="text-xs text-slate-400 leading-relaxed hidden"></div>

    <!-- Progress Bar -->
    <div class="bg-slate-900/40 border border-slate-800/60 rounded-xl p-4">
      <div class="flex items-center justify-between mb-2">
        <span class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Pipeline Progress</span>
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
          class="bg-indigo-500 h-full rounded-full transition-all duration-500"
          style="width: 0%"
        ></div>
      </div>
    </div>

    <!-- Node Pipeline -->
    <div>
      <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500 mb-3">Node Pipeline</div>
      <div id="jobNodesContainer" class="flex gap-3 overflow-x-auto pb-2 custom-scrollbar" role="list" aria-label="Pipeline nodes"></div>
    </div>

    <!-- Expanded Node Detail -->
    <div id="jobNodeDetail" class="hidden bg-slate-900/40 border border-slate-800/60 rounded-xl p-5 space-y-3">
      <div class="flex items-center justify-between">
        <h3 id="nodeDetailTitle" class="text-sm font-headline font-bold text-slate-200"></h3>
        <button id="nodeDetailClose" aria-label="Close node detail" class="p-1 rounded hover:bg-slate-800/60 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400">
          <span class="material-symbols-outlined text-slate-500 text-sm">close</span>
        </button>
      </div>
      <div id="nodeDetailStatus" class="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full"></div>
      <div id="nodeDetailInstructions" class="text-xs text-slate-400 leading-relaxed"></div>
      <div id="nodeDetailTools" class="flex flex-wrap gap-1.5"></div>
      <div id="nodeDetailOutput" class="hidden bg-slate-950/50 border border-slate-800/40 rounded-lg p-3 text-xs font-mono text-slate-400 max-h-40 overflow-y-auto custom-scrollbar whitespace-pre-wrap"></div>
      <div id="nodeDetailElapsed" class="text-[10px] font-mono text-slate-500"></div>
    </div>

    <!-- Output Files -->
    <div id="jobFilesSection" class="hidden">
      <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500 mb-3">Output Files</div>
      <div id="jobFilesList" class="space-y-2" role="list" aria-label="Job output files"></div>
    </div>

    <!-- Audit Trail -->
    <details class="group">
      <summary class="text-[10px] font-bold uppercase tracking-widest text-slate-500 cursor-pointer hover:text-slate-400 transition-colors flex items-center gap-1 select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 rounded">
        <span class="material-symbols-outlined text-xs transition-transform group-open:rotate-90">chevron_right</span>
        Audit Trail
      </summary>
      <div id="jobAuditList" class="mt-3 space-y-1 max-h-64 overflow-y-auto custom-scrollbar" role="log" aria-label="Job audit trail"></div>
    </details>

  </div>`;
}

// ---------------------------------------------------------------------------
// Injected CSS (small set of non-Tailwind helpers)
// ---------------------------------------------------------------------------

function _injectStyles() {
  if (document.getElementById("jobs-ui-styles")) return;
  const style = document.createElement("style");
  style.id = "jobs-ui-styles";
  style.textContent = `
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
    .job-card-link:focus-visible,
    .node-card:focus-visible { outline: 2px solid rgba(99,102,241,0.5); outline-offset: 2px; }
    #jobDropZone.drag-over { border-color: rgba(99,102,241,0.6); background: rgba(99,102,241,0.08); }
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
  el("jobRunBtn")?.addEventListener("click", () => _createJob("quick"));
  el("jobCustomizeBtn")?.addEventListener("click", () => _createJob("pipeline"));

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

  // Detail view: back
  el("jobBackBtn")?.addEventListener("click", _showListView);

  // Detail view: close expanded node
  el("nodeDetailClose")?.addEventListener("click", () => {
    el("jobNodeDetail")?.classList.add("hidden");
  });

  // Detail view: cancel job
  el("jobCancelBtn")?.addEventListener("click", _cancelCurrentJob);

  // Detail view: save as template
  el("jobSaveTemplateBtn")?.addEventListener("click", _saveAsTemplate);

  // Enter key in title input triggers Run
  el("jobTitleInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      _createJob("quick");
    }
  });

  // SSE for real-time updates
  _connectSSE();
}

// ---------------------------------------------------------------------------
// Show / Hide
// ---------------------------------------------------------------------------

/**
 * Programmatically show the Jobs view, load data, start polling.
 * Useful when navigating from code rather than the sidebar button.
 * (The sidebar click handler in events.js already reveals #jobsView;
 *  calling this additionally triggers data loading.)
 */
export function showJobsView() {
  const view = el("jobsView");
  if (view) view.classList.remove("hidden");
  _showListView();
  _loadJobs();
  _startPolling();
  _updateRunningBadge();
}

/**
 * Hide the Jobs view and stop polling.
 */
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
  el("jobsListView")?.classList.add("hidden");
  el("jobsDetailView")?.classList.remove("hidden");
  el("jobNodeDetail")?.classList.add("hidden");
  _selectedJobId = jobId;
  _loadJobDetail(jobId);
}

// ---------------------------------------------------------------------------
// Polling (fallback when SSE is unavailable)
// ---------------------------------------------------------------------------

function _startPolling() {
  _stopPolling();
  _pollTimer = setInterval(() => {
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
// SSE — real-time push from /api/jobs/activity
// ---------------------------------------------------------------------------

function _connectSSE() {
  if (_sseSource) return;
  try {
    _sseSource = new EventSource(`${API}/api/jobs/activity`);

    const refreshList = () => _loadJobs();
    const refreshDetail = (e) => {
      refreshList();
      try {
        const data = JSON.parse(e.data);
        if (_selectedJobId && data.job_id === _selectedJobId) {
          _loadJobDetail(_selectedJobId);
        }
      } catch { /* ignore parse errors */ }
    };

    _sseSource.addEventListener("job_created", refreshList);
    _sseSource.addEventListener("job_completed", refreshList);
    _sseSource.addEventListener("job_failed", refreshList);
    _sseSource.addEventListener("job_status_changed", refreshDetail);
    _sseSource.addEventListener("node_progress", refreshDetail);

    _sseSource.onerror = () => {
      _sseSource?.close();
      _sseSource = null;
      setTimeout(_connectSSE, 10_000);
    };
  } catch {
    // EventSource not supported or URL unreachable — polling covers us.
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
    _jobs = (data.jobs || []).sort(
      (a, b) => (b.created_at || "").localeCompare(a.created_at || ""),
    );
    _renderJobCards();
    _updateRunningBadge();
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

async function _createJob(mode) {
  const titleInput = el("jobTitleInput");
  const title = titleInput?.value?.trim();
  if (!title) {
    showToast("Please enter a job description", "error");
    titleInput?.focus();
    return;
  }

  const runBtn = el("jobRunBtn");
  const customBtn = el("jobCustomizeBtn");
  if (runBtn) runBtn.disabled = true;
  if (customBtn) customBtn.disabled = true;

  try {
    let res;

    if (_droppedFiles.length > 0) {
      const formData = new FormData();
      formData.append(
        "metadata",
        JSON.stringify({ title, description: title, mode }),
      );
      _droppedFiles.forEach((f) => formData.append("files", f));
      res = await fetch(`${API}/api/jobs`, { method: "POST", body: formData });
    } else {
      res = await fetch(`${API}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, description: title, mode }),
      });
    }

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      showToast(errData.detail || `Failed to create job (${res.status})`, "error");
      return;
    }

    const job = await res.json();
    showToast(`Job created: ${escapeHtml(job.title || title)}`, "info");

    // Clear inputs
    if (titleInput) titleInput.value = "";
    _droppedFiles = [];
    const preview = el("jobFilePreview");
    if (preview) preview.innerHTML = "";

    // Refresh & navigate
    await _loadJobs();
    if (job.id) _showDetailView(job.id);
  } catch (err) {
    console.error("[jobs_ui] Create job error:", err);
    showToast("Failed to create job", "error");
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
      _loadJobDetail(_selectedJobId);
      _loadJobs();
    } else {
      const errData = await res.json().catch(() => ({}));
      showToast(errData.detail || "Cancel failed", "error");
    }
  } catch {
    showToast("Cancel request failed", "error");
  } finally {
    if (btn) btn.disabled = false;
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
    showToast("Failed to save template", "error");
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
        <span class="material-symbols-outlined text-sm text-slate-500">description</span>
        <span class="truncate max-w-[150px]">${escapeHtml(f.name)}</span>
        <span class="text-[9px] text-slate-500 font-mono">${_formatSize(f.size)}</span>
        <button
          data-file-idx="${i}"
          aria-label="Remove file ${escapeHtml(f.name)}"
          class="text-slate-500 hover:text-red-400 transition-colors ml-1 focus:outline-none focus:ring-1 focus:ring-red-400 rounded"
        >
          <span class="material-symbols-outlined text-sm">close</span>
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
    grid.innerHTML =
      '<div class="text-sm text-slate-500 italic col-span-full py-8 text-center">No jobs found</div>';
    return;
  }

  grid.innerHTML = filtered
    .map((job) => {
      const cfg = STATUS_CFG[job.status] || STATUS_CFG.pending;
      const created = _timeAgo(job.created_at);
      const fileCount = job.file_count ?? 0;

      return `
      <div
        class="job-card-link bg-slate-900/40 border border-slate-800/60 rounded-xl p-4 space-y-3 cursor-pointer hover:bg-slate-800/40 hover:border-slate-700/50 transition-all group"
        role="listitem"
        tabindex="0"
        data-job-id="${escapeHtml(job.id)}"
        aria-label="Job: ${escapeHtml(job.title)}, status ${cfg.label}"
      >
        <div class="flex items-start justify-between gap-2">
          <h3 class="text-sm font-bold text-slate-200 group-hover:text-white transition-colors truncate flex-1">${escapeHtml(job.title)}</h3>
          <span class="inline-flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full shrink-0 bg-${cfg.color}-500/15 text-${cfg.color}-400 border border-${cfg.color}-500/20">
            <span class="material-symbols-outlined text-[11px]">${cfg.icon}</span>
            ${cfg.label}
          </span>
        </div>
        ${
          job.description && job.description !== job.title
            ? `<p class="text-[11px] text-slate-500 leading-relaxed line-clamp-2">${escapeHtml(job.description.substring(0, 120))}</p>`
            : ""
        }
        <div class="flex items-center gap-3 text-[9px] font-mono text-slate-500">
          <span class="flex items-center gap-1">
            <span class="material-symbols-outlined text-[11px]">schedule</span>
            ${escapeHtml(created)}
          </span>
          ${job.mode ? `<span class="uppercase">${escapeHtml(job.mode)}</span>` : ""}
          ${
            fileCount > 0
              ? `<span class="flex items-center gap-0.5"><span class="material-symbols-outlined text-[11px]">attach_file</span>${fileCount}</span>`
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
    statusEl.className = `inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2.5 py-1 rounded-full bg-${cfg.color}-500/15 text-${cfg.color}-400 border border-${cfg.color}-500/20`;
    statusEl.innerHTML = `<span class="material-symbols-outlined text-xs">${cfg.icon}</span> ${cfg.label}`;
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

  // Cancel button — only visible for non-terminal jobs
  const cancelBtn = el("jobCancelBtn");
  const terminalStates = ["done", "failed", "cancelled"];
  if (cancelBtn) {
    if (terminalStates.includes(job.status)) {
      cancelBtn.classList.add("hidden");
      cancelBtn.classList.remove("flex");
    } else {
      cancelBtn.classList.remove("hidden");
      cancelBtn.classList.add("flex");
    }
  }

  // Save-as-template button — only for done jobs
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
  _renderNodes(nodes);

  // Progress bar
  const completedCount = nodes.filter((n) => n.status === "completed").length;
  const totalCount = nodes.length;
  const pct = totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0;

  const bar = el("jobProgressBar");
  if (bar) {
    bar.style.width = `${pct}%`;
    bar.setAttribute("aria-valuenow", String(pct));
    bar.setAttribute("aria-valuemax", "100");
  }
  const progressLabel = el("jobProgressLabel");
  if (progressLabel) progressLabel.textContent = `${completedCount}/${totalCount} nodes (${pct}%)`;

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
            <span class="material-symbols-outlined text-indigo-400 group-hover:text-indigo-300">download</span>
            <div class="flex-1 min-w-0">
              <div class="text-xs font-medium text-slate-200 truncate">${escapeHtml(f.filename)}</div>
              <div class="text-[9px] font-mono text-slate-500">${f.mime_type || "unknown"}${f.size_bytes ? " - " + _formatSize(f.size_bytes) : ""}</div>
            </div>
          </a>`,
        )
        .join("");
    } else {
      filesSection.classList.add("hidden");
    }
  }

  // Audit trail
  const auditList = el("jobAuditList");
  const audit = job.audit || [];
  if (auditList) {
    if (audit.length === 0) {
      auditList.innerHTML =
        '<div class="text-[10px] text-slate-600 italic">No audit entries</div>';
    } else {
      auditList.innerHTML = audit
        .map(
          (a) => `
          <div class="flex items-start gap-2 text-[10px] font-mono text-slate-500 py-1 border-b border-slate-800/30 last:border-0">
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
// Render: Nodes Pipeline (horizontal card row)
// ---------------------------------------------------------------------------

function _renderNodes(nodes) {
  const container = el("jobNodesContainer");
  if (!container) return;

  if (nodes.length === 0) {
    container.innerHTML =
      '<div class="text-xs text-slate-500 italic py-4">No pipeline nodes yet</div>';
    return;
  }

  container.innerHTML = nodes
    .map((node, i) => {
      const cfg = NODE_STATUS_CFG[node.status] || NODE_STATUS_CFG.pending;
      const isLast = i === nodes.length - 1;

      return `
      <div class="flex items-center shrink-0">
        <div
          class="node-card bg-slate-900/60 border border-slate-800/50 rounded-xl p-4 min-w-[160px] max-w-[200px] cursor-pointer hover:bg-slate-800/50 hover:border-${cfg.color}-500/30 transition-all"
          tabindex="0"
          role="listitem"
          data-node-idx="${i}"
          aria-label="Node ${i + 1}: ${escapeHtml(node.title)}, status ${cfg.label}"
        >
          <div class="flex items-center gap-2 mb-2">
            <span class="material-symbols-outlined text-${cfg.color}-400 text-sm">${cfg.icon}</span>
            <span class="text-[9px] font-bold uppercase tracking-widest text-${cfg.color}-400">${cfg.label}</span>
          </div>
          <div class="text-xs font-medium text-slate-200 truncate">${escapeHtml(node.title)}</div>
          <div class="text-[9px] font-mono text-slate-600 mt-1">Step ${node.sequence ?? i + 1}</div>
        </div>
        ${!isLast ? '<span class="material-symbols-outlined text-slate-700 text-sm mx-1 shrink-0">chevron_right</span>' : ""}
      </div>`;
    })
    .join("");

  // Wire node click → expand detail
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

  // Title
  const titleEl = el("nodeDetailTitle");
  if (titleEl) titleEl.textContent = node.title || "Untitled Node";

  // Status
  const statusEl = el("nodeDetailStatus");
  if (statusEl) {
    statusEl.className = `inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full bg-${cfg.color}-500/15 text-${cfg.color}-400 border border-${cfg.color}-500/20`;
    statusEl.innerHTML = `<span class="material-symbols-outlined text-xs">${cfg.icon}</span> ${cfg.label}`;
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
                `<span class="text-[9px] font-mono bg-slate-800/60 border border-slate-700/40 text-slate-400 px-2 py-0.5 rounded">${escapeHtml(t)}</span>`,
            )
            .join("")
        : '<span class="text-[9px] text-slate-600 italic">No tool restrictions</span>';
  }

  // Output preview
  const outputEl = el("nodeDetailOutput");
  if (outputEl) {
    if (node.output_json) {
      try {
        const parsed =
          typeof node.output_json === "string"
            ? JSON.parse(node.output_json)
            : node.output_json;
        outputEl.textContent = JSON.stringify(parsed, null, 2);
      } catch {
        outputEl.textContent = String(node.output_json);
      }
      outputEl.classList.remove("hidden");
    } else {
      outputEl.classList.add("hidden");
    }
  }

  // Elapsed time
  const elapsedEl = el("nodeDetailElapsed");
  if (elapsedEl) {
    if (node.created_at && node.updated_at && node.status !== "pending") {
      const start = new Date(node.created_at).getTime();
      const end = new Date(node.updated_at).getTime();
      const diffSec = Math.max(0, Math.round((end - start) / 1000));
      elapsedEl.textContent = `Elapsed: ${_formatDuration(diffSec)}`;
    } else {
      elapsedEl.textContent = "";
    }
  }

  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------------------------------------------------------------------------
// Running-badge update (sidebar #jobsRunningBadge)
// ---------------------------------------------------------------------------

function _updateRunningBadge() {
  const badge = el("jobsRunningBadge");
  if (!badge) return;
  const running = _jobs.filter((j) =>
    ["pending", "planning", "executing", "reviewing", "cancelling"].includes(j.status),
  ).length;
  badge.textContent = String(running);
  if (running > 0) {
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
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

function _formatSize(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}
