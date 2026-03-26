import { API } from "../state.js";
import { escapeHtml, showToast } from "../utils.js";
import { updateSuccessRate } from "./dashboard.js";

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

    const groups = { in_progress: [], approved: [], proposed: [], failed: [], completed: [], denied: [] };
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
    for (const [status, config] of Object.entries(statusConfig)) {
      const items = groups[status] || [];
      if (items.length === 0) continue;

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
        html += renderProposalItem(p, status);
      }
      html += `</div>`;
      if (status === "completed" && items.length > 3) {
        html += `<div class="mt-2 text-[10px] text-center font-bold uppercase tracking-widest text-outline/40 py-2 border border-dashed border-outline-variant/20 rounded-lg hover:text-outline/60 cursor-pointer transition-colors">+${items.length - 3} more records archived</div>`;
      }
      html += `</div>`;
    }
    body.innerHTML = html;
    wirePipelineButtons(body);
  } catch {
    body.innerHTML = '<div class="task-pipeline-empty">Could not load tasks.</div>';
  }

  clearTimeout(_pipelineTimer);
  _pipelineTimer = setTimeout(renderTaskPipeline, 8000);
}

function renderProposalItem(p, status) {
  const title = escapeHtml(p.title || p.category || "Untitled");
  const cat = p.category ? `<span class="text-[8px] font-extrabold uppercase tracking-widest px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">${escapeHtml(p.category)}</span>` : "";
  const timeAgo = p.created_at ? `<span class="text-[9px] text-outline/60 font-medium">${formatTimeAgo(p.created_at)}</span>` : "";
  
  const approveBtn = status === "proposed" ? `<button class="pipeline-approve p-1 hover:text-primary" data-id="${escapeHtml(p.id)}" title="Approve"><span class="material-symbols-outlined text-xs">check</span></button>` : "";
  const denyBtn = status === "proposed" ? `<button class="pipeline-deny p-1 hover:text-error" data-id="${escapeHtml(p.id)}" title="Deny"><span class="material-symbols-outlined text-xs">close</span></button>` : "";
  const retryBtn = status === "failed" ? `<button class="pipeline-retry p-1 hover:text-primary" data-id="${escapeHtml(p.id)}" title="Retry"><span class="material-symbols-outlined text-xs">refresh</span></button>` : "";
  const dismissBtn = status === "failed" ? `<button class="pipeline-dismiss p-1 hover:text-error" data-id="${escapeHtml(p.id)}" title="Dismiss"><span class="material-symbols-outlined text-xs">close</span></button>` : "";

  let html = `<div class="group flex flex-col gap-1 p-3 rounded-lg bg-surface-container-low/40 border border-outline-variant/10 hover:border-primary/30 transition-all">`;
  html += `  <div class="flex justify-between items-start gap-2">`;
  html += `    <span class="text-[11px] font-medium text-on-surface leading-snug">${title}</span>`;
  html += `    <div class="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">`;
  html += `      ${approveBtn}${denyBtn}${retryBtn}${dismissBtn}`;
  html += `    </div>`;
  html += `  </div>`;
  html += `  <div class="flex items-center gap-2 mt-1">`;
  html += `    ${cat}<span class="w-1 h-1 rounded-full bg-outline-variant/30"></span>${timeAgo}`;
  html += `  </div>`;

  const dur = p.execution_duration;
  const tok = p.total_tokens;
  const model = p.model_used;
  if (dur || tok || model) {
    html += `  <div class="flex items-center gap-3 mt-2 pt-2 border-t border-outline-variant/10">`;
    if (dur !== null && dur !== undefined) html += `    <span class="text-[9px] text-outline/60 font-mono">⏱ ${dur}s</span>`;
    if (tok) html += `    <span class="text-[9px] text-outline/60 font-mono">🎟 ${tok > 999 ? (tok / 1000).toFixed(1) + 'k' : tok} tkns</span>`;
    if (model) html += `    <span class="text-[9px] text-primary/50 font-mono">${escapeHtml(model)}</span>`;
    html += `  </div>`;
  }
  html += `</div>`;
  return html;
}

function wirePipelineButtons(body) {
    const actions = ["approve", "deny", "retry", "dismiss"];
    actions.forEach(action => {
      body.querySelectorAll(`.pipeline-${action}`).forEach(btn => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          const id = btn.dataset.id;
          const endpoint = action === "dismiss" ? "deny" : action;
          try {
            const r = await fetch(`${API}/api/autonomy/proposals/${id}/${endpoint}`, { method: "POST" });
            const d = await r.json();
            if (d.ok) {
              showToast(`✅ ${action.charAt(0).toUpperCase() + action.slice(1)}ed`, "info");
              renderTaskPipeline();
              updateSuccessRate();
            } else {
              showToast(`❌ ${d.message || "Failed"}`, "error");
            }
          } catch { showToast("❌ Action failed", "error"); }
        });
      });
    });
}

function formatTimeAgo(ts) {
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}d`;
}
