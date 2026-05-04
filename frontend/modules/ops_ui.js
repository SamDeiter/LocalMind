/**
 * ops_ui.js — LocalMind v2 Operations page (Phase 3)
 *
 * Industrial mission-control dashboard rendered into #opsContainer.
 * Sections: hardware strip (CPU / RAM / GPU / Disk with sparklines),
 * queue timeline, worker roster, installed models, alert feed, log tail.
 *
 * Backend endpoints (read-only, no mutation):
 *   GET /api/hardware         CPU / RAM / loaded models + VRAM
 *   GET /api/health           disk_percent, uptime, ollama state
 *   GET /api/models           installed Ollama models + sizes
 *   GET /api/jobs/stats       job totals, cost_by_day, cost buckets
 *   GET /api/metrics/summary  tokens, LLM latency, error rate
 *   GET /api/alerts/recent    recent alert objects
 *   GET /api/swarm/status     worker/agent roster
 *   GET /api/swarm/agents     agent detail (fallback)
 *
 * No backend changes. Fails soft on any 404 / error — never crashes the page.
 */

import { API } from "./state.js";
import { escapeHtml } from "./utils.js";

// ── Module state ───────────────────────────────────────────────────
let _inited      = false;
let _pollFastId  = null;  // hardware poll, 3s
let _pollSlowId  = null;  // everything else, 15s
let _logPaused   = false;
let _resizeRaf   = 0;

const HARDWARE_POLL_MS = 3_000;
const SLOW_POLL_MS     = 15_000;
const SPARK_HISTORY    = 40;

// Circular buffers for sparklines
const _history = {
  cpu:  [],
  ram:  [],
  gpu:  [],
  disk: [],
};

// Log buffer
const _logs = [];
const LOG_MAX = 200;

// ── Public API ─────────────────────────────────────────────────────

export function initOpsUI() {
  const target = document.getElementById("opsContainer");
  if (!target) return;

  // Re-mount pattern: if we were inited before, tear down timers but re-render.
  if (_inited) {
    _stopPolling();
  }
  _inited = true;

  target.innerHTML = _renderShell();
  _bindEvents(target);
  _refreshAll();
  _startPolling();
}

export function stopOps() {
  _stopPolling();
}

// ── Render (static shell) ──────────────────────────────────────────

