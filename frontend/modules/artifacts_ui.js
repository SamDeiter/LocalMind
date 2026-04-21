/**
 * artifacts_ui.js — Artifacts page (Phase 1 stub)
 * Shows recent outputs from completed jobs.
 */

import { API } from "./state.js";
import { escapeHtml } from "./utils.js";

let _inited = false;

export function initArtifactsUI() {
  if (_inited) return;
  _inited = true;

  const target = document.getElementById("artifactsContainer");
  if (!target) return;

  target.innerHTML = /* html */ `
    <header class="lm-page__header">
      <div>
        <div class="lm-page__eyebrow">Outputs</div>
        <h1 class="lm-page__title">Artifacts</h1>
        <p class="lm-page__subtitle">Files, reports, and outputs produced by completed jobs.</p>
      </div>
    </header>
    <section id="artifactsList" class="lm-home__row" style="grid-template-columns:1fr;"></section>
  `;

  _load();
}

async function _load() {
  const list = document.getElementById("artifactsList");
  if (!list) return;

  list.innerHTML = _skeleton(4);

  try {
    const res = await fetch(`${API}/api/jobs?status=done&limit=20`);
    const data = await res.json();
    const jobs = Array.isArray(data) ? data : (data.jobs || data.items || []);

    if (!jobs.length) {
      list.innerHTML = _empty();
      return;
    }

    list.innerHTML = jobs.map(_jobCard).join("");
  } catch (e) {
    list.innerHTML = `<div class="lm-stub"><div class="lm-stub__title">Couldn't load artifacts</div><div class="lm-stub__desc">${escapeHtml(String(e))}</div></div>`;
  }
}

function _jobCard(job) {
  const id     = job.id || job.job_id || "";
  const title  = job.title || job.name || "(untitled)";
  const outs   = Array.isArray(job.outputs) ? job.outputs : (Array.isArray(job.files) ? job.files : []);
  const when   = job.completed_at || job.updated_at || job.created_at || "";
  return /* html */ `
    <article class="lm-card" data-job-id="${escapeHtml(id)}">
      <header class="lm-card__header">
        <span class="material-symbols-outlined lm-mute" aria-hidden="true">folder</span>
        <h3 class="lm-card__title">${escapeHtml(title)}</h3>
        <span class="lm-card__count">${outs.length || 0}</span>
      </header>
      <div class="lm-card__body">
        ${outs.length ? outs.slice(0, 5).map((f) => _fileRow(id, f)).join("") : `<div class="lm-home__empty">No files attached.</div>`}
        <div class="lm-home__list-row lm-mute" style="justify-content:flex-end;">
          <span class="lm-mono">${escapeHtml(_fmt(when))}</span>
        </div>
      </div>
    </article>
  `;
}

function _fileRow(jobId, f) {
  const name = f.name || f.filename || f.path || "file";
  const size = f.size ? _fmtSize(f.size) : "";
  const fid  = f.id || f.file_id || name;
  const href = `${API}/api/jobs/${encodeURIComponent(jobId)}/files/${encodeURIComponent(fid)}`;
  return `
    <a class="lm-home__list-row" href="${href}" target="_blank" rel="noopener">
      <span class="lm-home__list-title">${escapeHtml(name)}</span>
      <span class="lm-home__list-meta lm-mono">${escapeHtml(size)}</span>
    </a>
  `;
}

function _skeleton(n) {
  let s = "";
  for (let i = 0; i < n; i++) s += `<div class="lm-skeleton" style="height:120px;"></div>`;
  return s;
}

function _empty() {
  return /* html */ `
    <div class="lm-stub">
      <span class="material-symbols-outlined lm-stub__icon" aria-hidden="true">folder_open</span>
      <div class="lm-stub__title">No artifacts yet</div>
      <div class="lm-stub__desc">Outputs from completed jobs will show here.</div>
    </div>
  `;
}

function _fmt(iso) {
  if (!iso) return "";
  const t = typeof iso === "number" ? iso : Date.parse(iso);
  if (!t || isNaN(t)) return "";
  return new Date(t).toLocaleString();
}

function _fmtSize(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 ? 0 : 1)} ${units[i]}`;
}
