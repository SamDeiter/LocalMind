/**
 * Monaco code editor — file tree, Run button, Send to AI, drag-drop, resize.
 */

import { API, $, editorState, editorPanel, panelDivider, editorToggle } from "./state.js";
import { getLang, getFileIcon } from "./utils.js";
import { sendMessage } from "./chat.js";

// ── Language Map ────────────────────────────────────────────────
function showEditorStatus(msg, duration) {
  const el = document.getElementById("editorPath");
  if (!el) return;
  const prev = el.textContent;
  el.textContent = msg;
  el.style.color = "#34d399";
  if (duration)
    setTimeout(() => {
      el.textContent = prev;
      el.style.color = "";
    }, duration);
}

// ── Monaco Init ─────────────────────────────────────────────────
function initMonaco() {
  require.config({
    paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs" },
  });
  require(["vs/editor/editor.main"], () => {
    editorState.monacoEditor = monaco.editor.create(document.getElementById("monacoContainer"), {
      value: "// Open a file from the tree on the left\n// or create a new file with the + button",
      language: "javascript",
      theme: "vs-dark",
      fontSize: 14,
      fontFamily: "'JetBrains Mono', monospace",
      minimap: { enabled: true },
      wordWrap: "on",
      automaticLayout: true,
      fixedOverflowWidgets: true,
      renderLineHighlight: "all",
      scrollBeyondLastLine: false,
      padding: { top: 8 },
    });

    // Ctrl+S save
    editorState.monacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, async () => {
      if (!editorState.currentPath) return;
      const content = editorState.monacoEditor.getValue();
      try {
        const r = await fetch(`${API}/api/files/write`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: editorState.currentPath, content }),
        });
        const d = await r.json();
        showEditorStatus(d.success ? "Saved ✓" : `Error: ${d.error}`, 2000);
      } catch (e) {
        showEditorStatus(`Save failed: ${e.message}`, 3000);
      }
    });

    loadEditorFiles(".");
  });
}

// ── File Tree ───────────────────────────────────────────────────
async function loadEditorFiles(dirPath, parentEl) {
  try {
    const r = await fetch(`${API}/api/files/list?path=${encodeURIComponent(dirPath)}`);
    const d = await r.json();
    const container = parentEl || document.getElementById("editorFileList");
    if (!parentEl) container.innerHTML = "";

    d.files.forEach((f) => {
      const item = document.createElement("div");
      item.className = "file-tree-item";
      item.dataset.path = f.path;
      item.dataset.type = f.type;
      const icon = f.type === "directory" ? "📁" : getFileIcon(f.name);
      const indent = (f.path.split("/").length - 1) * 12;
      item.style.paddingLeft = `${8 + indent}px`;
      item.innerHTML = `<span class="file-icon">${icon}</span><span class="file-name">${f.name}</span>`;

      if (f.type === "directory") {
        item.addEventListener("click", (e) => {
          e.stopPropagation();
          toggleDir(item, f);
        });
      } else {
        item.addEventListener("click", (e) => {
          e.stopPropagation();
          openFileInEditor(f.path, f.name);
        });
      }
      container.appendChild(item);
    });
  } catch (e) {
    console.error("Failed to load files:", e);
  }
}

async function toggleDir(item, f) {
  const expanded = item.dataset.expanded === "true";
  if (expanded) {
    while (item.nextSibling && item.nextSibling.dataset?.path?.startsWith(f.path + "/")) {
      item.nextSibling.remove();
    }
    item.dataset.expanded = "false";
    item.querySelector(".file-icon").textContent = "📁";
  } else {
    item.dataset.expanded = "true";
    item.querySelector(".file-icon").textContent = "📂";
    const cr = await fetch(`${API}/api/files/list?path=${encodeURIComponent(f.path)}`);
    const cd = await cr.json();
    const frag = document.createDocumentFragment();
    cd.files.forEach((cf) => {
      const child = document.createElement("div");
      child.className = "file-tree-item";
      child.dataset.path = cf.path;
      child.dataset.type = cf.type;
      const cIndent = (cf.path.split("/").length - 1) * 12;
      child.style.paddingLeft = `${8 + cIndent}px`;
      const cIcon = cf.type === "directory" ? "📁" : getFileIcon(cf.name);
      child.innerHTML = `<span class="file-icon">${cIcon}</span><span class="file-name">${cf.name}</span>`;
      if (cf.type === "file") {
        child.addEventListener("click", (e2) => {
          e2.stopPropagation();
          openFileInEditor(cf.path, cf.name);
        });
      } else {
        child.addEventListener("click", (e2) => {
          e2.stopPropagation();
          toggleDir(child, cf);
        });
      }
      frag.appendChild(child);
    });
    const next = item.nextSibling;
    const parent = item.parentElement;
    Array.from(frag.children).forEach((c) => parent.insertBefore(c, next));
  }
}

