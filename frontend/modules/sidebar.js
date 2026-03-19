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

    // Sync mode toggle buttons from server state
    if (d.mode) {
      const supBtn = document.getElementById("modeSupervisedBtn");
      const autoBtn = document.getElementById("modeAutonomousBtn");
      if (supBtn && autoBtn) {
        supBtn.classList.toggle("active", d.mode === "supervised");
        autoBtn.classList.toggle("active", d.mode === "autonomous");
      }
      // Also update brain dashboard mode badge
      const brainMode = document.getElementById("brainMode");
      if (brainMode) {
        brainMode.textContent = d.mode === "autonomous" ? "🤖 Autonomous" : "🛡️ Supervised";
      }
    }

    // Sync brain dashboard counters from server
    if (d.reflection) {
      const ideasEl = document.getElementById("brainProposals");
      if (ideasEl) ideasEl.textContent = d.reflection.proposals_logged || 0;
      brainProposalCount = d.reflection.proposals_logged || 0;
    }
    if (d.execution) {
      const execEl = document.getElementById("brainExecuted");
      if (execEl) execEl.textContent = d.execution.proposals_executed || 0;
      brainExecutedCount = d.execution.proposals_executed || 0;
    }

    // Sync uptime from server start_time
    if (d.start_time) {
      window._brainBootTime = d.start_time * 1000; // convert to ms
      updateBrainUptime();
    }

    // On first poll, populate brain timeline from recent events
    if (d.recent_events && d.recent_events.length > 0 && !window._brainCaughtUp) {
      window._brainCaughtUp = true;
      const timeline = document.getElementById("brainTimeline");
      if (timeline) {
        timeline.innerHTML = "";
        // Show newest first (reverse)
        const events = [...d.recent_events].reverse();
        for (const event of events.slice(0, 20)) {
          const icon = ACTION_ICONS[event.action] || "📋";
          const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);
          const evEl = document.createElement("div");
          evEl.className = `brain-event ${isActive ? "brain-event-active" : ""}`;
          evEl.innerHTML = `
            <span class="brain-event-icon">${icon}</span>
            <span class="brain-event-text">${escapeHtml(event.detail || event.action)}</span>
            <span class="brain-event-time">${event.time || ""}</span>
          `;
          timeline.appendChild(evEl);
        }
      }
      // Also populate sidebar activity feed
      const feed = document.getElementById("activityFeed");
      if (feed && feed.children.length <= 1) {
        const events = [...d.recent_events].reverse();
        for (const event of events.slice(0, MAX_ACTIVITY_ITEMS)) {
          const icon = ACTION_ICONS[event.action] || "📋";
          const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);
          const item = document.createElement("div");
          item.className = `activity-item ${isActive ? "activity-active" : "activity-idle"}`;
          item.innerHTML = `
            <span class="activity-icon">${icon}</span>
            <span class="activity-text">${escapeHtml(event.detail || event.action)}</span>
            <span class="activity-time">${event.time || ""}</span>
          `;
          feed.appendChild(item);
        }
      }
    }
  } catch {
    /* server not ready yet */
  }
}

// ── Live Activity Feed (SSE) ──────────────────────────────────────
let activityEventSource = null;
const MAX_ACTIVITY_ITEMS = 15;

const ACTION_ICONS = {
  idle: "💤",
  reflecting: "🔍",
  proposal_created: "💡",
  auto_approved: "🤖",
  checking: "🔎",
  executing: "⚡",
  git: "🌿",
  writing: "✍️",
  testing: "🧪",
  completed: "✅",
  reverted: "⚠️",
  error: "❌",
  mode_changed: "🔄",
};

export function connectActivityFeed() {
  if (activityEventSource) activityEventSource.close();

  activityEventSource = new EventSource(`${API}/api/autonomy/activity`);

  activityEventSource.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      addActivityItem(event);
      updateActivityBar(event);
      updateBrainDashboard(event);
    } catch {
      /* ignore parse errors */
    }
  };

  activityEventSource.onerror = () => {
    // Reconnect after 5s on error
    setTimeout(() => connectActivityFeed(), 5000);
  };

  // Start uptime ticker
  if (!window._brainUptimeInterval) {
    window._brainBootTime = Date.now();
    window._brainUptimeInterval = setInterval(updateBrainUptime, 10000);
    updateBrainUptime();
  }
}

