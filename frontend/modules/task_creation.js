/**
 * task_creation.js -- Dashboard Task Creation Card
 * =================================================
 * Progressive-disclosure task creation widget for the main dashboard.
 *
 * Simple mode (default):
 *   - Large textarea, file drop zone, "Run Now" + "Customize Pipeline" buttons
 *
 * Expanded mode:
 *   - Horizontal node editor, template loader, priority selector, "Run Pipeline"
 *
 * Exports:
 *   initTaskCreation()  -- inject HTML into #taskCreationArea, bind events
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const AVAILABLE_TOOLS = [
  "web_search",
  "read_file",
  "write_file",
  "list_files",
  "run_code",
  "save_memory",
  "recall_memories",
  "analyze_image",
  "take_screenshot",
  "clipboard_read",
  "git_status",
  "git_diff",
  "git_log",
  "git_commit",
  "project_context",
  "gmail",
  "browser",
  "android_emulator",
];

// Friendly labels for tool checkboxes
const TOOL_LABELS = {
  web_search: "Web Search",
  read_file: "Read File",
  write_file: "Write File",
  list_files: "List Files",
  run_code: "Run Code",
  save_memory: "Save Memory",
  recall_memories: "Recall Memories",
  analyze_image: "Analyze Image",
  take_screenshot: "Screenshot",
  clipboard_read: "Clipboard",
  git_status: "Git Status",
  git_diff: "Git Diff",
  git_log: "Git Log",
  git_commit: "Git Commit",
  project_context: "Project Context",
  gmail: "Gmail",
  browser: "Browser",
  android_emulator: "Android Emulator",
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _pipelineExpanded = false;
let _nodes = [];
let _droppedFiles = [];
let _templates = [];

// ---------------------------------------------------------------------------
// DOM helper
// ---------------------------------------------------------------------------

const el = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// HTML — main card
// ---------------------------------------------------------------------------

function _buildHTML() {
  return `
<div class="bg-slate-900/50 border border-slate-800/40 rounded-xl p-5" id="tcCard">
  <!-- Header -->
  <div class="flex items-center gap-2.5 mb-4">
    <span class="material-symbols-outlined text-primary text-lg">add_task</span>
    <h2 class="font-headline font-semibold text-sm text-slate-200">New Task</h2>
  </div>

  <!-- Task description textarea -->
  <div class="mb-3">
    <textarea
      id="tcDescription"
      class="w-full bg-surface-container-low border border-outline-variant/25 rounded-lg px-3.5 py-2.5 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/40 resize-none custom-scrollbar transition-all"
      rows="2"
      placeholder="What would you like me to do? e.g., Research competitor pricing and create a summary report"
      title="Describe the task you want the AI to perform"
    ></textarea>
  </div>

  <!-- File drop zone -->
  <div
    id="tcDropZone"
    class="mb-3 border border-dashed border-slate-700/60 rounded-lg px-4 py-3 flex items-center justify-center gap-2 cursor-pointer hover:border-primary/40 hover:bg-primary/5 transition-all group"
    title="Drag and drop files here, or click to browse for files to attach"
  >
    <span class="material-symbols-outlined text-lg text-slate-500 group-hover:text-primary transition-colors">cloud_upload</span>
    <span class="text-xs text-slate-500 group-hover:text-slate-400 transition-colors">Drop files or click to upload</span>
    <input type="file" id="tcFileInput" class="hidden" multiple title="Select files to attach to this task" />
  </div>

  <!-- File preview list -->
  <div id="tcFilePreview" class="mb-3 flex flex-wrap gap-2 empty:hidden"></div>

  <!-- Action buttons -->
  <div class="flex items-center gap-2.5">
    <button
      id="tcRunNowBtn"
      class="btn-base btn-primary flex items-center gap-1.5"
      title="Submit this task immediately for quick AI processing"
    >
      <span class="material-symbols-outlined text-sm">bolt</span> Run Now
    </button>
    <button
      id="tcCustomizeBtn"
      class="btn-base btn-ghost flex items-center gap-1.5"
      title="Expand the pipeline editor to customize individual steps, choose tools, and set priority"
    >
      <span class="material-symbols-outlined text-sm" id="tcCustomizeIcon">tune</span>
      <span id="tcCustomizeLabel">Customize Pipeline</span>
    </button>
  </div>

  <!-- ========== Expanded pipeline editor (hidden by default) ========== -->
  <div id="tcPipelineEditor" class="hidden mt-5 border-t border-slate-800/40 pt-5">
    <!-- Top controls row -->
    <div class="flex items-center justify-between mb-4 flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <div class="flex items-center gap-2">
          <label for="tcTemplateSelect" class="text-[11px] font-semibold text-slate-400 uppercase tracking-widest">Template</label>
          <select
            id="tcTemplateSelect"
            class="bg-surface-container-low border border-outline-variant/30 rounded-lg text-xs text-slate-300 px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/40"
            title="Load a saved pipeline template to pre-fill the steps below"
          >
            <option value="">-- None --</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <label for="tcPriorityRange" class="text-[11px] font-semibold text-slate-400 uppercase tracking-widest">Priority</label>
          <input
            type="range"
            id="tcPriorityRange"
            min="1"
            max="10"
            value="5"
            class="w-24 accent-indigo-500"
            title="Set job priority (1 = lowest, 10 = highest)"
          />
          <span id="tcPriorityValue" class="text-xs font-mono text-primary w-4 text-center">5</span>
        </div>
      </div>
      <button
        id="tcAddNodeBtn"
        class="btn-base btn-ghost flex items-center gap-1"
        title="Add another step to the pipeline"
      >
        <span class="material-symbols-outlined text-sm">add</span> Add Step
      </button>
    </div>

    <!-- Node cards container (horizontal scroll) -->
    <div id="tcNodeContainer" class="flex gap-4 overflow-x-auto pb-4 custom-scrollbar"></div>

    <!-- Run Pipeline button -->
    <div class="flex justify-end mt-4">
      <button
        id="tcRunPipelineBtn"
        class="btn-base btn-primary flex items-center gap-1.5"
        title="Submit this multi-step pipeline for the AI to execute"
      >
        <span class="material-symbols-outlined text-sm">rocket_launch</span> Run Pipeline
      </button>
    </div>
  </div>
</div>
`;
}

// ---------------------------------------------------------------------------
// HTML — single node card
// ---------------------------------------------------------------------------

function _buildNodeCard(index, node) {
  const toolCheckboxes = AVAILABLE_TOOLS.map((t) => {
    const checked = (node.tools_allowed || []).includes(t) ? "checked" : "";
    return `
      <label class="flex items-center gap-1 cursor-pointer" title="Allow this step to use the ${TOOL_LABELS[t]} tool">
        <input type="checkbox" class="tc-node-tool accent-indigo-500 rounded" data-node="${index}" data-tool="${t}" ${checked} />
        <span class="text-xs text-slate-400">${TOOL_LABELS[t]}</span>
      </label>`;
  }).join("");

  return `
<div class="tc-node-card flex-shrink-0 w-72 bg-slate-800/50 border border-slate-700/30 rounded-xl p-4 flex flex-col gap-2.5" data-node-index="${index}">
  <!-- Step badge + remove -->
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-2">
      <span class="flex items-center justify-center w-5 h-5 rounded-full bg-primary/15 text-primary text-[11px] font-bold">${index + 1}</span>
      <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-widest">Step ${index + 1}</span>
    </div>
    <button class="tc-remove-node text-slate-600 hover:text-red-400 transition-colors p-1 rounded" data-node="${index}" title="Remove this step" aria-label="Remove step ${index + 1}">
      <span class="material-symbols-outlined text-sm pointer-events-none">close</span>
    </button>
  </div>

  <!-- Title -->
  <input
    type="text"
    class="tc-node-title bg-surface-container-low border border-outline-variant/25 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/30 transition-all"
    placeholder="Step title"
    data-node="${index}"
    value="${escapeHtml(node.title || "")}"
    title="Name this pipeline step"
  />

  <!-- Instructions -->
  <textarea
    class="tc-node-instructions bg-surface-container-low border border-outline-variant/25 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none custom-scrollbar transition-all"
    rows="3"
    placeholder="Instructions for this step..."
    data-node="${index}"
    title="Provide detailed instructions for what the AI should do in this step"
  >${escapeHtml(node.instructions || "")}</textarea>

  <!-- Tool checkboxes -->
  <div class="border-t border-slate-700/25 pt-2">
    <div class="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">Allowed Tools</div>
    <div class="grid grid-cols-2 gap-x-2 gap-y-1 max-h-32 overflow-y-auto custom-scrollbar pr-1">
      ${toolCheckboxes}
    </div>
  </div>
</div>`;
}

// ---------------------------------------------------------------------------
// Render functions
// ---------------------------------------------------------------------------

function _renderNodes() {
  const container = el("tcNodeContainer");
  if (!container) return;
  container.innerHTML = _nodes.map((n, i) => _buildNodeCard(i, n)).join("");
}

function _renderFilePreview() {
  const preview = el("tcFilePreview");
  if (!preview) return;
  if (_droppedFiles.length === 0) {
    preview.innerHTML = "";
    return;
  }
  preview.innerHTML = _droppedFiles
    .map(
      (f, i) => `
    <div class="flex items-center gap-1.5 bg-slate-800/80 border border-slate-700/40 rounded-lg px-2.5 py-1.5 text-xs text-slate-300">
      <span class="material-symbols-outlined text-xs text-slate-500">description</span>
      <span class="max-w-[120px] truncate" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
      <button class="tc-remove-file text-slate-500 hover:text-red-400 transition-colors ml-1" data-index="${i}" title="Remove this file" aria-label="Remove file ${escapeHtml(f.name)}">
        <span class="material-symbols-outlined text-xs pointer-events-none">close</span>
      </button>
    </div>`
    )
    .join("");
}

// ---------------------------------------------------------------------------
// Pipeline expand / collapse
// ---------------------------------------------------------------------------

function togglePipelineMode() {
  _pipelineExpanded = !_pipelineExpanded;
  const editor = el("tcPipelineEditor");
  const icon = el("tcCustomizeIcon");
  const label = el("tcCustomizeLabel");
  if (!editor) return;

  if (_pipelineExpanded) {
    editor.classList.remove("hidden");
    if (icon) icon.textContent = "expand_less";
    if (label) label.textContent = "Hide Pipeline";
    // Seed with one node if empty
    if (_nodes.length === 0) addNode();
    loadTemplateList();
  } else {
    editor.classList.add("hidden");
    if (icon) icon.textContent = "tune";
    if (label) label.textContent = "Customize Pipeline";
  }
}

// ---------------------------------------------------------------------------
// Node management
// ---------------------------------------------------------------------------

function addNode() {
  _nodes.push({ title: "", instructions: "", tools_allowed: [] });
  _renderNodes();
}

function removeNode(index) {
  if (index < 0 || index >= _nodes.length) return;
  _nodes.splice(index, 1);
  if (_nodes.length === 0) addNode(); // always keep at least one
  _renderNodes();
}

/** Read current form state from the DOM into _nodes array. */
function _syncNodesFromDOM() {
  const titles = document.querySelectorAll(".tc-node-title");
  const instructions = document.querySelectorAll(".tc-node-instructions");
  titles.forEach((input, i) => {
    if (!_nodes[i]) return;
    _nodes[i].title = input.value.trim();
  });
  instructions.forEach((ta, i) => {
    if (!_nodes[i]) return;
    _nodes[i].instructions = ta.value.trim();
  });
  // Sync tool checkboxes
  document.querySelectorAll(".tc-node-tool").forEach((cb) => {
    const idx = parseInt(cb.dataset.node, 10);
    const tool = cb.dataset.tool;
    if (!_nodes[idx]) return;
    if (!_nodes[idx].tools_allowed) _nodes[idx].tools_allowed = [];
    if (cb.checked && !_nodes[idx].tools_allowed.includes(tool)) {
      _nodes[idx].tools_allowed.push(tool);
    } else if (!cb.checked) {
      _nodes[idx].tools_allowed = _nodes[idx].tools_allowed.filter((t) => t !== tool);
    }
  });
}

