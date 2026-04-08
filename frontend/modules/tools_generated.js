/**
 * Generated Tools — Sprint 6
 *
 * Self-extending tools panel: list, create, validate, template-generate,
 * reload, and remove dynamically generated tools.  Pure vanilla JS.
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

// ── Constants ───────────────────────────────────────────────────
const POLL_MS = 30_000;

const STATUS_COLORS = {
  active: "#34d399",
  disabled: "#facc15",
  failed: "#f87171",
};
const DEFAULT_STATUS_COLOR = "#64748b";

let _pollInterval = null;

// ── Public API ──────────────────────────────────────────────────

export function initGeneratedTools() {
  _injectStyles();
  loadGeneratedTools();
  if (_pollInterval) clearInterval(_pollInterval);
  _pollInterval = setInterval(loadGeneratedTools, POLL_MS);
}

export async function loadGeneratedTools() {
  const container = document.getElementById("generatedToolsPanel");
  if (!container) return;

  try {
    const res = await fetch(`${API}/api/tools/generated`);
    const data = res.ok ? await res.json() : { tools: [], count: 0 };
    _renderPanel(container, data);
  } catch {
    container.innerHTML = `<div class="gt-empty">Unable to load generated tools</div>`;
  }
}

// ── Render: full panel ─────────────────────────────────────────

function _renderPanel(container, data) {
  const tools = data.tools || [];
  container.innerHTML = "";

  // Header
  const header = document.createElement("div");
  header.className = "gt-header";
  header.innerHTML = `
    <div class="gt-header-left">
      <span class="material-symbols-outlined gt-header-icon">extension</span>
      <h2 class="gt-title">Generated Tools</h2>
      <span class="gt-count">${tools.length}</span>
    </div>
    <div class="gt-header-actions">
      <button class="gt-btn gt-btn-primary" id="gtCreateToggle">
        <span class="material-symbols-outlined gt-btn-icon">add</span>New Tool
      </button>
      <button class="gt-btn gt-btn-secondary" id="gtTemplateToggle">
        <span class="material-symbols-outlined gt-btn-icon">code</span>Template
      </button>
    </div>`;
  container.appendChild(header);

  // Create form (hidden)
  container.appendChild(_buildCreateForm());

  // Template form (hidden)
  container.appendChild(_buildTemplateForm());

  // Tool list
  const section = document.createElement("div");
  section.className = "gt-section";
  if (tools.length === 0) {
    section.innerHTML = `
      <div class="gt-empty-state">
        <span class="material-symbols-outlined gt-empty-icon">extension_off</span>
        <p class="gt-empty-text">No generated tools yet</p>
        <p class="gt-empty-sub">Click "New Tool" to create your first self-extending tool</p>
      </div>`;
  } else {
    const grid = document.createElement("div");
    grid.className = "gt-grid";
    tools.forEach((t) => grid.appendChild(_renderToolCard(t)));
    section.appendChild(grid);
  }
  container.appendChild(section);

  _bindEvents();
}

// ── Render: tool card ──────────────────────────────────────────

function _renderToolCard(tool) {
  const card = document.createElement("div");
  card.className = "gt-card";

  const name = escapeHtml(tool.tool_name || "unnamed");
  const desc = tool.description ? escapeHtml(tool.description) : "";
  const status = tool.status || "active";
  const statusColor = STATUS_COLORS[status] || DEFAULT_STATUS_COLOR;
  const hash = tool.code_hash ? escapeHtml(tool.code_hash.substring(0, 8)) : "";
  const createdAt = tool.created_at ? _formatTimeAgo(tool.created_at) : "unknown";
  const toolName = escapeHtml(tool.tool_name || "");

  card.innerHTML = `
    <div class="gt-card-header">
      <div class="gt-card-title-row">
        <span class="material-symbols-outlined gt-card-icon">extension</span>
        <span class="gt-card-name">${name}</span>
        <span class="gt-card-status" style="color:${statusColor};border-color:${statusColor}30">${escapeHtml(status)}</span>
      </div>
      <div class="gt-card-actions">
        <button class="gt-card-btn gt-card-btn-reload" data-tool-name="${toolName}" title="Reload tool">
          <span class="material-symbols-outlined">refresh</span>
        </button>
        <button class="gt-card-btn gt-card-btn-remove" data-tool-name="${toolName}" title="Remove tool">
          <span class="material-symbols-outlined">delete</span>
        </button>
      </div>
    </div>
    ${desc ? `<div class="gt-card-desc">${desc}</div>` : ""}
    <div class="gt-card-meta">
      ${hash ? `<span class="gt-meta-item"><span class="gt-meta-label">hash</span> ${hash}</span>` : ""}
      <span class="gt-meta-item">created ${createdAt}</span>
    </div>`;

  return card;
}

// ── Build: create form ─────────────────────────────────────────

function _buildCreateForm() {
  const wrap = document.createElement("div");
  wrap.className = "gt-form gt-hidden";
  wrap.id = "gtCreateForm";
  wrap.innerHTML = `
    <div class="gt-form-title">
      <span class="material-symbols-outlined gt-form-icon">add_circle</span>
      Create New Tool
    </div>
    <div class="gt-form-fields">
      <div class="gt-field">
        <label class="gt-label">TOOL NAME</label>
        <input type="text" id="gtNewName" class="gt-input" placeholder="my_custom_tool" />
      </div>
      <div class="gt-field">
        <label class="gt-label">DESCRIPTION</label>
        <input type="text" id="gtNewDesc" class="gt-input" placeholder="What does this tool do?" />
      </div>
      <div class="gt-field gt-field-wide">
        <label class="gt-label">PYTHON CODE</label>
        <textarea id="gtNewCode" class="gt-input gt-code-textarea" placeholder="class MyTool:\n    ..." rows="10"></textarea>
      </div>
    </div>
    <div class="gt-form-actions">
      <button class="gt-btn gt-btn-primary" id="gtSubmitCreate">
        <span class="material-symbols-outlined gt-btn-icon">check</span>Create
      </button>
      <button class="gt-btn gt-btn-secondary" id="gtValidateCode">
        <span class="material-symbols-outlined gt-btn-icon">verified</span>Validate
      </button>
      <button class="gt-btn gt-btn-ghost" id="gtCancelCreate">Cancel</button>
    </div>
    <div id="gtValidationResult" class="gt-validation-result gt-hidden"></div>`;
  return wrap;
}

// ── Build: template form ───────────────────────────────────────

function _buildTemplateForm() {
  const wrap = document.createElement("div");
  wrap.className = "gt-form gt-hidden";
  wrap.id = "gtTemplateForm";
  wrap.innerHTML = `
    <div class="gt-form-title">
      <span class="material-symbols-outlined gt-form-icon">code</span>
      Generate Template
    </div>
    <div class="gt-form-fields">
      <div class="gt-field">
        <label class="gt-label">TOOL NAME</label>
        <input type="text" id="gtTplName" class="gt-input" placeholder="my_tool" />
      </div>
      <div class="gt-field">
        <label class="gt-label">DESCRIPTION</label>
        <input type="text" id="gtTplDesc" class="gt-input" placeholder="What should it do?" />
      </div>
      <div class="gt-field gt-field-wide">
        <label class="gt-label">PARAM SCHEMA (JSON)</label>
        <textarea id="gtTplSchema" class="gt-input gt-code-textarea" placeholder='{"query": "str", "limit": "int"}' rows="3"></textarea>
      </div>
    </div>
    <div class="gt-form-actions">
      <button class="gt-btn gt-btn-primary" id="gtSubmitTemplate">
        <span class="material-symbols-outlined gt-btn-icon">auto_fix_high</span>Generate
      </button>
      <button class="gt-btn gt-btn-ghost" id="gtCancelTemplate">Cancel</button>
    </div>
    <div id="gtTemplateOutput" class="gt-hidden">
      <label class="gt-label">GENERATED CODE</label>
      <textarea id="gtTemplateCode" class="gt-input gt-code-textarea" rows="12" readonly></textarea>
      <div class="gt-form-actions" style="margin-top:8px">
        <button class="gt-btn gt-btn-primary" id="gtUseTemplate">
          <span class="material-symbols-outlined gt-btn-icon">content_copy</span>Use in Create Form
        </button>
      </div>
    </div>`;
  return wrap;
}

// ── Events ─────────────────────────────────────────────────────

function _bindEvents() {
  // Toggle create form
  const createToggle = document.getElementById("gtCreateToggle");
  const createForm = document.getElementById("gtCreateForm");
  const cancelCreate = document.getElementById("gtCancelCreate");
  if (createToggle && createForm) {
    createToggle.addEventListener("click", () => createForm.classList.toggle("gt-hidden"));
  }
  if (cancelCreate && createForm) {
    cancelCreate.addEventListener("click", () => createForm.classList.add("gt-hidden"));
  }

  // Toggle template form
  const tplToggle = document.getElementById("gtTemplateToggle");
  const tplForm = document.getElementById("gtTemplateForm");
  const cancelTpl = document.getElementById("gtCancelTemplate");
  if (tplToggle && tplForm) {
    tplToggle.addEventListener("click", () => tplForm.classList.toggle("gt-hidden"));
  }
  if (cancelTpl && tplForm) {
    cancelTpl.addEventListener("click", () => tplForm.classList.add("gt-hidden"));
  }

  // Create tool
  const submitCreate = document.getElementById("gtSubmitCreate");
  if (submitCreate) submitCreate.addEventListener("click", _handleCreate);

  // Validate code
  const validateBtn = document.getElementById("gtValidateCode");
  if (validateBtn) validateBtn.addEventListener("click", _handleValidate);

  // Generate template
  const submitTpl = document.getElementById("gtSubmitTemplate");
  if (submitTpl) submitTpl.addEventListener("click", _handleTemplate);

  // Use template in create form
  const useTpl = document.getElementById("gtUseTemplate");
  if (useTpl) useTpl.addEventListener("click", _handleUseTemplate);

  // Reload buttons
  document.querySelectorAll(".gt-card-btn-reload").forEach((btn) => {
    btn.addEventListener("click", () => _handleReload(btn.dataset.toolName));
  });

  // Remove buttons
  document.querySelectorAll(".gt-card-btn-remove").forEach((btn) => {
    btn.addEventListener("click", () => _handleRemove(btn.dataset.toolName));
  });
}

async function _handleCreate() {
  const nameEl = document.getElementById("gtNewName");
  const descEl = document.getElementById("gtNewDesc");
  const codeEl = document.getElementById("gtNewCode");
  if (!nameEl || !codeEl) return;

  const tool_name = nameEl.value.trim();
  const python_code = codeEl.value.trim();
  const description = descEl ? descEl.value.trim() : "";

  if (!tool_name || !python_code) {
    showToast("Tool name and code are required", "error");
    return;
  }

  try {
    const res = await fetch(`${API}/api/tools/generated`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_name, python_code, description }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast("Tool created successfully", "info");
      loadGeneratedTools();
    } else {
      showToast(data.error || "Failed to create tool", "error");
    }
  } catch {
    showToast("Network error creating tool", "error");
  }
}

async function _handleValidate() {
  const codeEl = document.getElementById("gtNewCode");
  const resultEl = document.getElementById("gtValidationResult");
  if (!codeEl || !resultEl) return;

  const python_code = codeEl.value.trim();
  if (!python_code) {
    showToast("Enter code to validate", "error");
    return;
  }

  try {
    const res = await fetch(`${API}/api/tools/generated/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ python_code }),
    });
    const data = await res.json();
    resultEl.classList.remove("gt-hidden");

    if (data.valid) {
      resultEl.className = "gt-validation-result gt-valid";
      resultEl.innerHTML = `<span class="material-symbols-outlined" style="font-size:14px;color:#34d399">check_circle</span> Valid — class: <strong>${escapeHtml(data.class_name || "detected")}</strong>`;
    } else {
      const errors = (data.errors || []).map((e) => escapeHtml(e)).join("<br>");
      resultEl.className = "gt-validation-result gt-invalid";
      resultEl.innerHTML = `<span class="material-symbols-outlined" style="font-size:14px;color:#f87171">error</span> Invalid<br><span class="gt-error-details">${errors}</span>`;
    }
  } catch {
    showToast("Network error validating code", "error");
  }
}

async function _handleTemplate() {
  const nameEl = document.getElementById("gtTplName");
  const descEl = document.getElementById("gtTplDesc");
  const schemaEl = document.getElementById("gtTplSchema");
  const outputWrap = document.getElementById("gtTemplateOutput");
  const codeOut = document.getElementById("gtTemplateCode");
  if (!nameEl || !descEl) return;

  const tool_name = nameEl.value.trim();
  const description = descEl.value.trim();
  let param_schema = {};

  if (schemaEl && schemaEl.value.trim()) {
    try {
      param_schema = JSON.parse(schemaEl.value.trim());
    } catch {
      showToast("Invalid JSON in param schema", "error");
      return;
    }
  }

  if (!tool_name) {
    showToast("Tool name is required", "error");
    return;
  }

  try {
    const res = await fetch(`${API}/api/tools/generated/template`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_name, description, param_schema }),
    });
    const data = await res.json();
    if (data.ok && codeOut && outputWrap) {
      codeOut.value = data.code;
      outputWrap.classList.remove("gt-hidden");
    } else {
      showToast(data.error || "Failed to generate template", "error");
    }
  } catch {
    showToast("Network error generating template", "error");
  }
}

function _handleUseTemplate() {
  const tplCode = document.getElementById("gtTemplateCode");
  const createCode = document.getElementById("gtNewCode");
  const createForm = document.getElementById("gtCreateForm");
  const tplForm = document.getElementById("gtTemplateForm");

  if (tplCode && createCode) {
    createCode.value = tplCode.value;
  }
  if (tplForm) tplForm.classList.add("gt-hidden");
  if (createForm) createForm.classList.remove("gt-hidden");
  showToast("Template copied to create form", "info");
}

async function _handleReload(toolName) {
  if (!toolName) return;
  try {
    const res = await fetch(`${API}/api/tools/generated/${encodeURIComponent(toolName)}/reload`, {
      method: "POST",
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Tool '${toolName}' reloaded`, "info");
      loadGeneratedTools();
    } else {
      showToast(data.error || "Reload failed", "error");
    }
  } catch {
    showToast("Network error reloading tool", "error");
  }
}

async function _handleRemove(toolName) {
  if (!toolName) return;
  if (!confirm(`Remove tool "${toolName}"? This cannot be undone.`)) return;

  try {
    const res = await fetch(`${API}/api/tools/generated/${encodeURIComponent(toolName)}`, {
      method: "DELETE",
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Tool '${toolName}' removed`, "info");
      loadGeneratedTools();
    } else {
      showToast(data.error || "Remove failed", "error");
    }
  } catch {
    showToast("Network error removing tool", "error");
  }
}

// ── Helpers ────────────────────────────────────────────────────

function _formatTimeAgo(ts) {
  if (!ts) return "unknown";
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

// ── Styles ─────────────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById("gt-styles")) return;
  const style = document.createElement("style");
  style.id = "gt-styles";
  style.textContent = `
/* ── Generated Tools Layout ─────────────────────────────────── */
#generatedToolsPanel{padding:16px 20px;font-family:ui-sans-serif,system-ui,-apple-system,sans-serif}

