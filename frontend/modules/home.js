/**
 * home.js — LocalMind v2 Home (mission control)
 *
 * Rendered into #homeContainer. Shape:
 *   1. Composer (textarea + quick chips + Run)
 *   2. 3-card row: Running / Waiting approval / Recent artifacts
 *   3. 2-col row: Live activity / Failures
 *   4. Status row (ok/warn counts, avg duration, queue depth)
 *
 * No backend changes — reads from existing /api/jobs endpoints.
 */

import { API } from "./state.js";
import { escapeHtml } from "./utils.js";

let _pollId = null;
let _sse = null;
const POLL_INTERVAL_MS = 15_000;

// ── Public API ──────────────────────────────────────────────────

export function initHome() {
  const target = document.getElementById("homeContainer");
  if (!target) return;

  target.innerHTML = _render();
  _bindEvents(target);
  _refreshAll();
  _startPolling();
  _connectSSE();
}

export function stopHome() {
  if (_pollId) { clearInterval(_pollId); _pollId = null; }
  if (_sse)    { try { _sse.close(); } catch (_) {} _sse = null; }
}

// ── Render (template) ───────────────────────────────────────────

function _render() {
  return /* html */ `
    <header class="lm-page__header">
      <div>
        <div class="lm-page__eyebrow">Mission control</div>
        <h1 class="lm-page__title">What should LocalMind do?</h1>
        <p class="lm-page__subtitle">Describe the work — pick up the outcome later.</p>
      </div>
      <div class="lm-page__actions">
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="homeRefreshBtn">
          <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
          Refresh
        </button>
      </div>
    </header>

    <!-- ── Composer ───────────────────────────────────────────── -->
    <section class="lm-home__composer" id="homeComposer">
      <label class="lm-home__composer-label" for="homeComposerInput">Describe a job</label>
      <textarea
        id="homeComposerInput"
        class="lm-home__composer-input"
        placeholder="e.g. Pull this week's Linear tickets, group them by workstream, draft a status summary…"
        rows="3"></textarea>
      <div class="lm-home__composer-footer">
        <div class="lm-home__chips" id="homeComposerChips">
          <button type="button" class="lm-chip" data-chip="research">
            <span class="material-symbols-outlined" aria-hidden="true">manage_search</span>
            Research
          </button>
          <button type="button" class="lm-chip" data-chip="summarize">
            <span class="material-symbols-outlined" aria-hidden="true">summarize</span>
            Summarize
          </button>
          <button type="button" class="lm-chip" data-chip="code">
            <span class="material-symbols-outlined" aria-hidden="true">code</span>
            Code
          </button>
          <button type="button" class="lm-chip" data-chip="write">
            <span class="material-symbols-outlined" aria-hidden="true">edit_note</span>
            Write
          </button>
          <button type="button" class="lm-chip" data-chip="plan">
            <span class="material-symbols-outlined" aria-hidden="true">checklist</span>
            Plan
          </button>
        </div>
        <div class="lm-home__composer-action">
          <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="homeCustomizeBtn">
            <span class="material-symbols-outlined" aria-hidden="true">tune</span>
            Customize
          </button>
          <button type="button" class="lm-btn lm-btn--primary lm-btn--sm" id="homeRunBtn">
            <span class="material-symbols-outlined" aria-hidden="true">play_arrow</span>
            Run
          </button>
        </div>
      </div>
    </section>

    <!-- ── 3-card row ─────────────────────────────────────────── -->
    <section class="lm-home__row lm-home__row--3">
      ${_cardShell("Running now", "homeRunning", "play_circle", "home-running")}
      ${_cardShell("Waiting approval", "homeWaiting", "pending_actions", "home-waiting")}
      ${_cardShell("Recent artifacts", "homeArtifacts", "folder", "home-artifacts")}
    </section>

    <!-- ── 2-col row ──────────────────────────────────────────── -->
    <section class="lm-home__row lm-home__row--2">
      ${_cardShell("Live activity", "homeFeed", "radar", "home-feed")}
      ${_cardShell("Failures (last 24h)", "homeFailures", "warning", "home-failures")}
    </section>

    <!-- ── Status row ─────────────────────────────────────────── -->
    <div class="lm-home__status" id="homeStatusRow">
      <span class="lm-home__status-item">
        <span class="lm-status-dot lm-status-dot--ok" aria-hidden="true" style="width:8px;height:8px;border-radius:50%;background:var(--lm-status-ok);display:inline-block;"></span>
        <span id="homeStatusOk">— ok</span>
      </span>
      <span class="lm-home__status-sep"></span>
      <span class="lm-home__status-item">
        <span aria-hidden="true" style="width:8px;height:8px;border-radius:50%;background:var(--lm-status-waiting);display:inline-block;"></span>
        <span id="homeStatusWait">— waiting</span>
      </span>
      <span class="lm-home__status-sep"></span>
      <span class="lm-home__status-item">
        <span aria-hidden="true" style="width:8px;height:8px;border-radius:50%;background:var(--lm-status-failed);display:inline-block;"></span>
        <span id="homeStatusFail">— failed</span>
      </span>
      <span class="lm-home__status-sep"></span>
      <span class="lm-home__status-item">
        <span class="lm-mute">Avg</span>
        <span id="homeStatusAvg" class="lm-mono">—</span>
      </span>
      <span class="lm-home__status-sep"></span>
      <span class="lm-home__status-item">
        <span class="lm-mute">Queue</span>
        <span id="homeStatusQueue" class="lm-mono">—</span>
      </span>
    </div>
  `;
}

