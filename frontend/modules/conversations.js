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
  state.conversations.forEach((c) => {
    const div = document.createElement("div");
    div.className = `conversation-item flex items-center justify-between px-3 py-2 text-sm rounded-r-lg cursor-pointer transition-all group ${
      c.id === state.currentConvId
        ? "text-indigo-300 bg-primary/10 border-l-2 border-primary"
        : "text-slate-500 hover:text-slate-200 hover:bg-slate-900/50"
    }`;
    div.innerHTML = `
      <span class="truncate pr-2 pointer-events-none">${escapeHtml(c.title || "New Chat")}</span>
      <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button class="export-btn p-1 text-slate-500 hover:text-secondary rounded focus-visible:ring-2 focus-visible:ring-primary focus:outline-none transition-colors" title="Export Chat" aria-label="Export Chat">
          <span class="material-symbols-outlined text-base pointer-events-none">download</span>
        </button>
        <button class="delete-btn p-1 text-slate-500 hover:text-error rounded focus-visible:ring-2 focus-visible:ring-error focus:outline-none transition-colors" title="Delete Chat" aria-label="Delete Chat">
          <span class="material-symbols-outlined text-base pointer-events-none">delete</span>
        </button>
      </div>`;
    div.addEventListener("click", () => loadConversation(c.id));
    div.querySelector(".delete-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      if (window.confirm("Are you sure you want to delete this conversation?")) {
        deleteConversation(c.id);
      }
    });
    div.querySelector(".export-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      exportConversation(c.id, c.title || "conversation");
    });
    list.appendChild(div);
  });
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
