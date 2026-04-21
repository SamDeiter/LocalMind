/**
 * artifacts_ui.js — Artifact Center (Phase 2)
 *
 * Two-pane layout:
 *   left:  list of all artifacts across completed jobs, searchable + filterable by kind
 *   right: preview of the selected artifact (text / markdown / image / download link)
 *
 * Data: GET /api/jobs?status=done&limit=50 → flattens each job's outputs/files.
 *       Each row links to /api/jobs/{jobId}/files/{fileId} for download.
 */

import { API } from "./state.js";
import { escapeHtml } from "./utils.js";

const KIND_FILTERS = [
  { key: "all",   label: "All" },
  { key: "text",  label: "Text" },
  { key: "code",  label: "Code" },
  { key: "data",  label: "Data" },
  { key: "image", label: "Image" },
  { key: "pdf",   label: "PDF" },
];

let _inited = false;
let _artifacts = [];          // flattened [{ jobId, jobTitle, name, kind, size, fid, completed_at }]
let _filter = "all";
let _query = "";
let _selected = null;         // artifact row key (jobId + "/" + fid)

// ── Public API ──────────────────────────────────────────────────

export function initArtifactsUI() {
  if (_inited) return;
  _inited = true;

  const target = document.getElementById("artifactsContainer");
  if (!target) return;

  target.classList.add("lm-artifacts-page");
  target.innerHTML = /* html */ `
    <header class="lm-page__header">
      <div>
        <div class="lm-page__eyebrow">Outputs</div>
        <h1 class="lm-page__title">Artifacts</h1>
        <p class="lm-page__subtitle">Files, reports, and outputs produced by completed jobs.</p>
      </div>
      <div class="lm-page__actions">
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="artifactsRefresh" aria-label="Refresh">
          <span class="material-symbols-outlined" aria-hidden="true">refresh</span> Refresh
        </button>
      </div>
    </header>

    <div class="lm-artifacts-page__controls">
      <input
        id="artifactsSearch"
        type="search"
        class="lm-artifacts-page__search"
        placeholder="Search artifacts by name or job…"
        autocomplete="off"
      />
      <div class="lm-artifacts-page__filters" role="tablist" aria-label="Filter by kind">
        ${KIND_FILTERS.map((f) => `
          <button
            type="button"
            class="lm-artifacts-page__filter"
            role="tab"
            data-filter="${escapeHtml(f.key)}"
            aria-pressed="${f.key === "all" ? "true" : "false"}"
          >${escapeHtml(f.label)}</button>
        `).join("")}
      </div>
    </div>

    <div class="lm-artifacts-page__body">
      <aside class="lm-artifacts-page__list" id="artifactsList" aria-label="Artifacts list">
        ${_skeletonRows(6)}
      </aside>
      <section class="lm-artifacts-page__preview" id="artifactsPreview" aria-label="Preview">
        <button type="button" class="lm-artifacts-page__preview-close" id="artifactsPreviewClose" aria-label="Close preview">
          <span class="material-symbols-outlined" aria-hidden="true">close</span>
        </button>
        <div class="lm-artifacts-page__preview-empty">
          Select an artifact to preview it here.
        </div>
      </section>
    </div>
  `;

  _bindEvents(target);
  _load();
}

// ── Events ──────────────────────────────────────────────────────

function _bindEvents(root) {
  root.querySelector("#artifactsRefresh")?.addEventListener("click", _load);

  root.querySelector("#artifactsSearch")?.addEventListener("input", (e) => {
    _query = String(e.target.value || "").trim().toLowerCase();
    _renderList();
  });

  root.querySelectorAll(".lm-artifacts-page__filter").forEach((btn) => {
    btn.addEventListener("click", () => {
      _filter = btn.dataset.filter || "all";
      root.querySelectorAll(".lm-artifacts-page__filter").forEach((b) => {
        b.setAttribute("aria-pressed", String(b === btn));
      });
      _renderList();
    });
  });

  root.querySelector("#artifactsPreviewClose")?.addEventListener("click", () => {
    root.removeAttribute("data-preview");
  });
}

