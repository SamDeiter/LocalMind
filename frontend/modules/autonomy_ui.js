/**
 * Autonomy UI — SSE activity feed, brain dashboard, status polling,
 * mode toggle, trigger buttons.
 * Extracted from sidebar.js for maintainability.
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";
import { loadProposals } from "./proposals_ui.js";

// ── Autonomy Status ─────────────────────────────────────────────
export async function pollAutonomy() {
  try {
    const r = await fetch(`${API}/api/autonomy/status`);
    const d = await r.json();
    const indicator = document.getElementById("autonomyIndicator");
    const label = document.getElementById("autonomyLabel");
    const loadingBanner = document.getElementById("brainLoadingBanner");
    const brainPulse = document.getElementById("brainPulse");
    const modelReady = d.health_check && d.health_check.model_loaded;

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

      // Show toast notifications for key events
      if (event.action === "completed") {
        showToast(`✨ ${event.detail}`, "info");
      } else if (event.action === "error" || event.action === "reverted") {
        showToast(`${ACTION_ICONS[event.action] || "⚠️"} ${event.detail}`, "error");
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

  // Add event to brain timeline
  if (!timeline) return;
  const icon = ACTION_ICONS[event.action] || "📋";
  const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);
  const timeStr = event.time || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  const evEl = document.createElement("div");
  evEl.className = `brain-event ${isActive ? "brain-event-active" : ""}`;
  
  // Build detailed event text
  let eventText = escapeHtml(event.detail || event.action);
  if (event.task_description && isActive) {
    const shortDesc = event.task_description.length > 80 
      ? event.task_description.substring(0, 80) + "..." 
      : event.task_description;
    eventText += `<br><span class="brain-event-desc">${escapeHtml(shortDesc)}</span>`;
  }
  
  evEl.innerHTML = `
    <span class="brain-event-icon">${icon}</span>
    <span class="brain-event-text">${eventText}</span>
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
