/**
 * LocalMind v2 — Client Application
 * Chat + Agent mode with tool execution visualization and multi-model support.
 */

const API_BASE = window.location.origin;
const DEFAULT_SYSTEM_PROMPT = `You are LocalMind, a helpful and skilled AI coding assistant. You write clean, well-documented code with clear explanations. When asked to write code, always include comments. When debugging, explain the root cause before the fix.`;

// ── State ──────────────────────────────────────────────────────────────────
let state = {
  conversations: [],
  currentConversationId: null,
  currentMessages: [], // [{role, content, toolData?}]
  currentModel: "qwen2.5-coder:32b",
  systemPrompt: localStorage.getItem("systemPrompt") || DEFAULT_SYSTEM_PROMPT,
  mode: localStorage.getItem("mode") || "chat", // 'chat' or 'agent'
  workingDir: localStorage.getItem("workingDir") || "",
  isStreaming: false,
  abortController: null,
};

// ── DOM ────────────────────────────────────────────────────────────────────
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
const chatModeBtn = $("#chatModeBtn");
const agentModeBtn = $("#agentModeBtn");
const projectDirSection = $("#projectDirSection");
const projectDirInput = $("#projectDir");
const modeBadge = $("#modeBadge");
const modeHint = $("#modeHint");
const inputContainer = $("#inputContainer");

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {
  await checkHealth();
  await loadModels();
  await loadConversations();
  setupEventListeners();
  autoResizeTextarea();
  setMode(state.mode);

  systemPromptInput.value = state.systemPrompt;
  projectDirInput.value = state.workingDir;

  setInterval(checkHealth, 10000);
}

// ── Health ──────────────────────────────────────────────────────────────────
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

// ── Models ──────────────────────────────────────────────────────────────────
async function loadModels() {
  try {
    const resp = await fetch(`${API_BASE}/api/models`);
    const data = await resp.json();
    if (data.models && data.models.length > 0) {
      modelSelect.innerHTML = "";
      // Add auto-route option if multiple models installed
      if (data.models.length > 1) {
        const autoOpt = document.createElement("option");
        autoOpt.value = "auto";
        autoOpt.textContent = "🧠 Smart Auto-Route";
        modelSelect.appendChild(autoOpt);
      }
      data.models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        modelSelect.appendChild(opt);
      });
      // Restore last used model or use first
      const lastModel = localStorage.getItem("selectedModel");
      if (
        lastModel &&
        (lastModel === "auto" || data.models.some((m) => m.name === lastModel))
      ) {
        state.currentModel = lastModel;
        modelSelect.value = lastModel;
      } else {
        state.currentModel = data.models[0].name;
      }
      activeModelSpan.textContent =
        state.currentModel === "auto" ? "🧠 Auto" : state.currentModel;
    }
  } catch {
    /* keep default */
  }
}

// ── Mode Toggle ─────────────────────────────────────────────────────────────
function setMode(mode) {
  state.mode = mode;
  localStorage.setItem("mode", mode);

  chatModeBtn.classList.toggle("active", mode === "chat");
  agentModeBtn.classList.toggle("active", mode === "agent");

  if (mode === "agent") {
    projectDirSection.classList.remove("hidden");
    modeBadge.classList.remove("hidden");
    inputContainer.classList.add("agent-mode");
    sendBtn.classList.add("agent-mode");
    messageInput.placeholder = "Tell the agent what to build...";
    modeHint.textContent = "🤖 Agent mode · Tools enabled";
  } else {
    projectDirSection.classList.add("hidden");
    modeBadge.classList.add("hidden");
    inputContainer.classList.remove("agent-mode");
    sendBtn.classList.remove("agent-mode");
    messageInput.placeholder = "Ask me anything about code...";
    modeHint.textContent = "Running locally via Ollama";
  }
}

