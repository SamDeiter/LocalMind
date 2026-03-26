/**
 * Autonomy UI — SSE activity feed, brain dashboard, status polling,
 * mode toggle, trigger buttons.
 * Extracted from sidebar.js for maintainability.
 */

import { API, priorityInput, chatScreen, welcomeScreen } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";
import { loadProposals } from "./proposals_ui.js";
import { sendMessage } from "./chat.js";

// ── Autonomy Status ─────────────────────────────────────────────
export async function pollAutonomy() {
  try {
    const r = await fetch(`${API}/api/autonomy/status`);
    const d = await r.json();
    const indicator = document.getElementById("autonomyIndicator");
    const label = document.getElementById("autonomyLabel");
    const loadingBanner = document.getElementById("brainLoadingBanner");
    const brainPulse = document.getElementById("brainPulse");
    // Model is ready if health_check says so, OR if the engine is actively working
    const healthReady = d.health_check && d.health_check.model_loaded;
    const engineActive = (d.ideas_generated || 0) > 0 || (d.current_task && d.current_task !== "idle");
    const modelReady = healthReady || engineActive;


    if (indicator) {
      indicator.className = d.enabled ? "autonomy-dot autonomy-active" : "autonomy-dot autonomy-paused";
    }
    if (label) {
      if (!d.enabled) {
        label.textContent = "Paused";
      } else if (modelReady) {
        label.textContent = "Active";
      } else {
        label.textContent = "Loading…";
      }
    }

    // Show/hide loading banner in brain dashboard
    if (loadingBanner) {
      loadingBanner.style.display = (d.enabled && !modelReady) ? "flex" : "none";
    }

    // Pulse dot: amber while loading, green when ready
    if (brainPulse) {
      brainPulse.classList.toggle("loading", d.enabled && !modelReady);
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
        const isAuto = d.mode === "autonomous";
        brainMode.textContent = isAuto ? "🤖 Autonomous" : "🛡️ Supervised";
        brainMode.classList.toggle("autonomous", isAuto);
      }
    }

    // Sync brain dashboard counters from server
    if (d.reflection) {
      const ideasEl = document.getElementById("brainIdeas");
      if (ideasEl) ideasEl.textContent = d.reflection.proposals_logged || 0;
      brainProposalCount = d.reflection.proposals_logged || 0;
    }
    if (d.execution) {
      const execEl = document.getElementById("brainApplied");
      if (execEl) execEl.textContent = d.execution.proposals_executed || 0;
      brainExecutedCount = d.execution.proposals_executed || 0;
    }

    // Sync global counts for sidebar counters
    if (d.memories_count !== undefined) {
      const el = document.getElementById("memoryCount");
      if (el) el.textContent = d.memories_count;
    }
    if (d.documents_count !== undefined) {
      const el = document.getElementById("docCount");
      if (el) el.textContent = d.documents_count;
    }
    if (d.proposals_count !== undefined) {
      const el = document.getElementById("proposalCount");
      if (el) el.textContent = d.proposals_count;
    }

    // Sync uptime from server start_time
    if (d.start_time) {
      window._brainBootTime = d.start_time * 1000; // convert to ms
      updateBrainUptime();
    }

    // On first poll, populate the task pipeline from proposals (not raw events)
    if (d.recent_events && d.recent_events.length > 0 && !window._brainCaughtUp) {
      window._brainCaughtUp = true;
      // Render proper proposal cards in the action stream
      renderTaskPipeline();
      // Also populate sidebar activity feed with recent events
      const feed = document.getElementById("activityFeed");
      if (feed && feed.children.length <= 1) {
        const events = [...d.recent_events].reverse();
        for (const event of events.slice(0, MAX_ACTIVITY_ITEMS)) {
          const icon = ACTION_ICONS[event.action] || "📋";
          const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);
          const item = document.createElement("div");
          item.className = "group flex gap-3";
          const label = isActive ? event.action.toUpperCase() : "INFO";
          const colorCls = isActive ? "bg-primary" : "bg-surface-variant";
          const textClr = isActive ? "text-primary" : "text-on-surface-variant";
          item.innerHTML = `
            <div class="w-1 self-stretch ${colorCls} rounded-full mt-1"></div>
            <div class="flex-1 min-w-0">
                <div class="flex justify-between items-center mb-1">
                    <span class="text-[10px] font-bold ${textClr} uppercase tracking-tight">${label}</span>
                    <span class="text-[9px] font-mono opacity-30">${event.time || ""}</span>
                </div>
                <p class="text-[11px] text-on-surface-variant leading-snug break-words">${escapeHtml(event.detail || event.action)}</p>
            </div>
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
  thinking: "🧠",
  reflecting: "🔍",
  proposal_created: "💡",
  auto_approved: "🤖",
  checking: "🔎",
  executing: "⚡",
  git: "🌿",
  writing: "✍️",
  testing: "🧪",
  completed: "✅",
  merged: "🔀",
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

      // Show toast notifications for key events
      if (event.action === "completed") {
        showToast(`✨ ${event.detail}`, "info");
        updateSuccessRate();
        // Refresh proposals list in background
        import("./proposals_ui.js").then(m => m.loadProposals && m.loadProposals());
      } else if (event.action === "merged") {
        showToast(`🔀 ${event.detail}`, "info");
      } else if (event.action === "auto_approved") {
        showToast(`🔗 ${event.detail}`, "info");
        import("./proposals_ui.js").then(m => m.loadProposals && m.loadProposals());
      } else if (event.action === "error" || event.action === "reverted") {
        showToast(`${ACTION_ICONS[event.action] || "⚠️"} ${event.detail}`, "error");
        updateSuccessRate();
      }

    } catch (err) {
      console.error("Failed to parse SSE event:", err);
    }
  };

  activityEventSource.onerror = () => {
    // Close the broken connection before retrying
    if (activityEventSource) {
      activityEventSource.close();
      activityEventSource = null;
    }
    console.warn("[SSE] Connection lost — reconnecting in 5s...");
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
  // Divert items to the Task Pipeline (Action Stream) instead of sidebar feed
  renderTaskPipeline();

  // Feed into the AI Thinking panel
  const feed = document.getElementById("aiThinkingFeed");
  if (!feed) return;
  const icon = ACTION_ICONS[event.action] || "📡";
  const ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const line = document.createElement("p");
  line.innerHTML = `<span class="text-outline/40">${ts}</span>  ${icon} <span class="text-on-surface-variant">${escapeHtml(event.detail || event.action || "...")}</span>`;
  // Remove placeholder
  const placeholder = feed.querySelector(".italic");
  if (placeholder) placeholder.remove();
  feed.appendChild(line);
  // Keep max 20 lines
  while (feed.children.length > 20) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

function updateActivityBar(_event) {
  const activityEl = document.getElementById("autonomyActivity");
  if (activityEl) {
    const icon = ACTION_ICONS[_event.action] || "";
    activityEl.textContent = `${icon} ${_event.detail || _event.action}`;
  }
}

// ── Brain Dashboard (Welcome Screen) ──────────────────────────────

let brainProposalCount = 0;
let brainExecutedCount = 0;

function updateBrainDashboard(event) {
  const timeline = document.getElementById("taskPipelineBody");
  const statusEl = document.getElementById("brainStatus");

  // Update status text
  if (statusEl) {
    const icon = ACTION_ICONS[event.action] || "";
    statusEl.textContent = `${icon} ${event.action}`;
  }

  // Track counters
  if (event.ideas !== undefined) brainProposalCount = event.ideas;
  else if (event.action === "proposal_created") brainProposalCount++;

  if (event.applied !== undefined) brainExecutedCount = event.applied;
  else if (event.action === "completed") brainExecutedCount++;

  const ideasEl = document.getElementById("brainIdeas");
  const execEl = document.getElementById("brainApplied");
  if (ideasEl) ideasEl.textContent = brainProposalCount;
  if (execEl) execEl.textContent = brainExecutedCount;

  // Handle Live Progress Overlay
  const liveSection = document.getElementById("brainLiveProgress");
  const currentTaskEl = document.getElementById("brainCurrentTask");
  const progressBar = document.getElementById("brainProgressBar");
  const modelInfoEl = document.getElementById("brainModelInfo");

  if (liveSection) {
    const isExecuting = !["idle", "completed", "error", "reverted", "proposal_created"].includes(event.action);
    liveSection.style.display = isExecuting ? "block" : "none";

    if (isExecuting && currentTaskEl) {
      currentTaskEl.textContent = event.detail || event.action;

      // Show the task description (what the AI is actually doing)
      const descEl = document.getElementById("brainTaskDescription");
      if (descEl && event.task_description) {
        descEl.textContent = event.task_description;
        descEl.style.display = "block";
      } else if (descEl && event.proposal_title) {
        descEl.textContent = event.proposal_title;
        descEl.style.display = "block";
      } else if (descEl) {
        descEl.style.display = "none";
      }
      
      // Estimate progress based on action type
      if (progressBar) {
        let progress = 10;
        if (event.action === "executing" || event.action === "checking") progress = 15;
        else if (event.action === "git") progress = 30;
        else if (event.action === "writing") progress = 50;
        else if (event.action === "testing") progress = 70;
        else if (event.action === "committing") progress = 90;
        
        progressBar.style.width = `${progress}%`;
      }
    }
    
    if (modelInfoEl && event.model) {
      modelInfoEl.textContent = event.model;
    }
  }

  // Add event to action stream
  if (!timeline) return;
  renderTaskPipeline();
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
      // Update button states using the .active class defined in style.css
      const supervisedBtn = document.getElementById("modeSupervisedBtn");
      const autonomousBtn = document.getElementById("modeAutonomousBtn");

      if (supervisedBtn) {
        supervisedBtn.classList.toggle("active", mode === "supervised");
      }
      if (autonomousBtn) {
        autonomousBtn.classList.toggle("active", mode === "autonomous");
      }
      
      const brainMode = document.getElementById("brainMode");
      if (brainMode) {
        const isAuto = mode === "autonomous";
        brainMode.textContent = isAuto ? "🤖 Autonomous" : "🛡️ Supervised";
        brainMode.classList.toggle("autonomous", isAuto);
      }
      
      console.log(`[LocalMind] Autonomy mode: ${mode}`);
    }
  } catch (e) {
    console.warn("Mode switch failed:", e);
  }
}

export async function triggerReflection() {
  const btn = document.getElementById("brainReflectBtn");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("loading");
  }

  try {
    const resp = await fetch(`${API}/api/autonomy/reflect`, { method: "POST" });
    const data = await resp.json();
    if (data.ok) {
      showToast("🧠 AI Reflection triggered", "info");
    } else {
      showToast("❌ Failed to trigger reflection", "error");
    }
  } catch (err) {
    console.error("Failed to trigger reflection:", err);
    showToast("❌ Connection error", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("loading");
    }
  }
}

/**
 * Manually trigger the AI execution cycle.
 */
export async function triggerExecution() {
  const btn = document.getElementById("brainExecuteBtn");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("loading");
    btn.textContent = "Running...";
  }

  try {
    const resp = await fetch(`${API}/api/autonomy/execute`, { method: "POST" });
    const data = await resp.json();
    if (data.ok) {
      showToast("⚙️ AI Task Execution triggered", "info");
    } else {
      showToast("❌ Failed to trigger task execution", "error");
    }
  } catch (err) {
    console.error("Failed to trigger execution:", err);
    showToast("❌ Connection error", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("loading");
      btn.textContent = "Run Tasks";
    }
  }
}

// retryProposal is imported from proposals_ui.js

// Make globally available for onclick handlers
window.retryProposal = (id) => {
    fetch(`${API}/api/autonomy/proposals/${id}/retry`, { method: "POST" })
        .then(r => r.json())
        .then(d => { if(d.ok) { showToast("🔄 Retrying", "info"); loadProposals(); } else { showToast(`❌ ${d.message || "Retry failed"}`, "error"); } })
        .catch(() => showToast("❌ Connection error", "error"));
};
window.triggerReflection = triggerReflection;
window.triggerExecution = triggerExecution;
window.approveProposal = (id) => {
    fetch(`${API}/api/autonomy/proposals/${id}/approve`, { method: "POST" })
        .then(r => r.json())
        .then(d => { if(d.ok) { showToast("✅ Approved", "info"); loadProposals(); } });
};
window.denyProposal = (id) => {
    fetch(`${API}/api/autonomy/proposals/${id}/deny`, { method: "POST" })
        .then(r => r.json())
        .then(d => { if(d.ok) { showToast("❌ Denied", "info"); loadProposals(); } });
};

// ── Priority Queue ──────────────────────────────────────────────
export async function loadPriorities() {
  try {
    const r = await fetch(`${API}/api/autonomy/priorities`);
    const d = await r.json();
    const listEl = document.getElementById("priorityList");
    if (!listEl) return;
    const priorities = d.priorities || [];
    if (priorities.length === 0) {
      listEl.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:4px 0;">No priorities set — AI will self-direct.</div>';
      return;
    }
    listEl.innerHTML = priorities.map(p => `
      <div class="priority-item" data-id="${p.id}">
        <span class="priority-text">⭐ ${escapeHtml(p.description)}</span>
        <span class="priority-badge ${p.priority || 'medium'}">${p.priority || 'medium'}</span>
        <button class="priority-remove" data-id="${p.id}" title="Remove">✕</button>
      </div>
    `).join("");
    listEl.querySelectorAll(".priority-remove").forEach(btn => {
      btn.addEventListener("click", async () => {
        await fetch(`${API}/api/autonomy/priorities/${btn.dataset.id}`, { method: "DELETE" });
        loadPriorities();
      });
    });
  } catch { /* server not ready */ }
}

export async function addPriority(description) {
  if (!description.trim()) return;
  try {
    await fetch(`${API}/api/autonomy/priorities`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, priority: "medium" }),
    });
    showToast("⭐ Priority added", "info");
    loadPriorities();
  } catch {
    showToast("❌ Failed to add priority", "error");
  }
}

// ── Daily Digest ────────────────────────────────────────────────
export async function loadDigest() {
  const el = document.getElementById("brainDigest");
  if (!el) return;
  try {
    const r = await fetch(`${API}/api/autonomy/digest`);
    const d = await r.json();
    if (d.content) {
      el.textContent = d.content;
    } else if (d.summary) {
      el.textContent = `${d.summary}\n\n✅ Completed: ${d.completed || 0}  ❌ Failed: ${d.failed || 0}  📋 Pending: ${d.pending || 0}`;
    } else {
      el.textContent = "No digest available yet. The engine will generate one after running for a while.";
    }
  } catch {
    el.textContent = "Digest unavailable — server not ready.";
  }
}

// ── Success Rate ────────────────────────────────────────────────
export async function updateSuccessRate() {
  const el = document.getElementById("brainSuccessRate");
  if (!el) return;
  try {
    const r = await fetch(`${API}/api/autonomy/proposals`);
    const d = await r.json();
    const proposals = d.proposals || [];
    const completed = proposals.filter(p => p.status === "completed").length;
    const failed = proposals.filter(p => p.status === "failed").length;
    const total = completed + failed;
    if (total > 0) {
      const rate = Math.round((completed / total) * 100);
      el.textContent = `${rate}%`;
      el.style.color = rate >= 70 ? "#4caf50" : rate >= 40 ? "#ffc107" : "#f44336";
    } else {
      el.textContent = "—";
    }
  } catch { /* ignore */ }
}

// ── Category Stats Chart ────────────────────────────────────────
async function loadCategoryStats() {
  const el = document.getElementById("categoryChart");
  if (!el) return;
  try {
    const resp = await fetch(`${API}/api/autonomy/category-stats`);
    const { categories } = await resp.json();
    if (!categories || Object.keys(categories).length === 0) {
      el.textContent = "No data yet";
      return;
    }
    el.innerHTML = Object.entries(categories)
      .sort((a, b) => b[1].total - a[1].total)
      .map(([cat, s]) => {
        const cls = s.success_rate >= 70 ? "good" : s.success_rate >= 40 ? "mid" : "bad";
        return `<div class="cat-row">
          <span class="cat-label">${cat}</span>
          <div class="cat-bar-track">
            <div class="cat-bar-fill ${cls}" style="width:${s.success_rate}%"></div>
          </div>
          <span class="cat-stat">${s.completed}/${s.total}</span>
        </div>`;
      }).join("");
  } catch { el.textContent = "Error loading stats"; }
}

// ── Export Digest as Markdown ────────────────────────────────────
async function exportDigest() {
  try {
    const resp = await fetch(`${API}/api/autonomy/digest`);
    const data = await resp.json();
    const today = new Date().toISOString().split("T")[0];
    let md = `# LocalMind Daily Digest — ${today}\n\n`;
    if (data.digest) {
      md += typeof data.digest === "string" ? data.digest : JSON.stringify(data.digest, null, 2);
    } else {
      md += "No digest data available.\n";
    }
    // Trigger download
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `localmind-digest-${today}.md`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) { console.error("Export failed:", e); }
}

// ── Wire Priority + Digest Buttons ──────────────────────────────
export function initDashboardPanels() {
  const addBtn = document.getElementById("addPriorityBtn");
  const input = document.getElementById("priorityInput");
  if (addBtn && input) {
    addBtn.addEventListener("click", () => { addPriority(input.value); input.value = ""; });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { addPriority(input.value); input.value = ""; } });
  }
  const digestBtn = document.getElementById("refreshDigestBtn");
  if (digestBtn) {
    digestBtn.addEventListener("click", loadDigest);
  }
  const exportBtn = document.getElementById("exportDigestBtn");
  if (exportBtn) {
    exportBtn.addEventListener("click", exportDigest);
  }

  // Wire Reflect + Execute buttons
  const reflectBtn = document.getElementById("brainReflectBtn");
  if (reflectBtn) {
    reflectBtn.addEventListener("click", triggerReflection);
  }
  const executeBtn = document.getElementById("brainExecuteBtn");
  if (executeBtn) {
    executeBtn.addEventListener("click", triggerExecution);
  }

  // Initial load
  loadPriorities();
  loadDigest();
  updateSuccessRate();
  loadCategoryStats();
  renderTaskPipeline();
}

