/**
 * LocalMind v3 — Client Application
 * Handles chat with SSE streaming, tool call visualization,
 * WebRTC camera capture, Web Speech API voice output,
 * conversation management, and learning toggle.
 */

const API = window.location.origin;

// ── State ──────────────────────────────────────────────────────
const state = {
  conversations: [],
  currentConvId: null,
  messages: [],
  model: "qwen2.5-coder:32b",
  streaming: false,
  abortController: null,
  voiceEnabled: true, // Voice on by default — LocalMind talks like a person
  capturedImage: null, // base64 string
};

// ── DOM refs ───────────────────────────────────────────────────
const $ = (s) => document.querySelector(s);
const sidebar = $("#sidebar");
const sidebarToggle = $("#sidebarToggle");
const newChatBtn = $("#newChatBtn");
const conversationList = $("#conversationList");
const learningToggle = $("#learningToggle");
const modelSelect = $("#modelSelect");
const voiceToggle = $("#voiceToggle");
const voiceSelect = $("#voiceSelect");
const systemPromptBtn = $("#systemPromptBtn");
const systemPromptPanel = $("#systemPromptPanel");
const systemPromptText = $("#systemPromptText");
const resetPromptBtn = $("#resetPromptBtn");
const savePromptBtn = $("#savePromptBtn");
const welcomeScreen = $("#welcomeScreen");
const messagesContainer = $("#messagesContainer");
const messageInput = $("#messageInput");
const sendBtn = $("#sendBtn");
const cameraBtn = $("#cameraBtn");
const cameraModal = $("#cameraModal");
const cameraPreview = $("#cameraPreview");
const closeCameraBtn = $("#closeCameraBtn");
const snapBtn = $("#snapBtn");
const captureCanvas = $("#captureCanvas");
const imagePreview = $("#imagePreview");
const previewImg = $("#previewImg");
const removeImageBtn = $("#removeImageBtn");

// ── Init ───────────────────────────────────────────────────────
async function init() {
  // Load system prompt from localStorage
  const savedPrompt = localStorage.getItem("localmind_system_prompt");
  if (savedPrompt) systemPromptText.value = savedPrompt;

  await loadModels();
  await loadConversations();
  populateVoices();
  voiceToggle.classList.add("active"); // Voice on by default
  checkHealth();
  setInterval(checkHealth, 15000);
  bindEvents();
}

// ── Health Check ────────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch(`${API}/api/health`);
    const d = await r.json();
    // We could update a status indicator here if we had one
  } catch {
    /* server offline */
  }
}

// ── Models ──────────────────────────────────────────────────────
async function loadModels() {
  try {
    const r = await fetch(`${API}/api/models`);
    const d = await r.json();
    modelSelect.innerHTML = "";
    if (d.models && d.models.length > 0) {
      d.models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        modelSelect.appendChild(opt);
      });
      const saved = localStorage.getItem("localmind_model");
      if (saved && d.models.some((m) => m.name === saved)) {
        modelSelect.value = saved;
        state.model = saved;
      } else {
        state.model = d.models[0].name;
      }
    } else {
      const opt = document.createElement("option");
      opt.value = "qwen2.5-coder:32b";
      opt.textContent = "No models found";
      modelSelect.appendChild(opt);
    }
  } catch {
    modelSelect.innerHTML = "<option>Server offline</option>";
  }
}

// ── Conversations ───────────────────────────────────────────────
async function loadConversations() {
  try {
    const r = await fetch(`${API}/api/conversations`);
    const d = await r.json();
    state.conversations = d.conversations || [];
    renderConversations();
  } catch {
    state.conversations = [];
  }
}

function renderConversations() {
  conversationList.innerHTML = "";
  state.conversations.forEach((c) => {
    const div = document.createElement("div");
    div.className = `conversation-item${c.id === state.currentConvId ? " active" : ""}`;
    div.innerHTML = `
            <span>💬</span>
            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(c.title)}</span>
            <button class="delete-btn" title="Delete conversation">✕</button>
        `;
    div.addEventListener("click", (e) => {
      if (e.target.closest(".delete-btn")) {
        deleteConversation(c.id);
        return;
      }
      loadConversation(c.id);
    });
    conversationList.appendChild(div);
  });
}