function _renderShell() {
  return /* html */ `
    <header class="lm-page__header">
      <div>
        <div class="lm-page__eyebrow">System health</div>
        <h1 class="lm-page__title">Operations</h1>
        <p class="lm-page__subtitle">Live hardware, queue depth, workers, models, and alerts.</p>
      </div>
      <div class="lm-page__actions">
        <span class="lm-ops__updated lm-mono" id="opsLastUpdated" aria-live="polite">—</span>
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="opsRefreshBtn" aria-label="Refresh operations data">
          <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
          Refresh
        </button>
      </div>
    </header>

    <!-- ── Hardware strip ─────────────────────────────────────── -->
    <section class="lm-ops__strip" aria-label="Hardware utilization" role="status" aria-live="polite">
      ${_renderHardwareTile("cpu",  "CPU",  "memory",       "Load")}
      ${_renderHardwareTile("ram",  "RAM",  "database",     "In use")}
      ${_renderHardwareTile("gpu",  "GPU",  "developer_board", "VRAM")}
      ${_renderHardwareTile("disk", "Disk", "hard_drive",   "Used")}
    </section>

    <!-- ── Two-column row: queue timeline + workers ──────────── -->
    <section class="lm-ops__row lm-ops__row--2">
      <article class="lm-card lm-ops__panel">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">timeline</span>
          <h3 class="lm-card__title">Queue throughput</h3>
          <span class="lm-card__count lm-ops__panel-note" id="opsQueueNote">Last 30 days</span>
        </header>
        <div class="lm-card__body">
          <div class="lm-ops__chart" id="opsQueueChart" aria-label="Job volume over the last 30 days">
            ${_skeletonBars(18)}
          </div>
          <div class="lm-ops__chart-meta" id="opsQueueMeta">
            <span class="lm-mute">Total</span><span class="lm-mono" id="opsQueueTotal">—</span>
            <span class="lm-ops__sep" aria-hidden="true"></span>
            <span class="lm-mute">Done</span><span class="lm-mono" id="opsQueueDone">—</span>
            <span class="lm-ops__sep" aria-hidden="true"></span>
            <span class="lm-mute">Failed</span><span class="lm-mono" id="opsQueueFailed">—</span>
            <span class="lm-ops__sep" aria-hidden="true"></span>
            <span class="lm-mute">Avg cost</span><span class="lm-mono" id="opsQueueAvg">—</span>
          </div>
        </div>
      </article>

      <article class="lm-card lm-ops__panel">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">group</span>
          <h3 class="lm-card__title">Workers</h3>
          <span class="lm-ops__panel-note lm-mono" id="opsWorkersCount">—</span>
        </header>
        <div class="lm-card__body lm-ops__workers" id="opsWorkers">
          ${_skeletonRows(4)}
        </div>
      </article>
    </section>

    <!-- ── Models panel ──────────────────────────────────────── -->
    <article class="lm-card lm-ops__panel">
      <header class="lm-card__header">
        <span class="material-symbols-outlined lm-mute" aria-hidden="true">deployed_code</span>
        <h3 class="lm-card__title">Installed models</h3>
        <span class="lm-ops__panel-note lm-mono" id="opsModelsCount">—</span>
      </header>
      <div class="lm-card__body">
        <div class="lm-ops__models" id="opsModels">
          ${_skeletonRows(4)}
        </div>
      </div>
    </article>

    <!-- ── Two-column row: alerts + logs ─────────────────────── -->
    <section class="lm-ops__row lm-ops__row--2">
      <article class="lm-card lm-ops__panel">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">notifications_active</span>
          <h3 class="lm-card__title">Alerts</h3>
          <span class="lm-ops__panel-note lm-mono" id="opsAlertsCount">—</span>
        </header>
        <div class="lm-card__body lm-ops__alerts" id="opsAlerts" role="log" aria-live="polite">
          ${_skeletonRows(3)}
        </div>
      </article>

      <article class="lm-card lm-ops__panel">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">terminal</span>
          <h3 class="lm-card__title">Activity log</h3>
          <div class="lm-ops__log-actions">
            <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="opsLogPauseBtn" aria-label="Pause log updates">
              <span class="material-symbols-outlined" aria-hidden="true">pause</span>
              <span id="opsLogPauseLabel">Pause</span>
            </button>
            <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="opsLogCopyBtn" aria-label="Copy log contents">
              <span class="material-symbols-outlined" aria-hidden="true">content_copy</span>
              Copy
            </button>
          </div>
        </header>
        <div class="lm-card__body">
          <pre class="lm-ops__log" id="opsLog" tabindex="0" aria-label="Recent activity log"></pre>
        </div>
      </article>
    </section>
  `;
}

function _renderHardwareTile(key, label, icon, sublabel) {
  return /* html */ `
    <article class="lm-ops__tile" data-hw="${key}">
      <header class="lm-ops__tile-head">
        <span class="material-symbols-outlined lm-mute" aria-hidden="true">${icon}</span>
        <span class="lm-ops__tile-label">${escapeHtml(label)}</span>
      </header>
      <div class="lm-ops__tile-body">
        <div class="lm-ops__tile-value">
          <span class="lm-ops__tile-number lm-mono" id="opsHw-${key}-val">—</span>
          <span class="lm-ops__tile-unit" id="opsHw-${key}-unit">%</span>
        </div>
        <svg class="lm-ops__sparkline" id="opsHw-${key}-spark"
             viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
          <polyline class="lm-ops__sparkline-line" fill="none" stroke="currentColor"
                    stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"
                    points=""></polyline>
          <polygon class="lm-ops__sparkline-fill" fill="currentColor" opacity="0.10" points=""></polygon>
        </svg>
      </div>
      <footer class="lm-ops__tile-foot">
        <span class="lm-ops__tile-sub lm-mute">${escapeHtml(sublabel)}</span>
        <span class="lm-ops__tile-sub-val lm-mono" id="opsHw-${key}-sub">—</span>
      </footer>
    </article>
  `;
}

function _skeletonRows(n) {
  let out = "";
  for (let i = 0; i < n; i++) {
    out += `<div class="lm-skeleton lm-ops__skeleton-row" aria-hidden="true"></div>`;
  }
  return out;
}

