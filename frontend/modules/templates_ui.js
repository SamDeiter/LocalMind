/**
 * templates_ui.js -- Template Library view
 * ==========================================
 * Browse, preview, edit, and run pipeline templates that were saved from
 * completed jobs.  Also exposes the Node Editor used by "Customize Plan".
 *
 * Exports: initTemplatesUI, showTemplatesView
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";
import { showCardSkeletons, viewHeader, card, cardGrid, emptyState, badge } from "./ui_components.js";

// ---------------------------------------------------------------------------
// Known tools -- static catalogue used for the node editor checkboxes.
// Kept in sync with backend/tools/ -- descriptions surfaced as tooltips.
// ---------------------------------------------------------------------------

const KNOWN_TOOLS = [
  { name: "read_file",          desc: "Read the contents of a file on disk" },
  { name: "write_file",         desc: "Create or overwrite a file on disk" },
  { name: "list_files",         desc: "List files and directories" },
  { name: "web_search",         desc: "Search the web for information" },
  { name: "run_code",           desc: "Execute a code snippet (Python, JS, etc.)" },
  { name: "terminal",           desc: "Run a shell command and return output" },
  { name: "git_tools",          desc: "Git operations (commit, status, diff, etc.)" },
  { name: "project_context",    desc: "Retrieve project structure and context" },
  { name: "memory",             desc: "Save or recall long-term memories" },
  { name: "browser",            desc: "Open and interact with web pages" },
  { name: "screenshot",         desc: "Take a screenshot of the desktop" },
  { name: "vision",             desc: "Analyse an image with the vision model" },
  { name: "android_emulator",   desc: "Control an Android emulator via ADB" },
  { name: "gmail",              desc: "Read/send emails via Gmail" },
  { name: "self_edit",          desc: "Edit LocalMind source code" },
  { name: "self_test",          desc: "Run LocalMind test suite" },
  { name: "ast_analyzer",       desc: "Analyse source code AST structure" },
  { name: "rag",                desc: "Retrieve documents via RAG search" },
  { name: "pdf_tool",           desc: "Read/create PDF files" },
  { name: "excel_tool",         desc: "Read/create Excel spreadsheets" },
  { name: "word_tool",          desc: "Read/create Word documents" },
  { name: "pptx_tool",          desc: "Read/create PowerPoint presentations" },
  { name: "google_sheets_tool", desc: "Interact with Google Sheets" },
  { name: "google_slides_tool", desc: "Interact with Google Slides" },
];

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

let _templates = [];
let _activeTemplate = null;   // full template object being viewed/edited
let _editingNodes = null;     // mutable copy of nodes when in edit mode
let _isEditing = false;
let _dragSrcIndex = null;     // drag-and-drop source index

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

const _el = (id) => document.getElementById(id);

function _hide(el) { if (el) { el.classList.add("hidden"); el.style.display = "none"; } }
function _show(el) { if (el) { el.classList.remove("hidden"); el.style.display = ""; } }

/** Create an element with classes and optional attributes. */
function _ce(tag, classes = "", attrs = {}) {
  const el = document.createElement(tag);
  if (classes) el.className = classes;
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

// ---------------------------------------------------------------------------
// Time formatting
// ---------------------------------------------------------------------------

function _relativeTime(isoStr) {
  if (!isoStr) return "Never";
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (diff < 0) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

async function _fetchTemplates() {
  try {
    const res = await fetch(`${API}/api/jobs/templates`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _templates = data.templates || [];
  } catch (err) {
    console.error("[templates_ui] Failed to fetch templates:", err);
    _templates = [];
  }
}

async function _runTemplate(templateId) {
  try {
    const tmpl = _templates.find((t) => t.id === templateId);
    const title = tmpl ? tmpl.name : "Template job";
    const res = await fetch(`${API}/api/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, template_id: templateId, mode: "pipeline" }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const job = await res.json();
    showToast(`Job started: ${escapeHtml(job.title || job.id)}`, "info");
    return job;
  } catch (err) {
    console.error("[templates_ui] Run template failed:", err);
    showToast(`Failed to run template: ${err.message}`, "error");
    return null;
  }
}

async function _updateTemplate(templateId, payload) {
  try {
    const res = await fetch(`${API}/api/jobs/templates/${templateId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    showToast("Template saved", "info");
    return true;
  } catch (err) {
    console.error("[templates_ui] Update template failed:", err);
    showToast(`Save failed: ${err.message}`, "error");
    return false;
  }
}

// ---------------------------------------------------------------------------
// initTemplatesUI  --  one-time DOM creation
// ---------------------------------------------------------------------------

export function initTemplatesUI() {
  // If a templatesView element already exists in HTML, just wire it up
  let container = _el("templatesView");

  if (!container) {
    // Create the container and inject beside sibling views
    container = _ce("div", "hidden flex-1 flex flex-col p-8 overflow-y-auto custom-scrollbar gap-6", {
      id: "templatesView",
      role: "region",
      "aria-label": "Template Library",
    });

    const parent = _el("mainDashboardView");
    if (!parent) {
      console.warn("[templates_ui] #mainDashboardView not found; cannot mount templates view");
      return;
    }
    parent.appendChild(container);
  } else {
    container.setAttribute("role", "region");
    container.setAttribute("aria-label", "Template Library");
  }

  // Note: the sidebar button click handler lives in events.js which calls
  // showTemplatesView() directly -- no duplicate wiring needed here.
}

// ---------------------------------------------------------------------------
// showTemplatesView / hideTemplatesView
// ---------------------------------------------------------------------------

export function showTemplatesView() {
  const view = _el("templatesView");
  if (!view) return;

  // Hide sibling views
  _hide(_el("mainScrollArea"));
  _hide(_el("swarmDashboardView"));
  _show(view);

  // Reset to list
  _activeTemplate = null;
  _editingNodes = null;
  _isEditing = false;

  // Show skeleton while loading
  const hdr = viewHeader({ icon: "dashboard_customize", iconColor: "indigo", title: "Template", count: null, actionBtn: "" });
  view.innerHTML = `<div class="flex-1 flex flex-col p-5 lg:p-7 overflow-y-auto custom-scrollbar gap-5">${hdr}<div id="tplSkeletonGrid" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4"></div></div>`;
  showCardSkeletons(view.querySelector("#tplSkeletonGrid"), 6);

  _fetchTemplates().then(() => _renderList());
}

export function hideTemplatesView() {
  _hide(_el("templatesView"));
}

// ---------------------------------------------------------------------------
// List view
// ---------------------------------------------------------------------------

function _renderList() {
  const view = _el("templatesView");
  if (!view) return;

  const refreshBtn = `<button id="tplRefreshBtn" class="btn-base btn-ghost" aria-label="Refresh template list">
    <span class="material-symbols-outlined text-xs" aria-hidden="true">refresh</span> Refresh
  </button>`;
  const headerHtml = viewHeader({ icon: "dashboard_customize", iconColor: "indigo", title: "Template", count: _templates.length, actionBtn: refreshBtn });

  if (_templates.length === 0) {
    view.innerHTML = `<div class="flex-1 flex flex-col p-5 lg:p-7 overflow-y-auto custom-scrollbar gap-5">
      ${headerHtml}
      ${emptyState({ icon: "layers", message: "No templates yet — complete a job and save it as a template to see it here." })}
    </div>`;
    _wireRefreshBtn();
    return;
  }

  const cardsHtml = _templates.map((t, idx) => {
    const nodeCount = Array.isArray(t.nodes) ? t.nodes.length : 0;
    return card({
      id: t.id,
      dataAttr: "tpl-id",
      hoverColor: "indigo",
      ariaLabel: `Template: ${t.name}`,
      innerHTML: `
        <div class="flex items-start justify-between mb-3">
          <div class="flex-1 min-w-0">
            <h3 class="text-sm font-bold text-slate-100 truncate group-hover:text-indigo-300 transition-colors">${escapeHtml(t.name)}</h3>
            <p class="text-[11px] text-slate-500 mt-1 line-clamp-2">${escapeHtml(t.description || "No description")}</p>
          </div>
          <span class="text-[11px] font-mono text-slate-600 ml-3 whitespace-nowrap">${escapeHtml(t.id.slice(0, 8))}</span>
        </div>
        <div class="flex items-center gap-4 text-xs text-slate-500 font-mono mb-4">
          <span class="flex items-center gap-1" title="Number of pipeline nodes">
            <span class="material-symbols-outlined text-xs text-indigo-400" aria-hidden="true">account_tree</span>
            ${nodeCount} node${nodeCount !== 1 ? "s" : ""}
          </span>
          <span class="flex items-center gap-1" title="Times used">
            <span class="material-symbols-outlined text-xs text-emerald-400" aria-hidden="true">play_circle</span>
            ${t.use_count || 0} run${(t.use_count || 0) !== 1 ? "s" : ""}
          </span>
          <span class="flex items-center gap-1" title="Last updated">
            <span class="material-symbols-outlined text-xs text-slate-500" aria-hidden="true">schedule</span>
            ${_relativeTime(t.updated_at)}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <button class="tpl-run-btn btn-base btn-success flex-1 justify-center" data-idx="${idx}" aria-label="Run template ${escapeHtml(t.name)}">
            <span class="material-symbols-outlined text-xs" aria-hidden="true">play_arrow</span> Run This
          </button>
          <button class="tpl-preview-btn btn-base btn-ghost flex-1 justify-center" data-idx="${idx}" aria-label="Preview template ${escapeHtml(t.name)}">
            <span class="material-symbols-outlined text-xs" aria-hidden="true">visibility</span> Preview
          </button>
        </div>`,
    });
  }).join("");

  view.innerHTML = `<div class="flex-1 flex flex-col p-5 lg:p-7 overflow-y-auto custom-scrollbar gap-5">
    ${headerHtml}
    ${cardGrid({ ariaLabel: "Templates", innerHTML: cardsHtml })}
  </div>`;

  // Wire events
  _wireRefreshBtn();

  view.querySelectorAll(".tpl-run-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const t = _templates[parseInt(btn.dataset.idx, 10)];
      if (!t) return;
      btn.disabled = true;
      btn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin" aria-hidden="true">progress_activity</span> Starting...';
      await _runTemplate(t.id);
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">play_arrow</span> Run This';
    });
  });

  view.querySelectorAll(".tpl-preview-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const t = _templates[parseInt(btn.dataset.idx, 10)];
      if (t) _showDetail(t);
    });
  });
}