function _cardShell(title, bodyId, icon, headerClass) {
  return /* html */ `
    <article class="lm-card">
      <header class="lm-card__header">
        <span class="material-symbols-outlined lm-mute" aria-hidden="true">${icon}</span>
        <h3 class="lm-card__title">${escapeHtml(title)}</h3>
        <span class="lm-card__count" id="${bodyId}Count" hidden></span>
      </header>
      <div class="lm-card__body" id="${bodyId}">
        ${_skeletonRows(3)}
      </div>
    </article>
  `;
}

function _skeletonRows(n) {
  let out = "";
  for (let i = 0; i < n; i++) {
    out += `<div class="lm-skeleton" style="height:14px;margin:8px 12px;"></div>`;
  }
  return out;
}

// ── Event wiring ────────────────────────────────────────────────

function _bindEvents(root) {
  // Composer Run / Customize
  const input = root.querySelector("#homeComposerInput");
  const run   = root.querySelector("#homeRunBtn");
  const cust  = root.querySelector("#homeCustomizeBtn");

  const submit = () => {
    const text = (input?.value || "").trim();
    if (!text) {
      input?.focus();
      return;
    }
    _createJob(text).catch((err) => console.warn("[home] createJob failed", err));
  };

  run?.addEventListener("click", submit);
  input?.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  });

  // Customize → prefill the New Job drawer
  cust?.addEventListener("click", () => {
    const drawer = document.getElementById("newJobDrawer");
    if (!drawer) return;
    drawer.dataset.open = "true";
    drawer.setAttribute("aria-hidden", "false");
    const taskField = document.querySelector("#taskCreationArea textarea");
    if (taskField) taskField.value = input?.value || "";
  });

  // Quick chips — prefix the prompt
  root.querySelectorAll("[data-chip]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const kind = btn.dataset.chip;
      const prefix = _chipPrefix(kind);
      if (!input) return;
      if (!input.value.trim()) input.value = prefix;
      else input.value = `${prefix}${input.value}`;
      input.focus();
      input.selectionStart = input.selectionEnd = input.value.length;
    });
  });

  // Refresh
  root.querySelector("#homeRefreshBtn")?.addEventListener("click", _refreshAll);

  // Clicking a Jobs card item → switch to Jobs view and scroll/focus
  root.querySelectorAll("[data-card-items]").forEach((card) => {
    card.addEventListener("click", (e) => {
      const row = e.target.closest("[data-job-id]");
      if (!row) return;
      import("./nav_rail.js").then((m) => m.switchNav?.("jobs"));
    });
  });
}

function _chipPrefix(kind) {
  const map = {
    research:  "Research: ",
    summarize: "Summarize: ",
    code:      "Build: ",
    write:     "Write: ",
    plan:      "Plan: ",
  };
  return map[kind] || "";
}

// ── Data fetchers ───────────────────────────────────────────────

async function _refreshAll() {
  await Promise.allSettled([
    _refreshRunning(),
    _refreshWaiting(),
    _refreshArtifacts(),
    _refreshFeed(),
    _refreshFailures(),
    _refreshStats(),
  ]);
}

function _startPolling() {
  if (_pollId) clearInterval(_pollId);
  _pollId = setInterval(() => {
    if (document.hidden) return;
    _refreshAll();
  }, POLL_INTERVAL_MS);
}