function _skeletonBars(n) {
  let out = `<div class="lm-ops__bars">`;
  for (let i = 0; i < n; i++) {
    const h = 20 + Math.floor(Math.random() * 50);
    out += `<span class="lm-ops__bar lm-ops__bar--placeholder" style="height:${h}%"></span>`;
  }
  out += `</div>`;
  return out;
}

// ── Event wiring ───────────────────────────────────────────────────

function _bindEvents(root) {
  root.querySelector("#opsRefreshBtn")?.addEventListener("click", () => {
    _refreshAll();
  });

  const pauseBtn = root.querySelector("#opsLogPauseBtn");
  pauseBtn?.addEventListener("click", () => {
    _logPaused = !_logPaused;
    const label = root.querySelector("#opsLogPauseLabel");
    const icon  = pauseBtn.querySelector(".material-symbols-outlined");
    if (label) label.textContent = _logPaused ? "Resume" : "Pause";
    if (icon)  icon.textContent  = _logPaused ? "play_arrow" : "pause";
    pauseBtn.setAttribute("aria-label", _logPaused ? "Resume log updates" : "Pause log updates");
  });

  const copyBtn = root.querySelector("#opsLogCopyBtn");
  copyBtn?.addEventListener("click", async () => {
    const pre = root.querySelector("#opsLog");
    if (!pre) return;
    const text = pre.textContent || "";
    try {
      await navigator.clipboard.writeText(text);
      _flash(copyBtn, "Copied");
    } catch (_) {
      _flash(copyBtn, "Failed");
    }
  });

  // Re-draw sparklines on resize (debounced via rAF)
  window.addEventListener("resize", _onResize, { passive: true });
}

function _onResize() {
  if (_resizeRaf) return;
  _resizeRaf = requestAnimationFrame(() => {
    _resizeRaf = 0;
    _redrawAllSparks();
  });
}

function _flash(btn, text) {
  const original = btn.textContent;
  const pill = document.createElement("span");
  pill.className = "lm-ops__flash";
  pill.textContent = text;
  btn.appendChild(pill);
  setTimeout(() => pill.remove(), 1400);
}

// ── Polling ────────────────────────────────────────────────────────

function _opsTabVisible() {
  const panel = document.querySelector('[data-nav="ops"]');
  return panel && !panel.hidden;
}

function _startPolling() {
  _stopPolling();
  _pollFastId = setInterval(() => {
    if (document.hidden) return;
    if (!_opsTabVisible()) return;
    _refreshHardware();
  }, HARDWARE_POLL_MS);

  _pollSlowId = setInterval(() => {
    if (document.hidden) return;
    if (!_opsTabVisible()) return;
    _refreshSlow();
  }, SLOW_POLL_MS);
}

function _stopPolling() {
  if (_pollFastId) { clearInterval(_pollFastId); _pollFastId = null; }
  if (_pollSlowId) { clearInterval(_pollSlowId); _pollSlowId = null; }
  window.removeEventListener("resize", _onResize);
}

async function _refreshAll() {
  await Promise.allSettled([_refreshHardware(), _refreshSlow()]);
  _setUpdated();
}

async function _refreshSlow() {
  await Promise.allSettled([
    _refreshQueueStats(),
    _refreshWorkers(),
    _refreshModels(),
    _refreshAlerts(),
    _refreshLog(),
  ]);
  _setUpdated();
}

// ── Hardware strip ─────────────────────────────────────────────────

async function _refreshHardware() {
  // Pull both hardware and health in parallel. /api/hardware gives CPU+RAM+GPU;
  // /api/health adds disk_percent and uptime.
  const [hwRes, healthRes] = await Promise.allSettled([
    _safeJson("/api/hardware"),
    _safeJson("/api/health"),
  ]);

  const hw     = hwRes.status === "fulfilled" ? hwRes.value : null;
  const health = healthRes.status === "fulfilled" ? healthRes.value : null;

  // CPU
  const cpu = _firstNum(hw?.system?.cpu_percent, health?.cpu_percent);
  _pushHistory("cpu", cpu);
  _setTile("cpu", cpu, "%", _cpuSub(hw, health));

  // RAM
  const ram = _firstNum(hw?.system?.ram_percent, health?.memory_percent);
  _pushHistory("ram", ram);
  _setTile("ram", ram, "%", _ramSub(hw));

  // GPU — derived from loaded Ollama models' VRAM footprint relative to total used
  const gpu = _gpuPercent(hw);
  _pushHistory("gpu", gpu);
  _setTile("gpu", gpu, "%", _gpuSub(hw));

  // Disk
  const disk = _firstNum(health?.disk_percent);
  _pushHistory("disk", disk);
  _setTile("disk", disk, "%", _diskSub(health));

  _redrawAllSparks();
  _setUpdated();
}