// ── Task Pipeline Widget ────────────────────────────────────────
let _pipelineTimer = null;

export async function renderTaskPipeline() {
  const body = document.getElementById("taskPipelineBody");
  const countEl = document.getElementById("taskPipelineCount");
  if (!body) return;

  try {
    const r = await fetch(`${API}/api/autonomy/proposals`);
    const d = await r.json();
    const proposals = d.proposals || [];

    if (proposals.length === 0) {
      body.innerHTML = '<div class="task-pipeline-empty">No tasks yet — engine will generate proposals soon.</div>';
      if (countEl) countEl.textContent = "0";
      return;
    }

    // Group by status
    const groups = {
      in_progress: [],
      approved: [],
      proposed: [],
      failed: [],
      completed: [],
      denied: [],
    };

    for (const p of proposals) {
      const s = p.status || "proposed";
      if (groups[s]) groups[s].push(p);
    }

    const activeCount = groups.in_progress.length + groups.approved.length + groups.proposed.length;
    if (countEl) countEl.textContent = activeCount;

    const statusConfig = {
      in_progress: { icon: "🔧", label: "Processing", cls: "processing", color: "#00eefc" },
      approved:    { icon: "⏳", label: "Queued",     cls: "queued",     color: "#ffc107" },
      proposed:    { icon: "📋", label: "Pending",    cls: "pending",    color: "#aaabb2" },
      failed:      { icon: "❌", label: "Failed",     cls: "failed",     color: "#f44336" },
      completed:   { icon: "✅", label: "Done",       cls: "done",       color: "#4caf50" },
    };

    let html = "";

    // Render each group
    for (const [status, config] of Object.entries(statusConfig)) {
      const items = groups[status] || [];
      if (items.length === 0) continue;

      // For completed, only show last 3 collapsed
      const displayItems = status === "completed" ? items.slice(-3) : items;

      const isCollapsible = status === "completed" || status === "denied" || status === "failed";
      const defaultClosed = status === "completed" || status === "denied";
      html += `<div class="pipeline-group pipeline-${config.cls} mb-6">`;
      html += `<div class="flex items-center justify-between mb-3 px-1 ${isCollapsible ? 'cursor-pointer select-none hover:opacity-80' : ''}" ${isCollapsible ? `onclick="this.nextElementSibling.classList.toggle('hidden');this.querySelector('.chevron-icon').classList.toggle('rotate-90')"` : ''}>`;
      html += `  <div class="flex items-center gap-2">`;
      html += `    <span class="text-sm">${config.icon}</span>`;
      html += `    <span class="text-[10px] font-bold uppercase tracking-[0.2em]" style="color: ${config.color}">${config.label}</span>`;
      if (isCollapsible) {
        html += `    <span class="material-symbols-outlined text-xs text-outline/40 chevron-icon transition-transform ${defaultClosed ? '' : 'rotate-90'}" style="font-size:14px">chevron_right</span>`;
      }
      html += `  </div>`;
      html += `  <span class="text-[9px] font-bold bg-white/5 border border-white/10 px-1.5 py-0.5 rounded text-outline">${items.length}</span>`;
      html += `</div>`;

      html += `<div class="space-y-2 ${defaultClosed ? 'hidden' : ''}">`;
      for (const p of displayItems) {
        const title = escapeHtml(p.title || p.category || "Untitled");
        const cat = p.category ? `<span class="text-[8px] font-extrabold uppercase tracking-widest px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">${escapeHtml(p.category)}</span>` : "";
        const timeAgo = p.created_at ? `<span class="text-[9px] text-outline/60 font-medium">${formatTimeAgo(p.created_at)}</span>` : "";
        const approveBtn = status === "proposed"
          ? `<button class="pipeline-approve p-1 hover:text-primary" data-id="${escapeHtml(p.id)}" title="Approve"><span class="material-symbols-outlined text-xs">check</span></button>`
          : "";
        const denyBtn = status === "proposed"
          ? `<button class="pipeline-deny p-1 hover:text-error" data-id="${escapeHtml(p.id)}" title="Deny"><span class="material-symbols-outlined text-xs">close</span></button>`
          : "";
        const retryBtn = status === "failed"
          ? `<button class="pipeline-retry p-1 hover:text-primary" data-id="${escapeHtml(p.id)}" title="Retry"><span class="material-symbols-outlined text-xs">refresh</span></button>`
          : "";
        const dismissBtn = status === "failed"
          ? `<button class="pipeline-dismiss p-1 hover:text-error" data-id="${escapeHtml(p.id)}" title="Dismiss"><span class="material-symbols-outlined text-xs">close</span></button>`
          : "";

        html += `<div class="group flex flex-col gap-1 p-3 rounded-lg bg-surface-container-low/40 border border-outline-variant/10 hover:border-primary/30 transition-all">`;
        html += `  <div class="flex justify-between items-start gap-2">`;
        html += `    <span class="text-[11px] font-medium text-on-surface leading-snug">${title}</span>`;
        html += `    <div class="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">`;
        html += `      ${approveBtn}`;
        html += `      ${denyBtn}`;
        html += `      ${retryBtn}`;
        html += `      ${dismissBtn}`;
        html += `    </div>`;
        html += `  </div>`;
        html += `  <div class="flex items-center gap-2 mt-1">`;
        html += `    ${cat}`;
        html += `    <span class="w-1 h-1 rounded-full bg-outline-variant/30"></span>`;
        html += `    ${timeAgo}`;
        html += `  </div>`;

        // Performance metrics row
        const dur = p.execution_duration;
        const tok = p.total_tokens;
        const model = p.model_used;
        if (dur || tok || model) {
          html += `  <div class="flex items-center gap-3 mt-2 pt-2 border-t border-outline-variant/10">`;
          if (dur != null) html += `    <span class="text-[9px] text-outline/60 font-mono">⏱ ${dur}s</span>`;
          if (tok) html += `    <span class="text-[9px] text-outline/60 font-mono">🎟 ${tok > 999 ? (tok / 1000).toFixed(1) + 'k' : tok} tkns</span>`;
          if (model) html += `    <span class="text-[9px] text-primary/50 font-mono">${escapeHtml(model)}</span>`;
          html += `  </div>`;
        }

        html += `</div>`;
      }
      html += `</div>`;

      if (status === "completed" && items.length > 3) {
        html += `<div class="mt-2 text-[10px] text-center font-bold uppercase tracking-widest text-outline/40 py-2 border border-dashed border-outline-variant/20 rounded-lg hover:text-outline/60 cursor-pointer transition-colors">+${items.length - 3} more records archived</div>`;
      }

      html += `</div>`;
    }

    body.innerHTML = html;

    // Wire retry buttons
    body.querySelectorAll(".pipeline-retry").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        try {
          const r = await fetch(`${API}/api/autonomy/proposals/${id}/retry`, { method: "POST" });
          const d = await r.json();
          if (d.ok) {
            showToast("🔄 Retrying", "info");
            renderTaskPipeline();
            updateSuccessRate(); loadCategoryStats(); loadDigest();
          } else {
            showToast(`❌ ${d.message || "Retry failed"}`, "error");
          }
        } catch { showToast("❌ Retry failed", "error"); }
      });
    });

    // Wire approve buttons
    body.querySelectorAll(".pipeline-approve").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        try {
          const r = await fetch(`${API}/api/autonomy/proposals/${id}/approve`, { method: "POST" });
          const d = await r.json();
          if (d.ok) {
            showToast("✅ Approved", "info");
            renderTaskPipeline();
            updateSuccessRate(); loadCategoryStats(); loadDigest();
          } else {
            showToast(`❌ ${d.message || "Approval failed"}`, "error");
          }
        } catch { showToast("❌ Approval failed", "error"); }
      });
    });

    // Wire deny buttons
    body.querySelectorAll(".pipeline-deny").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        try {
          const r = await fetch(`${API}/api/autonomy/proposals/${id}/deny`, { method: "POST" });
          const d = await r.json();
          if (d.ok) {
            showToast("❌ Denied", "info");
            renderTaskPipeline();
            updateSuccessRate(); loadCategoryStats(); loadDigest();
          } else {
            showToast(`❌ ${d.message || "Deny failed"}`, "error");
          }
        } catch { showToast("❌ Deny failed", "error"); }
      });
    });

    // Wire dismiss buttons
    body.querySelectorAll(".pipeline-dismiss").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        try {
          const r = await fetch(`${API}/api/autonomy/proposals/${id}/deny`, { method: "POST" });
          const d = await r.json();
          if (d.ok) {
            showToast("✅ Dismissed", "info");
            renderTaskPipeline();
            updateSuccessRate(); loadCategoryStats(); loadDigest();
          } else {
            showToast(`❌ ${d.message || "Dismiss failed"}`, "error");
          }
        } catch { showToast("❌ Dismiss failed", "error"); }
      });
    });

  } catch {
    body.innerHTML = '<div class="task-pipeline-empty">Could not load tasks.</div>';
  }

  // Auto-refresh every 8 seconds
  clearTimeout(_pipelineTimer);
  _pipelineTimer = setTimeout(renderTaskPipeline, 8000);
}