.gt-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:10px}
.gt-header-left{display:flex;align-items:center;gap:8px}
.gt-header-icon{font-size:20px;color:#818cf8}
.gt-title{font-size:14px;font-weight:700;color:#e2e8f0;margin:0}
.gt-count{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#64748b;background:rgba(100,116,139,.15);padding:2px 8px;border-radius:9999px}
.gt-header-actions{display:flex;gap:8px}

/* ── Buttons ────────────────────────────────────────────────── */
.gt-btn{display:inline-flex;align-items:center;gap:4px;padding:6px 14px;border:none;border-radius:8px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;cursor:pointer;transition:background .15s,opacity .15s}
.gt-btn:disabled{opacity:.5;cursor:not-allowed}
.gt-btn-icon{font-size:14px}
.gt-btn-primary{background:rgba(99,102,241,.2);color:#818cf8;border:1px solid rgba(99,102,241,.25)}
.gt-btn-primary:hover:not(:disabled){background:rgba(99,102,241,.3)}
.gt-btn-secondary{background:rgba(100,116,139,.12);color:#94a3b8;border:1px solid rgba(100,116,139,.2)}
.gt-btn-secondary:hover:not(:disabled){background:rgba(100,116,139,.2)}
.gt-btn-ghost{background:transparent;color:#64748b;border:1px solid transparent}
.gt-btn-ghost:hover{color:#94a3b8}

/* ── Forms ──────────────────────────────────────────────────── */
.gt-hidden{display:none!important}
.gt-form{background:rgba(30,41,59,.5);border:1px solid rgba(51,65,85,.3);border-radius:12px;padding:16px;margin-bottom:16px}
.gt-form-title{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:700;color:#e2e8f0;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px}
.gt-form-icon{font-size:16px;color:#818cf8}
.gt-form-fields{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.gt-field-wide{grid-column:1/-1}
.gt-label{display:block;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#64748b;margin-bottom:4px}
.gt-input{width:100%;background:rgba(15,23,42,.6);border:1px solid rgba(51,65,85,.4);border-radius:8px;padding:7px 10px;font-size:11px;color:#e2e8f0;outline:none;transition:border-color .15s;box-sizing:border-box}
.gt-input::placeholder{color:#475569}
.gt-input:focus{border-color:rgba(99,102,241,.5)}
.gt-code-textarea{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;background:rgba(2,6,23,.7);resize:vertical;min-height:80px;line-height:1.5;tab-size:4}
.gt-form-actions{display:flex;gap:8px}

/* ── Validation Result ──────────────────────────────────────── */
.gt-validation-result{margin-top:10px;padding:8px 12px;border-radius:8px;font-size:10px;color:#e2e8f0;display:flex;align-items:flex-start;gap:6px;flex-wrap:wrap}
.gt-valid{background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.2)}
.gt-invalid{background:rgba(248,113,113,.08);border:1px solid rgba(248,113,113,.2)}
.gt-error-details{font-family:ui-monospace,monospace;font-size:10px;color:#f87171;line-height:1.6;display:block;width:100%}

/* ── Sections & Grid ────────────────────────────────────────── */
.gt-section{margin-bottom:20px}
.gt-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}

/* ── Tool Card ──────────────────────────────────────────────── */
.gt-card{background:rgba(30,41,59,.5);border:1px solid rgba(51,65,85,.3);border-radius:12px;padding:14px;transition:background .15s,border-color .15s}
.gt-card:hover{background:rgba(30,41,59,.7);border-color:rgba(99,102,241,.2)}

.gt-card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.gt-card-title-row{display:flex;align-items:center;gap:6px;min-width:0}
.gt-card-icon{font-size:16px;color:#818cf8}
.gt-card-name{font-size:12px;font-weight:700;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gt-card-status{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;border:1px solid;border-radius:9999px;padding:1px 8px;flex-shrink:0}
.gt-card-actions{display:flex;gap:4px;flex-shrink:0}
.gt-card-btn{background:none;border:none;color:#475569;cursor:pointer;padding:3px;border-radius:6px;transition:color .15s,background .15s;display:flex;align-items:center}
.gt-card-btn .material-symbols-outlined{font-size:16px}
.gt-card-btn-reload:hover{color:#818cf8;background:rgba(99,102,241,.1)}
.gt-card-btn-remove:hover{color:#f87171;background:rgba(248,113,113,.1)}

.gt-card-desc{font-size:10px;color:#94a3b8;margin-bottom:8px;line-height:1.4}
.gt-card-meta{display:flex;align-items:center;gap:10px;margin-top:6px}
.gt-meta-item{font-size:9px;color:#64748b;text-transform:uppercase;letter-spacing:.06em}
.gt-meta-label{font-weight:700;color:#475569}

/* ── Empty States ──────────────────────────────────────────── */
.gt-empty{color:#64748b;font-size:11px;text-align:center;padding:24px 0;font-family:ui-monospace,monospace}
.gt-empty-state{text-align:center;padding:32px 16px}
.gt-empty-icon{font-size:32px;color:#334155;display:block;margin:0 auto 8px}
.gt-empty-text{font-size:11px;color:#64748b;margin:0 0 4px}
.gt-empty-sub{font-size:10px;color:#475569;margin:0}
`;
  document.head.appendChild(style);
}
