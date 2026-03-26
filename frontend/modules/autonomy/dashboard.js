import { ACTION_ICONS } from "./constants.js";
import { escapeHtml } from "../utils.js";

export function updateBrainDashboard(event) {
  const timeline = document.getElementById("taskPipelineBody");
  if (!timeline) return;

  const icon = ACTION_ICONS[event.action] || "📋";
  const isActive = !["idle", "completed", "error", "reverted"].includes(event.action);

  const evEl = document.createElement("div");
  evEl.className = `brain-event ${isActive ? "brain-event-active" : ""}`;
  evEl.innerHTML = `
    <span class="brain-event-icon">${icon}</span>
    <span class="brain-event-text">${escapeHtml(event.detail || event.action)}</span>
    <span class="brain-event-time">${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
  `;

  timeline.prepend(evEl);
  if (timeline.children.length > 30) {
    timeline.removeChild(timeline.lastChild);
  }
}

export function updateBrainUptime() {
  const uptimeEl = document.getElementById("brainUptime");
  if (!uptimeEl || !window._brainBootTime) return;

  const diffHrs = (Date.now() - window._brainBootTime) / 3600000;
  uptimeEl.textContent = diffHrs.toFixed(1) + "h";
}

export function updateSuccessRate() {
  const rateEl = document.getElementById("brainSuccessRate");
  if (!rateEl) return;
  
  const total = (window.brainProposalCount || 0);
  const success = (window.brainExecutedCount || 0);
  if (total === 0) {
    rateEl.textContent = "100%";
    return;
  }
  const pct = Math.round((success / total) * 100);
  rateEl.textContent = pct + "%";
}
