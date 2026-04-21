/**
 * Sidebar features — Hardware dashboard, Memory viewer, Document RAG,
 * Version badge.
 */

import { API } from "./state.js";
import { escapeHtml } from "./utils.js";

// ── Hardware polling (utility strip + Ops page) ─────────────────
let hwInterval = null;

function setText(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v;
}

export async function pollHardware() {
  try {
    const r = await fetch(`${API}/api/hardware`);
    if (!r.ok) throw new Error("HTTP " + r.status);
    const h = await r.json();

    const cpu = h.cpu != null ? Math.round(h.cpu) : null;
    const ram = h.ram != null ? Math.round(h.ram) : (h.memory != null ? Math.round(h.memory) : null);
    const gpu = h.gpu != null ? Math.round(h.gpu) : null;

    setText("utilCpu", cpu != null ? `${cpu}%` : "—");
    setText("utilRam", ram != null ? `${ram}%` : "—");
    setText("utilGpu", gpu != null ? `${gpu}%` : "—");

    const dot = document.getElementById("utilOnlineDot");
    if (dot) dot.style.background = "var(--lm-status-ok)";
    setText("utilOnline", "online");
  } catch (_) {
    const dot = document.getElementById("utilOnlineDot");
    if (dot) dot.style.background = "var(--lm-status-failed)";
    setText("utilOnline", "offline");
  }

  try {
    const r = await fetch(`${API}/api/jobs/stats`);
    if (r.ok) {
      const s = await r.json();
      setText("utilQueue",   String(s.queued ?? s.queue_depth ?? 0));
      setText("utilWorkers", String(s.workers ?? s.active_workers ?? "—"));
    }
  } catch (_) { /* silent */ }
}

export function startHwPolling() {
  if (hwInterval) return;
  pollHardware();
  hwInterval = setInterval(() => {
    if (!document.hidden) pollHardware();
  }, 3000);
}

// ── Memory Viewer ───────────────────────────────────────────────
const CATEGORY_ICON = {
  fact: "lightbulb",
  preference: "favorite",
  instruction: "assignment",
  interaction: "forum",
  general: "memory",
  goal: "flag",
  feedback: "rate_review",
  project: "folder_managed",
  reference: "link",
  user: "person",
};

function categoryIcon(cat) {
  return CATEGORY_ICON[cat] || "memory";
}

function formatMemoryTime(raw) {
  // Backend sends "2026-04-11 15:29" (local-ish, no tz). Treat as local.
  if (!raw || raw === "unknown") return { rel: "—", abs: "unknown" };
  const iso = raw.replace(" ", "T");
  const d = new Date(iso);
  if (isNaN(d.getTime())) return { rel: raw, abs: raw };
  const now = new Date();
  const diffSec = Math.floor((now - d) / 1000);
  let rel;
  if (diffSec < 60) rel = "just now";
  else if (diffSec < 3600) rel = `${Math.floor(diffSec / 60)}m ago`;
  else if (diffSec < 86400) rel = `${Math.floor(diffSec / 3600)}h ago`;
  else if (diffSec < 86400 * 7) rel = `${Math.floor(diffSec / 86400)}d ago`;
  else
    rel = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  const abs = d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return { rel, abs };
}

function buildMemoryCard(m) {
  const cat = (m.category || "general").toLowerCase();
  const { rel, abs } = formatMemoryTime(m.created_at);
  const content = String(m.content || "");
  const isLong = content.length > 220;
  const preview = isLong ? content.slice(0, 220).trimEnd() + "…" : content;

  const art = document.createElement("article");
  art.className = "memory-card";
  art.dataset.memoryId = m.id ?? "";
  art.dataset.cat = cat;
  art.innerHTML = `
    <header class="memory-card-head">
      <span class="memory-cat memory-cat-${escapeHtml(cat)}">
        <span class="material-symbols-outlined memory-cat-icon">${categoryIcon(cat)}</span>
        <span class="memory-cat-label">${escapeHtml(cat)}</span>
      </span>
      <time class="memory-time"></time>
      <button class="memory-del" title="Delete memory" aria-label="Delete memory">
        <span class="material-symbols-outlined">close</span>
      </button>
    </header>
    <div class="memory-text${isLong ? " memory-text-clipped" : ""}"></div>
    ${isLong ? '<button class="memory-expand" type="button">Show more</button>' : ""}
  `;

  const timeEl = art.querySelector(".memory-time");
  timeEl.textContent = rel;
  timeEl.title = abs;

  const delBtn = art.querySelector(".memory-del");
  delBtn.dataset.memoryId = m.id ?? "";

  const textEl = art.querySelector(".memory-text");
  textEl.dataset.full = content;
  textEl.dataset.preview = preview;
  textEl.textContent = preview;

  return art;
}

