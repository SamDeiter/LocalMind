/**
 * ui_components.js -- Shared UI primitives
 * ==========================================
 * Reusable card wrappers, skeleton loaders, badges, and empty states
 * used across Jobs, Templates, Approvals, and Learning views.
 */

import { escapeHtml } from "./utils.js";

// ── Skeleton Loaders ─────────────────────────────────────────────

/**
 * Render N skeleton card placeholders into a container.
 * Call `clearSkeletons(container)` or just overwrite innerHTML when data arrives.
 */
export function showCardSkeletons(container, count = 6) {
  if (!container) return;
  const cards = Array.from({ length: count }, () => `
    <div class="skeleton-card bg-slate-900/40 border border-slate-800/40 rounded-xl p-5 space-y-3" aria-hidden="true">
      <div class="flex items-center justify-between">
        <div class="skeleton-line h-4 w-24 rounded"></div>
        <div class="skeleton-line h-4 w-12 rounded-full"></div>
      </div>
      <div class="skeleton-line h-3 w-full rounded"></div>
      <div class="skeleton-line h-3 w-3/4 rounded"></div>
      <div class="flex gap-2 pt-2">
        <div class="skeleton-line h-8 flex-1 rounded-lg"></div>
        <div class="skeleton-line h-8 flex-1 rounded-lg"></div>
      </div>
    </div>
  `).join("");
  container.innerHTML = cards;
}

/**
 * Render N skeleton list rows (for sidebar-style lists like approvals).
 */
export function showListSkeletons(container, count = 4) {
  if (!container) return;
  const rows = Array.from({ length: count }, () => `
    <div class="skeleton-card bg-slate-900/40 border border-slate-800/40 rounded-xl p-4 space-y-2" aria-hidden="true">
      <div class="flex items-center justify-between">
        <div class="skeleton-line h-3.5 w-32 rounded"></div>
        <div class="skeleton-line h-3.5 w-16 rounded-full"></div>
      </div>
      <div class="skeleton-line h-3 w-2/3 rounded"></div>
    </div>
  `).join("");
  container.innerHTML = rows;
}

/**
 * Render skeleton stat cards (for dashboard-style metric grids).
 */
export function showStatSkeletons(container, count = 3) {
  if (!container) return;
  const cards = Array.from({ length: count }, () => `
    <div class="skeleton-card bg-slate-900/40 border border-slate-800/40 rounded-xl p-4 space-y-2" aria-hidden="true">
      <div class="skeleton-line h-3 w-20 rounded"></div>
      <div class="skeleton-line h-7 w-16 rounded"></div>
    </div>
  `).join("");
  container.innerHTML = cards;
}

export function clearSkeletons(container) {
  if (!container) return;
  container.querySelectorAll(".skeleton-card").forEach((el) => el.remove());
}

// ── Card Wrapper ─────────────────────────────────────────────────

/**
 * Build a standard card wrapper element.
 *
 * @param {Object} opts
 * @param {string}  opts.id           - Value for a data-* identifier attribute
 * @param {string}  opts.dataAttr     - Name of the data attribute (e.g. "job-id")
 * @param {string}  [opts.hoverColor] - Tailwind color for hover border (e.g. "indigo")
 * @param {string}  [opts.role]       - ARIA role (default "article")
 * @param {string}  [opts.ariaLabel]  - Accessible label
 * @param {string}  [opts.extraClass] - Additional classes
 * @param {string}  opts.innerHTML    - Card inner HTML
 * @returns {string} HTML string
 */
export function card({
  id,
  dataAttr = "id",
  hoverColor = "indigo",
  role = "article",
  ariaLabel = "",
  extraClass = "",
  innerHTML = "",
}) {
  const hover = `hover:border-${hoverColor}-500/25 hover:bg-slate-800/40`;
  return `<div class="bg-slate-900/40 border border-slate-800/40 rounded-xl p-5 transition-all ${hover} group ${extraClass}"
    data-${dataAttr}="${escapeHtml(String(id))}"
    role="${role}"
    ${ariaLabel ? `aria-label="${escapeHtml(ariaLabel)}"` : ""}>
    ${innerHTML}
  </div>`;
}

// ── Card Grid ────────────────────────────────────────────────────

export function cardGrid({ ariaLabel = "Items", id = "", innerHTML = "" }) {
  return `<div${id ? ` id="${id}"` : ""} class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4" role="list" aria-label="${escapeHtml(ariaLabel)}" aria-live="polite">
    ${innerHTML}
  </div>`;
}

// ── Empty State ──────────────────────────────────────────────────

export function emptyState({ icon = "inbox", message = "Nothing here yet", action = "" }) {
  return `<div class="empty-state" role="status">
    <span class="material-symbols-outlined" aria-hidden="true">${escapeHtml(icon)}</span>
    <p>${escapeHtml(message)}</p>
    ${action}
  </div>`;
}

// ── Badge ────────────────────────────────────────────────────────

/**
 * Render a small colored badge.
 * @param {string} text
 * @param {"indigo"|"emerald"|"amber"|"red"|"slate"|"purple"|"cyan"} color
 */
export function badge(text, color = "slate") {
  const colors = {
    indigo:  "bg-indigo-500/15 text-indigo-400",
    emerald: "bg-emerald-500/15 text-emerald-400",
    amber:   "bg-amber-500/15 text-amber-400",
    red:     "bg-red-500/15 text-red-400",
    slate:   "bg-slate-700/60 text-slate-400",
    purple:  "bg-purple-500/15 text-purple-400",
    cyan:    "bg-cyan-500/15 text-cyan-400",
  };
  const cls = colors[color] || colors.slate;
  return `<span class="text-[11px] font-semibold uppercase tracking-widest px-2 py-0.5 rounded-full ${cls}">${escapeHtml(text)}</span>`;
}

// ── View Header ──────────────────────────────────────────────────

/**
 * Standard view header with icon, title, and optional count badge.
 */
export function viewHeader({ icon, iconColor = "indigo", title, count, actionBtn = "" }) {
  const countBadge = count != null
    ? badge(`${count} ${title.toLowerCase()}${count !== 1 ? "s" : ""}`, iconColor)
    : "";
  return `<div class="flex items-center justify-between">
    <div class="flex items-center gap-2.5">
      <span class="material-symbols-outlined text-${iconColor}-400 text-xl" aria-hidden="true">${escapeHtml(icon)}</span>
      <h2 class="text-lg font-headline font-bold tracking-tight text-slate-100">${escapeHtml(title)}</h2>
      ${countBadge}
    </div>
    ${actionBtn}
  </div>`;
}