function _cpuSub(hw, health) {
  const uptime = health?.uptime_sec;
  if (!uptime) return "Load";
  return `up ${_formatUptime(uptime)}`;
}

function _ramSub(hw) {
  const used = hw?.system?.ram_used_gb;
  const total = hw?.system?.ram_total_gb;
  if (used != null && total != null) return `${used} / ${total} GB`;
  return "In use";
}

function _gpuPercent(hw) {
  const models = hw?.models || [];
  if (!models.length) return 0;
  // Approximate: percent of model weight resident in VRAM
  let totalSize = 0, totalVram = 0;
  for (const m of models) {
    totalSize += Number(m.size_gb || 0);
    totalVram += Number(m.vram_gb || 0);
  }
  if (totalSize <= 0) return 0;
  return Math.min(100, (totalVram / totalSize) * 100);
}

function _gpuSub(hw) {
  const models = hw?.models || [];
  if (!models.length) return "idle";
  let totalVram = 0;
  for (const m of models) totalVram += Number(m.vram_gb || 0);
  return `${totalVram.toFixed(1)} GB VRAM`;
}

function _diskSub(health) {
  const db = health?.db_size_mb;
  if (db == null) return "Used";
  return `DB ${db.toFixed ? db.toFixed(1) : db} MB`;
}

function _setTile(key, value, unit, sub) {
  const valEl  = document.getElementById(`opsHw-${key}-val`);
  const unitEl = document.getElementById(`opsHw-${key}-unit`);
  const subEl  = document.getElementById(`opsHw-${key}-sub`);
  const tile   = document.querySelector(`.lm-ops__tile[data-hw="${key}"]`);

  if (valEl) valEl.textContent = (value == null || Number.isNaN(value))
    ? "—"
    : _roundForDisplay(value);
  if (unitEl) unitEl.textContent = (value == null || Number.isNaN(value)) ? "" : unit;
  if (subEl)  subEl.textContent  = sub || "—";

  if (tile) {
    tile.dataset.level = _utilLevel(value);
  }
}

function _utilLevel(v) {
  if (v == null || Number.isNaN(v)) return "idle";
  if (v >= 90) return "err";
  if (v >= 70) return "warn";
  return "ok";
}

function _pushHistory(key, v) {
  if (v == null || Number.isNaN(v)) return;
  const buf = _history[key];
  buf.push(v);
  if (buf.length > SPARK_HISTORY) buf.shift();
}

function _redrawAllSparks() {
  _drawSpark("cpu");
  _drawSpark("ram");
  _drawSpark("gpu");
  _drawSpark("disk");
}

function _drawSpark(key) {
  const svg = document.getElementById(`opsHw-${key}-spark`);
  if (!svg) return;
  const line = svg.querySelector(".lm-ops__sparkline-line");
  const fill = svg.querySelector(".lm-ops__sparkline-fill");
  if (!line || !fill) return;

  const history = _history[key];
  if (history.length < 2) {
    line.setAttribute("points", "");
    fill.setAttribute("points", "");
    return;
  }

  const W = 100, H = 30;
  const n = history.length;
  const step = W / Math.max(n - 1, 1);

  const linePts = [];
  for (let i = 0; i < n; i++) {
    const x = (i * step).toFixed(2);
    const v = Math.max(0, Math.min(100, history[i] || 0));
    const y = (H - (v / 100) * (H - 2) - 1).toFixed(2);
    linePts.push(`${x},${y}`);
  }
  line.setAttribute("points", linePts.join(" "));

  // Close the polygon to form a filled area
  const fillPts = [`0,${H}`, ...linePts, `${W},${H}`].join(" ");
  fill.setAttribute("points", fillPts);
}

// ── Queue throughput ───────────────────────────────────────────────

