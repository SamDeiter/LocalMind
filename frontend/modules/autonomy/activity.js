import { API, insightContent } from "../state.js";
import { escapeHtml, showToast } from "../utils.js";
import { ACTION_ICONS, MAX_ACTIVITY_ITEMS } from "./constants.js";
import { updateBrainDashboard, updateSuccessRate } from "./dashboard.js";

let activityEventSource = null;

export function connectActivityFeed() {
  if (activityEventSource) activityEventSource.close();

  activityEventSource = new EventSource(`${API}/api/autonomy/activity`);

  activityEventSource.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      addActivityItem(event);
      updateActivityBar(event);
      updateBrainDashboard(event);

      if (event.action === "completed") {
        showToast(`✨ ${event.detail}`, "info");
        updateSuccessRate();
        import("../proposals_ui.js").then(m => m.loadProposals && m.loadProposals());
      } else if (event.action === "merged") {
        showToast(`🔀 ${event.detail}`, "info");
      } else if (event.action === "auto_approved") {
        showToast(`🔗 ${event.detail}`, "info");
        import("../proposals_ui.js").then(m => m.loadProposals && m.loadProposals());
      } else if (event.action === "error" || event.action === "reverted") {
        showToast(`${ACTION_ICONS[event.action] || "⚠️"} ${event.detail}`, "error");
        updateSuccessRate();
      }

      if (["thinking", "reflecting", "checking", "writing", "executing"].includes(event.action)) {
        updateArchitectInsight(event.detail || event.action);
      } else if (event.action === "idle" || event.action === "completed") {
        updateArchitectInsight("Standing by for reasoning vectors...");
      }
    } catch (err) {
      console.error("Failed to parse SSE event:", err);
    }
  };

  activityEventSource.onerror = () => {
    console.warn("Activity feed connection lost, retrying...");
    activityEventSource.close();
    setTimeout(connectActivityFeed, 5000);
  };
}

export function addActivityItem(event) {
  const feed = document.getElementById("activityFeed");
  if (!feed) return;

  const icon = ACTION_ICONS[event.action] || "📋";
  const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);

  const item = document.createElement("div");
  item.className = `activity-item ${isActive ? "activity-active" : "activity-idle"}`;
  item.innerHTML = `
    <span class="activity-icon">${icon}</span>
    <span class="activity-text">${escapeHtml(event.detail || event.action)}</span>
    <span class="activity-time">${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
  `;

  feed.prepend(item);
  while (feed.children.length > MAX_ACTIVITY_ITEMS) {
    feed.removeChild(feed.lastChild);
  }
}

export function updateActivityBar(event) {
  const bar = document.getElementById("activityBarText");
  const icon = document.getElementById("activityBarIcon");
  if (bar) bar.textContent = event.detail || event.action;
  if (icon) icon.textContent = ACTION_ICONS[event.action] || "📋";
}

export function updateArchitectInsight(text) {
  if (insightContent) {
    insightContent.innerHTML = `<span class="insight-cursor"></span> ${escapeHtml(text)}`;
  }
}
