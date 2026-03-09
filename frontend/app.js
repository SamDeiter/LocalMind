/**
 * LocalMind — Client Application
 * Handles chat UI, streaming responses, conversation management, and settings.
 */

const API_BASE = window.location.origin;
const DEFAULT_SYSTEM_PROMPT = `You are LocalMind, a helpful and skilled AI coding assistant. You write clean, well-documented code with clear explanations. When asked to write code, always include comments. When debugging, explain the root cause before the fix.`;

// ── State ──────────────────────────────────────────────────────────────────
let state = {
  conversations: [],
  currentConversationId: null,
  currentMessages: [],
  currentModel: "qwen2.5-coder:32b",
  systemPrompt: localStorage.getItem("systemPrompt") || DEFAULT_SYSTEM_PROMPT,
  isStreaming: false,
  abortController: null,
};

// ── DOM References ─────────────────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const messagesContainer = $("#messagesContainer");
const messagesEl = $("#messages");
const welcomeScreen = $("#welcomeScreen");
const messageInput = $("#messageInput");
const sendBtn = $("#sendBtn");
const stopBtn = $("#stopBtn");
const newChatBtn = $("#newChatBtn");
const conversationList = $("#conversationList");
const modelSelect = $("#modelSelect");
const activeModelSpan = $("#activeModel");
const chatTitle = $("#chatTitle");
const statusDot = $(".status-dot");
const statusText = $(".status-text");
const settingsBtn = $("#settingsBtn");
const settingsModal = $("#settingsModal");
const closeSettings = $("#closeSettings");
const systemPromptInput = $("#systemPrompt");
const saveSettingsBtn = $("#saveSettingsBtn");
const resetPromptBtn = $("#resetPromptBtn");
const sidebarToggle = $("#sidebarToggle");
const sidebar = $("#sidebar");
const editTitleBtn = $("#editTitleBtn");

// ── Initialize ─────────────────────────────────────────────────────────────
async function init() {
  await checkHealth();
  await loadModels();
  await loadConversations();
  setupEventListeners();
  autoResizeTextarea();

  // Restore system prompt in settings
  systemPromptInput.value = state.systemPrompt;

  // Health check every 10 seconds
  setInterval(checkHealth, 10000);
}

// ── Health Check ───────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const resp = await fetch(`${API_BASE}/api/health`);
    const data = await resp.json();
    if (data.ollama) {
      statusDot.classList.add("connected");
      statusText.textContent = "Ollama connected";
    } else {
      statusDot.classList.remove("connected");
      statusText.textContent = "Ollama not running";
    }
  } catch {
    statusDot.classList.remove("connected");
    statusText.textContent = "Server offline";
  }
}

// ── Models ─────────────────────────────────────────────────────────────────
async function loadModels() {
  try {
    const resp = await fetch(`${API_BASE}/api/models`);
    const data = await resp.json();
    if (data.models && data.models.length > 0) {
      modelSelect.innerHTML = "";
      data.models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        modelSelect.appendChild(opt);
      });
      state.currentModel = data.models[0].name;
      activeModelSpan.textContent = state.currentModel;
    }
  } catch {
    // Keep default option
  }
}

// ── Conversations ──────────────────────────────────────────────────────────
async function loadConversations() {
  try {
    const resp = await fetch(`${API_BASE}/api/conversations`);
    const data = await resp.json();
    state.conversations = data.conversations || [];
    renderConversationList();
  } catch {
    state.conversations = [];
  }
}

function renderConversationList() {
  conversationList.innerHTML = "";
  state.conversations.forEach((conv) => {
    const item = document.createElement("div");
    item.className = `conv-item ${conv.id === state.currentConversationId ? "active" : ""}`;
    item.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink:0;color:var(--text-muted)"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
            <span class="conv-title">${escapeHtml(conv.title)}</span>
            <button class="conv-delete" title="Delete conversation">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            </button>
        `;
    item.addEventListener("click", (e) => {
      if (e.target.closest(".conv-delete")) {
        deleteConversation(conv.id);
        return;
      }
      switchConversation(conv.id);
    });
    conversationList.appendChild(item);
  });
}

async function createNewConversation(firstMessage = null) {
  const title = firstMessage
    ? firstMessage.substring(0, 60) + (firstMessage.length > 60 ? "..." : "")
    : "New Conversation";
  try {
    const resp = await fetch(`${API_BASE}/api/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        model: state.currentModel,
        system_prompt: state.systemPrompt,
      }),
    });
    const data = await resp.json();
    state.currentConversationId = data.id;
    state.currentMessages = [];
    chatTitle.textContent = title;
    await loadConversations();
    return data.id;
  } catch (e) {
    console.error("Failed to create conversation:", e);
    return null;
  }
}

