import { API } from "../state.js";
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
      updateAiThinkingFeed(event);

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

  feed.prepend(item);
  
  // Trigger entry animation
  requestAnimationFrame(() => {
    item.classList.remove("opacity-0", "translate-y-2");
  });

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

const THINKING_COLORS = {
  reflecting: { icon: "psychology", color: "text-purple-400", label: "THINKING" },
  thinking: { icon: "neurology", color: "text-purple-400", label: "REASONING" },
  checking: { icon: "fact_check", color: "text-blue-400", label: "CHECKING" },
  writing: { icon: "edit_note", color: "text-green-400", label: "WRITING" },
  executing: { icon: "bolt", color: "text-amber-400", label: "EXECUTING" },
  research_started: { icon: "biotech", color: "text-cyan-400", label: "RESEARCH" },
  research_analyzing: { icon: "analytics", color: "text-cyan-400", label: "ANALYZING" },
  research_complete: { icon: "science", color: "text-cyan-400", label: "RESEARCH" },
  completed: { icon: "check_circle", color: "text-green-400", label: "DONE" },
  merged: { icon: "merge", color: "text-green-400", label: "MERGED" },
  auto_approved: { icon: "verified", color: "text-secondary", label: "APPROVED" },
  error: { icon: "error", color: "text-red-400", label: "ERROR" },
  reverted: { icon: "undo", color: "text-red-400", label: "REVERTED" },
  health_ok: { icon: "monitor_heart", color: "text-green-400", label: "HEALTH" },
  mode_changed: { icon: "swap_horiz", color: "text-blue-400", label: "MODE" },
  idle: { icon: "hourglass_empty", color: "text-outline", label: "IDLE" },
};

let _thinkingFeedInitialized = false;

function updateAiThinkingFeed(event) {
  const feed = document.getElementById("aiThinkingFeed");
  if (!feed) return;

  // Clear placeholder on first real event
  if (!_thinkingFeedInitialized) {
    feed.innerHTML = "";
    _thinkingFeedInitialized = true;
  }

  const config = THINKING_COLORS[event.action] || { icon: "info", color: "text-outline", label: event.action?.toUpperCase() || "INFO" };
  const detail = escapeHtml(event.detail || event.action || "...");
  const time = event.time || new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  const entry = document.createElement("div");
  entry.className = "flex items-start gap-3 py-2 px-2 rounded-lg hover:bg-surface-container-high/50 transition-colors opacity-0 translate-y-1";
  entry.innerHTML = `
    <div class="flex-shrink-0 mt-0.5">
      <span class="material-symbols-outlined text-base ${config.color}">${config.icon}</span>
    </div>
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-2 mb-0.5">
        <span class="text-[9px] font-bold ${config.color} uppercase tracking-widest">${config.label}</span>
        <span class="text-[9px] font-mono opacity-30">${time}</span>
      </div>
      <p class="text-[11px] text-on-surface-variant/80 leading-relaxed break-words">${detail}</p>
    </div>
  `;

  feed.prepend(entry);

  // Animate in
  requestAnimationFrame(() => {
    entry.classList.remove("opacity-0", "translate-y-1");
    entry.style.transition = "opacity 0.4s ease, transform 0.4s ease";
  });

  // Keep max 30 entries
  while (feed.children.length > 30) {
    feed.removeChild(feed.lastChild);
  }
}