function openFileInEditor(path, name) {
  if (!editorState.monacoEditor) return;
  fetch(`${API}/api/files/read?path=${encodeURIComponent(path)}`)
    .then((r) => r.json())
    .then((d) => {
      if (d.error) {
        showEditorStatus(`Error: ${d.error}`, 3000);
        return;
      }
      editorState.currentPath = path;
      const lang = getLang(name);
      monaco.editor.setModelLanguage(editorState.monacoEditor.getModel(), lang);
      editorState.monacoEditor.setValue(d.content);
  document.getElementById('monacoContainer').scrollIntoView({ behavior: 'smooth' });
      document.getElementById("editorLang").textContent = lang;
      document.getElementById("editorPath").textContent = path;
      document.querySelectorAll(".file-tree-item").forEach((el) => el.classList.remove("active"));
      const active = document.querySelector(`.file-tree-item[data-path="${path}"]`);
      if (active) active.classList.add("active");
    })
    .catch((e) => {
      showEditorStatus(`Load failed: ${e.message}`, 3000);
    });
}

// ── Panel Toggle ────────────────────────────────────────────────
export function toggleEditorPanel() {
  if (!editorPanel) return;
  const visible = editorPanel.classList.toggle("visible");
  if (panelDivider) panelDivider.classList.toggle("visible", visible);
  if (editorToggle) editorToggle.classList.toggle("active", visible);
  localStorage.setItem("localmind_editor", visible ? "on" : "off");
  if (visible && !editorState.monacoEditor) initMonaco();
}