// ── Conversations ───────────────────────────────────────────────────────────
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
    const icon = conv.is_agent ? "🤖" : "💬";
    item.innerHTML = `
            <span class="conv-icon">${icon}</span>
            <span class="conv-title">${escapeHtml(conv.title)}</span>
            <button class="conv-delete" title="Delete">
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
        working_dir: state.workingDir,
        is_agent: state.mode === "agent",
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
    state.currentMessages = (data.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
      toolData: m.tool_data ? JSON.parse(m.tool_data) : null,
    }));
    chatTitle.textContent = data.conversation.title;

    // Restore mode from conversation
    if (data.conversation.is_agent) {
      setMode("agent");
      if (data.conversation.working_dir) {
        state.workingDir = data.conversation.working_dir;
        projectDirInput.value = state.workingDir;
      }
    }

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
    console.error("Failed to delete:", e);
  }
}

// ── Send Message ────────────────────────────────────────────────────────────
async function sendMessage() {
  const content = messageInput.value.trim();
  if (!content || state.isStreaming) return;

  if (!state.currentConversationId) {
    const convId = await createNewConversation(content);
    if (!convId) return;
  }

  state.currentMessages.push({ role: "user", content });
  messageInput.value = "";
  autoResizeTextarea();
  hideWelcome();
  renderMessages();
  scrollToBottom();

  state.isStreaming = true;
  sendBtn.classList.add("hidden");
  stopBtn.classList.remove("hidden");

  // Add placeholder for assistant
  const assistantMsg = { role: "assistant", content: "", toolData: [] };
  state.currentMessages.push(assistantMsg);
  renderMessages();

  state.abortController = new AbortController();

  const isAgent = state.mode === "agent";
  const endpoint = isAgent ? "/api/agent/chat" : "/api/chat";

  // Build messages for API (only role + content)
  const apiMessages = state.currentMessages.slice(0, -1).map((m) => ({
    role: m.role,
    content: m.content,
  }));

  // Smart model routing — if set to 'auto', ask the server which model is best
  let modelToUse = state.currentModel;
  if (state.currentModel === "auto") {
    try {
      const routeResp = await fetch(`${API_BASE}/api/route-model`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: content }),
      });
      const routeData = await routeResp.json();
      modelToUse = routeData.selected_model;
      activeModelSpan.textContent = `🧠 → ${modelToUse} (${routeData.task_type})`;
    } catch {
      modelToUse = modelSelect.options[1]?.value || "qwen2.5-coder:32b";
    }
  }

  try {
    const resp = await fetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: modelToUse,
        messages: apiMessages,
        conversation_id: state.currentConversationId,
        system_prompt: state.systemPrompt,
        working_dir: state.workingDir,
        auto_execute: true,
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
        if (!line.startsWith("data: ")) continue;
        try {
          const event = JSON.parse(line.slice(6));
          handleStreamEvent(event, assistantMsg);
        } catch {
          /* skip */
        }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") {
      assistantMsg.content += `\n\n**Error:** ${e.message}`;
      updateLastMessage(assistantMsg);
    }
  }

  state.isStreaming = false;
  state.abortController = null;
  sendBtn.classList.remove("hidden");
  stopBtn.classList.add("hidden");
  scrollToBottom();
}

function handleStreamEvent(event, assistantMsg) {
  switch (event.type) {
    case "thinking":
      // Show thinking indicator
      updateLastMessage(assistantMsg, true);
      scrollToBottom();
      break;

    case "tool_call":
      if (!assistantMsg.toolData) assistantMsg.toolData = [];
      assistantMsg.toolData.push({
        type: "call",
        name: event.tool.name,
        arguments: event.tool.arguments,
        iteration: event.tool.iteration,
        status: "running",
      });
      updateLastMessage(assistantMsg);
      scrollToBottom();
      break;

    case "tool_result":
      if (assistantMsg.toolData) {
        // Find the last tool call with this name and update its status
        for (let i = assistantMsg.toolData.length - 1; i >= 0; i--) {
          if (
            assistantMsg.toolData[i].type === "call" &&
            assistantMsg.toolData[i].name === event.result.name &&
            assistantMsg.toolData[i].status === "running"
          ) {
            assistantMsg.toolData[i].status = event.result.success
              ? "success"
              : "error";
            assistantMsg.toolData[i].result = event.result.data;
            break;
          }
        }
      }
      updateLastMessage(assistantMsg);
      scrollToBottom();
      break;

    case "content":
      assistantMsg.content += event.content;
      updateLastMessage(assistantMsg);
      scrollToBottom();
      break;

    case "error":
      assistantMsg.content += `\n\n**Error:** ${event.error}`;
      updateLastMessage(assistantMsg);
      break;

    case "done":
      updateLastMessage(assistantMsg);
      break;
  }
}

function stopStreaming() {
  if (state.abortController) state.abortController.abort();
}

// ── Rendering ───────────────────────────────────────────────────────────────
function renderMessages() {
  messagesEl.innerHTML = "";
  state.currentMessages.forEach((msg, idx) => {
    messagesEl.appendChild(createMessageElement(msg, idx));
  });
  highlightCodeBlocks();
}

function createMessageElement(msg, idx) {
  const div = document.createElement("div");
  div.className = `message ${msg.role}`;
  div.id = `message-${idx}`;

  const avatar = msg.role === "user" ? "S" : "🧠";
  let contentHtml = "";

  // Render tool cards if present
  if (msg.toolData && msg.toolData.length > 0) {
    contentHtml += renderToolCards(msg.toolData);
  }

  // Render text content
  if (msg.content) {
    contentHtml += renderMarkdown(msg.content);
  } else if (!msg.toolData || msg.toolData.length === 0) {
    contentHtml =
      '<div class="typing-indicator"><span></span><span></span><span></span></div>';
  }

  div.innerHTML = `
        <div class="message-inner">
            <div class="message-avatar">${avatar}</div>
            <div class="message-content">${contentHtml}</div>
        </div>
    `;
  return div;
}

function updateLastMessage(msg, showThinking = false) {
  const msgs = messagesEl.querySelectorAll(".message");
  const lastMsg = msgs[msgs.length - 1];
  if (!lastMsg) return;

  const contentEl = lastMsg.querySelector(".message-content");
  let html = "";

  if (msg.toolData && msg.toolData.length > 0) {
    html += renderToolCards(msg.toolData);
  }

  if (showThinking && !msg.content) {
    html +=
      '<div class="thinking-indicator"><div class="thinking-dots"><span></span><span></span><span></span></div><span>Thinking...</span></div>';
  }

  if (msg.content) {
    html += renderMarkdown(msg.content);
  }

  contentEl.innerHTML = html;
  highlightCodeBlocks();

  // Attach toggle listeners to new tool cards
  contentEl.querySelectorAll(".tool-card-header").forEach((header) => {
    header.addEventListener("click", () => {
      const body = header.nextElementSibling;
      if (body) body.classList.toggle("open");
    });
  });
}

// ── Tool Card Rendering ─────────────────────────────────────────────────────
function renderToolCards(toolData) {
  return toolData
    .filter((t) => t.type === "call")
    .map((tool) => {
      const icons = {
        read_file: "📄",
        write_file: "✏️",
        list_directory: "📁",
        run_command: "⚡",
        search_files: "🔍",
        web_search: "🌐",
      };
      const icon = icons[tool.name] || "🔧";
      const statusIcon =
        tool.status === "running"
          ? "⏳"
          : tool.status === "success"
            ? "✅"
            : "❌";
      const statusClass = tool.status || "running";

      // Format args for display
      let argsStr = "";
      if (tool.arguments) {
        if (tool.name === "run_command") argsStr = tool.arguments.command || "";
        else if (tool.name === "read_file" || tool.name === "write_file")
          argsStr = tool.arguments.path || "";
        else if (tool.name === "list_directory")
          argsStr = tool.arguments.path || ".";
        else if (tool.name === "search_files")
          argsStr = `"${tool.arguments.pattern || ""}"`;
        else if (tool.name === "web_search")
          argsStr = tool.arguments.query || "";
        else argsStr = JSON.stringify(tool.arguments);
      }

      // Format result for display
      let resultHtml = "";
      if (tool.result) {
        const r = tool.result;
        if (tool.name === "read_file" && r.content) {
          resultHtml = `<pre>${escapeHtml(r.content.substring(0, 2000))}${r.content.length > 2000 ? "\n...(truncated)" : ""}</pre>`;
        } else if (tool.name === "run_command") {
          let out = "";
          if (r.stdout) out += r.stdout;
          if (r.stderr) out += (out ? "\n" : "") + r.stderr;
          resultHtml = `<pre>${escapeHtml((out || "(no output)").substring(0, 2000))}</pre>`;
        } else if (tool.name === "list_directory" && r.items) {
          resultHtml =
            "<pre>" +
            r.items
              .map(
                (i) =>
                  `${i.type === "directory" ? "📁" : "📄"} ${i.name}${i.type === "file" ? ` (${formatBytes(i.size)})` : ""}`,
              )
              .join("\n") +
            "</pre>";
        } else if (tool.name === "search_files" && r.matches) {
          resultHtml =
            "<pre>" +
            r.matches
              .slice(0, 20)
              .map((m) => `${m.file}:${m.line} — ${m.content}`)
              .join("\n") +
            (r.matches.length > 20
              ? `\n...(${r.matches.length - 20} more)`
              : "") +
            "</pre>";
        } else if (tool.name === "web_search" && r.results) {
          resultHtml =
            "<pre>" +
            r.results
              .map((res) => `${res.title}\n  ${res.snippet}`)
              .join("\n\n") +
            "</pre>";
        } else if (tool.name === "write_file") {
          resultHtml = `<pre>${r.action || "wrote"} ${r.path || ""} (${formatBytes(r.bytes || 0)})</pre>`;
        } else {
          resultHtml = `<pre>${escapeHtml(JSON.stringify(r, null, 2).substring(0, 1000))}</pre>`;
        }
      }

      return `
            <div class="tool-card">
                <div class="tool-card-header">
                    <span class="tool-icon">${icon}</span>
                    <span class="tool-name">${tool.name}</span>
                    <span class="tool-args">${escapeHtml(argsStr)}</span>
                    <span class="tool-status ${statusClass}">${statusIcon}</span>
                </div>
                <div class="tool-card-body">
                    ${resultHtml}
                </div>
            </div>
        `;
    })
    .join("");
}

// ── Markdown Rendering ──────────────────────────────────────────────────────
function renderMarkdown(text) {
  marked.setOptions({
    breaks: true,
    gfm: true,
    highlight: (code, lang) => {
      if (lang && hljs.getLanguage(lang))
        return hljs.highlight(code, { language: lang }).value;
      return hljs.highlightAuto(code).value;
    },
  });

  let html = marked.parse(text);

  html = html.replace(
    /<pre><code class="language-(\w+)">/g,
    `<pre><div class="code-header"><span>$1</span><button class="copy-btn" onclick="copyCode(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy</button></div><code class="language-$1">`,
  );
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

