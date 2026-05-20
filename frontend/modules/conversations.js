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
    div.dataset.id = c.id;
    div.className = `conversation-item flex items-center justify-between px-3 py-2 text-sm rounded-r-lg transition-all group focus-within:bg-[#1c1b1b] ${
      c.id === state.currentConvId
        ? "text-[#c0c1ff] bg-primary/10 border-l-2 border-[#6366f1]"
        : "text-[#8e9192] hover:text-[#e5e2e1] hover:bg-[#1c1b1b]"
    }`;
    div.innerHTML = `
      <button class="load-btn flex-1 text-left truncate pr-2 focus:outline-none" aria-label="Load conversation: ${escapeHtml(c.title || "New Chat")}">
        ${escapeHtml(c.title || "New Chat")}
      </button>
      <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
        <button class="export-btn p-1 text-outline hover:text-secondary rounded flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed" title="Export" aria-label="Export conversation">
          <span class="material-symbols-outlined text-[18px]">download</span>
        </button>
        <button class="delete-btn p-1 text-outline hover:text-error rounded flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed" title="Delete" aria-label="Delete conversation">
          <span class="material-symbols-outlined text-[18px]">delete</span>
        </button>
      </div>`;
    div.querySelector(".load-btn").addEventListener("click", () => loadConversation(c.id));
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
  if (!confirm("Are you sure you want to delete this conversation? This action cannot be undone.")) return;

  const btn = document.querySelector(`.conversation-item[data-id="${id}"] .delete-btn`);
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(`${API}/api/conversations/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to delete conversation");

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
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function exportConversation(id, title) {
  const btn = document.querySelector(`.conversation-item[data-id="${id}"] .export-btn`);
  if (btn) btn.disabled = true;

  try {
    const r = await fetch(`${API}/api/conversations/${id}/export?format=md`);
    if (!r.ok) throw new Error("Export failed");

    const text = await r.text();
    const blob = new Blob([text], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title.replace(/[^a-z0-9]/gi, "_")}.md`;
    a.click();
    URL.revokeObjectURL(url);
    showToast("Conversation exported", "success");
  } catch (e) {
    console.error("Export failed:", e);
    showToast("Export failed", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}
