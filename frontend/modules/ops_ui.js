/**
 * ops_ui.js — Operations page (Phase 1 stub)
 * Landing for swarm + monitoring + eval + time machine (surfaced as health).
 */

import { API } from "./state.js";

let _inited = false;
let _pollId = null;

export function initOpsUI() {
  if (_inited) return;
  _inited = true;

  const target = document.getElementById("opsContainer");
  if (!target) return;

  target.innerHTML = /* html */ `
    <header class="lm-page__header">
      <div>
        <div class="lm-page__eyebrow">Infrastructure</div>
        <h1 class="lm-page__title">Operations</h1>
        <p class="lm-page__subtitle">Workers, models, hardware, and run-time health.</p>
      </div>
      <div class="lm-page__actions">
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="opsRefreshBtn">
          <span class="material-symbols-outlined" aria-hidden="true">refresh</span> Refresh
        </button>
      </div>
    </header>

    <section class="lm-home__row lm-home__row--3">
      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">memory</span>
          <h3 class="lm-card__title">Hardware</h3>
        </header>
        <div class="lm-card__body" id="opsHardware">—</div>
      </article>

      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">deployed_code</span>
          <h3 class="lm-card__title">Models</h3>
        </header>
        <div class="lm-card__body" id="opsModels">—</div>
      </article>

      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">group</span>
          <h3 class="lm-card__title">Workers</h3>
        </header>
        <div class="lm-card__body" id="opsWorkers">—</div>
      </article>
    </section>

    <section class="lm-home__row lm-home__row--2">
      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">queue</span>
          <h3 class="lm-card__title">Queue</h3>
        </header>
        <div class="lm-card__body" id="opsQueue">—</div>
      </article>
      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">monitoring</span>
          <h3 class="lm-card__title">Telemetry</h3>
        </header>
        <div class="lm-card__body" id="opsTelemetry">
          <div class="lm-home__empty">Monitoring charts moving here in Phase 3.</div>
        </div>
      </article>
    </section>
  `;

  document.getElementById("opsRefreshBtn")?.addEventListener("click", _refresh);
  _refresh();
  _startPolling();
}

function _startPolling() {
  if (_pollId) clearInterval(_pollId);
  _pollId = setInterval(() => {
    if (!document.hidden) _refresh();
  }, 8000);
}

async function _refresh() {
  await Promise.allSettled([_refreshHardware(), _refreshModels(), _refreshWorkers(), _refreshQueue()]);
}

async function _refreshHardware() {
  const el = document.getElementById("opsHardware");
  if (!el) return;
  try {
    const r = await fetch(`${API}/api/hardware`);
    if (!r.ok) throw new Error("HTTP " + r.status);
    const h = await r.json();
    el.innerHTML = `
      <div class="lm-home__list-row"><span class="lm-mute">CPU</span><span class="lm-mono">${_fmt(h.cpu)}%</span></div>
      <div class="lm-home__list-row"><span class="lm-mute">RAM</span><span class="lm-mono">${_fmt(h.ram || h.memory)}%</span></div>
      <div class="lm-home__list-row"><span class="lm-mute">GPU</span><span class="lm-mono">${_fmt(h.gpu)}%</span></div>
      <div class="lm-home__list-row"><span class="lm-mute">VRAM</span><span class="lm-mono">${_fmt(h.vram)}%</span></div>
    `;
  } catch (_) {
    el.innerHTML = `<div class="lm-home__empty">Hardware service unavailable.</div>`;
  }
}

async function _refreshModels() {
  const el = document.getElementById("opsModels");
  if (!el) return;
  try {
    const r = await fetch(`${API}/api/models`);
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    const list = Array.isArray(data) ? data : (data.models || []);
    if (!list.length) {
      el.innerHTML = `<div class="lm-home__empty">No models registered.</div>`;
      return;
    }
    el.innerHTML = list.slice(0, 6).map((m) => `
      <div class="lm-home__list-row">
        <span class="lm-home__list-title lm-mono">${_esc(m.name || m.id || "model")}</span>
        <span class="lm-status lm-status--${m.loaded ? "ok" : "paused"}"><span class="lm-status__dot"></span>${m.loaded ? "loaded" : "idle"}</span>
      </div>
    `).join("");
  } catch (_) {
    el.innerHTML = `<div class="lm-home__empty">Model router unavailable.</div>`;
  }
}

async function _refreshWorkers() {
  const el = document.getElementById("opsWorkers");
  if (!el) return;
  try {
    const r = await fetch(`${API}/api/swarm/status`);
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    const agents = data.agents || data.workers || [];
    if (!agents.length) {
      el.innerHTML = `<div class="lm-home__empty">No workers active.</div>`;
      return;
    }
    el.innerHTML = agents.slice(0, 6).map((a) => `
      <div class="lm-home__list-row">
        <span class="lm-home__list-title lm-mono">${_esc(a.id || a.name || "worker")}</span>
        <span class="lm-status lm-status--${a.busy ? "running" : "ok"}"><span class="lm-status__dot"></span>${a.busy ? "busy" : "idle"}</span>
      </div>
    `).join("");
  } catch (_) {
    el.innerHTML = `<div class="lm-home__empty">Swarm status unavailable.</div>`;
  }
}

async function _refreshQueue() {
  const el = document.getElementById("opsQueue");
  if (!el) return;
  try {
    const r = await fetch(`${API}/api/jobs/stats`);
    if (!r.ok) throw new Error("HTTP " + r.status);
    const s = await r.json();
    el.innerHTML = `
      <div class="lm-home__list-row"><span class="lm-mute">Queued</span><span class="lm-mono">${s.queued ?? s.queue_depth ?? 0}</span></div>
      <div class="lm-home__list-row"><span class="lm-mute">Running</span><span class="lm-mono">${s.running_count ?? s.running ?? 0}</span></div>
      <div class="lm-home__list-row"><span class="lm-mute">Waiting</span><span class="lm-mono">${s.waiting_count ?? 0}</span></div>
      <div class="lm-home__list-row"><span class="lm-mute">Failed (24h)</span><span class="lm-mono">${s.failed_count ?? 0}</span></div>
    `;
  } catch (_) {
    el.innerHTML = `<div class="lm-home__empty">Stats unavailable.</div>`;
  }
}

function _fmt(v) { return (typeof v === "number") ? v.toFixed(0) : "—"; }
function _esc(s) { return String(s || "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c])); }