async function loadConversation(id) {
  if (state.streaming) return;
  state.currentConvId = id;
  try {
    const r = await fetch(`${API}/api/conversations/${id}/messages`);
    const d = await r.json();
    state.messages = (d.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
    }));
    renderMessages();
    welcomeScreen.style.display = "none";
    renderConversations();
  } catch (e) {
    console.error("Failed to load conversation", e);
  }
}

async function deleteConversation(id) {
  try {
    await fetch(`${API}/api/conversations/${id}`, { method: "DELETE" });
    if (state.currentConvId === id) {
      state.currentConvId = null;
      state.messages = [];
      welcomeScreen.style.display = "";
      clearMessages();
    }
    await loadConversations();
  } catch (e) {
    console.error("Failed to delete", e);
  }
}

// ── Send Message ────────────────────────────────────────────────
async function sendMessage() {
  const text = messageInput.value.trim();
  if (!text || state.streaming) return;

  // Hide welcome
  welcomeScreen.style.display = "none";

  // Add user message to local state and render
  state.messages.push({ role: "user", content: text });
  appendMessage("user", text);
  messageInput.value = "";
  autoResize();

  // Prepare placeholder for assistant response
  const assistantEl = appendMessage("assistant", "");
  addTypingIndicator(assistantEl);

  state.streaming = true;
  sendBtn.disabled = true;
  state.abortController = new AbortController();

  const body = {
    model: state.model,
    message: text,
    conversation_id: state.currentConvId || undefined,
  };
  if (state.capturedImage) {
    body.image = state.capturedImage;
    clearCapturedImage();
  }

  try {
    const resp = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: state.abortController.signal,
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";
    const contentEl = assistantEl.querySelector(".message-content");

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const evt = JSON.parse(line.slice(6));

          // Token stream
          if (evt.token) {
            fullText += evt.token;
            contentEl.innerHTML = renderMarkdown(fullText);
            highlightCode();
            scrollToBottom();
          }
          // Tool call event
          if (evt.tool_call) {
            const card = createToolCallCard(evt.tool_call);
            contentEl.appendChild(card);
            scrollToBottom();
          }
          // Tool result event
          if (evt.tool_result) {
            updateToolResult(contentEl, evt.tool_result);
            scrollToBottom();
          }
          // Conversation ID (first message creates the conv)
          if (evt.conversation_id && !state.currentConvId) {
            state.currentConvId = evt.conversation_id;
            await loadConversations();
          }
          // Error
          if (evt.error) {
            fullText += `\n\n**Error:** ${evt.error}`;
            contentEl.innerHTML = renderMarkdown(fullText);
          }
          // Done
          if (evt.done) {
            state.messages.push({ role: "assistant", content: fullText });
          }
        } catch {
          /* skip bad JSON */
        }
      }
    }

    // Speak response if voice is enabled
    if (state.voiceEnabled && fullText) {
      speak(fullText);
    }
  } catch (e) {
    if (e.name !== "AbortError") {
      const contentEl = assistantEl.querySelector(".message-content");
      contentEl.innerHTML = `<p style="color:var(--error)">Connection error: ${e.message}</p>`;
    }
  }

  state.streaming = false;
  sendBtn.disabled = false;
  state.abortController = null;
  scrollToBottom();
}

// ── Message Rendering ───────────────────────────────────────────
function clearMessages() {
  const msgsEl = messagesContainer.querySelector(".chat-messages");
  if (msgsEl) msgsEl.innerHTML = "";
}

function renderMessages() {
  // Remove old messages div
  let msgsEl = messagesContainer.querySelector(".chat-messages");
  if (!msgsEl) {
    msgsEl = document.createElement("div");
    msgsEl.className = "chat-messages";
    messagesContainer.appendChild(msgsEl);
  }
  msgsEl.innerHTML = "";
  state.messages.forEach((m) => {
    const div = createMessageEl(m.role, m.content);
    msgsEl.appendChild(div);
  });
  highlightCode();
  scrollToBottom();
}