function _wireRefreshBtn() {
  const btn = _el("tplRefreshBtn");
  if (btn) {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin" aria-hidden="true">progress_activity</span> Loading...';
      await _fetchTemplates();
      _renderList();
    });
  }
}

// ---------------------------------------------------------------------------
// Detail / Preview view
// ---------------------------------------------------------------------------

function _showDetail(template) {
  _activeTemplate = template;
  _isEditing = false;
  _editingNodes = null;
  _renderDetail();
}

function _renderDetail() {
  const view = _el("templatesView");
  if (!view || !_activeTemplate) return;

  const t = _activeTemplate;
  const nodes = _isEditing ? _editingNodes : (t.nodes || []);

  const headerHtml = `
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-3">
        <button id="tplBackBtn" class="text-slate-400 hover:text-white transition-colors p-1 rounded-lg hover:bg-slate-800/60"
                aria-label="Back to template list">
          <span class="material-symbols-outlined text-lg" aria-hidden="true">arrow_back</span>
        </button>
        <div>
          <h2 class="text-lg font-headline font-bold tracking-tight text-slate-100">${escapeHtml(t.name)}</h2>
          <p class="text-[11px] text-slate-500">${escapeHtml(t.description || "No description")} -- ${(t.nodes || []).length} nodes, ${t.use_count || 0} runs</p>
        </div>
      </div>
      <div class="flex gap-2">
        ${_isEditing ? `
          <button id="tplCancelEditBtn" class="bg-slate-800/60 hover:bg-slate-700/60 text-slate-300 text-xs font-bold px-4 py-2 rounded-lg uppercase tracking-wider transition-colors border border-slate-700/40"
                  aria-label="Cancel editing">
            Cancel
          </button>
          <button id="tplSaveBtn" class="bg-indigo-500/20 hover:bg-indigo-500/30 text-indigo-400 text-xs font-bold px-4 py-2 rounded-lg uppercase tracking-wider transition-colors border border-indigo-500/30"
                  aria-label="Save template changes">
            <span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">save</span> Save
          </button>
        ` : `
          <button id="tplEditBtn" class="bg-slate-800/60 hover:bg-slate-700/60 text-slate-300 text-xs font-bold px-4 py-2 rounded-lg uppercase tracking-wider transition-colors border border-slate-700/40"
                  aria-label="Edit template">
            <span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">edit</span> Edit
          </button>
          <button id="tplRunDetailBtn" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-xs font-bold px-4 py-2 rounded-lg uppercase tracking-wider transition-colors border border-emerald-500/20"
                  aria-label="Run this template">
            <span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">play_arrow</span> Run
          </button>
        `}
      </div>
    </div>`;

  // Pipeline visualisation
  let pipelineHtml;
  if (nodes.length === 0) {
    pipelineHtml = `
      <div class="flex items-center justify-center py-12 text-slate-600 text-sm" role="status">
        <span class="material-symbols-outlined mr-2" aria-hidden="true">info</span>
        This template has no nodes.${_isEditing ? ' Click "Add Node" below to begin.' : ""}
      </div>`;
  } else if (_isEditing) {
    pipelineHtml = _renderNodeEditor(nodes);
  } else {
    pipelineHtml = _renderPipelinePreview(nodes);
  }

  // In edit mode, always show the Add Node button even when nodes exist
  const addNodeBtnHtml = _isEditing ? `
    <button id="tplAddNodeBtn" class="mt-4 w-full bg-slate-900/40 border border-dashed border-slate-700/60 rounded-xl py-3 text-slate-500 hover:text-indigo-400 hover:border-indigo-500/30 transition-colors text-xs font-bold uppercase tracking-wider"
            aria-label="Add a new node to the pipeline">
      <span class="material-symbols-outlined text-sm align-middle mr-1" aria-hidden="true">add</span> Add Node
    </button>` : "";

  view.innerHTML = `${headerHtml}<div class="mt-2">${pipelineHtml}${addNodeBtnHtml}</div>`;

  // Wire header buttons
  const backBtn = _el("tplBackBtn");
  if (backBtn) {
    backBtn.addEventListener("click", () => {
      _activeTemplate = null;
      _renderList();
    });
    // Also handle keyboard
    backBtn.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        backBtn.click();
      }
    });
  }

  const editBtn = _el("tplEditBtn");
  if (editBtn) {
    editBtn.addEventListener("click", () => {
      _isEditing = true;
      _editingNodes = JSON.parse(JSON.stringify(_activeTemplate.nodes || []));
      _renderDetail();
      // Focus the first node title input for keyboard accessibility
      const firstInput = view.querySelector(".node-title-input");
      if (firstInput) firstInput.focus();
    });
  }

  const cancelBtn = _el("tplCancelEditBtn");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", () => {
      _isEditing = false;
      _editingNodes = null;
      _renderDetail();
    });
  }

  const saveBtn = _el("tplSaveBtn");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      _syncNodesFromDOM();
      saveBtn.disabled = true;
      saveBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin" aria-hidden="true">progress_activity</span> Saving...';
      const ok = await _updateTemplate(_activeTemplate.id, {
        name: _activeTemplate.name,
        nodes: _editingNodes,
      });
      if (ok) {
        _activeTemplate.nodes = JSON.parse(JSON.stringify(_editingNodes));
        _isEditing = false;
        _editingNodes = null;
        // Update the cached list as well
        const idx = _templates.findIndex((tp) => tp.id === _activeTemplate.id);
        if (idx !== -1) _templates[idx] = { ..._templates[idx], nodes: _activeTemplate.nodes };
        _renderDetail();
      } else {
        saveBtn.disabled = false;
        saveBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">save</span> Save';
      }
    });
  }

  const runBtn = _el("tplRunDetailBtn");
  if (runBtn) {
    runBtn.addEventListener("click", async () => {
      runBtn.disabled = true;
      runBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin" aria-hidden="true">progress_activity</span> Starting...';
      await _runTemplate(_activeTemplate.id);
      runBtn.disabled = false;
      runBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1" aria-hidden="true">play_arrow</span> Run';
    });
  }

  // Wire node editor events if editing
  if (_isEditing) {
    _wireNodeEditorEvents();
  }
}

