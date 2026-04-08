/**
 * Cross-Project Hub — Sprint 5
 *
 * Registers multiple projects, scans them for metadata, mines cross-project
 * patterns, and surfaces actionable insights.  Pure vanilla JS, no frameworks.
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

// ── Constants ───────────────────────────────────────────────────
const POLL_MS = 60_000;

const PATTERN_COLORS = {
  dependency: "#34d399",
  code_pattern: "#818cf8",
  architecture: "#f59e0b",
  naming: "#a78bfa",
  issue: "#f87171",
};

const LANG_COLORS = {
  python: "#3572A5",
  javascript: "#f1e05a",
  typescript: "#3178c6",
  html: "#e34c26",
  css: "#563d7c",
  java: "#b07219",
  go: "#00ADD8",
  rust: "#dea584",
  ruby: "#701516",
  c: "#555555",
  cpp: "#f34b7d",
  shell: "#89e051",
  markdown: "#083fa1",
  json: "#292929",
  yaml: "#cb171e",
  sql: "#e38c00",
  php: "#4F5D95",
};

const DEFAULT_LANG_COLOR = "#64748b";

let _pollInterval = null;

// ── Public API ──────────────────────────────────────────────────

export function initHub() {
  _injectStyles();
  loadHub();
  if (_pollInterval) clearInterval(_pollInterval);
  _pollInterval = setInterval(loadHub, POLL_MS);
}

export async function loadHub() {
  const container = document.getElementById("crossProjectHub");
  if (!container) return;

  try {
    const [projRes, patRes, insRes] = await Promise.all([
      fetch(`${API}/api/hub/projects`),
      fetch(`${API}/api/hub/patterns`),
      fetch(`${API}/api/hub/insights`),
    ]);

    const projData = projRes.ok ? await projRes.json() : { projects: [] };
    const patData = patRes.ok ? await patRes.json() : { patterns: [] };
    const insData = insRes.ok ? await insRes.json() : { insights: { suggestions: [] } };

    _renderHub(container, projData, patData, insData);
  } catch {
    container.innerHTML = `<div class="hub-empty">Unable to load Hub data</div>`;
  }
}

// ── Render: full hub ────────────────────────────────────────────

function _renderHub(container, projData, patData, insData) {
  const projects = projData.projects || [];
  const patterns = patData.patterns || [];
  const insights = insData.insights || {};
  const suggestions = insights.suggestions || [];

  container.innerHTML = "";

  // Header
  const header = document.createElement("div");
  header.className = "hub-header";
  header.innerHTML = `
    <div class="hub-header-left">
      <span class="material-symbols-outlined hub-header-icon">hub</span>
      <h2 class="hub-title">Cross-Project Hub</h2>
      <span class="hub-count">${projects.length} project${projects.length !== 1 ? "s" : ""}</span>
    </div>
    <div class="hub-header-actions">
      <button class="hub-btn hub-btn-primary" id="hubRegisterToggle">
        <span class="material-symbols-outlined hub-btn-icon">add</span>Register Project
      </button>
      <button class="hub-btn hub-btn-secondary" id="hubMineBtn">
        <span class="material-symbols-outlined hub-btn-icon">search</span>Mine Patterns
      </button>
    </div>`;
  container.appendChild(header);

  // Register form (hidden)
  const formWrap = document.createElement("div");
  formWrap.className = "hub-register-form hub-hidden";
  formWrap.id = "hubRegisterForm";
  formWrap.innerHTML = `
    <div class="hub-form-title">
      <span class="material-symbols-outlined hub-form-icon">folder</span>
      Register a New Project
    </div>
    <div class="hub-form-fields">
      <div class="hub-field">
        <label class="hub-label">NAME</label>
        <input type="text" id="hubProjName" class="hub-input" placeholder="my-project" />
      </div>
      <div class="hub-field">
        <label class="hub-label">PATH</label>
        <input type="text" id="hubProjPath" class="hub-input" placeholder="/home/user/projects/my-project" />
      </div>
      <div class="hub-field hub-field-wide">
        <label class="hub-label">DESCRIPTION</label>
        <textarea id="hubProjDesc" class="hub-input hub-textarea" placeholder="Optional description..." rows="2"></textarea>
      </div>
    </div>
    <div class="hub-form-actions">
      <button class="hub-btn hub-btn-primary" id="hubSubmitProject">
        <span class="material-symbols-outlined hub-btn-icon">check</span>Register
      </button>
      <button class="hub-btn hub-btn-ghost" id="hubCancelRegister">Cancel</button>
    </div>`;
  container.appendChild(formWrap);

  // Projects grid
  const projSection = document.createElement("div");
  projSection.className = "hub-section";
  if (projects.length === 0) {
    projSection.innerHTML = `
      <div class="hub-empty-state">
        <span class="material-symbols-outlined hub-empty-icon">folder_off</span>
        <p class="hub-empty-text">No projects registered yet</p>
        <p class="hub-empty-sub">Click "Register Project" to add your first project</p>
      </div>`;
  } else {
    const grid = document.createElement("div");
    grid.className = "hub-projects-grid";
    projects.forEach((p) => grid.appendChild(_renderProjectCard(p)));
    projSection.appendChild(grid);
  }
  container.appendChild(projSection);

  // Patterns section
  const patSection = document.createElement("div");
  patSection.className = "hub-section";
  const patHeader = document.createElement("div");
  patHeader.className = "hub-section-header";
  patHeader.innerHTML = `
    <span class="material-symbols-outlined hub-section-icon">analytics</span>
    <h3 class="hub-section-title">Discovered Patterns</h3>
    <span class="hub-count">${patterns.length}</span>`;
  patSection.appendChild(patHeader);

  if (patterns.length === 0) {
    const empty = document.createElement("div");
    empty.className = "hub-empty-state hub-empty-sm";
    empty.innerHTML = `<p class="hub-empty-text">No patterns discovered</p><p class="hub-empty-sub">Click "Mine Patterns" to analyze cross-project similarities</p>`;
    patSection.appendChild(empty);
  } else {
    // Group by type
    const grouped = {};
    patterns.forEach((p) => {
      const t = p.pattern_type || "unknown";
      if (!grouped[t]) grouped[t] = [];
      grouped[t].push(p);
    });
    Object.entries(grouped).forEach(([type, items]) => {
      const group = document.createElement("div");
      group.className = "hub-pattern-group";
      const color = PATTERN_COLORS[type] || DEFAULT_LANG_COLOR;
      group.innerHTML = `<div class="hub-pattern-type-label" style="color:${color}"><span class="hub-pattern-dot" style="background:${color}"></span>${escapeHtml(type.replace(/_/g, " "))}</div>`;
      const list = document.createElement("div");
      list.className = "hub-pattern-list";
      items.forEach((pat) => list.appendChild(_renderPatternCard(pat)));
      group.appendChild(list);
      patSection.appendChild(group);
    });
  }
  container.appendChild(patSection);

  // Insights section
  const insSection = document.createElement("div");
  insSection.className = "hub-section";
  const insHeader = document.createElement("div");
  insHeader.className = "hub-section-header";
  insHeader.innerHTML = `
    <span class="material-symbols-outlined hub-section-icon">lightbulb</span>
    <h3 class="hub-section-title">Insights</h3>
    <span class="hub-count">${suggestions.length}</span>`;
  insSection.appendChild(insHeader);

  if (suggestions.length === 0) {
    const empty = document.createElement("div");
    empty.className = "hub-empty-state hub-empty-sm";
    empty.innerHTML = `<p class="hub-empty-text">No insights available</p><p class="hub-empty-sub">Insights are generated after pattern mining</p>`;
    insSection.appendChild(empty);
  } else {
    const insGrid = document.createElement("div");
    insGrid.className = "hub-insights-grid";
    suggestions.forEach((s) => {
      const card = document.createElement("div");
      card.className = "hub-insight-card";
      card.innerHTML = `
        <div class="hub-insight-content">${escapeHtml(typeof s === "string" ? s : s.text || s.description || JSON.stringify(s))}</div>`;
      insGrid.appendChild(card);
    });
    insSection.appendChild(insGrid);
  }
  container.appendChild(insSection);

  // Wire events
  _bindEvents();
}

// ── Render: project card ────────────────────────────────────────

function _renderProjectCard(project) {
  const card = document.createElement("div");
  card.className = "hub-project-card";

  const name = escapeHtml(project.name || "Untitled");
  const path = escapeHtml(project.path || "");
  const desc = project.description ? escapeHtml(project.description) : "";
  const fileCount = project.file_count ?? 0;
  const totalLines = project.total_lines ?? 0;
  const scannedAt = project.last_scanned_at ? _formatTimeAgo(project.last_scanned_at) : "never";

  card.innerHTML = `
    <div class="hub-card-header">
      <div class="hub-card-title-row">
        <span class="material-symbols-outlined hub-card-icon">folder</span>
        <span class="hub-card-name">${name}</span>
      </div>
      <div class="hub-card-actions">
        <button class="hub-card-btn hub-card-btn-scan" data-project-id="${project.id}" title="Scan project">
          <span class="material-symbols-outlined">refresh</span>
        </button>
        <button class="hub-card-btn hub-card-btn-remove" data-project-id="${project.id}" title="Remove project">
          <span class="material-symbols-outlined">delete</span>
        </button>
      </div>
    </div>
    <div class="hub-card-path">${path}</div>
    ${desc ? `<div class="hub-card-desc">${desc}</div>` : ""}
    <div class="hub-card-lang-bar" id="hubLangBar-${project.id}"></div>
    <div class="hub-card-stats">
      <span class="hub-stat"><span class="hub-stat-val">${fileCount.toLocaleString()}</span> files</span>
      <span class="hub-stat"><span class="hub-stat-val">${totalLines.toLocaleString()}</span> lines</span>
      <span class="hub-stat">scanned ${scannedAt}</span>
    </div>`;

  // Language bar
  const langBarContainer = card.querySelector(`#hubLangBar-${project.id}`);
  if (langBarContainer && project.language_breakdown) {
    langBarContainer.appendChild(_renderLanguageBar(project.language_breakdown));
  }

  return card;
}

// ── Render: pattern card ────────────────────────────────────────

function _renderPatternCard(pattern) {
  const card = document.createElement("div");
  card.className = "hub-pattern-card";
  const color = PATTERN_COLORS[pattern.pattern_type] || DEFAULT_LANG_COLOR;
  const confidence = pattern.confidence != null ? Math.round(pattern.confidence * 100) : null;
  const occurrences = pattern.occurrences ?? 0;
  const lastSeen = pattern.last_seen_at ? _formatTimeAgo(pattern.last_seen_at) : "";

  card.innerHTML = `
    <div class="hub-pattern-header">
      <span class="hub-pattern-title">${escapeHtml(pattern.title || "Untitled")}</span>
      ${confidence != null ? `<span class="hub-pattern-confidence" style="color:${color}">${confidence}%</span>` : ""}
    </div>
    ${pattern.description ? `<div class="hub-pattern-desc">${escapeHtml(pattern.description)}</div>` : ""}
    <div class="hub-pattern-meta">
      <span class="hub-pattern-occ">${occurrences} occurrence${occurrences !== 1 ? "s" : ""}</span>
      ${lastSeen ? `<span class="hub-pattern-seen">last seen ${lastSeen}</span>` : ""}
    </div>`;

  return card;
}

// ── Render: language bar ────────────────────────────────────────

function _renderLanguageBar(breakdown) {
  const bar = document.createElement("div");
  bar.className = "hub-lang-bar";

  let data;
  if (typeof breakdown === "string") {
    try { data = JSON.parse(breakdown); } catch { data = {}; }
  } else {
    data = breakdown || {};
  }

  const entries = Object.entries(data);
  if (entries.length === 0) return bar;

  const total = entries.reduce((sum, [, v]) => sum + v, 0);
  if (total === 0) return bar;

  // Sort descending
  entries.sort((a, b) => b[1] - a[1]);

  entries.forEach(([lang, count]) => {
    const pct = (count / total) * 100;
    if (pct < 0.5) return; // skip tiny slices
    const color = LANG_COLORS[lang.toLowerCase()] || DEFAULT_LANG_COLOR;
    const seg = document.createElement("div");
    seg.className = "hub-lang-seg";
    seg.style.width = `${pct}%`;
    seg.style.background = color;
    seg.title = `${escapeHtml(lang)}: ${pct.toFixed(1)}%`;
    bar.appendChild(seg);
  });

  // Legend
  const legend = document.createElement("div");
  legend.className = "hub-lang-legend";
  entries.slice(0, 5).forEach(([lang, count]) => {
    const pct = ((count / total) * 100).toFixed(1);
    const color = LANG_COLORS[lang.toLowerCase()] || DEFAULT_LANG_COLOR;
    const item = document.createElement("span");
    item.className = "hub-lang-legend-item";
    item.innerHTML = `<span class="hub-lang-dot" style="background:${color}"></span>${escapeHtml(lang)} ${pct}%`;
    legend.appendChild(item);
  });

  const wrapper = document.createElement("div");
  wrapper.appendChild(bar);
  wrapper.appendChild(legend);
  return wrapper;
}

// ── Events ──────────────────────────────────────────────────────

function _bindEvents() {
  // Toggle register form
  const toggleBtn = document.getElementById("hubRegisterToggle");
  const form = document.getElementById("hubRegisterForm");
  const cancelBtn = document.getElementById("hubCancelRegister");
  if (toggleBtn && form) {
    toggleBtn.addEventListener("click", () => form.classList.toggle("hub-hidden"));
  }
  if (cancelBtn && form) {
    cancelBtn.addEventListener("click", () => form.classList.add("hub-hidden"));
  }

  // Submit new project
  const submitBtn = document.getElementById("hubSubmitProject");
  if (submitBtn) {
    submitBtn.addEventListener("click", _handleRegisterProject);
  }

  // Mine patterns
  const mineBtn = document.getElementById("hubMineBtn");
  if (mineBtn) {
    mineBtn.addEventListener("click", _handleMinePatterns);
  }

  // Scan buttons
  document.querySelectorAll(".hub-card-btn-scan").forEach((btn) => {
    btn.addEventListener("click", () => _handleScanProject(btn.dataset.projectId));
  });

  // Remove buttons
  document.querySelectorAll(".hub-card-btn-remove").forEach((btn) => {
    btn.addEventListener("click", () => _handleRemoveProject(btn.dataset.projectId));
  });
}

async function _handleRegisterProject() {
  const nameEl = document.getElementById("hubProjName");
  const pathEl = document.getElementById("hubProjPath");
  const descEl = document.getElementById("hubProjDesc");
  if (!nameEl || !pathEl) return;

  const name = nameEl.value.trim();
  const path = pathEl.value.trim();
  const description = descEl ? descEl.value.trim() : "";

  if (!name || !path) {
    showToast("Name and path are required", "error");
    return;
  }

  try {
    const res = await fetch(`${API}/api/hub/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, path, description }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast("Project registered", "info");
      loadHub();
    } else {
      showToast(data.error || "Failed to register project", "error");
    }
  } catch {
    showToast("Network error registering project", "error");
  }
}

async function _handleScanProject(projectId) {
  if (!projectId) return;
  showToast("Scanning project...", "info");
  try {
    const res = await fetch(`${API}/api/hub/projects/${projectId}/scan`, { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      showToast("Scan complete", "info");
      loadHub();
    } else {
      showToast(data.error || "Scan failed", "error");
    }
  } catch {
    showToast("Network error during scan", "error");
  }
}

async function _handleRemoveProject(projectId) {
  if (!projectId) return;
  try {
    const res = await fetch(`${API}/api/hub/projects/${projectId}`, { method: "DELETE" });
    const data = await res.json();
    if (data.ok) {
      showToast("Project removed", "info");
      loadHub();
    } else {
      showToast(data.error || "Failed to remove project", "error");
    }
  } catch {
    showToast("Network error removing project", "error");
  }
}

async function _handleMinePatterns() {
  const btn = document.getElementById("hubMineBtn");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="material-symbols-outlined hub-btn-icon hub-spin">refresh</span>Mining...`;
  }
  try {
    const res = await fetch(`${API}/api/hub/mine`, { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      showToast("Pattern mining complete", "info");
      loadHub();
    } else {
      showToast(data.error || "Mining failed", "error");
    }
  } catch {
    showToast("Network error during mining", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<span class="material-symbols-outlined hub-btn-icon">search</span>Mine Patterns`;
    }
  }
}

// ── Helpers ─────────────────────────────────────────────────────

function _formatTimeAgo(ts) {
  if (!ts) return "unknown";
  // Accept both unix timestamps and ISO strings
  const date = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  const now = Date.now();
  const diff = Math.floor((now - date.getTime()) / 1000);

  if (diff < 0) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return date.toLocaleDateString();
}

// ── Styles ──────────────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById("hub-styles")) return;
  const style = document.createElement("style");
  style.id = "hub-styles";
  style.textContent = `
/* ── Hub Layout ─────────────────────────────────────────────── */
#crossProjectHub{padding:16px 20px;font-family:ui-sans-serif,system-ui,-apple-system,sans-serif}