async function _refreshQueueStats() {
  const chart   = document.getElementById("opsQueueChart");
  const totalEl = document.getElementById("opsQueueTotal");
  const doneEl  = document.getElementById("opsQueueDone");
  const failEl  = document.getElementById("opsQueueFailed");
  const avgEl   = document.getElementById("opsQueueAvg");

  const stats = await _safeJson("/api/jobs/stats");
  if (!stats) {
    if (chart) chart.innerHTML = _emptyState("Queue stats unavailable");
    return;
  }

  if (totalEl) totalEl.textContent = String(stats.total_jobs ?? 0);
  if (doneEl)  doneEl.textContent  = String(stats.completed_jobs ?? 0);
  if (failEl)  failEl.textContent  = String(stats.failed_jobs ?? 0);
  if (avgEl)   avgEl.textContent   = _formatCents(stats.avg_cost_cents);

  const series = Array.isArray(stats.cost_by_day) ? stats.cost_by_day : [];
  if (!chart) return;

  if (!series.length) {
    chart.innerHTML = _emptyState("No jobs in the last 30 days.");
    return;
  }

  // series is DESC by day — reverse for oldest-first left-to-right reading.
  const ordered = series.slice().reverse();
  const max = ordered.reduce((m, d) => Math.max(m, Number(d.job_count || 0)), 1);

  const bars = ordered.map((d) => {
    const count = Number(d.job_count || 0);
    const h = (count / max) * 100;
    const label = `${d.date}: ${count} job${count === 1 ? "" : "s"}`;
    return `<span class="lm-ops__bar" style="height:${h.toFixed(1)}%" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"></span>`;
  }).join("");

  chart.innerHTML = `<div class="lm-ops__bars">${bars}</div>`;
}

// ── Workers ────────────────────────────────────────────────────────

async function _refreshWorkers() {
  const el = document.getElementById("opsWorkers");
  const countEl = document.getElementById("opsWorkersCount");
  if (!el) return;

  const status = await _safeJson("/api/swarm/status");
  let workers = [];

  if (status && !status.error) {
    workers = _extractWorkers(status);
  }

  // Fallback to /api/swarm/agents if status was empty or errored.
  if (!workers.length) {
    const agents = await _safeJson("/api/swarm/agents");
    if (agents) workers = _extractWorkers(agents);
  }

  if (countEl) countEl.textContent = workers.length ? `${workers.length} active` : "none";

  if (!workers.length) {
    el.innerHTML = _emptyState("Swarm idle — no workers active.");
    return;
  }

  el.innerHTML = workers.map((w) => {
    const name = w.name || w.id || w.agent_id || "worker";
    const role = w.role || w.type || w.specialty || "";
    const busy = w.busy || w.state === "busy" || w.status === "running";
    const statusKey = busy ? "running" : (w.state === "error" ? "failed" : "ok");
    const statusLbl = busy ? "Busy" : (w.state === "error" ? "Error" : "Idle");
    const task = w.current_task || w.current_job || w.task || "";
    const lastSeen = _relativeTime(w.last_seen || w.updated_at || w.last_heartbeat);
    return `
      <div class="lm-ops__worker">
        <span class="lm-ops__worker-status lm-status lm-status--${statusKey}" aria-label="${statusLbl}">
          <span class="lm-status__dot" aria-hidden="true"></span>
        </span>
        <div class="lm-ops__worker-body">
          <div class="lm-ops__worker-name">
            <span class="lm-ops__worker-title">${escapeHtml(name)}</span>
            ${role ? `<span class="lm-ops__worker-role lm-mute">${escapeHtml(role)}</span>` : ""}
          </div>
          <div class="lm-ops__worker-meta lm-mute">
            ${task ? `<span class="lm-ops__worker-task" title="${escapeHtml(task)}">${escapeHtml(_truncate(task, 64))}</span>` : `<span>${statusLbl}</span>`}
            ${lastSeen ? `<span class="lm-ops__sep" aria-hidden="true"></span><span class="lm-mono">${escapeHtml(lastSeen)}</span>` : ""}
          </div>
        </div>
      </div>
    `;
  }).join("");
}

function _extractWorkers(obj) {
  if (!obj) return [];
  if (Array.isArray(obj)) return obj;
  if (Array.isArray(obj.agents)) return obj.agents;
  if (Array.isArray(obj.workers)) return obj.workers;
  // Some shapes: { swarm: { agents: [] } }
  if (obj.swarm && Array.isArray(obj.swarm.agents)) return obj.swarm.agents;
  return [];
}

// ── Models ─────────────────────────────────────────────────────────