async function switchConversation(convId) {
  if (state.isStreaming) return;
  state.currentConversationId = convId;
  try {
    const resp = await fetch(`${API_BASE}/api/conversations/${convId}`);
    const data = await resp.json();
    state.currentMessages = data.messages || [];
    chatTitle.textContent = data.conversation.title;
    renderMessages();
    renderConversationList();
  } catch (e) {
    console.error("Failed to load conversation:", e);
  }
}

async function deleteConversation(convId) {
  try {
    await fetch(`${API_BASE}/api/conversations/${convId}`, {
      method: "DELETE",
    });
    if (state.currentConversationId === convId) {
      state.currentConversationId = null;
      state.currentMessages = [];
      showWelcome();
    }
    await loadConversations();
  } catch (e) {
    console.error("Failed to delete conversation:", e);
  }
}

// ── Chat ───────────────────────────────────────────────────────────────────
async function sendMessage() {
  const content = messageInput.value.trim();
  if (!content || state.isStreaming) return;

  // Create conversation if needed
  if (!state.currentConversationId) {
    const convId = await createNewConversation(content);
    if (!convId) return;
  }

  // Add user message
  state.currentMessages.push({ role: "user", content });
  messageInput.value = "";
  autoResizeTextarea();
  hideWelcome();
  renderMessages();
  scrollToBottom();

  // Start streaming
  state.isStreaming = true;
  sendBtn.classList.add("hidden");
  stopBtn.classList.remove("hidden");

  // Add placeholder for assistant
  const assistantMsg = { role: "assistant", content: "" };
  state.currentMessages.push(assistantMsg);
  renderMessages();

  state.abortController = new AbortController();

  try {
    const resp = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: state.currentModel,
        messages: state.currentMessages.slice(0, -1), // Exclude empty assistant
        conversation_id: state.currentConversationId,
        system_prompt: state.systemPrompt,
      }),
      signal: state.abortController.signal,
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            if (data.content) {
              assistantMsg.content += data.content;
              updateLastMessage(assistantMsg.content);
              scrollToBottom();
            }
            if (data.error) {
              assistantMsg.content += `\n\n**Error:** ${data.error}`;
              updateLastMessage(assistantMsg.content);
            }
          } catch {
            // Skip malformed JSON
          }
        }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") {
      assistantMsg.content += `\n\n**Connection error:** ${e.message}`;
      updateLastMessage(assistantMsg.content);
    }
  }

  state.isStreaming = false;
  state.abortController = null;
  sendBtn.classList.remove("hidden");
  stopBtn.classList.add("hidden");
  scrollToBottom();
}

function stopStreaming() {
  if (state.abortController) {
    state.abortController.abort();
  }
}

// ── Rendering ──────────────────────────────────────────────────────────────
function renderMessages() {
  messagesEl.innerHTML = "";
  state.currentMessages.forEach((msg, idx) => {
    const msgEl = createMessageElement(msg, idx);
    messagesEl.appendChild(msgEl);
  });
  highlightCodeBlocks();
}

function createMessageElement(msg, idx) {
  const div = document.createElement("div");
  div.className = `message ${msg.role}`;
  div.id = `message-${idx}`;

  const avatar = msg.role === "user" ? "S" : "🧠";
  const content =
    msg.content ||
    '<div class="typing-indicator"><span></span><span></span><span></span></div>';
  const rendered = msg.content ? renderMarkdown(msg.content) : content;

  div.innerHTML = `
        <div class="message-inner">
            <div class="message-avatar">${avatar}</div>
            <div class="message-content">${rendered}</div>
        </div>
    `;
  return div;
}

function updateLastMessage(content) {
  const msgs = messagesEl.querySelectorAll(".message");
  const lastMsg = msgs[msgs.length - 1];
  if (lastMsg) {
    const contentEl = lastMsg.querySelector(".message-content");
    contentEl.innerHTML = renderMarkdown(content);
    highlightCodeBlocks();
  }
}

