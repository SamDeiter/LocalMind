/**
 * eval_ui.js -- Evaluation Dashboard
 * ====================================
 * Eval case management, run execution, and pass-rate trend tracking.
 *
 * Features:
 *   - Cases table: browse all eval cases with name, tags, expected output
 *   - Run button: trigger a full eval run, show live progress + results
 *   - Recent runs list: browse past runs with pass/fail counts
 *   - Trend display: pass-rate over time sparkline area
 *
 * Exports:
 *   initEvalUI()  -- bootstrap: render layout, load initial data
 */

import { API } from "./state.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _running = false;

// ---------------------------------------------------------------------------
// DOM helper
// ---------------------------------------------------------------------------

const el = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// HTML helpers
// ---------------------------------------------------------------------------

function escapeHTML(str) {
  return (str || "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

function timeAgo(ts) {
  if (!ts) return "";
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ---------------------------------------------------------------------------
// Shell HTML -- injected into #evalView container
// ---------------------------------------------------------------------------

function buildShellHTML() {
  return `
  <!-- Header -->
  <div class="flex items-center justify-between flex-wrap gap-4 mb-6">
    <div class="flex items-center gap-3">
      <span class="material-symbols-outlined text-indigo-400 text-2xl" aria-hidden="true">labs</span>
      <h2 class="text-xl font-headline font-bold tracking-tight text-slate-100">Evaluations</h2>
      <span id="evalStatusBadge"
            class="text-[11px] font-bold uppercase tracking-widest bg-slate-500/20 text-slate-400 px-2.5 py-1 rounded-full">
        Ready
      </span>
    </div>
    <div class="flex items-center gap-3">
      <button id="evalRunBtn"
              class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-xs font-bold px-4 py-2 rounded-lg uppercase tracking-wider transition-colors border border-emerald-500/20 focus:outline-none focus:ring-2 focus:ring-emerald-400"
              aria-label="Run all evaluations">
        <span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">play_arrow</span> Run Evals
      </button>
      <button id="evalRefreshBtn"
              class="bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 text-xs font-bold px-4 py-2 rounded-lg uppercase tracking-wider transition-colors border border-indigo-500/20 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              aria-label="Refresh eval data">
        <span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">refresh</span> Refresh
      </button>
    </div>
  </div>

  <!-- Tab bar -->
  <div id="evalTabBar" class="flex items-center gap-2 mb-6" role="tablist" aria-label="Evaluation tabs"></div>

  <!-- Trend Sparkline Area -->
  <div id="evalTrendPanel" class="bg-slate-800 border border-slate-700 rounded-lg p-5 mb-6">
    <div class="flex items-center gap-2 mb-4">
      <span class="material-symbols-outlined text-cyan-400 text-lg" aria-hidden="true">trending_up</span>
      <h3 class="text-sm font-bold text-slate-200 uppercase tracking-wider">Pass Rate Trend</h3>
    </div>
    <div id="evalTrendContent" class="flex items-end gap-1 h-24" role="img" aria-label="Pass rate trend chart">
      <div class="text-xs text-slate-500 italic p-4 w-full text-center">Loading trend data...</div>
    </div>
  </div>

  <!-- Cases Table -->
  <div id="evalCasesPanel" class="bg-slate-800 border border-slate-700 rounded-lg mb-6">
    <div class="flex items-center gap-2 p-4 pb-2 border-b border-slate-700/50">
      <span class="material-symbols-outlined text-amber-400 text-lg" aria-hidden="true">checklist</span>
      <h3 class="text-sm font-bold text-slate-200 uppercase tracking-wider">Eval Cases</h3>
      <span id="evalCaseCount" class="ml-auto text-[11px] font-bold uppercase tracking-widest bg-slate-700/50 text-slate-400 px-2 py-0.5 rounded-full">0</span>
    </div>
    <div id="evalCasesContent" class="overflow-x-auto custom-scrollbar">
      <div class="text-xs text-slate-500 italic p-6 text-center">Loading cases...</div>
    </div>
  </div>

  <!-- Results + Runs row -->
  <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">

    <!-- Results Panel (2 cols) -->
    <div id="evalResultsPanel" class="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-lg">
      <div class="flex items-center gap-2 p-4 pb-2 border-b border-slate-700/50">
        <span class="material-symbols-outlined text-indigo-400 text-lg" aria-hidden="true">assignment_turned_in</span>
        <h3 class="text-sm font-bold text-slate-200 uppercase tracking-wider">Latest Results</h3>
        <span id="evalResultSummary" class="ml-auto text-[11px] font-bold uppercase tracking-widest bg-slate-700/50 text-slate-400 px-2 py-0.5 rounded-full">--</span>
      </div>
      <div id="evalResultsContent" class="overflow-y-auto custom-scrollbar max-h-[400px] p-2">
        <div class="flex flex-col items-center justify-center py-8 gap-2">
          <span class="material-symbols-outlined text-2xl text-slate-600" aria-hidden="true">science</span>
          <div class="text-xs text-slate-500">Run evals to see results</div>
        </div>
      </div>
    </div>

    <!-- Recent Runs (1 col) -->
    <div class="bg-slate-800 border border-slate-700 rounded-lg flex flex-col max-h-[400px]">
      <div class="flex items-center gap-2 p-4 pb-2 border-b border-slate-700/50">
        <span class="material-symbols-outlined text-purple-400 text-lg" aria-hidden="true">history</span>
        <h3 class="text-sm font-bold text-slate-200 uppercase tracking-wider">Recent Runs</h3>
      </div>
      <div id="evalRunsContent" class="flex-1 overflow-y-auto custom-scrollbar p-2"
           role="log" aria-label="Recent eval runs">
        <div class="text-xs text-slate-500 italic p-4 text-center">Loading runs...</div>
      </div>
    </div>

  </div>
  `;
}

// ---------------------------------------------------------------------------
// Tab system
// ---------------------------------------------------------------------------

const EVAL_TABS = [
  { id: "overview",  label: "Overview" },
  { id: "cases",     label: "Cases" },
  { id: "results",   label: "Results" },
];

let activeEvalTab = "overview";

function initEvalTabs() {
  const bar = el("evalTabBar");
  if (!bar || bar.children.length > 0) return;

  EVAL_TABS.forEach(tab => {
    const btn = document.createElement("button");
    btn.dataset.evalTab = tab.id;
    btn.textContent = tab.label;
    btn.className = tabClass(tab.id === activeEvalTab);
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", tab.id === activeEvalTab ? "true" : "false");
    btn.addEventListener("click", () => switchEvalTab(tab.id));
    bar.appendChild(btn);
  });
}

function tabClass(active) {
  const base = "px-4 py-1.5 rounded-lg text-xs font-bold uppercase tracking-widest transition-colors";
  return active
    ? `${base} bg-indigo-500/20 text-indigo-400 border border-indigo-500/30`
    : `${base} text-slate-500 hover:text-slate-300 hover:bg-slate-800/40 border border-transparent`;
}

function switchEvalTab(tabId) {
  activeEvalTab = tabId;

  // Update button styles
  const bar = el("evalTabBar");
  if (bar) {
    [...bar.children].forEach(btn => {
      const isActive = btn.dataset.evalTab === tabId;
      btn.className = tabClass(isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });
  }

  // Panel visibility
  const trendPanel = el("evalTrendPanel");
  const casesPanel = el("evalCasesPanel");
  const resultsPanel = el("evalResultsPanel");

  if (trendPanel) trendPanel.classList.toggle("hidden", tabId === "cases");
  if (casesPanel) casesPanel.classList.toggle("hidden", tabId === "results");
  if (resultsPanel) {
    const parent = resultsPanel.closest(".grid");
    if (parent) parent.classList.toggle("hidden", tabId === "cases");
  }
}

// ---------------------------------------------------------------------------
// Data fetchers
// ---------------------------------------------------------------------------

async function loadCases() {
  try {
    const res = await fetch(`${API}/api/evals/cases`);
    if (!res.ok) throw new Error(`Cases API ${res.status}`);
    const data = await res.json();
    const cases = Array.isArray(data) ? data : (data.cases || []);
    renderCasesTable(cases);
    return cases;
  } catch (err) {
    console.warn("eval_ui: cases fetch failed", err);
    renderCasesTable([]);
    return [];
  }
}

async function runEvals() {
  if (_running) return;
  _running = true;

  const runBtn = el("evalRunBtn");
  const badge = el("evalStatusBadge");

  // Update UI to running state
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin">refresh</span> Running...';
  }
  if (badge) {
    badge.textContent = "Running";
    badge.className = "text-[11px] font-bold uppercase tracking-widest bg-amber-500/20 text-amber-400 px-2.5 py-1 rounded-full animate-pulse";
  }

  // Show progress placeholder
  const resultsContent = el("evalResultsContent");
  if (resultsContent) {
    resultsContent.innerHTML = `
      <div class="flex flex-col items-center justify-center py-12 gap-3">
        <span class="material-symbols-outlined text-3xl text-indigo-400 animate-spin" aria-hidden="true">progress_activity</span>
        <div class="text-xs text-slate-400">Running evaluations...</div>
      </div>
    `;
  }

  try {
    const res = await fetch(`${API}/api/evals/run`, { method: "POST" });
    if (!res.ok) throw new Error(`Run API ${res.status}`);
    const data = await res.json();
    renderResults(data);

    // Update badge
    if (badge) {
      const allPassed = data.fail_count === 0;
      badge.textContent = allPassed ? "All Passed" : `${data.fail_count} Failed`;
      badge.className = `text-[11px] font-bold uppercase tracking-widest px-2.5 py-1 rounded-full ${
        allPassed
          ? "bg-emerald-500/20 text-emerald-400"
          : "bg-red-500/20 text-red-400"
      }`;
    }

    // Refresh runs + trend after a run completes
    await Promise.allSettled([loadRuns(), loadTrend()]);
  } catch (err) {
    console.warn("eval_ui: run failed", err);
    if (resultsContent) {
      resultsContent.innerHTML = `
        <div class="flex flex-col items-center justify-center py-8 gap-2">
          <span class="material-symbols-outlined text-2xl text-red-400" aria-hidden="true">error</span>
          <div class="text-xs text-red-400">Eval run failed: ${escapeHTML(err.message)}</div>
        </div>
      `;
    }
    if (badge) {
      badge.textContent = "Error";
      badge.className = "text-[11px] font-bold uppercase tracking-widest bg-red-500/20 text-red-400 px-2.5 py-1 rounded-full";
    }
  } finally {
    _running = false;
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">play_arrow</span> Run Evals';
    }
  }
}

async function loadRuns() {
  try {
    const res = await fetch(`${API}/api/evals/runs?limit=20&offset=0`);
    if (!res.ok) throw new Error(`Runs API ${res.status}`);
    const data = await res.json();
    const runs = Array.isArray(data) ? data : (data.runs || []);
    renderRunsList(runs);
    return runs;
  } catch (err) {
    console.warn("eval_ui: runs fetch failed", err);
    renderRunsList([]);
    return [];
  }
}

async function loadTrend() {
  try {
    const res = await fetch(`${API}/api/evals/trend`);
    if (!res.ok) throw new Error(`Trend API ${res.status}`);
    const data = await res.json();
    const points = Array.isArray(data) ? data : (data.points || data.trend || []);
    renderTrend(points);
    return points;
  } catch (err) {
    console.warn("eval_ui: trend fetch failed", err);
    renderTrend([]);
    return [];
  }
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function renderCasesTable(cases) {
  const container = el("evalCasesContent");
  const countBadge = el("evalCaseCount");

  if (countBadge) countBadge.textContent = cases.length;

  if (!container) return;

  if (!cases.length) {
    container.innerHTML = `
      <div class="flex flex-col items-center justify-center py-8 gap-2">
        <span class="material-symbols-outlined text-2xl text-slate-600" aria-hidden="true">inbox</span>
        <div class="text-xs text-slate-500">No eval cases found</div>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <table class="w-full text-left">
      <thead>
        <tr class="border-b border-slate-700/50">
          <th class="text-xs font-bold uppercase tracking-widest text-slate-500 px-4 py-3">Name</th>
          <th class="text-xs font-bold uppercase tracking-widest text-slate-500 px-4 py-3">Tags</th>
          <th class="text-xs font-bold uppercase tracking-widest text-slate-500 px-4 py-3">Input</th>
          <th class="text-xs font-bold uppercase tracking-widest text-slate-500 px-4 py-3">Expected</th>
        </tr>
      </thead>
      <tbody>
        ${cases.map(c => `
          <tr class="border-b border-slate-700/30 hover:bg-slate-700/20 transition-colors">
            <td class="px-4 py-3">
              <div class="text-xs font-bold text-slate-200">${escapeHTML(c.name || c.id || "--")}</div>
            </td>
            <td class="px-4 py-3">
              <div class="flex flex-wrap gap-1">
                ${(c.tags || []).map(t => `
                  <span class="text-[11px] font-bold uppercase tracking-widest bg-indigo-500/15 text-indigo-400 px-2 py-0.5 rounded-full">${escapeHTML(t)}</span>
                `).join("")}
              </div>
            </td>
            <td class="px-4 py-3">
              <div class="text-[11px] text-slate-400 font-mono truncate max-w-[200px]" title="${escapeHTML(c.input || "")}">${escapeHTML((c.input || "").slice(0, 80))}${(c.input || "").length > 80 ? "..." : ""}</div>
            </td>
            <td class="px-4 py-3">
              <div class="text-[11px] text-slate-400 font-mono truncate max-w-[200px]" title="${escapeHTML(c.expected || "")}">${escapeHTML((c.expected || "").slice(0, 80))}${(c.expected || "").length > 80 ? "..." : ""}</div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderResults(data) {
  const container = el("evalResultsContent");
  const summary = el("evalResultSummary");

  if (summary) {
    summary.textContent = `${data.pass_count || 0}/${data.total_cases || 0} passed`;
    const allPassed = data.fail_count === 0 && data.total_cases > 0;
    summary.className = `ml-auto text-[11px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full ${
      allPassed
        ? "bg-emerald-500/20 text-emerald-400"
        : "bg-red-500/20 text-red-400"
    }`;
  }

  if (!container) return;

  const results = data.results || [];
  if (!results.length) {
    container.innerHTML = `
      <div class="flex flex-col items-center justify-center py-8 gap-2">
        <span class="material-symbols-outlined text-2xl text-slate-600" aria-hidden="true">science</span>
        <div class="text-xs text-slate-500">No results returned</div>
      </div>
    `;
    return;
  }

  // Summary cards
  const passRate = data.total_cases > 0
    ? ((data.pass_count / data.total_cases) * 100).toFixed(1)
    : "0.0";

  container.innerHTML = `
    <!-- Summary row -->
    <div class="grid grid-cols-3 gap-3 p-3 mb-2">
      <div class="bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-center">
        <div class="text-xl font-bold text-emerald-400 tabular-nums">${data.pass_count || 0}</div>
        <div class="text-xs font-bold uppercase tracking-widest text-emerald-500/70">Passed</div>
      </div>
      <div class="bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-center">
        <div class="text-xl font-bold text-red-400 tabular-nums">${data.fail_count || 0}</div>
        <div class="text-xs font-bold uppercase tracking-widest text-red-500/70">Failed</div>
      </div>
      <div class="bg-indigo-500/10 border border-indigo-500/20 rounded-lg p-3 text-center">
        <div class="text-xl font-bold text-indigo-400 tabular-nums">${passRate}%</div>
        <div class="text-xs font-bold uppercase tracking-widest text-indigo-500/70">Pass Rate</div>
      </div>
    </div>

    <!-- Individual results -->
    ${results.map(r => {
      const passed = r.passed || r.pass || r.status === "pass";
      const color = passed ? "emerald" : "red";
      const icon = passed ? "check_circle" : "cancel";
      const score = r.score != null ? r.score : null;

      return `
        <div class="flex items-start gap-3 p-3 rounded-lg hover:bg-slate-700/30 transition-colors border-l-2 border-l-${color}-500 mb-1">
          <span class="material-symbols-outlined text-sm text-${color}-400 mt-0.5 flex-shrink-0" aria-hidden="true">${icon}</span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <div class="text-[11px] font-bold text-slate-200">${escapeHTML(r.case_name || r.name || r.case_id || "--")}</div>
              ${score != null ? `<span class="text-[11px] font-mono text-cyan-400 bg-cyan-500/10 px-1.5 py-0.5 rounded">${typeof score === "number" ? score.toFixed(2) : score}</span>` : ""}
            </div>
            ${r.reason || r.message || r.output ? `
              <div class="text-xs text-slate-400 mt-1 leading-relaxed font-mono truncate">${escapeHTML((r.reason || r.message || r.output || "").slice(0, 120))}</div>
            ` : ""}
          </div>
          <span class="text-[11px] font-bold uppercase tracking-widest text-${color}-400 flex-shrink-0">${passed ? "PASS" : "FAIL"}</span>
        </div>
      `;
    }).join("")}
  `;
}

function renderRunsList(runs) {
  const container = el("evalRunsContent");
  if (!container) return;

  if (!runs.length) {
    container.innerHTML = `
      <div class="flex flex-col items-center justify-center py-8 gap-2">
        <span class="material-symbols-outlined text-2xl text-slate-600" aria-hidden="true">history</span>
        <div class="text-xs text-slate-500">No runs yet</div>
      </div>
    `;
    return;
  }

  container.innerHTML = runs.map(run => {
    const passCount = run.pass_count ?? 0;
    const failCount = run.fail_count ?? 0;
    const total = run.total_cases ?? (passCount + failCount);
    const passRate = total > 0 ? ((passCount / total) * 100).toFixed(0) : "0";
    const allPassed = failCount === 0 && total > 0;
    const color = allPassed ? "emerald" : "red";
    const ts = run.timestamp || run.created_at || run.date || "";

    return `
      <div class="flex items-center gap-3 p-3 rounded-lg hover:bg-slate-700/30 transition-colors border-l-2 border-l-${color}-500 mb-1">
        <div class="flex-shrink-0">
          <div class="w-8 h-8 rounded-full bg-${color}-500/15 border border-${color}-500/20 flex items-center justify-center">
            <span class="text-xs font-bold text-${color}-400 tabular-nums">${passRate}%</span>
          </div>
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="text-[11px] font-bold text-slate-200">${passCount}/${total} passed</span>
            ${failCount > 0 ? `<span class="text-[11px] font-bold text-red-400">${failCount} failed</span>` : ""}
          </div>
          <div class="text-[11px] font-mono text-slate-500 mt-0.5">${ts ? timeAgo(ts) : "--"}</div>
        </div>
      </div>
    `;
  }).join("");
}

function renderTrend(points) {
  const container = el("evalTrendContent");
  if (!container) return;

  if (!points.length) {
    container.innerHTML = `
      <div class="text-xs text-slate-500 italic p-4 w-full text-center">No trend data available. Run evals to populate.</div>
    `;
    return;
  }

  // Find min/max for scaling
  const rates = points.map(p => p.pass_rate ?? p.rate ?? p.value ?? 0);
  const maxRate = Math.max(...rates, 1);
  const barCount = points.length;

  // Render as vertical bar sparkline
  container.innerHTML = `
    <div class="flex items-end gap-1 h-24 w-full">
      ${points.map((p, i) => {
        const rate = p.pass_rate ?? p.rate ?? p.value ?? 0;
        const heightPct = Math.max((rate / maxRate) * 100, 4);
        const color = rate >= 90 ? "emerald" : rate >= 70 ? "amber" : "red";
        const label = p.date || p.label || p.timestamp || `Run ${i + 1}`;

        return `
          <div class="flex-1 flex flex-col items-center gap-1 group relative" style="min-width: 0">
            <div class="text-xs font-mono text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity absolute -top-4">
              ${rate.toFixed(0)}%
            </div>
            <div class="w-full bg-${color}-400/80 rounded-t transition-all duration-500 hover:bg-${color}-400"
                 style="height: ${heightPct}%"
                 title="${escapeHTML(label)}: ${rate.toFixed(1)}% pass rate"
                 role="presentation"></div>
          </div>
        `;
      }).join("")}
    </div>
    <div class="flex items-center justify-between mt-2">
      <span class="text-[11px] font-mono text-slate-500">${escapeHTML(points[0]?.date || points[0]?.label || "oldest")}</span>
      <span class="text-[11px] font-mono text-slate-500">${escapeHTML(points[points.length - 1]?.date || points[points.length - 1]?.label || "latest")}</span>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function initEvalUI() {
  const container = el("evalView");
  if (!container) {
    console.warn("eval_ui: #evalView not found in DOM");
    return;
  }

  // Inject shell HTML
  container.innerHTML = `
    <div class="flex-1 flex flex-col p-8 overflow-y-auto custom-scrollbar">
      ${buildShellHTML()}
    </div>
  `;

  // Init tab bar
  initEvalTabs();

  // Bind run button
  const runBtn = el("evalRunBtn");
  if (runBtn) {
    runBtn.addEventListener("click", () => runEvals());
  }

  // Bind refresh button
  const refreshBtn = el("evalRefreshBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.disabled = true;
      refreshBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin">refresh</span> Refreshing...';
      await Promise.allSettled([loadCases(), loadRuns(), loadTrend()]);
      setTimeout(() => {
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1">refresh</span> Refresh';
      }, 500);
    });
  }

  // Load initial data
  Promise.allSettled([loadCases(), loadRuns(), loadTrend()]);
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

export { initEvalUI };
