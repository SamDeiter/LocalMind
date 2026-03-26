import { API } from "../state.js";
import { escapeHtml } from "../utils.js";
import { ACTION_ICONS, MAX_ACTIVITY_ITEMS } from "./constants.js";
import { updateBrainUptime } from "./dashboard.js";

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

    if (loadingBanner) {
      loadingBanner.style.display = (d.enabled && !modelReady) ? "flex" : "none";
    }

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
      window.brainProposalCount = d.reflection.proposals_logged || 0;
    }
    if (d.execution) {
      const execEl = document.getElementById("brainApplied");
      if (execEl) execEl.textContent = d.execution.proposals_executed || 0;
      window.brainExecutedCount = d.execution.proposals_executed || 0;
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
      window._brainBootTime = d.start_time * 1000;
      updateBrainUptime();
    }

    // On first poll, populate brain timeline from recent events
    if (d.recent_events && d.recent_events.length > 0 && !window._brainCaughtUp) {
      window._brainCaughtUp = true;
      populateInitialEvents(d.recent_events);
    }
  } catch {
    /* server not ready yet */
  }
}

function populateInitialEvents(recentEvents) {
  const timeline = document.getElementById("taskPipelineBody");
  if (timeline) {
    timeline.innerHTML = "";
    const events = [...recentEvents].reverse();
    for (const event of events.slice(0, 20)) {
      const icon = ACTION_ICONS[event.action] || "📋";
      const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);
      const evEl = document.createElement("div");
      const colorClass = isActive ? "bg-primary" : "bg-surface-variant";
      evEl.className = "flex gap-3 py-1";
      evEl.innerHTML = `
        <div class="w-1 self-stretch ${colorClass} rounded-full"></div>
        <div class="flex-1 min-w-0">
            <p class="text-[11px] text-on-surface-variant leading-snug break-words">${escapeHtml(event.detail || event.action)}</p>
        </div>
        <span class="text-[9px] font-mono opacity-30 shrink-0">${event.time || ""}</span>
      `;
      timeline.appendChild(evEl);
    }
  }

  const feed = document.getElementById("activityFeed");
  if (feed && feed.children.length <= 1) {
    const events = [...recentEvents].reverse();
    for (const event of events.slice(0, MAX_ACTIVITY_ITEMS)) {
      const icon = ACTION_ICONS[event.action] || "📋";
      const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);
      const item = document.createElement("div");
      item.className = "group flex gap-3";
      const label = isActive ? event.action.toUpperCase() : "INFO";
      const colorClass = isActive ? "bg-primary" : "bg-surface-variant";
      const textColor = isActive ? "text-primary" : "text-on-surface-variant";
      item.innerHTML = `
        <div class="w-1 self-stretch ${colorClass} rounded-full mt-1"></div>
        <div class="flex-1 min-w-0">
            <div class="flex justify-between items-center mb-1">
                <span class="text-[10px] font-bold ${textColor} uppercase tracking-tight">${label}</span>
                <span class="text-[9px] font-mono opacity-30">${event.time || ""}</span>
            </div>
            <p class="text-[11px] text-on-surface-variant leading-snug break-words">${escapeHtml(event.detail || event.action)}</p>
        </div>
      `;
      feed.appendChild(item);
    }
  }
}