// ---------------------------------------------------------------------------
// Pipeline preview (read-only horizontal cards)
// ---------------------------------------------------------------------------

function _renderPipelinePreview(nodes) {
  const cards = nodes.map((n, i) => {
    const tools = (n.tools_allowed || []).map((t) =>
      `<span class="inline-block bg-indigo-500/10 text-indigo-400 text-[11px] font-mono px-1.5 py-0.5 rounded border border-indigo-500/20" title="${escapeHtml(_toolTip(t))}">${escapeHtml(t)}</span>`
    ).join(" ");

    return `
      <div class="flex-shrink-0 w-72 bg-slate-900/40 border border-slate-800/60 rounded-xl p-4 snap-start" role="listitem" aria-label="Node ${i + 1}: ${escapeHtml(n.title || "")}">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-[11px] font-bold bg-indigo-500/20 text-indigo-400 w-5 h-5 flex items-center justify-center rounded-full" aria-hidden="true">${i + 1}</span>
          <h4 class="text-xs font-bold text-slate-200 truncate flex-1">${escapeHtml(n.title || "Untitled")}</h4>
        </div>
        <p class="text-xs text-slate-400 leading-relaxed mb-3 line-clamp-3">${escapeHtml(n.instructions || "")}</p>
        ${tools ? `<div class="flex flex-wrap gap-1 mb-3" aria-label="Allowed tools">${tools}</div>` : ""}
        ${n.expected_output ? `
          <div class="text-[11px] text-slate-500 border-t border-slate-800/40 pt-2 mt-auto">
            <span class="text-emerald-500 font-bold uppercase tracking-widest" aria-hidden="true">Output:</span>
            <span class="ml-1">${escapeHtml(n.expected_output)}</span>
          </div>` : ""}
      </div>
      ${i < nodes.length - 1 ? '<div class="flex-shrink-0 flex items-center text-slate-700" aria-hidden="true"><span class="material-symbols-outlined text-lg">arrow_forward</span></div>' : ""}`;
  }).join("");

  return `
    <div class="flex gap-3 overflow-x-auto pb-4 custom-scrollbar snap-x snap-mandatory" role="list" aria-label="Pipeline nodes">
      ${cards}
    </div>`;
}