export async function loadMemories() {
  try {
    const res = await fetch(`${API}/api/memories`);
    const data = await res.json();
    const countEl = document.getElementById("memoryCount");
    const listEl = document.getElementById("memoryList");
    if (!listEl) return;
    if (countEl) countEl.textContent = data.count || 0;

    if (!data.memories || data.memories.length === 0) {
      listEl.innerHTML =
        '<div class="memory-empty">No memories yet. Chat naturally and I\'ll learn!</div>';
      return;
    }

    // Group by category for clearer hierarchy
    const groups = {};
    for (const m of data.memories) {
      const cat = (m.category || "general").toLowerCase();
      (groups[cat] ||= []).push(m);
    }

    const catOrder = Object.keys(groups).sort(
      (a, b) => groups[b].length - groups[a].length || a.localeCompare(b),
    );

    const header = `
      <div class="memory-toolbar">
        <div class="memory-stats">
          <span class="memory-total">${data.count || data.memories.length} total</span>
          ${catOrder
            .map(
              (c) =>
                `<span class="memory-chip memory-cat-${escapeHtml(c)}" data-filter="${escapeHtml(c)}">
                   <span class="material-symbols-outlined memory-cat-icon">${categoryIcon(c)}</span>
                   ${escapeHtml(c)} · ${groups[c].length}
                 </span>`,
            )
            .join("")}
        </div>
        <input type="search" id="memorySearch" class="memory-search" placeholder="Search memories…" />
      </div>
    `;

    listEl.innerHTML = header;
    for (const c of catOrder) {
      const section = document.createElement("section");
      section.className = "memory-group";
      section.dataset.group = c;
      section.innerHTML = `
        <h3 class="memory-group-title">
          <span class="material-symbols-outlined memory-cat-icon">${categoryIcon(c)}</span>
          ${escapeHtml(c)}
          <span class="memory-group-count">${groups[c].length}</span>
        </h3>
        <div class="memory-group-body"></div>
      `;
      const body = section.querySelector(".memory-group-body");
      for (const m of groups[c]) body.appendChild(buildMemoryCard(m));
      listEl.appendChild(section);
    }

    // Delete
    listEl.querySelectorAll(".memory-del").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteMemory(btn.dataset.memoryId);
      });
    });

    // Expand/collapse long content
    listEl.querySelectorAll(".memory-expand").forEach((btn) => {
      btn.addEventListener("click", () => {
        const card = btn.closest(".memory-card");
        const text = card?.querySelector(".memory-text");
        if (!text) return;
        const expanded = text.classList.toggle("memory-text-expanded");
        text.classList.toggle("memory-text-clipped", !expanded);
        text.textContent = expanded ? text.dataset.full : text.dataset.preview;
        btn.textContent = expanded ? "Show less" : "Show more";
      });
    });

    // Search
    const searchEl = document.getElementById("memorySearch");
    if (searchEl) {
      searchEl.addEventListener("input", () => {
        const q = searchEl.value.trim().toLowerCase();
        listEl.querySelectorAll(".memory-card").forEach((card) => {
          const text = (card.querySelector(".memory-text")?.dataset.full || "").toLowerCase();
          const cat = (card.dataset.cat || "").toLowerCase();
          const match = !q || text.includes(q) || cat.includes(q);
          card.style.display = match ? "" : "none";
        });
        // Hide empty groups
        listEl.querySelectorAll(".memory-group").forEach((g) => {
          const anyVisible = Array.from(g.querySelectorAll(".memory-card")).some(
            (c) => c.style.display !== "none",
          );
          g.style.display = anyVisible ? "" : "none";
        });
      });
    }

    // Category chip filter
    listEl.querySelectorAll(".memory-chip[data-filter]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const target = chip.dataset.filter;
        const active = chip.classList.toggle("memory-chip-active");
        listEl
          .querySelectorAll(".memory-chip[data-filter]")
          .forEach((c) => c !== chip && c.classList.remove("memory-chip-active"));
        listEl.querySelectorAll(".memory-group").forEach((g) => {
          g.style.display = !active || g.dataset.group === target ? "" : "none";
        });
      });
    });
  } catch (e) {
    console.warn("Memory load failed:", e);
  }
}

export async function deleteMemory(id) {
  try {
    await fetch(`/api/memories/${id}`, { method: "DELETE" });
    await loadMemories();
  } catch (e) {
    console.warn("Memory delete failed:", e);
  }
}

export function toggleMemoryList() {
  const list = document.getElementById("memoryList");
  if (list) list.classList.toggle("open");
}

// ── Document RAG ────────────────────────────────────────────────
export async function uploadDocuments(files) {
  for (const file of files) {
    const formData = new FormData();
    formData.append("file", file);
    try {
      const r = await fetch(`${API}/api/documents/upload`, {
        method: "POST",
        body: formData,
      });
      const d = await r.json();
      if (d.success) {
        console.log(`Indexed ${file.name}: ${d.chunks} chunks`);
      } else {
        console.error(`Upload failed: ${d.error}`);
      }
    } catch (e) {
      console.error("Upload error:", e);
    }
  }
  await loadDocuments();
}

export async function loadDocuments() {
  try {
    const r = await fetch(`${API}/api/documents/`);
    const d = await r.json();
    const list = document.getElementById("documentList");
    const count = document.getElementById("docCount");
    if (!list) return;

    const docs = d.documents || [];
    if (count) count.textContent = docs.length;
    list.innerHTML = "";

    docs.forEach((doc) => {
      const div = document.createElement("div");
      div.className = "document-item";
      div.innerHTML = `
        <span class="doc-icon">📄</span>
        <span class="doc-name" title="${escapeHtml(doc.filename)}">${escapeHtml(doc.filename)}</span>
        <span class="doc-chunks">${doc.chunks} chunks</span>
        <button class="delete-btn" title="Remove">✕</button>
      `;
      div.querySelector(".delete-btn").addEventListener("click", async () => {
        await fetch(`${API}/api/documents/${encodeURIComponent(doc.filename)}`, {
          method: "DELETE",
        });
        await loadDocuments();
      });
      list.appendChild(div);
    });
  } catch {
    /* ignore */
  }
}

// ── Version Badge ───────────────────────────────────────────────
export async function loadVersion() {
  try {
    const r = await fetch(`${API}/api/version`);
    const d = await r.json();
    const badge = document.querySelector(".version-badge");
    if (badge && d.version) {
      badge.textContent = `v${d.version} #${d.build || "?"}`;
      badge.title = `LocalMind v${d.version} build #${d.build} — ${d.codename || ""}`;
    }
  } catch {
    /* ignore — version badge stays at placeholder */
  }
}