// ── Data ────────────────────────────────────────────────────────

async function _load() {
  const listEl = document.getElementById("artifactsList");
  if (!listEl) return;
  listEl.innerHTML = _skeletonRows(6);

  try {
    const res = await fetch(`${API}/api/jobs?status=done&limit=50`);
    const data = await res.json();
    const jobs = Array.isArray(data) ? data : (data.jobs || data.items || data.results || []);

    _artifacts = [];
    for (const job of jobs) {
      const jobId = job.id || job.job_id;
      const jobTitle = job.title || job.name || "(untitled)";
      const when = job.completed_at || job.updated_at || job.created_at || "";
      const files = job.outputs || job.files || job.artifacts || [];
      if (!Array.isArray(files)) continue;
      for (const f of files) {
        const name = f.name || f.filename || f.path || "file";
        const fid  = f.id || f.file_id || name;
        _artifacts.push({
          jobId,
          jobTitle,
          name,
          kind: f.kind || f.type || _inferKind(name),
          size: f.size || 0,
          fid,
          completed_at: when,
        });
      }
    }

    _renderList();
  } catch (e) {
    listEl.innerHTML = `<div class="lm-stub"><div class="lm-stub__title">Couldn't load artifacts</div><div class="lm-stub__desc">${escapeHtml(String(e))}</div></div>`;
  }
}

// ── Render ──────────────────────────────────────────────────────

function _renderList() {
  const listEl = document.getElementById("artifactsList");
  if (!listEl) return;

  const filtered = _artifacts.filter((a) => {
    if (_filter !== "all" && a.kind !== _filter) return false;
    if (_query && !(a.name.toLowerCase().includes(_query) || a.jobTitle.toLowerCase().includes(_query))) return false;
    return true;
  });

  if (!filtered.length) {
    listEl.innerHTML = `<div class="lm-stub">
      <span class="material-symbols-outlined lm-stub__icon" aria-hidden="true">folder_open</span>
      <div class="lm-stub__title">${_artifacts.length ? "No matches" : "No artifacts yet"}</div>
      <div class="lm-stub__desc">${_artifacts.length
        ? "Try a different filter or clear the search."
        : "Outputs from completed jobs will show up here."}</div>
    </div>`;
    return;
  }

  listEl.innerHTML = filtered.map(_row).join("");

  listEl.querySelectorAll(".lm-artifacts-page__row").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.key;
      _selected = key;
      listEl.querySelectorAll(".lm-artifacts-page__row").forEach((b) => {
        b.setAttribute("aria-selected", String(b.dataset.key === key));
      });
      const artifact = _artifacts.find((a) => `${a.jobId}/${a.fid}` === key);
      if (artifact) {
        _renderPreview(artifact);
        document.getElementById("artifactsContainer")?.setAttribute("data-preview", "open");
      }
    });
  });
}

function _row(a) {
  const key = `${a.jobId}/${a.fid}`;
  const selected = _selected === key;
  return `
    <div
      class="lm-artifacts-page__row"
      data-key="${escapeHtml(key)}"
      role="button"
      tabindex="0"
      aria-selected="${selected ? "true" : "false"}"
    >
      <span class="material-symbols-outlined lm-mute" aria-hidden="true">${_iconFor(a.kind)}</span>
      <span class="lm-artifacts-page__row-name" title="${escapeHtml(a.name)}">${escapeHtml(a.name)}</span>
      <span class="lm-artifacts-page__row-kind">${escapeHtml(a.kind)}</span>
      <span class="lm-artifacts-page__row-job" title="${escapeHtml(a.jobTitle)}">${escapeHtml(a.jobTitle)}</span>
      <span class="lm-artifacts-page__row-size">${escapeHtml(a.size ? _fmtSize(a.size) : "")}</span>
    </div>
  `;
}