// ---------------------------------------------------------------------------
// Node editor (edit mode -- full CRUD)
// ---------------------------------------------------------------------------

function _renderNodeEditor(nodes) {
  const cards = nodes.map((n, i) => _renderNodeCard(n, i)).join("");

  return `
    <div id="nodeEditorContainer" class="flex flex-col gap-4" role="list" aria-label="Editable pipeline nodes">
      ${cards}
    </div>`;
}

function _renderNodeCard(node, index) {
  const toolCheckboxes = KNOWN_TOOLS.map((t) => {
    const checked = (node.tools_allowed || []).includes(t.name) ? "checked" : "";
    const cbId = `tool_${index}_${t.name}`;
    return `
      <label class="flex items-center gap-1.5 cursor-pointer group/tool" for="${cbId}" title="${escapeHtml(t.desc)}">
        <input type="checkbox" id="${cbId}" class="node-tool-cb rounded border-slate-600 bg-slate-800 text-indigo-500 focus:ring-indigo-500/40 w-3.5 h-3.5"
               data-tool="${escapeHtml(t.name)}" ${checked} aria-label="${escapeHtml(t.name)}: ${escapeHtml(t.desc)}">
        <span class="text-[11px] text-slate-400 group-hover/tool:text-slate-200 transition-colors font-mono">${escapeHtml(t.name)}</span>
      </label>`;
  }).join("");

  return `
    <div class="node-card bg-slate-900/40 border border-slate-800/60 rounded-xl p-4 transition-all hover:border-indigo-500/20"
         data-node-idx="${index}" draggable="true" role="listitem" aria-label="Node ${index + 1}: ${escapeHtml(node.title || "Untitled")}">
      <div class="flex items-center gap-3 mb-3">
        <div class="node-drag-handle cursor-grab active:cursor-grabbing text-slate-600 hover:text-slate-400 transition-colors select-none"
             title="Drag to reorder (or Alt+Arrow keys)" aria-label="Drag handle for node ${index + 1}" tabindex="0"
             role="button" aria-roledescription="sortable">
          <span class="material-symbols-outlined text-base" aria-hidden="true">drag_indicator</span>
        </div>
        <span class="text-[11px] font-bold bg-indigo-500/20 text-indigo-400 w-5 h-5 flex items-center justify-center rounded-full" aria-hidden="true">${index + 1}</span>
        <input type="text" class="node-title-input flex-1 bg-slate-800/60 border border-slate-700/40 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:ring-1 focus:ring-indigo-500/40 focus:border-indigo-500/40 outline-none"
               value="${escapeHtml(node.title || "")}" placeholder="Node title" aria-label="Title for node ${index + 1}">
        <button class="node-delete-btn text-slate-600 hover:text-red-400 transition-colors p-1 rounded-lg hover:bg-red-500/10"
                title="Delete this node" aria-label="Delete node ${index + 1}">
          <span class="material-symbols-outlined text-base" aria-hidden="true">close</span>
        </button>
      </div>

      <div class="space-y-3">
        <div>
          <label class="block text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1" for="instr_${index}">Instructions</label>
          <textarea id="instr_${index}" class="node-instructions-input w-full bg-slate-800/60 border border-slate-700/40 rounded-lg px-3 py-2 text-[11px] text-slate-300 placeholder-slate-600 focus:ring-1 focus:ring-indigo-500/40 focus:border-indigo-500/40 outline-none resize-y min-h-[60px]"
                    rows="3" placeholder="Step-by-step instructions for this node..." aria-label="Instructions for node ${index + 1}">${escapeHtml(node.instructions || "")}</textarea>
        </div>

        <div>
          <span class="block text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1.5" id="tools_label_${index}">Allowed Tools</span>
          <div class="flex flex-wrap gap-x-3 gap-y-1.5" role="group" aria-labelledby="tools_label_${index}">
            ${toolCheckboxes}
          </div>
        </div>

        <div>
          <label class="block text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1" for="output_${index}">Expected Output</label>
          <input type="text" id="output_${index}" class="node-output-input w-full bg-slate-800/60 border border-slate-700/40 rounded-lg px-3 py-1.5 text-[11px] text-slate-300 placeholder-slate-600 focus:ring-1 focus:ring-indigo-500/40 focus:border-indigo-500/40 outline-none"
                 value="${escapeHtml(node.expected_output || "")}" placeholder="Describe the expected output of this node" aria-label="Expected output for node ${index + 1}">
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Node editor event wiring
// ---------------------------------------------------------------------------

function _wireNodeEditorEvents() {
  const view = _el("templatesView");
  if (!view) return;

  // Delete node
  view.querySelectorAll(".node-delete-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const card = btn.closest(".node-card");
      const idx = parseInt(card.dataset.nodeIdx, 10);
      _syncNodesFromDOM();
      _editingNodes.splice(idx, 1);
      _renderDetail();
    });
  });

  // Add node
  const addBtn = _el("tplAddNodeBtn");
  if (addBtn) {
    addBtn.addEventListener("click", () => {
      _syncNodesFromDOM();
      _editingNodes.push({
        title: "",
        instructions: "",
        tools_allowed: [],
        expected_output: "",
        depends_on: [],
        timeout_sec: 300,
      });
      _renderDetail();
      // Focus the new node's title input
      const cards = view.querySelectorAll(".node-card");
      const lastCard = cards[cards.length - 1];
      if (lastCard) {
        const titleInput = lastCard.querySelector(".node-title-input");
        if (titleInput) titleInput.focus();
      }
    });
  }

  // Drag and drop reordering
  const nodeCards = view.querySelectorAll(".node-card");
  nodeCards.forEach((card) => {
    card.addEventListener("dragstart", (e) => {
      _dragSrcIndex = parseInt(card.dataset.nodeIdx, 10);
      card.classList.add("opacity-50");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(_dragSrcIndex));
    });

    card.addEventListener("dragend", () => {
      card.classList.remove("opacity-50");
      _dragSrcIndex = null;
      view.querySelectorAll(".node-card").forEach((c) => c.classList.remove("border-indigo-500/60"));
    });

    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      card.classList.add("border-indigo-500/60");
    });

    card.addEventListener("dragleave", () => {
      card.classList.remove("border-indigo-500/60");
    });

    card.addEventListener("drop", (e) => {
      e.preventDefault();
      card.classList.remove("border-indigo-500/60");
      const dstIndex = parseInt(card.dataset.nodeIdx, 10);
      if (_dragSrcIndex === null || _dragSrcIndex === dstIndex) return;
      _syncNodesFromDOM();
      const [moved] = _editingNodes.splice(_dragSrcIndex, 1);
      _editingNodes.splice(dstIndex, 0, moved);
      _renderDetail();
    });
  });

  // Keyboard reorder with drag handle (Alt+Arrow Up/Down)
  view.querySelectorAll(".node-drag-handle").forEach((handle) => {
    handle.addEventListener("keydown", (e) => {
      const card = handle.closest(".node-card");
      const idx = parseInt(card.dataset.nodeIdx, 10);

      if (e.altKey && e.key === "ArrowUp" && idx > 0) {
        e.preventDefault();
        _syncNodesFromDOM();
        [_editingNodes[idx - 1], _editingNodes[idx]] = [_editingNodes[idx], _editingNodes[idx - 1]];
        _renderDetail();
        // Restore focus at the new position
        const handles = view.querySelectorAll(".node-drag-handle");
        if (handles[idx - 1]) handles[idx - 1].focus();
      } else if (e.altKey && e.key === "ArrowDown" && idx < _editingNodes.length - 1) {
        e.preventDefault();
        _syncNodesFromDOM();
        [_editingNodes[idx], _editingNodes[idx + 1]] = [_editingNodes[idx + 1], _editingNodes[idx]];
        _renderDetail();
        const handles = view.querySelectorAll(".node-drag-handle");
        if (handles[idx + 1]) handles[idx + 1].focus();
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Sync DOM inputs -> _editingNodes state
// ---------------------------------------------------------------------------

function _syncNodesFromDOM() {
  if (!_editingNodes) return;

  const view = _el("templatesView");
  if (!view) return;

  const cards = view.querySelectorAll(".node-card");
  cards.forEach((card, i) => {
    if (!_editingNodes[i]) return;

    const titleInput = card.querySelector(".node-title-input");
    const instrInput = card.querySelector(".node-instructions-input");
    const outputInput = card.querySelector(".node-output-input");

    if (titleInput) _editingNodes[i].title = titleInput.value;
    if (instrInput) _editingNodes[i].instructions = instrInput.value;
    if (outputInput) _editingNodes[i].expected_output = outputInput.value;

    // Collect checked tools
    const checked = [];
    card.querySelectorAll(".node-tool-cb:checked").forEach((cb) => {
      checked.push(cb.dataset.tool);
    });
    _editingNodes[i].tools_allowed = checked;
  });
}

// ---------------------------------------------------------------------------
// Tool tooltip lookup
// ---------------------------------------------------------------------------

function _toolTip(toolName) {
  const found = KNOWN_TOOLS.find((t) => t.name === toolName);
  return found ? found.desc : toolName;
}