function _connectSSE() {
  try {
    if (_sse) { _sse.close(); _sse = null; }
    _sse = new EventSource(`${API}/api/jobs/activity`);
    _sse.onmessage = () => {
      // Debounced refresh when activity ticks
      _refreshRunning();
      _refreshWaiting();
      _refreshFeed();
    };
    _sse.onerror = () => {
      // SSE is best-effort; polling covers the rest
      try { _sse.close(); } catch (_) {}
      _sse = null;
    };
  } catch (_) { /* SSE not critical */ }
}

async function _refreshRunning() {
  const el = document.getElementById("homeRunning");
  if (!el) return;
  try {
    const res = await fetch(`${API}/api/jobs?status=executing,planning,running&limit=5`);
    const data = await res.json();
    const jobs = _extractJobs(data);
    _countBadge("homeRunningCount", jobs.length);
    el.innerHTML = jobs.length === 0
      ? `<div class="lm-home__empty">Nothing running.</div>`
      : jobs.map(_listRow).join("");
  } catch (e) {
    el.innerHTML = `<div class="lm-home__empty">Could not load jobs.</div>`;
  }
}

async function _refreshWaiting() {
  const el = document.getElementById("homeWaiting");
  if (!el) return;
  try {
    const res = await fetch(`${API}/api/jobs?status=reviewing,waiting_approval&limit=5`);
    const data = await res.json();
    const jobs = _extractJobs(data);
    _countBadge("homeWaitingCount", jobs.length);
    el.innerHTML = jobs.length === 0
      ? `<div class="lm-home__empty">Nothing needs your approval.</div>`
      : jobs.map(_listRow).join("");
  } catch (e) {
    el.innerHTML = `<div class="lm-home__empty">Could not load approvals.</div>`;
  }
}

async function _refreshArtifacts() {
  const el = document.getElementById("homeArtifacts");
  if (!el) return;
  try {
    const res = await fetch(`${API}/api/jobs?status=done&limit=5`);
    const data = await res.json();
    const jobs = _extractJobs(data);
    _countBadge("homeArtifactsCount", jobs.length);
    el.innerHTML = jobs.length === 0
      ? `<div class="lm-home__empty">No finished jobs yet.</div>`
      : jobs.map((j) => _listRow(j, "done")).join("");
  } catch (e) {
    el.innerHTML = `<div class="lm-home__empty">Could not load artifacts.</div>`;
  }
}

async function _refreshFeed() {
  const el = document.getElementById("homeFeed");
  if (!el) return;
  try {
    // Derive activity from recent job events — fall back to recent jobs list
    const res = await fetch(`${API}/api/jobs?limit=10`);
    const data = await res.json();
    const jobs = _extractJobs(data).slice(0, 8);
    if (jobs.length === 0) {
      el.innerHTML = `<div class="lm-home__empty">Quiet.</div>`;
      return;
    }
    el.innerHTML = `<div class="lm-feed">${jobs.map(_feedRow).join("")}</div>`;
  } catch (e) {
    el.innerHTML = `<div class="lm-home__empty">Could not load activity.</div>`;
  }
}

async function _refreshFailures() {
  const el = document.getElementById("homeFailures");
  if (!el) return;
  try {
    const res = await fetch(`${API}/api/jobs?status=failed,error&limit=5`);
    const data = await res.json();
    const jobs = _extractJobs(data);
    _countBadge("homeFailuresCount", jobs.length);
    if (jobs.length === 0) {
      el.innerHTML = `<div class="lm-home__empty">No failures in the last 24h.</div>`;
      return;
    }
    el.innerHTML = jobs.map(_failureRow).join("");
  } catch (e) {
    el.innerHTML = `<div class="lm-home__empty">Could not load failures.</div>`;
  }
}

async function _refreshStats() {
  try {
    const res = await fetch(`${API}/api/jobs/stats`);
    if (!res.ok) return;
    const s = await res.json();
    _setText("homeStatusOk",    `${s.ok_count ?? s.done_count ?? 0} ok`);
    _setText("homeStatusWait",  `${s.waiting_count ?? s.reviewing_count ?? 0} waiting`);
    _setText("homeStatusFail",  `${s.failed_count ?? 0} failed`);
    _setText("homeStatusAvg",   _formatAvg(s.avg_duration_ms || s.avg_ms));
    _setText("homeStatusQueue", String(s.queue_depth ?? s.queued ?? "—"));
  } catch (_) { /* silent */ }
}