async function _renderPreview(a) {
  const box = document.getElementById("artifactsPreview");
  if (!box) return;

  const href = `${API}/api/jobs/${encodeURIComponent(a.jobId)}/files/${encodeURIComponent(a.fid)}`;

  box.innerHTML = `
    <div class="lm-artifacts-page__preview-head">
      <div style="min-width:0;flex:1;">
        <div class="lm-artifacts-page__preview-name">${escapeHtml(a.name)}</div>
        <div class="lm-artifacts-page__preview-meta">
          <span>${escapeHtml(a.kind)}</span>
          ${a.size ? `<span class="lm-mute">·</span><span class="lm-mono">${escapeHtml(_fmtSize(a.size))}</span>` : ""}
          <span class="lm-mute">·</span>
          <span class="lm-mono">${escapeHtml(a.jobTitle)}</span>
        </div>
      </div>
      <a class="lm-btn lm-btn--ghost lm-btn--sm" href="${href}" target="_blank" rel="noopener">
        <span class="material-symbols-outlined" aria-hidden="true">download</span> Download
      </a>
    </div>
    <div class="lm-artifacts-page__preview-body" id="artifactsPreviewBody">
      <div class="lm-artifacts-page__preview-empty">Loading preview…</div>
    </div>
  `;

  const bodyEl = document.getElementById("artifactsPreviewBody");
  if (!bodyEl) return;

  // Text / code / data → fetch as text and render inline
  if (["text", "code", "data"].includes(a.kind)) {
    try {
      const r = await fetch(href);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const text = await r.text();
      const capped = text.length > 200_000
        ? text.slice(0, 200_000) + "\n\n… (truncated — download to see full file)"
        : text;

      if (a.kind === "text" && typeof window !== "undefined" && window.marked && /\.(md|markdown)$/i.test(a.name)) {
        bodyEl.innerHTML = window.marked.parse(capped);
      } else {
        bodyEl.innerHTML = `<pre>${escapeHtml(capped)}</pre>`;
      }
      return;
    } catch (e) {
      bodyEl.innerHTML = `<div class="lm-artifacts-page__preview-empty">Couldn't load preview. ${escapeHtml(String(e))}</div>`;
      return;
    }
  }

  // Image → inline
  if (a.kind === "image") {
    bodyEl.innerHTML = `<img src="${href}" alt="${escapeHtml(a.name)}" />`;
    return;
  }

  // PDF / binary / unknown → download prompt
  bodyEl.innerHTML = `
    <div class="lm-artifacts-page__preview-empty">
      Preview not available for this type.<br/>
      <a class="lm-btn lm-btn--primary lm-btn--sm" style="margin-top:12px;" href="${href}" target="_blank" rel="noopener">
        <span class="material-symbols-outlined" aria-hidden="true">download</span> Download
      </a>
    </div>
  `;
}

// ── Helpers ─────────────────────────────────────────────────────

function _skeletonRows(n) {
  let s = "";
  for (let i = 0; i < n; i++) s += `<div class="lm-skeleton" style="height:48px;"></div>`;
  return s;
}

function _inferKind(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["md", "markdown", "txt", "log"].includes(ext)) return "text";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "image";
  if (["py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "c", "cpp", "h", "rb", "sh"].includes(ext)) return "code";
  if (["pdf"].includes(ext)) return "pdf";
  if (["csv", "json", "yaml", "yml", "xml", "tsv"].includes(ext)) return "data";
  return "file";
}

function _iconFor(kind) {
  return {
    text:  "description",
    image: "image",
    code:  "code",
    pdf:   "picture_as_pdf",
    data:  "dataset",
    file:  "draft",
  }[kind] || "draft";
}

function _fmtSize(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 ? 0 : 1)} ${units[i]}`;
}