function formatTimeAgo(ts) {
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}d`;
}

/** Execute a global high-priority directive */
export function executeDirective() {
  if (!priorityInput) return;
  const text = priorityInput.value.trim();
  if (!text) return;

  // Switch to chat view
  if (welcomeScreen) welcomeScreen.style.display = "none";
  if (chatScreen) chatScreen.style.display = "flex";

  // Use the unified sendMessage logic
  sendMessage();
  // Note: priorityInput is cleared by sendMessage() or manually if needed
}

/** Update the Architect's Insight panel with AI meta-commentary */
export function updateArchitectInsight(text, digest = null) {
  if (insightContent) {
    insightContent.textContent = text || "Standing by for reasoning vectors...";
    insightContent.classList.toggle("italic", !text);
  }
  if (brainDigest) {
    if (digest) {
      brainDigest.innerHTML = escapeHtml(digest);
      brainDigest.classList.remove("hidden");
    } else {
      brainDigest.classList.add("hidden");
    }
  }
}

// Wire insight panel buttons
document.addEventListener("DOMContentLoaded", () => {
  const analyzeBtn = document.querySelector("#architectInsight button:first-of-type");
  const dismissBtn = document.querySelector("#architectInsight button:last-of-type");
  
  analyzeBtn?.addEventListener("click", () => {
    // Force a reflection or deeper analysis
    triggerReflection();
  });
  
  dismissBtn?.addEventListener("click", () => {
    updateArchitectInsight(null, null);
  });

  // Task Pipeline delegation
  const taskPipelineBody = document.getElementById("taskPipelineBody");
  taskPipelineBody?.addEventListener("click", (e) => {
    const retryBtn = e.target.closest(".pipeline-retry");
    if (retryBtn) {
      const id = retryBtn.dataset.id;
      window.dispatchEvent(new CustomEvent("task-retry", { detail: { id } }));
    }
    
    const dismissBtn = e.target.closest(".pipeline-dismiss");
    if (dismissBtn) {
      const id = dismissBtn.dataset.id;
      // Dispatch event for specialized handling (e.g., removing from list)
      window.dispatchEvent(new CustomEvent("task-dismiss", { detail: { id } }));
      // Locally remove the closest pipeline item for instant feedback
      const item = e.target.closest(".group");
      if (item) {
        item.style.opacity = "0";
        setTimeout(() => item.remove(), 300);
      }
    }
  });
});

