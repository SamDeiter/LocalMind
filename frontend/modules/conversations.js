/**
 * Conversation CRUD — list, load, delete, export.
 */

import { API, state, messagesContainer, welcomeScreen } from "./state.js";
import { escapeHtml } from "./utils.js";
import { renderMessages } from "./chat.js";

export async function loadConversations() {
  try {
    const r = await fetch(`${API}/api/conversations`);
    const d = await r.json();
    state.conversations = d.conversations || [];
    renderConversations();
  } catch (e) {
    console.warn("Failed to load conversations:", e);
  }
}

export function renderConversations() {
  const list = document.getElementById("conversationList");
  if (!list) return;
  list.innerHTML = "";

  // Group conversations by date bucket
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const startOfYesterday = startOfToday - 86400;
  const startOf7Days = startOfToday - 86400 * 6;
  const startOf30Days = startOfToday - 86400 * 29;

  const groups = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Previous 7 Days", items: [] },
    { label: "Previous 30 Days", items: [] },
    { label: "Older", items: [] },
  ];

  for (const c of state.conversations) {
    const ts = c.updated_at || c.created_at || 0;
    if (ts >= startOfToday) groups[0].items.push(c);
    else if (ts >= startOfYesterday) groups[1].items.push(c);
    else if (ts >= startOf7Days) groups[2].items.push(c);
    else if (ts >= startOf30Days) groups[3].items.push(c);
    else groups[4].items.push(c);
  }

  for (const group of groups) {
    if (group.items.length === 0) continue;

    const header = document.createElement("div");
    header.className = "px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500";
    header.textContent = group.label;
    list.appendChild(header);

    for (const c of group.items) {
      const div = document.createElement("div");
      div.className = `conversation-item flex items-center justify-between px-3 py-2 text-sm rounded-r-lg cursor-pointer transition-all group ${
        c.id === state.currentConvId
          ? "text-[#c0c1ff] bg-primary/10 border-l-2 border-[#6366f1]"
          : "text-[#8e9192] hover:text-[#e5e2e1] hover:bg-[#1c1b1b]"
      }`;
      div.innerHTML = `
        <span class="truncate pr-2 pointer-events-none">${escapeHtml(c.title || "New Chat")}</span>
        <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button class="export-btn p-1 text-outline hover:text-secondary rounded" title="Export">📥</button>
          <button class="delete-btn p-1 text-outline hover:text-error rounded" title="Delete">✕</button>
        </div>`;
      div.addEventListener("click", () => loadConversation(c.id));
      div.querySelector(".delete-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteConversation(c.id);
      });
      div.querySelector(".export-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        exportConversation(c.id, c.title || "conversation");
      });
      list.appendChild(div);
    }
  }
}

export async function loadConversation(id) {
  try {
    const r = await fetch(`${API}/api/conversations/${id}/messages`);
    const d = await r.json();
    state.currentConvId = id;
    state.messages = d.messages || [];
    renderConversations();
    renderMessages();
    return d.messages || [];
  } catch (e) {
    console.error("Failed to load conversation:", e);
    return [];
  }
}

export async function deleteConversation(id) {
  try {
    await fetch(`${API}/api/conversations/${id}`, { method: "DELETE" });
    if (state.currentConvId === id) {
      state.currentConvId = null;
      state.messages = [];
      if (messagesContainer) messagesContainer.innerHTML = "";
      if (welcomeScreen) welcomeScreen.style.display = "";
    }
    await loadConversations();
  } catch (e) {
    console.error("Delete conversation failed:", e);
  }
}

export async function exportConversation(id, title) {
  try {
    const r = await fetch(`${API}/api/conversations/${id}/export?format=md`);
    if (!r.ok) return;
    const text = await r.text();
    const blob = new Blob([text], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title.replace(/[^a-z0-9]/gi, "_")}.md`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    console.error("Export failed:", e);
  }
}