function appendMessage(role, content) {
  let msgsEl = messagesContainer.querySelector(".chat-messages");
  if (!msgsEl) {
    msgsEl = document.createElement("div");
    msgsEl.className = "chat-messages";
    messagesContainer.appendChild(msgsEl);
  }
  const div = createMessageEl(role, content);
  msgsEl.appendChild(div);
  highlightCode();
  scrollToBottom();
  return div;
}

function createMessageEl(role, content) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  const avatar = role === "user" ? "👤" : "🧠";
  div.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-body">
            <div class="message-content">${content ? renderMarkdown(content) : ""}</div>
        </div>
    `;
  return div;
}

function addTypingIndicator(el) {
  const content = el.querySelector(".message-content");
  content.innerHTML =
    '<div class="typing-indicator"><span></span><span></span><span></span></div>';
}

// ── Tool Cards ──────────────────────────────────────────────────
function createToolCallCard(tc) {
  const card = document.createElement("div");
  card.className = "tool-call-card running";
  card.dataset.toolName = tc.name;
  const icons = {
    web_search: "🌐",
    read_file: "📄",
    write_file: "✏️",
    list_files: "📁",
    run_code: "💻",
    save_memory: "🧠",
    recall_memories: "🔍",
    analyze_image: "📷",
    take_screenshot: "📸",
    clipboard_read: "📋",
  };
  card.innerHTML = `
        <div class="tool-call-header">
            <span class="tool-icon">${icons[tc.name] || "🔧"}</span>
            <span class="tool-name">${tc.name}</span>
            <span class="tool-status">⏳ Running</span>
        </div>
        <div class="tool-call-body">${escapeHtml(JSON.stringify(tc.arguments || {}, null, 2))}</div>
    `;
  card.querySelector(".tool-call-header").addEventListener("click", () => {
    card.classList.toggle("expanded");
  });
  return card;
}

function updateToolResult(container, result) {
  const cards = container.querySelectorAll(
    `.tool-call-card[data-tool-name="${result.name}"]`,
  );
  const card = cards[cards.length - 1]; // last matching
  if (!card) return;
  card.classList.remove("running");
  card.classList.add("success");
  const statusEl = card.querySelector(".tool-status");
  statusEl.textContent = "✅ Done";
  const body = card.querySelector(".tool-call-body");
  body.textContent =
    typeof result.result === "string"
      ? result.result.substring(0, 2000)
      : JSON.stringify(result.result, null, 2).substring(0, 2000);
}

// ── Markdown ────────────────────────────────────────────────────
function renderMarkdown(text) {
  if (typeof marked === "undefined") return escapeHtml(text);
  try {
    marked.setOptions({ breaks: true, gfm: true });
    return marked.parse(text);
  } catch {
    return escapeHtml(text);
  }
}

function highlightCode() {
  if (typeof hljs === "undefined") return;
  document.querySelectorAll(".message-content pre code").forEach((block) => {
    if (!block.dataset.highlighted) {
      hljs.highlightElement(block);
      block.dataset.highlighted = "true";
    }
  });
}

// ── Voice (Web Speech API) ──────────────────────────────────────
function populateVoices() {
  const loadVoices = () => {
    const voices = speechSynthesis.getVoices();
    voiceSelect.innerHTML = '<option value="">Default</option>';
    voices.forEach((v, i) => {
      const opt = document.createElement("option");
      opt.value = i;
      opt.textContent = `${v.name} (${v.lang})`;
      voiceSelect.appendChild(opt);
    });
  };
  speechSynthesis.onvoiceschanged = loadVoices;
  loadVoices();
}

function speak(text) {
  // Strip markdown for speech
  const clean = text
    .replace(/[#*`\[\]()_~>|-]/g, "")
    .replace(/\n+/g, ". ")
    .substring(0, 3000);
  const utter = new SpeechSynthesisUtterance(clean);
  const voices = speechSynthesis.getVoices();
  const idx = parseInt(voiceSelect.value);
  if (!isNaN(idx) && voices[idx]) utter.voice = voices[idx];
  utter.rate = 1.0;
  utter.pitch = 1.0;
  speechSynthesis.speak(utter);
}

