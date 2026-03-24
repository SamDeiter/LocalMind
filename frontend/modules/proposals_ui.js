/**
 * Proposals Panel — Dashboard rendering, approve/deny/retry actions.
 * Extracted from sidebar.js for maintainability.
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

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

    // Confidence badge helper
    const confidenceBadge = (score) => {
      if (score === undefined || score === null) return "";
      const color = score >= 70 ? "#4caf50" : score >= 40 ? "#ffc107" : "#f44336";
      const icon = score >= 70 ? "🟢" : score >= 40 ? "🟡" : "🔴";
      return `<span class="confidence-badge" style="color:${color}" title="Confidence: ${score}/100">${icon} ${score}</span>`;
    };

    // Timeline helper for relative time
    const timeAgo = (ts) => {
      if (!ts) return null;
      const diff = Math.floor((Date.now() / 1000) - ts);
      if (diff < 60) return `${diff}s ago`;
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
      if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
      return `${Math.floor(diff / 86400)}d ago`;
    };

    listEl.innerHTML = proposals
      .map(
        (p) => `
      <div class="proposal-card proposal-status-${p.status || "proposed"}" data-id="${escapeHtml(p.id)}">
        <div class="proposal-header">
          <span class="proposal-type">${categoryIcons[p.category] || "📋"} ${escapeHtml(p.category || "improvement")}</span>
          <span class="proposal-priority" style="color:${priorityColors[p.priority] || "#ffc107"}">${escapeHtml(p.priority || "medium")}</span>
          ${confidenceBadge(p.confidence)}
          <span class="proposal-status-badge">${statusIcons[p.status] || "❓"} ${escapeHtml(p.status || "proposed")}</span>
        </div>
        <div class="proposal-title">${escapeHtml(p.title || "Untitled")}</div>
        <div class="proposal-desc">${escapeHtml(p.description || "").substring(0, 150)}${(p.description || "").length > 150 ? "…" : ""}</div>
        <div class="proposal-timeline">
          <span class="tl-step">📝 ${timeAgo(p.created_at) || "—"}</span>
          ${p.status_changed_at ? `<span class="tl-arrow">→</span><span class="tl-step">✅ ${timeAgo(p.status_changed_at)}</span>` : ""}
          ${p.execution_finished_at ? `<span class="tl-arrow">→</span><span class="tl-step">🏁 ${timeAgo(p.execution_finished_at)}</span>` : ""}
        </div>
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
            ? `<div class="proposal-result">${escapeHtml(p.execution_result || "")}</div>
               <div class="proposal-actions">
                 <button class="rollback-btn" data-id="${escapeHtml(p.id)}" title="Revert this change">↩️ Rollback</button>
               </div>`
            : ""
        }
        ${
          p.status === "failed"
            ? `
          <div class="proposal-error">${escapeHtml(p.error || "Unknown error")}</div>
          <div class="proposal-actions">
            <button class="retry-btn" data-id="${escapeHtml(p.id)}" title="Retry this proposal">🔄 Retry</button>
          </div>`
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

    // Wire retry buttons
    listEl.querySelectorAll(".retry-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "⏳ Retrying...";
        await retryProposal(btn.dataset.id);
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

    // Wire rollback buttons
    listEl.querySelectorAll(".rollback-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Revert this change? This will undo the merge on main.")) return;
        btn.disabled = true;
        btn.textContent = "⏳ Reverting...";
        try {
          const r = await fetch(`${API}/api/autonomy/proposals/${btn.dataset.id}/rollback`, {
            method: "POST",
          });
          const d = await r.json();
          if (d.ok) {
            showToast("↩️ Change reverted successfully", "info");
            await loadProposals();
          } else {
            showToast(`❌ ${d.error || "Rollback failed"}`, "error");
            btn.disabled = false;
            btn.textContent = "↩️ Rollback";
          }
        } catch (err) {
          console.error("[LocalMind] Rollback error:", err);
          btn.disabled = false;
          btn.textContent = "↩️ Rollback";
        }
      });
    });
  } catch (e) {
    console.warn("Proposals load failed:", e);
  }
}

/**
 * Reset a failed proposal to approved status for re-execution.
 */
export async function retryProposal(id) {
  try {
    const r = await fetch(`${API}/api/autonomy/proposals/${id}/retry`, { method: "POST" });
    const data = await r.json();
    if (data.ok) {
      showToast("🔄 Proposal queued for retry", "info");
      loadProposals();
    } else {
      showToast("❌ " + (data.message || "Unknown error"), "error");
    }
  } catch {
    showToast("❌ Connection error", "error");
  }
}