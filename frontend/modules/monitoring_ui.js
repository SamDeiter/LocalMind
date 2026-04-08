/**
 * monitoring_ui.js -- System Monitoring Dashboard
 * ================================================
 * Real-time system health, metrics, and alert monitoring for LocalMind.
 *
 * Features:
 *   - Health cards: CPU, Memory, Disk, Ollama, DB size, Uptime (color-coded)
 *   - Metrics panel: jobs/24h, avg duration, error rate, tokens used
 *   - Alert log: scrollable severity-colored recent alerts
 *   - Auto-polls every 15 seconds
 *
 * Exports:
 *   initMonitoring()  -- bootstrap: render layout, start polling
 */

import { API } from "./state.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _pollTimer = null;
const POLL_INTERVAL = 15_000;

// ---------------------------------------------------------------------------
// DOM helper
// ---------------------------------------------------------------------------

const el = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Tailwind safelist -- ensures JIT includes our dynamic status colours
// ---------------------------------------------------------------------------

const _TW_SAFELIST = [
  "text-emerald-400", "bg-emerald-500/15", "border-emerald-500/20",
  "text-amber-400", "bg-amber-500/15", "border-amber-500/20",
  "text-red-400", "bg-red-500/15", "border-red-500/20",
  "text-cyan-400", "bg-cyan-500/15", "border-cyan-500/20",
  "text-blue-400", "bg-blue-500/15", "border-blue-500/20",
  "text-slate-400", "bg-slate-500/15", "border-slate-500/20",
  "bg-emerald-400", "bg-amber-400", "bg-red-400",
];
void _TW_SAFELIST;

// ---------------------------------------------------------------------------
// Color helpers
// ---------------------------------------------------------------------------

/** Return tailwind color key based on percentage thresholds */
function pctColor(pct) {
  if (pct >= 90) return "red";
  if (pct >= 70) return "amber";
  return "emerald";
}

function pctTextClass(pct) {
  return `text-${pctColor(pct)}-400`;
}

function pctBgClass(pct) {
  return `bg-${pctColor(pct)}-400`;
}

/** Severity color for alert levels */
function alertColor(level) {
  const l = (level || "info").toLowerCase();
  if (l === "critical" || l === "error") return "red";
  if (l === "warning" || l === "warn") return "amber";
  return "blue";
}

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

