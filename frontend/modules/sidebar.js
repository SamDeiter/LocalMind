/**
 * Sidebar features — Hardware dashboard, Memory viewer, Document RAG,
 * Proposals dashboard, Autonomy status, Version badge.
 */

import { API, state } from "./state.js";
import { escapeHtml } from "./utils.js";

// ── Hardware Dashboard ──────────────────────────────────────────
let hwInterval = null;

export async function pollHardware() {
  try {
    const r = await fetch(`${API}/api/hardware`);
    const d = await r.json();

    const cpuBar = document.getElementById("cpuBar");
    const cpuVal = document.getElementById("cpuVal");
    if (cpuBar && d.system) {
      cpuBar.style.width = `${d.system.cpu_percent}%`;
      cpuVal.textContent = `${Math.round(d.system.cpu_percent)}%`;
      cpuBar.className = `hw-fill ${d.system.cpu_percent > 80 ? "hw-fill-red" : d.system.cpu_percent > 50 ? "hw-fill-yellow" : "hw-fill-green"}`;
    }

    const ramBar = document.getElementById("ramBar");
    const ramVal = document.getElementById("ramVal");
    if (ramBar && d.system) {
      ramBar.style.width = `${d.system.ram_percent}%`;
      ramVal.textContent = `${d.system.ram_used_gb}/${d.system.ram_total_gb} GB`;
      ramBar.className = `hw-fill ${d.system.ram_percent > 85 ? "hw-fill-red" : d.system.ram_percent > 60 ? "hw-fill-yellow" : "hw-fill-green"}`;
    }

    const vramBar = document.getElementById("vramBar");
    const vramVal = document.getElementById("vramVal");
    const modelLabel = document.getElementById("modelLabel");
    if (vramBar && d.models && d.models.length > 0) {
      const m = d.models[0];
      const pct = m.size_gb > 0 ? Math.round((m.vram_gb / m.size_gb) * 100) : 0;
      vramBar.style.width = `${pct}%`;
      vramVal.textContent = `${m.vram_gb} GB VRAM`;
      modelLabel.textContent = m.name.split(":")[0];
      vramBar.className = "hw-fill hw-fill-purple";
    } else {
      if (vramBar) vramBar.style.width = "0%";
      if (vramVal) vramVal.textContent = "Warming up...";
      if (modelLabel) modelLabel.textContent = "Model";
      if (vramBar) vramBar.className = "hw-fill hw-fill-dim";
    }

    // Also poll autonomy status
    await pollAutonomy();
  } catch {
    /* ignore */
  }
}

export function startHwPolling() {
  if (hwInterval) return;
  pollHardware();
  hwInterval = setInterval(pollHardware, 3000);
}

// ── Autonomy Status ─────────────────────────────────────────────
async function pollAutonomy() {
  try {
    const r = await fetch(`${API}/api/autonomy/status`);
    const d = await r.json();
    const indicator = document.getElementById("autonomyIndicator");
    const label = document.getElementById("autonomyLabel");
    if (indicator) {
      indicator.className = d.enabled ? "autonomy-dot autonomy-active" : "autonomy-dot autonomy-paused";
    }
    if (label) {
      if (!d.enabled) {
        label.textContent = "Paused";
      } else if (d.health_check && d.health_check.model_loaded) {
        label.textContent = "Active";
      } else {
        label.textContent = "Starting...";
      }
    }
  } catch {
    /* server not ready yet */
  }
}

// ── Memory Viewer ───────────────────────────────────────────────
export async function loadMemories() {
  try {
    const res = await fetch("/api/memories");
    const data = await res.json();
    const countEl = document.getElementById("memoryCount");
    const listEl = document.getElementById("memoryList");
    if (!countEl || !listEl) return;
    countEl.textContent = data.count || 0;
    if (!data.memories || data.memories.length === 0) {
      listEl.innerHTML =
        '<div class="memory-empty">No memories yet. Chat naturally and I\'ll learn!</div>';
      return;
    }
    listEl.innerHTML = data.memories
      .map(
        (m) => `
      <div class="memory-item">
        <span class="memory-cat memory-cat-${m.category}">${m.category}</span>
        <span class="memory-text">${escapeHtml(m.content)}</span>
        <span class="memory-time">${m.created_at}</span>
        <button class="memory-del" data-memory-id="${m.id}" title="Delete">✕</button>
      </div>
    `,
      )
      .join("");

    // Attach event listeners instead of inline onclick
    listEl.querySelectorAll(".memory-del").forEach((btn) => {
      btn.addEventListener("click", () => deleteMemory(btn.dataset.memoryId));
    });
  } catch (e) {
    console.warn("Memory load failed:", e);
  }
}

export async function deleteMemory(id) {
  try {
    await fetch(`/api/memories/${id}`, { method: "DELETE" });
    await loadMemories();
  } catch (e) {
    console.warn("Memory delete failed:", e);
  }
}

export function toggleMemoryList() {
  const list = document.getElementById("memoryList");
  if (list) list.classList.toggle("open");
}

// ── Document RAG ────────────────────────────────────────────────
export async function uploadDocuments(files) {
  for (const file of files) {
    const formData = new FormData();
    formData.append("file", file);
    try {
      const r = await fetch(`${API}/api/documents/upload`, {
        method: "POST",
        body: formData,
      });
      const d = await r.json();
      if (d.success) {
        console.log(`Indexed ${file.name}: ${d.chunks} chunks`);
      } else {
        console.error(`Upload failed: ${d.error}`);
      }
    } catch (e) {
      console.error("Upload error:", e);
    }
  }
  await loadDocuments();
}