// ── Camera (WebRTC) ─────────────────────────────────────────────
let cameraStream = null;

async function openCamera() {
  cameraModal.style.display = "";
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment" },
      audio: false,
    });
    cameraPreview.srcObject = cameraStream;
  } catch (e) {
    alert("Camera access denied: " + e.message);
    cameraModal.style.display = "none";
  }
}

function closeCamera() {
  if (cameraStream) {
    cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
  }
  cameraPreview.srcObject = null;
  cameraModal.style.display = "none";
}

function captureFrame() {
  const video = cameraPreview;
  captureCanvas.width = video.videoWidth;
  captureCanvas.height = video.videoHeight;
  captureCanvas.getContext("2d").drawImage(video, 0, 0);
  const dataUrl = captureCanvas.toDataURL("image/jpeg", 0.8);
  state.capturedImage = dataUrl.split(",")[1]; // base64 only
  previewImg.src = dataUrl;
  imagePreview.style.display = "";
  closeCamera();
}

function clearCapturedImage() {
  state.capturedImage = null;
  imagePreview.style.display = "none";
  previewImg.src = "";
}

// ── Helpers ─────────────────────────────────────────────────────
function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function autoResize() {
  messageInput.style.height = "auto";
  messageInput.style.height = Math.min(messageInput.scrollHeight, 150) + "px";
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ── Event Binding ───────────────────────────────────────────────
function bindEvents() {
  // Send
  sendBtn.addEventListener("click", sendMessage);
  messageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  messageInput.addEventListener("input", autoResize);

  // New chat
  newChatBtn.addEventListener("click", () => {
    state.currentConvId = null;
    state.messages = [];
    welcomeScreen.style.display = "";
    clearMessages();
    renderConversations();
    messageInput.focus();
  });

  // Model change
  modelSelect.addEventListener("change", () => {
    state.model = modelSelect.value;
    localStorage.setItem("localmind_model", state.model);
  });

  // Voice toggle
  voiceToggle.addEventListener("click", () => {
    state.voiceEnabled = !state.voiceEnabled;
    voiceToggle.classList.toggle("active", state.voiceEnabled);
  });

  // System prompt panel toggle
  systemPromptBtn.addEventListener("click", () => {
    systemPromptPanel.classList.toggle("open");
  });
  savePromptBtn.addEventListener("click", () => {
    localStorage.setItem("localmind_system_prompt", systemPromptText.value);
    systemPromptPanel.classList.remove("open");
  });
  resetPromptBtn.addEventListener("click", () => {
    systemPromptText.value = "";
    localStorage.removeItem("localmind_system_prompt");
  });

  // Learning toggle
  learningToggle.addEventListener("change", async () => {
    try {
      await fetch(`${API}/api/memory/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: learningToggle.checked }),
      });
    } catch {
      /* ignore */
    }
  });

  // Sidebar toggle (mobile)
  sidebarToggle.addEventListener("click", () =>
    sidebar.classList.toggle("open"),
  );

  // Camera
  cameraBtn.addEventListener("click", openCamera);
  closeCameraBtn.addEventListener("click", closeCamera);
  snapBtn.addEventListener("click", captureFrame);
  removeImageBtn.addEventListener("click", clearCapturedImage);

  // Feature cards (welcome screen quick prompts)
  document.querySelectorAll(".feature-card").forEach((card) => {
    card.addEventListener("click", () => {
      const prompt = card.dataset.prompt;
      if (prompt) {
        messageInput.value = prompt;
        autoResize();
        sendMessage();
      }
    });
  });

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "n") {
      e.preventDefault();
      newChatBtn.click();
    }
  });
}

// ── Boot ────────────────────────────────────────────────────────
init();
