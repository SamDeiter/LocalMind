import { escapeHtml, showToast } from "../utils.js";
import { ACTION_ICONS, MAX_ACTIVITY_ITEMS } from "./constants.js";
import { updateBrainDashboard, updateSuccessRate } from "./dashboard.js";
import { onActivity, onStatus, init as initWsClient } from "../ws_client.js";

let _wsClientInitialized = false;

/**
 * Connect the activity feed via the shared ws_client (WebSocket with SSE fallback).
 *
 * Safe to call multiple times -- only the first call initializes the ws_client
 * and registers the activity handler. Subsequent calls are no-ops.
 */
export function connectActivityFeed() {
  if (_wsClientInitialized) return;
  _wsClientInitialized = true;

  // Register the activity event handler
  onActivity((event) => {
    addActivityItem(event);
    updateActivityBar(event);
    updateBrainDashboard(event);

    if (event.action === "completed") {
      showToast(`\u2728 ${event.detail}`, "info");
      updateSuccessRate();
      import("../proposals_ui.js").then(m => m.loadProposals && m.loadProposals());
    } else if (event.action === "merged") {
      showToast(`\uD83D\uDD00 ${event.detail}`, "info");
    } else if (event.action === "auto_approved") {
      showToast(`\uD83D\uDD17 ${event.detail}`, "info");
      import("../proposals_ui.js").then(m => m.loadProposals && m.loadProposals());
    } else if (event.action === "error" || event.action === "reverted") {
      showToast(`${ACTION_ICONS[event.action] || "\u26A0\uFE0F"} ${event.detail}`, "error");
      updateSuccessRate();
    }
  });

  // Optional: show connection status in the activity bar
  onStatus((status, transport) => {
    const bar = document.getElementById("activityBarText");
    if (!bar) return;
    if (status === "disconnected") {
      bar.textContent = "Activity feed disconnected -- reconnecting...";
    } else if (status === "connecting") {
      bar.textContent = "Connecting activity feed...";
    }
    // When "connected", the next activity event will overwrite this naturally
  });

  // Start the ws_client connection
  initWsClient();
}

export function addActivityItem(event) {
  // Update System Log Feed (Right Sidebar)
  const sysFeed = document.getElementById("systemLogFeed");
  if (sysFeed) {
    // Remove static placeholder logs if present
    if (sysFeed.innerHTML.includes("Semantic index reconstruction complete")) {
      sysFeed.innerHTML = "";
    }

    const action = event.action || "info";
    let label = "INFO";
    let colorClass = "bg-primary";
    let textColorClass = "text-primary";

    if (["completed", "merged", "auto_approved"].includes(action)) {
      label = "SUCCESS";
      colorClass = "bg-secondary";
      textColorClass = "text-secondary";
    } else if (["error", "reverted"].includes(action)) {
      label = "ERROR";
      colorClass = "bg-error";
      textColorClass = "text-error";
    } else if (["thinking", "reflecting", "checking", "writing", "executing"].includes(action)) {
      label = action.toUpperCase();
      colorClass = "bg-primary";
      textColorClass = "text-primary";
    }

    const item = document.createElement("div");
    item.className = "group flex gap-3 opacity-0 translate-y-2 transition-all duration-500";
    item.innerHTML = `
      <div class="w-1 self-stretch ${colorClass} rounded-full mt-1"></div>
      <div class="flex-1">
          <div class="flex justify-between items-center mb-1">
              <span class="text-[10px] font-bold ${textColorClass} uppercase tracking-tight">${label}</span>
              <span class="text-[9px] font-mono opacity-30">${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
          </div>
          <p class="text-[11px] text-on-surface-variant leading-snug break-words">${escapeHtml(event.detail || event.action)}</p>
      </div>
    `;

    sysFeed.prepend(item);
    requestAnimationFrame(() => {
      item.classList.remove("opacity-0", "translate-y-2");
    });
    while (sysFeed.children.length > MAX_ACTIVITY_ITEMS) {
      sysFeed.removeChild(sysFeed.lastChild);
    }
  }

  // Update AI Thinking Feed (Main Dashboard Console)
  const thinkFeed = document.getElementById("aiThinkingFeed");
  if (thinkFeed) {
    const icon = ACTION_ICONS[event.action] || "📡";
    const ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const line = document.createElement("p");
    line.innerHTML = `<span class="text-outline/40">${ts}</span>  ${icon} <span class="text-on-surface-variant">${escapeHtml(event.detail || event.action || "...")}</span>`;
    
    const placeholder = thinkFeed.querySelector(".italic");
    if (placeholder) placeholder.remove();
    
    thinkFeed.appendChild(line);
    while (thinkFeed.children.length > 20) thinkFeed.removeChild(thinkFeed.firstChild);
    thinkFeed.scrollTop = thinkFeed.scrollHeight;
  }
  
  // Also refresh the task pipeline
  // renderTaskPipeline moved to specific events to avoid flickering
}

export function updateActivityBar(event) {
  const bar = document.getElementById("activityBarText");
  const icon = document.getElementById("activityBarIcon");
  if (bar) bar.textContent = event.detail || event.action;
  if (icon) icon.textContent = ACTION_ICONS[event.action] || "📋";
}