function addActivityItem(event) {
  const feed = document.getElementById("activityFeed");
  if (!feed) return;

  const icon = ACTION_ICONS[event.action] || "📋";
  const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);

  const item = document.createElement("div");
  item.className = `activity-item ${isActive ? "activity-active" : "activity-idle"}`;
  item.innerHTML = `
    <span class="activity-icon">${icon}</span>
    <span class="activity-text">${escapeHtml(event.detail || event.action)}</span>
    <span class="activity-time">${event.time || ""}</span>
  `;

  // Prepend (newest first)
  feed.prepend(item);

  // Trim old items
  while (feed.children.length > MAX_ACTIVITY_ITEMS) {
    feed.removeChild(feed.lastChild);
  }
}

function updateActivityBar(event) {
  const activityEl = document.getElementById("autonomyActivity");
  if (activityEl) {
    const icon = ACTION_ICONS[event.action] || "";
    activityEl.textContent = `${icon} ${event.detail || event.action}`;
  }
}

// ── Brain Dashboard (Welcome Screen) ──────────────────────────────

let brainProposalCount = 0;
let brainExecutedCount = 0;

function updateBrainDashboard(event) {
  const timeline = document.getElementById("brainTimeline");
  const statusEl = document.getElementById("brainStatus");

  // Update status text
  if (statusEl) {
    const icon = ACTION_ICONS[event.action] || "";
    statusEl.textContent = `${icon} ${event.action}`;
  }

  // Track counters
  if (event.action === "proposal_created") brainProposalCount++;
  if (event.action === "completed") brainExecutedCount++;

  const ideasEl = document.getElementById("brainProposals");
  const execEl = document.getElementById("brainExecuted");
  if (ideasEl) ideasEl.textContent = brainProposalCount;
  if (execEl) execEl.textContent = brainExecutedCount;

  // Add event to brain timeline
  if (!timeline) return;
  const icon = ACTION_ICONS[event.action] || "📋";
  const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);
  const timeStr = event.time || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  const evEl = document.createElement("div");
  evEl.className = `brain-event ${isActive ? "brain-event-active" : ""}`;
  evEl.innerHTML = `
    <span class="brain-event-icon">${icon}</span>
    <span class="brain-event-text">${escapeHtml(event.detail || event.action)}</span>
    <span class="brain-event-time">${timeStr}</span>
  `;

  // Prepend newest first — remove placeholder if present
  if (timeline.children.length === 1 && timeline.firstChild.textContent.includes("warming up")) {
    timeline.innerHTML = "";
  }
  timeline.prepend(evEl);

  // Keep max 20 events
  while (timeline.children.length > 20) {
    timeline.removeChild(timeline.lastChild);
  }
}

function updateBrainUptime() {
  const el = document.getElementById("brainUptime");
  if (!el || !window._brainBootTime) return;
  const secs = Math.floor((Date.now() - window._brainBootTime) / 1000);
  if (secs < 60) el.textContent = `${secs}s`;
  else if (secs < 3600) el.textContent = `${Math.floor(secs / 60)}m`;
  else el.textContent = `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

// ── Activity Toggle ─────────────────────────────────────────────
export function toggleActivityFeed() {
  const feed = document.getElementById("activityFeed");
  if (feed) feed.classList.toggle("open");
}

// ── Autonomy Mode Toggle ────────────────────────────────────────
export async function setAutonomyMode(mode) {
  try {
    const r = await fetch(`${API}/api/autonomy/mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const d = await r.json();
    if (d.ok) {
      // Update button states
      document.getElementById("modeSupervisedBtn")?.classList.toggle("active", mode === "supervised");
      document.getElementById("modeAutonomousBtn")?.classList.toggle("active", mode === "autonomous");
      console.log(`[LocalMind] Autonomy mode: ${mode}`);
    }
  } catch (e) {
    console.warn("Mode switch failed:", e);
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