function formatUptime(sec) {
  if (sec == null || sec < 0) return "--";
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h < 24) return `${h}h ${m}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

function formatDuration(sec) {
  if (sec == null) return "--";
  if (sec < 1) return `${Math.round(sec * 1000)}ms`;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
}

function formatNumber(n) {
  if (n == null) return "--";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
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
// Shell HTML -- injected into #monitoringView container
// ---------------------------------------------------------------------------

function buildShellHTML() {
  return `
  <!-- Header -->
  <div class="flex items-center justify-between flex-wrap gap-4 mb-6">
    <div class="flex items-center gap-3">
      <span class="material-symbols-outlined text-indigo-400 text-2xl" aria-hidden="true">monitoring</span>
      <h2 class="text-xl font-headline font-bold tracking-tight text-slate-100">System Monitor</h2>
      <span id="monHealthBadge"
            class="text-[9px] font-bold uppercase tracking-widest bg-slate-500/20 text-slate-400 px-2.5 py-1 rounded-full">
        Loading...
      </span>
    </div>
    <div class="flex items-center gap-3">
      <span id="monLastPoll" class="text-[9px] font-mono text-slate-500"></span>
      <button id="monRefreshBtn"
              class="bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 text-[10px] font-bold px-4 py-2 rounded-lg uppercase tracking-wider transition-colors border border-indigo-500/20 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              aria-label="Refresh monitoring data">
        <span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">refresh</span> Refresh
      </button>
    </div>
  </div>

  <!-- Health Cards Grid -->
  <div id="monHealthGrid"
       class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6"
       role="region" aria-label="System health metrics">
    ${buildHealthCardPlaceholders()}
  </div>

  <!-- Metrics + Alerts row -->
  <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">

    <!-- Metrics Panel (2 cols) -->
    <div class="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-lg p-5">
      <div class="flex items-center gap-2 mb-4">
        <span class="material-symbols-outlined text-cyan-400 text-lg" aria-hidden="true">analytics</span>
        <h3 class="text-sm font-bold text-slate-200 uppercase tracking-wider">Throughput (24h)</h3>
      </div>
      <div id="monMetricsGrid"
           class="grid grid-cols-2 md:grid-cols-4 gap-4"
           role="region" aria-label="Throughput metrics">
        ${buildMetricPlaceholders()}
      </div>
    </div>

    <!-- Alert Log (1 col) -->
    <div class="bg-slate-800 border border-slate-700 rounded-lg flex flex-col max-h-[400px]">
      <div class="flex items-center gap-2 p-4 pb-2 border-b border-slate-700/50">
        <span class="material-symbols-outlined text-amber-400 text-lg" aria-hidden="true">notifications_active</span>
        <h3 class="text-sm font-bold text-slate-200 uppercase tracking-wider">Recent Alerts</h3>
        <span id="monAlertCount" class="ml-auto text-[9px] font-bold uppercase tracking-widest bg-slate-700/50 text-slate-400 px-2 py-0.5 rounded-full">0</span>
      </div>
      <div id="monAlertLog"
           class="flex-1 overflow-y-auto custom-scrollbar p-2"
           role="log" aria-label="Recent alerts" aria-live="polite">
        <div class="text-xs text-slate-500 italic p-4 text-center">Loading alerts...</div>
      </div>
    </div>

  </div>
  `;
}

function buildHealthCardPlaceholders() {
  const cards = ["CPU", "Memory", "Disk", "Ollama", "DB Size", "Uptime"];
  return cards.map(label => `
    <div class="bg-slate-800 border border-slate-700 rounded-lg p-4 flex flex-col gap-2">
      <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">${label}</div>
      <div class="text-2xl font-bold text-slate-600 tabular-nums">--</div>
      <div class="w-full bg-slate-700/50 h-1.5 rounded-full overflow-hidden">
        <div class="h-full rounded-full bg-slate-600 transition-all duration-700" style="width: 0%"></div>
      </div>
    </div>
  `).join("");
}

function buildMetricPlaceholders() {
  const items = ["Jobs Completed", "Avg Duration", "Error Rate", "Tokens Used"];
  return items.map(label => `
    <div class="bg-slate-900/40 border border-slate-700/50 rounded-lg p-4 text-center">
      <div class="text-2xl font-bold text-slate-600 tabular-nums mb-1">--</div>
      <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">${label}</div>
    </div>
  `).join("");
}

// ---------------------------------------------------------------------------
// Data fetchers
// ---------------------------------------------------------------------------

async function loadHealthData() {
  try {
    const res = await fetch(`${API}/api/health`);
    if (!res.ok) throw new Error(`Health API ${res.status}`);
    const data = await res.json();
    renderHealthCards(data);
    return data;
  } catch (err) {
    console.warn("Monitoring: health fetch failed", err);
    renderHealthOffline();
    return null;
  }
}

async function loadMetrics() {
  try {
    const res = await fetch(`${API}/api/metrics/summary`);
    if (!res.ok) throw new Error(`Metrics API ${res.status}`);
    const data = await res.json();
    renderMetricsPanel(data);
    return data;
  } catch (err) {
    console.warn("Monitoring: metrics fetch failed", err);
    return null;
  }
}

async function loadAlerts() {
  try {
    const res = await fetch(`${API}/api/alerts/recent`);
    if (!res.ok) throw new Error(`Alerts API ${res.status}`);
    const data = await res.json();
    const alerts = Array.isArray(data) ? data : (data.alerts || []);
    renderAlertLog(alerts);
    return alerts;
  } catch (err) {
    console.warn("Monitoring: alerts fetch failed", err);
    renderAlertLog([]);
    return [];
  }
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function renderHealthCards(data) {
  const grid = el("monHealthGrid");
  if (!grid) return;

  const cpuPct = data.cpu_percent ?? 0;
  const memPct = data.memory_percent ?? 0;
  const diskPct = data.disk_percent ?? 0;
  const ollamaUp = (data.ollama_status || "").toLowerCase() === "running" ||
                   (data.ollama_status || "").toLowerCase() === "ok" ||
                   (data.ollama_status || "").toLowerCase() === "connected";
  const dbSize = data.db_size_mb ?? null;
  const uptime = data.uptime_sec ?? data.uptime_seconds ?? null;
  const activeJobs = data.active_jobs ?? 0;
  const totalDone = data.total_jobs_completed ?? 0;
  const overallStatus = data.status || "unknown";

  // Update header badge
  const badge = el("monHealthBadge");
  if (badge) {
    const isHealthy = overallStatus === "healthy" || overallStatus === "ok";
    badge.textContent = overallStatus.charAt(0).toUpperCase() + overallStatus.slice(1);
    badge.className = `text-[9px] font-bold uppercase tracking-widest px-2.5 py-1 rounded-full ${
      isHealthy
        ? "bg-emerald-500/20 text-emerald-400"
        : "bg-red-500/20 text-red-400"
    }`;
  }

  grid.innerHTML = `
    ${healthCard("CPU", `${Math.round(cpuPct)}%`, cpuPct, "memory")}
    ${healthCard("Memory", `${Math.round(memPct)}%`, memPct, "neurology")}
    ${healthCard("Disk", `${Math.round(diskPct)}%`, diskPct, "hard_drive")}
    ${ollamaCard(ollamaUp, data.ollama_status)}
    ${statCard("DB Size", dbSize != null ? `${dbSize.toFixed(1)} MB` : "--", "database", "cyan")}
    ${uptimeCard(uptime)}
  `;

  // Update poll timestamp
  const pollEl = el("monLastPoll");
  if (pollEl) pollEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

function healthCard(label, valueText, pct, icon) {
  const color = pctColor(pct);
  return `
    <div class="bg-slate-800 border border-slate-700 rounded-lg p-4 flex flex-col gap-2 hover:border-slate-600 transition-colors">
      <div class="flex items-center justify-between">
        <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">${label}</div>
        <span class="material-symbols-outlined text-sm text-${color}-400" aria-hidden="true">${icon}</span>
      </div>
      <div class="text-2xl font-bold text-${color}-400 tabular-nums">${valueText}</div>
      <div class="w-full bg-slate-700/50 h-1.5 rounded-full overflow-hidden">
        <div class="h-full rounded-full bg-${color}-400 transition-all duration-700"
             style="width: ${Math.min(pct, 100)}%"
             role="progressbar" aria-valuenow="${Math.round(pct)}" aria-valuemin="0" aria-valuemax="100"
             aria-label="${label} usage ${Math.round(pct)} percent"></div>
      </div>
    </div>
  `;
}

function ollamaCard(isUp, statusText) {
  const color = isUp ? "emerald" : "red";
  const label = isUp ? "Connected" : (statusText || "Offline");
  return `
    <div class="bg-slate-800 border border-slate-700 rounded-lg p-4 flex flex-col gap-2 hover:border-slate-600 transition-colors">
      <div class="flex items-center justify-between">
        <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Ollama</div>
        <span class="material-symbols-outlined text-sm text-${color}-400" aria-hidden="true">smart_toy</span>
      </div>
      <div class="flex items-center gap-2">
        <div class="w-2.5 h-2.5 rounded-full bg-${color}-400 ${isUp ? "animate-pulse" : ""}" aria-hidden="true"></div>
        <span class="text-lg font-bold text-${color}-400">${label}</span>
      </div>
      <div class="text-[9px] text-slate-500 font-mono">LLM inference engine</div>
    </div>
  `;
}

function statCard(label, value, icon, color) {
  return `
    <div class="bg-slate-800 border border-slate-700 rounded-lg p-4 flex flex-col gap-2 hover:border-slate-600 transition-colors">
      <div class="flex items-center justify-between">
        <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">${label}</div>
        <span class="material-symbols-outlined text-sm text-${color}-400" aria-hidden="true">${icon}</span>
      </div>
      <div class="text-2xl font-bold text-${color}-400 tabular-nums">${value}</div>
      <div class="h-1.5"></div>
    </div>
  `;
}

function uptimeCard(sec) {
  const text = formatUptime(sec);
  return `
    <div class="bg-slate-800 border border-slate-700 rounded-lg p-4 flex flex-col gap-2 hover:border-slate-600 transition-colors">
      <div class="flex items-center justify-between">
        <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Uptime</div>
        <span class="material-symbols-outlined text-sm text-blue-400" aria-hidden="true">schedule</span>
      </div>
      <div class="text-2xl font-bold text-blue-400 tabular-nums">${text}</div>
      <div class="text-[9px] text-slate-500 font-mono">System runtime</div>
    </div>
  `;
}

function renderHealthOffline() {
  const badge = el("monHealthBadge");
  if (badge) {
    badge.textContent = "Offline";
    badge.className = "text-[9px] font-bold uppercase tracking-widest bg-red-500/20 text-red-400 px-2.5 py-1 rounded-full";
  }
}

function renderMetricsPanel(data) {
  const grid = el("monMetricsGrid");
  if (!grid) return;

  const jobsDone = data.jobs_completed_24h ?? null;
  const avgDur = data.avg_job_duration ?? null;
  const errRate = data.error_rate ?? null;
  const tokens = data.tokens_used_24h ?? null;

  const errColor = errRate != null && errRate > 10 ? "red" : errRate != null && errRate > 5 ? "amber" : "emerald";

  grid.innerHTML = `
    <div class="bg-slate-900/40 border border-slate-700/50 rounded-lg p-4 text-center hover:bg-slate-900/60 transition-colors">
      <div class="text-2xl font-bold text-indigo-400 tabular-nums mb-1">${jobsDone != null ? formatNumber(jobsDone) : "--"}</div>
      <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Jobs Completed</div>
    </div>
    <div class="bg-slate-900/40 border border-slate-700/50 rounded-lg p-4 text-center hover:bg-slate-900/60 transition-colors">
      <div class="text-2xl font-bold text-cyan-400 tabular-nums mb-1">${avgDur != null ? formatDuration(avgDur) : "--"}</div>
      <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Avg Duration</div>
    </div>
    <div class="bg-slate-900/40 border border-slate-700/50 rounded-lg p-4 text-center hover:bg-slate-900/60 transition-colors">
      <div class="text-2xl font-bold text-${errColor}-400 tabular-nums mb-1">${errRate != null ? `${errRate.toFixed(1)}%` : "--"}</div>
      <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Error Rate</div>
    </div>
    <div class="bg-slate-900/40 border border-slate-700/50 rounded-lg p-4 text-center hover:bg-slate-900/60 transition-colors">
      <div class="text-2xl font-bold text-purple-400 tabular-nums mb-1">${tokens != null ? formatNumber(tokens) : "--"}</div>
      <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Tokens Used</div>
    </div>
  `;
}

function renderAlertLog(alerts) {
  const container = el("monAlertLog");
  const countBadge = el("monAlertCount");

  if (countBadge) {
    countBadge.textContent = alerts.length;
  }

  if (!container) return;

  if (!alerts.length) {
    container.innerHTML = `
      <div class="flex flex-col items-center justify-center py-8 gap-2">
        <span class="material-symbols-outlined text-2xl text-slate-600" aria-hidden="true">check_circle</span>
        <div class="text-xs text-slate-500">No recent alerts</div>
      </div>
    `;
    return;
  }

  container.innerHTML = alerts.map(alert => {
    const color = alertColor(alert.level);
    const icon = color === "red" ? "error" : color === "amber" ? "warning" : "info";
    const ts = alert.timestamp ? timeAgo(alert.timestamp) : "";
    // Escape HTML in message to prevent XSS
    const msg = (alert.message || "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);

    return `
      <div class="flex items-start gap-3 p-3 rounded-lg hover:bg-slate-700/30 transition-colors border-l-2 border-l-${color}-500 mb-1">
        <span class="material-symbols-outlined text-sm text-${color}-400 mt-0.5 flex-shrink-0" aria-hidden="true">${icon}</span>
        <div class="flex-1 min-w-0">
          <div class="text-[11px] text-slate-200 leading-relaxed">${msg}</div>
          <div class="flex items-center gap-2 mt-1">
            <span class="text-[9px] font-bold uppercase tracking-widest text-${color}-400">${(alert.level || "info").toUpperCase()}</span>
            <span class="text-[9px] font-mono text-slate-500">${ts}</span>
          </div>
        </div>
      </div>
    `;
  }).join("");
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

async function pollAll() {
  await Promise.allSettled([
    loadHealthData(),
    loadMetrics(),
    loadAlerts(),
  ]);
}

function startPolling() {
  if (_pollTimer) return;
  pollAll();
  _pollTimer = setInterval(pollAll, POLL_INTERVAL);
}

function stopPolling() {
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function initMonitoring() {
  const container = el("monitoringView");
  if (!container) {
    console.warn("monitoring_ui: #monitoringView not found in DOM");
    return;
  }

  // Inject shell HTML
  container.innerHTML = `
    <div class="flex-1 flex flex-col p-8 overflow-y-auto custom-scrollbar">
      ${buildShellHTML()}
    </div>
  `;

  // Bind refresh button
  const refreshBtn = el("monRefreshBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.disabled = true;
      refreshBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin">refresh</span> Refreshing...';
      await pollAll();
      setTimeout(() => {
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1">refresh</span> Refresh';
      }, 500);
    });
  }

  // Start polling
  startPolling();
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

export { initMonitoring };