// ── Enhancement Setup (called once from app.js init) ────────────
export function initEditorEnhancements() {
  // Send to AI button
  document.getElementById("editorSendToAI")?.addEventListener("click", () => {
    if (!editorState.monacoEditor || !editorState.currentPath) return;
    const code = editorState.monacoEditor.getValue();
    const lang = getLang(editorState.currentPath.split("/").pop());
    const context = `[Code from ${editorState.currentPath}]\n\`\`\`${lang}\n${code}\n\`\`\`\n\nPlease review/help with this code:`;
    const input = document.getElementById("messageInput");
    input.value = context;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    setTimeout(() => sendMessage(), 100);
  });

  // Drag & Drop Files to Chat
  const inputArea =
    document.querySelector(".input-wrapper") || document.querySelector(".input-area");
  if (inputArea) {
    const dropZone = document.createElement("div");
    dropZone.className = "drop-zone-overlay";
    dropZone.innerHTML = '<div class="drop-zone-content">📂 Drop file here to attach</div>';
    inputArea.style.position = "relative";
    inputArea.appendChild(dropZone);

    let dragCounter = 0;
    inputArea.addEventListener("dragenter", (e) => {
      e.preventDefault();
      dragCounter++;
      dropZone.classList.add("active");
    });
    inputArea.addEventListener("dragleave", (e) => {
      e.preventDefault();
      dragCounter--;
      if (dragCounter <= 0) {
        dropZone.classList.remove("active");
        dragCounter = 0;
      }
    });
    inputArea.addEventListener("dragover", (e) => e.preventDefault());
    inputArea.addEventListener("drop", async (e) => {
      e.preventDefault();
      dragCounter = 0;
      dropZone.classList.remove("active");

      const files = Array.from(e.dataTransfer.files);
      if (!files.length) return;

      const input = document.getElementById("messageInput");
      const contextParts = [];

      for (const file of files) {
        if (file.type.startsWith("image/")) {
          const reader = new FileReader();
          const base64 = await new Promise((resolve) => {
            reader.onload = () => resolve(reader.result);
            reader.readAsDataURL(file);
          });
          contextParts.push(`[Image: ${file.name}]\n(Image attached for analysis)`);
          if (!window._droppedImages) window._droppedImages = [];
          window._droppedImages.push({ name: file.name, data: base64 });
        } else {
          const text = await file.text();
          const lang = getLang(file.name);
          contextParts.push(`[File: ${file.name}]\n\`\`\`${lang}\n${text}\n\`\`\``);
        }
      }

      const existing = input.value.trim();
      input.value =
        contextParts.join("\n\n") +
        (existing ? "\n\n" + existing : "\n\nPlease review these files:");
      input.focus();
      input.dispatchEvent(new Event("input", { bubbles: true }));
      showEditorStatus(`${files.length} file(s) attached`, 2000);
    });
  }

  // Run Python Script Button
  document.getElementById("editorRunBtn")?.addEventListener("click", async () => {
    if (!editorState.monacoEditor || !editorState.currentPath) return;
    const code = editorState.monacoEditor.getValue();
    const outputPanel = document.getElementById("editorOutput");
    const outputContent = document.getElementById("editorOutputContent");
    if (!outputPanel || !outputContent) return;

    outputPanel.style.display = "flex";
    outputContent.textContent = "▶ Running...\n";

    try {
      const r = await fetch(`${API}/api/tools/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, language: "python" }),
      });
      const d = await r.json();
      if (d.success) {
        outputContent.textContent = d.result?.output || d.result || "✓ Completed (no output)";
      } else {
        outputContent.textContent = `❌ Error: ${d.error || d.result?.error || "Unknown error"}`;
      }
    } catch (e) {
      outputContent.textContent = `❌ Failed: ${e.message}`;
    }
  });

  // Output close button
  document.getElementById("editorOutputClose")?.addEventListener("click", () => {
    const panel = document.getElementById("editorOutput");
    if (panel) panel.style.display = "none";
  });

  // New file button
  document.getElementById("editorNewFile")?.addEventListener("click", async () => {
    const name = prompt("New file name (e.g. script.py):");
    if (!name) return;
    try {
      await fetch(`${API}/api/files/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: name, content: "" }),
      });
      loadEditorFiles(".");
      setTimeout(() => openFileInEditor(name, name), 300);
    } catch (e) {
      showEditorStatus(`Create failed: ${e.message}`, 3000);
    }
  });

  // Refresh file tree
  document.getElementById("editorRefresh")?.addEventListener("click", () => loadEditorFiles("."));

  // Panel divider drag-to-resize
  if (panelDivider) {
    let dragging = false;
    panelDivider.addEventListener("mousedown", (e) => {
      dragging = true;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const container = document.querySelector(".app-container");
      const rect = container.getBoundingClientRect();
      const sidebarEl = document.querySelector(".sidebar");
      const sidebarW = sidebarEl ? sidebarEl.offsetWidth : 0;
      const x = e.clientX - rect.left - sidebarW;
      const width = Math.max(250, Math.min(rect.width * 0.6, x));
      editorPanel.style.width = `${width}px`;
    });
    document.addEventListener("mouseup", () => {
      if (dragging) {
        dragging = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    });
  }

  // File tree vertical resize
  const tree = document.querySelector(".editor-file-tree");
  if (tree) {
    const handle = document.createElement("div");
    handle.className = "file-tree-resize-handle";
    tree.appendChild(handle);
    let dragging = false;
    handle.addEventListener("mousedown", (e) => {
      dragging = true;
      document.body.style.cursor = "row-resize";
      document.body.style.userSelect = "none";
      e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const rect = tree.parentElement.getBoundingClientRect();
      const h = e.clientY - rect.top;
      tree.style.maxHeight = `${Math.max(80, Math.min(rect.height * 0.6, h))}px`;
    });
    document.addEventListener("mouseup", () => {
      if (dragging) {
        dragging = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    });
  }

  // Editor toggle button
  editorToggle?.addEventListener("click", toggleEditorPanel);
}