// ---------------------------------------------------------------------------
// Template loading
// ---------------------------------------------------------------------------

async function loadTemplateList() {
  try {
    const res = await fetch(`${API}/api/jobs/templates`);
    if (!res.ok) return;
    const data = await res.json();
    _templates = data.templates || [];
  } catch {
    _templates = [];
  }

  const select = el("tcTemplateSelect");
  if (!select) return;
  // Keep the "-- None --" option, rebuild the rest
  select.innerHTML = `<option value="">-- None --</option>`;
  _templates.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t.id || t.name;
    opt.textContent = t.name || t.id;
    select.appendChild(opt);
  });
}

function _applyTemplate(templateId) {
  if (!templateId) return;
  const tmpl = _templates.find((t) => (t.id || t.name) === templateId);
  if (!tmpl) return;

  // Populate nodes from template
  const templateNodes = tmpl.nodes || tmpl.steps || [];
  if (templateNodes.length > 0) {
    _nodes = templateNodes.map((n) => ({
      title: n.title || "",
      instructions: n.instructions || n.description || "",
      tools_allowed: n.tools_allowed || n.tools || [],
    }));
  }
  _renderNodes();

  // Set priority if template has one
  if (tmpl.priority) {
    const range = el("tcPriorityRange");
    const valueLabel = el("tcPriorityValue");
    if (range) range.value = tmpl.priority;
    if (valueLabel) valueLabel.textContent = tmpl.priority;
  }

  showToast(`Template loaded: ${escapeHtml(tmpl.name || templateId)}`, "info");
}