async function _refreshModels() {
  const el = document.getElementById("opsModels");
  const countEl = document.getElementById("opsModelsCount");
  if (!el) return;

  const [modelsRes, hwRes] = await Promise.all([
    _safeJson("/api/models"),
    _safeJson("/api/hardware"),
  ]);

  const list = Array.isArray(modelsRes) ? modelsRes : (modelsRes?.models || []);
  const loadedList = hwRes?.models || [];
  const loadedNames = new Set(loadedList.map((m) => m.name));

  if (countEl) countEl.textContent = list.length ? `${list.length} installed` : "none";

  if (!list.length) {
    el.innerHTML = _emptyState("No models installed. Pull one via ollama.");
    return;
  }

  el.innerHTML = `
    <table class="lm-ops__models-table">
      <thead>
        <tr>
          <th scope="col">Model</th>
          <th scope="col">Family</th>
          <th scope="col" class="lm-ops__col-right">Size</th>
          <th scope="col" class="lm-ops__col-right">Status</th>
        </tr>
      </thead>
      <tbody>
        ${list.map((m) => _modelRow(m, loadedNames, loadedList)).join("")}
      </tbody>
    </table>
  `;
}

function _modelRow(m, loadedNames, loadedList) {
  const name = m.name || m.id || "model";
  const family = _modelFamily(name);
  const sizeGb = Number(m.size || 0) / (1024 ** 3);
  const loaded = loadedNames.has(name);
  const vramEntry = loadedList.find((l) => l.name === name);
  return `
    <tr class="lm-ops__model-row ${loaded ? "is-loaded" : ""}">
      <td class="lm-ops__model-name">
        <span class="lm-mono">${escapeHtml(name)}</span>
      </td>
      <td class="lm-ops__model-family">
        <span class="lm-chip lm-chip--static">${escapeHtml(family)}</span>
      </td>
      <td class="lm-ops__col-right lm-mono">${sizeGb ? sizeGb.toFixed(1) + " GB" : "—"}</td>
      <td class="lm-ops__col-right">
        ${loaded
          ? `<span class="lm-status lm-status--ok"><span class="lm-status__dot" aria-hidden="true"></span>Loaded${vramEntry ? ` · ${vramEntry.vram_gb} GB` : ""}</span>`
          : `<span class="lm-status lm-status--paused"><span class="lm-status__dot" aria-hidden="true"></span>Idle</span>`}
      </td>
    </tr>
  `;
}

function _modelFamily(name) {
  const lower = String(name || "").toLowerCase();
  if (lower.startsWith("llama"))   return "Llama";
  if (lower.startsWith("gemma"))   return "Gemma";
  if (lower.startsWith("deepseek"))return "DeepSeek";
  if (lower.startsWith("qwen"))    return "Qwen";
  if (lower.startsWith("mistral")) return "Mistral";
  if (lower.startsWith("phi"))     return "Phi";
  if (lower.startsWith("medgemma"))return "MedGemma";
  if (lower.includes("coder"))     return "Coder";
  if (lower.includes("embed"))     return "Embed";
  return "Other";
}

// ── Alerts ─────────────────────────────────────────────────────────

async function _refreshAlerts() {
  const el = document.getElementById("opsAlerts");
  const countEl = document.getElementById("opsAlertsCount");
  if (!el) return;

  const data = await _safeJson("/api/alerts/recent");
  const alerts = (data && Array.isArray(data.alerts)) ? data.alerts : [];

  if (countEl) countEl.textContent = alerts.length ? `${alerts.length} open` : "all clear";

  if (!alerts.length) {
    el.innerHTML = `
      <div class="lm-ops__alerts-empty">
        <span class="material-symbols-outlined" aria-hidden="true">check_circle</span>
        <span>No active alerts. Systems nominal.</span>
      </div>
    `;
    return;
  }

  el.innerHTML = alerts.map((a) => {
    const severity = String(a.severity || a.level || "warn").toLowerCase();
    const key = (severity === "critical" || severity === "error" || severity === "failed")
      ? "failed"
      : (severity === "ok" || severity === "info" ? "ok" : "waiting");
    const msg = a.message || a.title || a.description || a.msg || "Alert triggered";
    const src = a.source || a.component || a.metric || "";
    const when = _relativeTime(a.timestamp || a.created_at || a.time);
    const icon = key === "failed" ? "error" : (key === "ok" ? "check_circle" : "warning");
    return `
      <div class="lm-ops__alert lm-ops__alert--${key}">
        <span class="material-symbols-outlined lm-ops__alert-icon" aria-hidden="true">${icon}</span>
        <div class="lm-ops__alert-body">
          <div class="lm-ops__alert-msg">${escapeHtml(_truncate(msg, 160))}</div>
          ${src ? `<div class="lm-ops__alert-src lm-mute">${escapeHtml(src)}</div>` : ""}
        </div>
        ${when ? `<span class="lm-ops__alert-time lm-mono lm-mute">${escapeHtml(when)}</span>` : ""}
      </div>
    `;
  }).join("");
}