// ── Row templates ───────────────────────────────────────────────

function _listRow(job, forcedStatus) {
  const id     = job.id || job.job_id || "";
  const title  = job.title || job.name || job.description || job.prompt || "(untitled)";
  const status = forcedStatus || _statusOf(job);
  const meta   = _relativeTime(job.updated_at || job.created_at);
  return `
    <div class="lm-home__list-row" data-job-id="${escapeHtml(id)}" tabindex="0" role="button">
      <span class="lm-home__list-title" title="${escapeHtml(title)}">${escapeHtml(_truncate(title, 80))}</span>
      <span class="lm-home__list-meta lm-mono">${escapeHtml(meta)}</span>
      ${_statusChip(status)}
    </div>
  `;
}

function _feedRow(job) {
  const status = _statusOf(job);
  const title  = job.title || job.name || job.prompt || "(untitled)";
  const t      = _relativeTime(job.updated_at || job.created_at);
  return `
    <div class="lm-feed__row">
      <span class="lm-feed__time lm-mono">${escapeHtml(t)}</span>
      <span class="lm-feed__body">
        <span class="lm-feed__subject">${escapeHtml(_truncate(title, 60))}</span>
        ${_statusChip(status)}
      </span>
    </div>
  `;
}

function _failureRow(job) {
  const title  = job.title || job.name || "(untitled)";
  const reason = job.error || job.failure_reason || job.last_error || "Unknown error";
  return `
    <div class="lm-failure">
      <span class="material-symbols-outlined lm-failure__icon" aria-hidden="true">error</span>
      <div class="lm-failure__body">
        <div class="lm-failure__title">${escapeHtml(_truncate(title, 60))}</div>
        <div class="lm-failure__reason">${escapeHtml(_truncate(String(reason), 120))}</div>
      </div>
    </div>
  `;
}

function _statusChip(status) {
  const map = {
    running: ["running", "Running"],
    executing: ["running", "Executing"],
    planning: ["running", "Planning"],
    reviewing: ["waiting", "Reviewing"],
    waiting_approval: ["waiting", "Approval"],
    done: ["ok", "Done"],
    completed: ["ok", "Done"],
    success: ["ok", "Done"],
    failed: ["failed", "Failed"],
    error: ["failed", "Failed"],
    paused: ["paused", "Paused"],
    cancelled: ["paused", "Cancelled"],
  };
  const [variant, label] = map[status] || ["paused", status || "Unknown"];
  return `<span class="lm-status lm-status--${variant}"><span class="lm-status__dot" aria-hidden="true"></span>${escapeHtml(label)}</span>`;
}

// ── Helpers ─────────────────────────────────────────────────────

function _extractJobs(data) {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  if (Array.isArray(data.jobs)) return data.jobs;
  if (Array.isArray(data.items)) return data.items;
  if (Array.isArray(data.results)) return data.results;
  return [];
}

function _statusOf(job) {
  return (job.status || job.state || "").toLowerCase();
}

function _countBadge(id, count) {
  const el = document.getElementById(id);
  if (!el) return;
  if (count > 0) {
    el.textContent = String(count);
    el.hidden = false;
  } else {
    el.hidden = true;
  }
}

function _setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function _truncate(s, n) {
  const v = String(s || "");
  return v.length > n ? v.slice(0, n - 1) + "…" : v;
}

function _relativeTime(iso) {
  if (!iso) return "—";
  const t = typeof iso === "number" ? iso : Date.parse(iso);
  if (!t || isNaN(t)) return "—";
  const delta = Math.floor((Date.now() - t) / 1000);
  if (delta < 60)    return `${delta}s`;
  if (delta < 3600)  return `${Math.floor(delta / 60)}m`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h`;
  return `${Math.floor(delta / 86400)}d`;
}

function _formatAvg(ms) {
  if (!ms) return "—";
  const s = ms / 1000;
  if (s < 60)   return `${s.toFixed(1)}s`;
  if (s < 3600) return `${(s / 60).toFixed(1)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

async function _createJob(text) {
  const body = { title: text, description: text, prompt: text };
  const res = await fetch(`${API}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  // Clear the composer and refresh running list
  const input = document.getElementById("homeComposerInput");
  if (input) input.value = "";
  _refreshRunning();
  _refreshFeed();
}