.hub-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:10px}
.hub-header-left{display:flex;align-items:center;gap:8px}
.hub-header-icon{font-size:20px;color:#818cf8}
.hub-title{font-size:14px;font-weight:700;color:#e2e8f0;margin:0}
.hub-count{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#64748b;background:rgba(100,116,139,.15);padding:2px 8px;border-radius:9999px}
.hub-header-actions{display:flex;gap:8px}

/* ── Buttons ────────────────────────────────────────────────── */
.hub-btn{display:inline-flex;align-items:center;gap:4px;padding:6px 14px;border:none;border-radius:8px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;cursor:pointer;transition:background .15s,opacity .15s}
.hub-btn:disabled{opacity:.5;cursor:not-allowed}
.hub-btn-icon{font-size:14px}
.hub-btn-primary{background:rgba(99,102,241,.2);color:#818cf8;border:1px solid rgba(99,102,241,.25)}
.hub-btn-primary:hover:not(:disabled){background:rgba(99,102,241,.3)}
.hub-btn-secondary{background:rgba(100,116,139,.12);color:#94a3b8;border:1px solid rgba(100,116,139,.2)}
.hub-btn-secondary:hover:not(:disabled){background:rgba(100,116,139,.2)}
.hub-btn-ghost{background:transparent;color:#64748b;border:1px solid transparent}
.hub-btn-ghost:hover{color:#94a3b8}

/* ── Register Form ──────────────────────────────────────────── */
.hub-hidden{display:none!important}
.hub-register-form{background:rgba(30,41,59,.5);border:1px solid rgba(51,65,85,.3);border-radius:12px;padding:16px;margin-bottom:16px}
.hub-form-title{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:700;color:#e2e8f0;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px}
.hub-form-icon{font-size:16px;color:#818cf8}
.hub-form-fields{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.hub-field-wide{grid-column:1/-1}
.hub-label{display:block;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#64748b;margin-bottom:4px}
.hub-input{width:100%;background:rgba(15,23,42,.6);border:1px solid rgba(51,65,85,.4);border-radius:8px;padding:7px 10px;font-size:11px;color:#e2e8f0;outline:none;transition:border-color .15s;box-sizing:border-box}
.hub-input::placeholder{color:#475569}
.hub-input:focus{border-color:rgba(99,102,241,.5)}
.hub-textarea{resize:vertical;min-height:40px}
.hub-form-actions{display:flex;gap:8px}

/* ── Sections ───────────────────────────────────────────────── */
.hub-section{margin-bottom:20px}
.hub-section-header{display:flex;align-items:center;gap:6px;margin-bottom:10px}
.hub-section-icon{font-size:16px;color:#818cf8}
.hub-section-title{font-size:12px;font-weight:700;color:#e2e8f0;margin:0}

/* ── Projects Grid ──────────────────────────────────────────── */
.hub-projects-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}

.hub-project-card{background:rgba(30,41,59,.5);border:1px solid rgba(51,65,85,.3);border-radius:12px;padding:14px;transition:background .15s,border-color .15s}
.hub-project-card:hover{background:rgba(30,41,59,.7);border-color:rgba(99,102,241,.2)}

.hub-card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.hub-card-title-row{display:flex;align-items:center;gap:6px;min-width:0}
.hub-card-icon{font-size:16px;color:#818cf8}
.hub-card-name{font-size:12px;font-weight:700;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hub-card-actions{display:flex;gap:4px;flex-shrink:0}
.hub-card-btn{background:none;border:none;color:#475569;cursor:pointer;padding:3px;border-radius:6px;transition:color .15s,background .15s;display:flex;align-items:center}
.hub-card-btn .material-symbols-outlined{font-size:16px}
.hub-card-btn-scan:hover{color:#818cf8;background:rgba(99,102,241,.1)}
.hub-card-btn-remove:hover{color:#f87171;background:rgba(248,113,113,.1)}

.hub-card-path{font-size:10px;color:#64748b;font-family:ui-monospace,monospace;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hub-card-desc{font-size:10px;color:#94a3b8;margin-bottom:8px;line-height:1.4}

.hub-card-stats{display:flex;align-items:center;gap:10px;margin-top:8px}
.hub-stat{font-size:9px;color:#64748b;text-transform:uppercase;letter-spacing:.06em}
.hub-stat-val{color:#94a3b8;font-weight:700}

/* ── Language Bar ───────────────────────────────────────────── */
.hub-lang-bar{display:flex;height:4px;border-radius:2px;overflow:hidden;gap:1px}
.hub-lang-seg{border-radius:1px;transition:opacity .15s}
.hub-lang-seg:hover{opacity:.7}
.hub-lang-legend{display:flex;flex-wrap:wrap;gap:8px;margin-top:4px}
.hub-lang-legend-item{font-size:9px;color:#64748b;display:flex;align-items:center;gap:3px}
.hub-lang-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}

/* ── Patterns ───────────────────────────────────────────────── */
.hub-pattern-group{margin-bottom:12px}
.hub-pattern-type-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;display:flex;align-items:center;gap:6px;margin-bottom:6px}
.hub-pattern-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.hub-pattern-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px}

.hub-pattern-card{background:rgba(30,41,59,.4);border:1px solid rgba(51,65,85,.25);border-radius:10px;padding:10px 12px;transition:background .15s}
.hub-pattern-card:hover{background:rgba(30,41,59,.6)}
.hub-pattern-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}
.hub-pattern-title{font-size:11px;font-weight:600;color:#e2e8f0}
.hub-pattern-confidence{font-size:10px;font-weight:700;font-family:ui-monospace,monospace}
.hub-pattern-desc{font-size:10px;color:#94a3b8;line-height:1.4;margin-bottom:6px}
.hub-pattern-meta{display:flex;gap:10px}
.hub-pattern-occ,.hub-pattern-seen{font-size:9px;color:#64748b;text-transform:uppercase;letter-spacing:.06em}

/* ── Insights ───────────────────────────────────────────────── */
.hub-insights-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px}
.hub-insight-card{background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.12);border-radius:10px;padding:10px 12px;transition:background .15s}
.hub-insight-card:hover{background:rgba(99,102,241,.1)}
.hub-insight-content{font-size:11px;color:#cbd5e1;line-height:1.5}

/* ── Empty States ───────────────────────────────────────────── */
.hub-empty{color:#64748b;font-size:11px;text-align:center;padding:24px 0;font-family:ui-monospace,monospace}
.hub-empty-state{text-align:center;padding:32px 16px}
.hub-empty-sm{padding:16px}
.hub-empty-icon{font-size:32px;color:#334155;display:block;margin:0 auto 8px}
.hub-empty-text{font-size:11px;color:#64748b;margin:0 0 4px}
.hub-empty-sub{font-size:10px;color:#475569;margin:0}

/* ── Utilities ──────────────────────────────────────────────── */
.hub-spin{animation:hub-spin 1s linear infinite}
@keyframes hub-spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
`;
  document.head.appendChild(style);
}