// ── Log tail ───────────────────────────────────────────────────────
// No dedicated /api/logs endpoint exists. Build a synthetic activity
// stream from recent jobs so the viewer isn't empty.

async function _refreshLog() {
  if (_logPaused) return;
  const el = document.getElementById("opsLog");
  if (!el) return;

  const data = await _safeJson("/api/jobs?limit=12");
  const jobs = _extractList(data);

  const now = new Date();
  const lines = jobs.map((j) => {
    const t = j.updated_at || j.created_at;
    const ts = t ? _isoShort(t) : _isoShort(now.toISOString());
    const status = (j.status || j.state || "?").padEnd(8, " ");
    const title = _truncate(j.title || j.name || j.description || j.id || "(job)", 80);
    return `${ts}  [${status}]  ${title}`;
  });

  // Replace buffer rather than append — jobs endpoint is state-based, not log-based.
  _logs.length = 0;
  for (const line of lines.slice(0, LOG_MAX)) _logs.push(line);

  if (!_logs.length) {
    el.textContent = "No recent activity.";
    return;
  }

  el.textContent = _logs.join("\n");
  el.scrollTop = el.scrollHeight;
}

function _extractList(data) {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  if (Array.isArray(data.jobs)) return data.jobs;
  if (Array.isArray(data.items)) return data.items;
  if (Array.isArray(data.results)) return data.results;
  return [];
}

// ── Fetch helpers ──────────────────────────────────────────────────

async function _safeJson(path) {
  try {
    const res = await fetch(`${API}${path}`, { cache: "no-store" });
    if (!res.ok) {
      console.warn(`[ops] ${path} → HTTP ${res.status}`);
      return null;
    }
    return await res.json();
  } catch (err) {
    console.warn(`[ops] ${path} fetch failed:`, err);
    return null;
  }
}

// ── Formatting helpers ─────────────────────────────────────────────

function _firstNum(...vals) {
  for (const v of vals) {
    if (v != null && !Number.isNaN(Number(v))) return Number(v);
  }
  return null;
}

function _roundForDisplay(v) {
  if (v >= 100) return "100";
  if (v >= 10)  return v.toFixed(0);
  return v.toFixed(1);
}

function _formatUptime(sec) {
  if (!sec || sec < 60) return `${Math.round(sec || 0)}s`;
  const m = Math.floor(sec / 60);
  if (m < 60)   return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48)   return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

function _formatCents(v) {
  if (v == null) return "—";
  const cents = Number(v);
  if (!cents) return "$0.00";
  if (cents < 1) return `${cents.toFixed(3)}¢`;
  return `$${(cents / 100).toFixed(3)}`;
}

function _truncate(s, n) {
  const str = String(s || "");
  return str.length > n ? str.slice(0, n - 1) + "…" : str;
}

function _relativeTime(iso) {
  if (!iso) return "";
  const t = typeof iso === "number" ? iso : Date.parse(iso);
  if (!t || isNaN(t)) return "";
  const delta = Math.floor((Date.now() - t) / 1000);
  if (delta < 60)    return `${delta}s`;
  if (delta < 3600)  return `${Math.floor(delta / 60)}m`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h`;
  return `${Math.floor(delta / 86400)}d`;
}

function _isoShort(iso) {
  try {
    const d = new Date(iso);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    return `${hh}:${mm}:${ss}`;
  } catch {
    return "--:--:--";
  }
}

function _emptyState(msg) {
  return `<div class="lm-ops__empty">${escapeHtml(msg)}</div>`;
}

function _setUpdated() {
  const el = document.getElementById("opsLastUpdated");
  if (!el) return;
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  el.textContent = `Updated ${hh}:${mm}:${ss}`;
}