// ---------------------------------------------------------------------------
// Submit functions
// ---------------------------------------------------------------------------

export async function submitQuickTask(description, files) {
  if (!description) {
    showToast("Please describe your task", "error");
    el("tcDescription")?.focus();
    return;
  }

  const btn = el("tcRunNowBtn");
  if (btn) btn.disabled = true;

  try {
    const payload = {
      title: description,
      description: description,
      mode: "quick",
      priority: 5,
    };

    let res;
    if (files && files.length > 0) {
      const formData = new FormData();
      formData.append("metadata", JSON.stringify(payload));
      files.forEach((f) => formData.append("files", f));
      res = await fetch(`${API}/api/jobs`, { method: "POST", body: formData });
    } else {
      res = await fetch(`${API}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || `Failed to create task (${res.status})`, "error");
      return;
    }

    const job = await res.json();
    showToast(`Task queued: ${escapeHtml(job.title || description)}`, "info");

    // Clear inputs
    const desc = el("tcDescription");
    if (desc) desc.value = "";
    _droppedFiles = [];
    _renderFilePreview();
  } catch (err) {
    console.error("[task_creation] submitQuickTask error:", err);
    showToast("Network error creating task", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function submitPipelineTask(nodes, priority, files) {
  if (!nodes || nodes.length === 0) {
    showToast("Add at least one step to the pipeline", "error");
    return;
  }

  // Validate: at least one node has a title or instructions
  const hasContent = nodes.some((n) => n.title || n.instructions);
  if (!hasContent) {
    showToast("Please fill in at least one step", "error");
    return;
  }

  const description = el("tcDescription")?.value?.trim() || "";
  const title = description || nodes[0].title || "Pipeline Task";

  const btn = el("tcRunPipelineBtn");
  if (btn) btn.disabled = true;

  try {
    const payload = {
      title,
      description: description || title,
      mode: "pipeline",
      priority: priority || 5,
      nodes: nodes.map((n) => ({
        title: n.title || "",
        instructions: n.instructions || "",
        tools_allowed: n.tools_allowed || [],
      })),
    };

    let res;
    if (files && files.length > 0) {
      const formData = new FormData();
      formData.append("metadata", JSON.stringify(payload));
      files.forEach((f) => formData.append("files", f));
      res = await fetch(`${API}/api/jobs`, { method: "POST", body: formData });
    } else {
      res = await fetch(`${API}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || `Failed to create pipeline (${res.status})`, "error");
      return;
    }

    const job = await res.json();
    showToast(`Pipeline queued: ${escapeHtml(job.title || title)}`, "info");

    // Reset state
    const desc = el("tcDescription");
    if (desc) desc.value = "";
    _nodes = [{ title: "", instructions: "", tools_allowed: [] }];
    _droppedFiles = [];
    _renderFilePreview();
    _renderNodes();
    togglePipelineMode(); // collapse
  } catch (err) {
    console.error("[task_creation] submitPipelineTask error:", err);
    showToast("Network error creating pipeline", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Event binding
// ---------------------------------------------------------------------------

function _bindEvents() {
  // Run Now
  el("tcRunNowBtn")?.addEventListener("click", () => {
    const desc = el("tcDescription")?.value?.trim() || "";
    submitQuickTask(desc, _droppedFiles);
  });

  // Customize Pipeline toggle
  el("tcCustomizeBtn")?.addEventListener("click", togglePipelineMode);

  // Run Pipeline
  el("tcRunPipelineBtn")?.addEventListener("click", () => {
    _syncNodesFromDOM();
    const priority = parseInt(el("tcPriorityRange")?.value || "5", 10);
    submitPipelineTask(_nodes, priority, _droppedFiles);
  });

  // Add node
  el("tcAddNodeBtn")?.addEventListener("click", addNode);

  // Remove node (delegated)
  el("tcNodeContainer")?.addEventListener("click", (e) => {
    const removeBtn = e.target.closest(".tc-remove-node");
    if (!removeBtn) return;
    const idx = parseInt(removeBtn.dataset.node, 10);
    _syncNodesFromDOM();
    removeNode(idx);
  });

  // Priority range live update
  el("tcPriorityRange")?.addEventListener("input", (e) => {
    const val = el("tcPriorityValue");
    if (val) val.textContent = e.target.value;
  });

  // Template select
  el("tcTemplateSelect")?.addEventListener("change", (e) => {
    _applyTemplate(e.target.value);
  });

  // File drop zone — click to open file picker
  const dropZone = el("tcDropZone");
  const fileInput = el("tcFileInput");

  dropZone?.addEventListener("click", () => fileInput?.click());

  fileInput?.addEventListener("change", (e) => {
    const files = Array.from(e.target.files || []);
    _droppedFiles.push(...files);
    _renderFilePreview();
    fileInput.value = ""; // reset so same file can be re-selected
  });

  // Drag & drop
  dropZone?.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.add("border-primary/50", "bg-primary/5");
  });

  dropZone?.addEventListener("dragleave", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.remove("border-primary/50", "bg-primary/5");
  });

  dropZone?.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.remove("border-primary/50", "bg-primary/5");
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length > 0) {
      _droppedFiles.push(...files);
      _renderFilePreview();
    }
  });

  // Remove file (delegated from preview area)
  el("tcFilePreview")?.addEventListener("click", (e) => {
    const removeBtn = e.target.closest(".tc-remove-file");
    if (!removeBtn) return;
    const idx = parseInt(removeBtn.dataset.index, 10);
    _droppedFiles.splice(idx, 1);
    _renderFilePreview();
  });

  // Ctrl+Enter / Cmd+Enter to submit from textarea
  el("tcDescription")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      if (_pipelineExpanded) {
        _syncNodesFromDOM();
        const priority = parseInt(el("tcPriorityRange")?.value || "5", 10);
        submitPipelineTask(_nodes, priority, _droppedFiles);
      } else {
        const desc = el("tcDescription")?.value?.trim() || "";
        submitQuickTask(desc, _droppedFiles);
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

export function initTaskCreation() {
  const target = el("taskCreationArea");
  if (!target) {
    console.warn("[task_creation] #taskCreationArea not found in DOM");
    return;
  }

  target.innerHTML = _buildHTML();
  _bindEvents();
}