// ── Copy Code ───────────────────────────────────────────────────────────────
window.copyCode = function (btn) {
  const codeBlock = btn.closest("pre").querySelector("code");
  navigator.clipboard.writeText(codeBlock.textContent).then(() => {
    btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg> Copied!`;
    btn.classList.add("copied");
    setTimeout(() => {
      btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy`;
      btn.classList.remove("copied");
    }, 2000);
  });
};

// ── Helpers ──────────────────────────────────────────────────────────────────
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
function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

// ── Event Listeners ─────────────────────────────────────────────────────────
function setupEventListeners() {
  sendBtn.addEventListener("click", sendMessage);
  messageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  messageInput.addEventListener("input", autoResizeTextarea);
  stopBtn.addEventListener("click", stopStreaming);

  newChatBtn.addEventListener("click", () => {
    state.currentConversationId = null;
    state.currentMessages = [];
    showWelcome();
    renderConversationList();
    messageInput.focus();
  });

  // Mode toggle
  chatModeBtn.addEventListener("click", () => setMode("chat"));
  agentModeBtn.addEventListener("click", () => setMode("agent"));

  // Project directory
  projectDirInput.addEventListener("change", (e) => {
    state.workingDir = e.target.value.trim();
    localStorage.setItem("workingDir", state.workingDir);
  });

  // Model selector
  modelSelect.addEventListener("change", (e) => {
    state.currentModel = e.target.value;
    activeModelSpan.textContent = state.currentModel;
    localStorage.setItem("selectedModel", state.currentModel);
  });

  // Quick prompts
  document.querySelectorAll(".quick-prompt").forEach((btn) => {
    btn.addEventListener("click", () => {
      const promptMode = btn.dataset.mode || "chat";
      if (promptMode === "agent" && state.mode !== "agent") {
        setMode("agent");
      }
      messageInput.value = btn.dataset.prompt;
      autoResizeTextarea();
      sendMessage();
    });
  });

  // Settings
  settingsBtn.addEventListener("click", () => {
    settingsModal.classList.remove("hidden");
    systemPromptInput.value = state.systemPrompt;
  });
  closeSettings.addEventListener("click", () =>
    settingsModal.classList.add("hidden"),
  );
  saveSettingsBtn.addEventListener("click", () => {
    state.systemPrompt = systemPromptInput.value;
    localStorage.setItem("systemPrompt", state.systemPrompt);
    settingsModal.classList.add("hidden");
  });
  resetPromptBtn.addEventListener("click", () => {
    systemPromptInput.value = DEFAULT_SYSTEM_PROMPT;
  });
  settingsModal.addEventListener("click", (e) => {
    if (e.target === settingsModal) settingsModal.classList.add("hidden");
  });

  // Sidebar toggle
  sidebarToggle.addEventListener("click", () =>
    sidebar.classList.toggle("open"),
  );

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

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "n") {
      e.preventDefault();
      newChatBtn.click();
    }
  });
}

// ── Start ───────────────────────────────────────────────────────────────────
init();