export async function loadDocuments() {
  try {
    const r = await fetch(`${API}/api/documents`);
    const d = await r.json();
    const list = document.getElementById("documentList");
    const count = document.getElementById("docCount");
    if (!list) return;

    const docs = d.documents || [];
    if (count) count.textContent = docs.length;
    list.innerHTML = "";

    docs.forEach((doc) => {
      const div = document.createElement("div");
      div.className = "document-item";
      div.innerHTML = `
        <span class="doc-icon">📄</span>
        <span class="doc-name" title="${escapeHtml(doc.filename)}">${escapeHtml(doc.filename)}</span>
        <span class="doc-chunks">${doc.chunks} chunks</span>
        <button class="delete-btn" title="Remove">✕</button>
      `;
      div.querySelector(".delete-btn").addEventListener("click", async () => {
        await fetch(`${API}/api/documents/${encodeURIComponent(doc.filename)}`, {
          method: "DELETE",
        });
        await loadDocuments();
      });
      list.appendChild(div);
    });
  } catch {
    /* ignore */
  }
}

// ── Version Badge ───────────────────────────────────────────────
export async function loadVersion() {
  try {
    const r = await fetch(`${API}/api/version`);
    const d = await r.json();
    const badge = document.querySelector(".version-badge");
    if (badge && d.version) {
      badge.textContent = `v${d.version} #${d.build || "?"}`;
      badge.title = `LocalMind v${d.version} build #${d.build} — ${d.codename || ""}`;
    }
  } catch {
    /* ignore — version badge stays at placeholder */
  }
}

// ── Proposals Dashboard ─────────────────────────────────────────
export function toggleProposalList() {
  const list = document.getElementById("proposalList");
  if (list) list.classList.toggle("open");
}

export async function loadProposals() {
  try {
    const r = await fetch(`${API}/api/autonomy/proposals`);
    const d = await r.json();
    const countEl = document.getElementById("proposalCount");
    const listEl = document.getElementById("proposalList");
    if (!countEl || !listEl) return;

    const proposals = d.proposals || [];
    // Show count of actionable proposals (proposed = needs review)
    const actionable = proposals.filter((p) => p.status === "proposed");
    countEl.textContent = actionable.length;

    if (proposals.length === 0) {
      listEl.innerHTML =
        '<div class="memory-empty">No proposals yet. The AI will suggest improvements after running for a while.</div>';
      return;
    }

    const statusIcons = {
      proposed: "📋",
      approved: "⏳",
      in_progress: "🔧",
      completed: "✅",
      failed: "❌",
      denied: "🚫",
    };
    const priorityColors = {
      critical: "#f44336",
      high: "#ff9800",
      medium: "#ffc107",
      low: "#4caf50",
    };
    const categoryIcons = {
      performance: "⚡",
      feature: "✨",
      bugfix: "🐛",
      ux: "🎨",
      security: "🔒",
      code_quality: "🧹",
    };

    listEl.innerHTML = proposals
      .map(
        (p) => `
      <div class="proposal-card proposal-status-${p.status || "proposed"}" data-id="${escapeHtml(p.id)}">
        <div class="proposal-header">
          <span class="proposal-type">${categoryIcons[p.category] || "📋"} ${escapeHtml(p.category || "improvement")}</span>
          <span class="proposal-priority" style="color:${priorityColors[p.priority] || "#ffc107"}">${escapeHtml(p.priority || "medium")}</span>
          <span class="proposal-status-badge">${statusIcons[p.status] || "❓"} ${escapeHtml(p.status || "proposed")}</span>
        </div>
        <div class="proposal-title">${escapeHtml(p.title || "Untitled")}</div>
        <div class="proposal-desc">${escapeHtml(p.description || "").substring(0, 150)}${(p.description || "").length > 150 ? "…" : ""}</div>
        ${
          p.status === "proposed"
            ? `
          <div class="proposal-actions">
            <button class="approval-btn approve" data-id="${escapeHtml(p.id)}" title="Approve this proposal">✅ Approve</button>
            <button class="approval-btn deny" data-id="${escapeHtml(p.id)}" title="Deny this proposal">❌ Deny</button>
          </div>`
            : ""
        }
        ${
          p.status === "completed"
            ? `<div class="proposal-result">${escapeHtml(p.execution_result || "")}</div>`
            : ""
        }
        ${
          p.status === "failed"
            ? `<div class="proposal-error">${escapeHtml(p.error || "Unknown error")}</div>`
            : ""
        }
      </div>
    `,
      )
      .join("");

    // Wire approve/deny buttons
    listEl.querySelectorAll(".approval-btn.approve").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "⏳ Approving...";
        try {
          await fetch(`${API}/api/autonomy/proposals/${btn.dataset.id}/approve`, {
            method: "POST",
          });
          await loadProposals();
        } catch (err) {
          console.error("[LocalMind] Proposal approve error:", err);
          btn.disabled = false;
          btn.textContent = "✅ Approve";
        }
      });
    });

    listEl.querySelectorAll(".approval-btn.deny").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "⏳ Denying...";
        try {
          await fetch(`${API}/api/autonomy/proposals/${btn.dataset.id}/deny`, {
            method: "POST",
          });
          await loadProposals();
        } catch (err) {
          console.error("[LocalMind] Proposal deny error:", err);
          btn.disabled = false;
          btn.textContent = "❌ Deny";
        }
      });
    });
  } catch (e) {
    console.warn("Proposals load failed:", e);
  }
}

