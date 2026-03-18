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
  } catch {
    /* offline */
  }
}

export function renderConversations() {
  const list = document.getElementById("conversationList");
  if (!list) return;
  list.innerHTML = "";
  state.conversations.forEach((c) => {
    const div = document.createElement("div");
    div.className = `conversation-item ${c.id === state.currentConvId ? "active" : ""}`;
    div.innerHTML = `
      <span>${escapeHtml(c.title || "New Chat")}</span>
      <div>
        <button class="export-btn" title="Export">📥</button>
        <button class="delete-btn" title="Delete">✕</button>
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
