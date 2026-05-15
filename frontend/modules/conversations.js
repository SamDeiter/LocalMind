/**
 * Conversation CRUD — list, load, delete, export.
 */

import { API, state, messagesContainer, welcomeScreen } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";
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
        ? "text-[#c0c1ff] bg-primary/10 border-l-2 border-[#6366f1]"
        : "text-[#8e9192] hover:text-[#e5e2e1] hover:bg-[#1c1b1b]"
    }`;
    div.innerHTML = `
      <span class="truncate pr-2 pointer-events-none">${escapeHtml(c.title || "New Chat")}</span>
      <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
        <button class="export-btn p-1 text-outline hover:text-secondary rounded" title="Export" aria-label="Export conversation">
          <span class="material-symbols-outlined text-base">download</span>
        </button>
        <button class="delete-btn p-1 text-outline hover:text-error rounded" title="Delete" aria-label="Delete conversation">
          <span class="material-symbols-outlined text-base">delete</span>
        </button>
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
  if (!confirm("Are you sure you want to delete this conversation?")) return;
  try {
    const r = await fetch(`${API}/api/conversations/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error("Delete failed");

    if (state.currentConvId === id) {
      state.currentConvId = null;
      state.messages = [];
      if (messagesContainer) messagesContainer.innerHTML = "";
      if (welcomeScreen) welcomeScreen.style.display = "";
    }
    await loadConversations();
    showToast("Conversation deleted", "success");
  } catch (e) {
    console.error("Delete conversation failed:", e);
    showToast("Failed to delete conversation", "error");
  }
}

export async function exportConversation(id, title) {
  try {
    showToast("Exporting conversation...", "info");
    const r = await fetch(`${API}/api/conversations/${id}/export?format=md`);
    if (!r.ok) {
      showToast("Export failed", "error");
      return;
    }
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
    showToast("Export failed", "error");
  }
}