function renderMarkdown(text) {
  // Configure marked
  marked.setOptions({
    breaks: true,
    gfm: true,
    highlight: function (code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    },
  });

  let html = marked.parse(text);

  // Add code headers with copy buttons
  html = html.replace(
    /<pre><code class="language-(\w+)">/g,
    `<pre><div class="code-header"><span>$1</span><button class="copy-btn" onclick="copyCode(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy</button></div><code class="language-$1">`,
  );

  // Handle code blocks without language
  html = html.replace(
    /<pre><code(?!.*class="language-)/g,
    `<pre><div class="code-header"><span>code</span><button class="copy-btn" onclick="copyCode(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy</button></div><code`,
  );

  return html;
}

function highlightCodeBlocks() {
  document.querySelectorAll(".message-content pre code").forEach((block) => {
    if (!block.dataset.highlighted) {
      hljs.highlightElement(block);
      block.dataset.highlighted = "true";
    }
  });
}

// ── Copy Code ──────────────────────────────────────────────────────────────
window.copyCode = function (btn) {
  const codeBlock = btn.closest("pre").querySelector("code");
  const text = codeBlock.textContent;
  navigator.clipboard.writeText(text).then(() => {
    btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg> Copied!`;
    btn.classList.add("copied");
    setTimeout(() => {
      btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy`;
      btn.classList.remove("copied");
    }, 2000);
  });
};

// ── UI Helpers ─────────────────────────────────────────────────────────────
function showWelcome() {
  welcomeScreen.classList.remove("hidden");
  messagesEl.innerHTML = "";
  chatTitle.textContent = "New Conversation";
}

function hideWelcome() {
  welcomeScreen.classList.add("hidden");
}

function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function autoResizeTextarea() {
  messageInput.style.height = "auto";
  messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + "px";
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ── Event Listeners ────────────────────────────────────────────────────────
function setupEventListeners() {
  // Send message
  sendBtn.addEventListener("click", sendMessage);
  messageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  messageInput.addEventListener("input", autoResizeTextarea);

  // Stop streaming
  stopBtn.addEventListener("click", stopStreaming);

  // New chat
  newChatBtn.addEventListener("click", () => {
    state.currentConversationId = null;
    state.currentMessages = [];
    showWelcome();
    renderConversationList();
    messageInput.focus();
  });

  // Model selector
  modelSelect.addEventListener("change", (e) => {
    state.currentModel = e.target.value;
    activeModelSpan.textContent = state.currentModel;
  });

  // Quick prompts
  document.querySelectorAll(".quick-prompt").forEach((btn) => {
    btn.addEventListener("click", () => {
      messageInput.value = btn.dataset.prompt;
      autoResizeTextarea();
      sendMessage();
    });
  });

  // Settings modal
  settingsBtn.addEventListener("click", () => {
    settingsModal.classList.remove("hidden");
    systemPromptInput.value = state.systemPrompt;
  });

  closeSettings.addEventListener("click", () => {
    settingsModal.classList.add("hidden");
  });

  saveSettingsBtn.addEventListener("click", () => {
    state.systemPrompt = systemPromptInput.value;
    localStorage.setItem("systemPrompt", state.systemPrompt);
    settingsModal.classList.add("hidden");
  });

  resetPromptBtn.addEventListener("click", () => {
    systemPromptInput.value = DEFAULT_SYSTEM_PROMPT;
  });

  settingsModal.addEventListener("click", (e) => {
    if (e.target === settingsModal) {
      settingsModal.classList.add("hidden");
    }
  });

  // Sidebar toggle (mobile)
  sidebarToggle.addEventListener("click", () => {
    sidebar.classList.toggle("open");
  });

  // Edit title
  editTitleBtn.addEventListener("click", async () => {
    if (!state.currentConversationId) return;
    const newTitle = prompt("Enter new title:", chatTitle.textContent);
    if (newTitle && newTitle.trim()) {
      chatTitle.textContent = newTitle.trim();
      await fetch(
        `${API_BASE}/api/conversations/${state.currentConversationId}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: newTitle.trim() }),
        },
      );
      await loadConversations();
    }
  });

  // Keyboard shortcut: Ctrl+N for new chat
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "n") {
      e.preventDefault();
      newChatBtn.click();
    }
  });
}

// ── Start ──────────────────────────────────────────────────────────────────
init();
