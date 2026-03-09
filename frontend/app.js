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
  voiceEnabled: localStorage.getItem("localmind_voice") !== "off", // Persisted
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
const micBtn = $("#micBtn");

// ── Init ───────────────────────────────────────────────────────
async function init() {
  // Load system prompt from localStorage
  const savedPrompt = localStorage.getItem("localmind_system_prompt");
  if (savedPrompt) systemPromptText.value = savedPrompt;

  await loadModels();
  await loadConversations();
  populateVoices();
  if (state.voiceEnabled) voiceToggle.classList.add("active");
  else voiceToggle.classList.remove("active");
  checkHealth();
  loadVersion();
  startHwPolling();
  loadDocuments();
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
            <button class="export-btn" title="Export conversation">📥</button>
            <button class="delete-btn" title="Delete conversation">✕</button>
        `;
    div.addEventListener("click", (e) => {
      if (e.target.closest(".delete-btn")) {
        deleteConversation(c.id);
        return;
      }
      if (e.target.closest(".export-btn")) {
        exportConversation(c.id, c.title);
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
    // Load messages
    const r = await fetch(`${API}/api/conversations/${id}/messages`);
    const d = await r.json();
    state.messages = (d.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
    }));

    // Load conversation metadata (system prompt)
    try {
      const convR = await fetch(`${API}/api/conversations/${id}`);
      const convD = await convR.json();
      if (convD.system_prompt) {
        systemPromptText.value = convD.system_prompt;
      } else {
        // Load default if no custom prompt saved
        systemPromptText.value = "";
      }
    } catch {
      /* ignore */
    }

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
  let typingRemoved = false;

  state.streaming = true;
  sendBtn.disabled = true;
  state.abortController = new AbortController();

  const body = {
    model: state.model,
    message: text,
    conversation_id: state.currentConvId || undefined,
    system_prompt: systemPromptText.value || undefined,
  };
  if (state.capturedImage) {
    body.image = state.capturedImage;
    clearCapturedImage();
  }

  try {
    console.log("[LocalMind] Sending chat request:", {
      model: body.model,
      msg_len: body.message?.length,
      conv_id: body.conversation_id,
    });
    const resp = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: state.abortController.signal,
    });
    console.log(
      "[LocalMind] Fetch response status:",
      resp.status,
      resp.statusText,
    );

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";
    let chunkCount = 0;
    const contentEl = assistantEl.querySelector(".message-content");

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        console.log(
          "[LocalMind] Reader done. Total chunks:",
          chunkCount,
          "fullText length:",
          fullText.length,
        );
        break;
      }

      const rawChunk = decoder.decode(value, { stream: true });
      chunkCount++;
      if (chunkCount <= 5 || chunkCount % 20 === 0) {
        console.log(
          `[LocalMind] Chunk #${chunkCount}:`,
          rawChunk.substring(0, 200),
        );
      }
      buffer += rawChunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const evt = JSON.parse(line.slice(6));

          // Token stream
          if (evt.token) {
            if (!typingRemoved) {
              typingRemoved = true;
              console.log(
                "[LocalMind] First token received, removing typing indicator",
              );
            }
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
          // Thinking event — model context info at stream start
          if (evt.thinking) {
            const t = evt.thinking;
            const thinkingEl = document.createElement("div");
            thinkingEl.className = "thinking-panel";
            thinkingEl.dataset.collapsed = "true";
            thinkingEl.innerHTML = `
              <div class="thinking-header">
                <span>🧠 ${escapeHtml(t.model)}</span>
                <span class="thinking-meta">${t.messages} msgs • ${Math.round(t.context_chars / 1000)}k chars${t.tools_enabled ? " • 🔧 tools" : ""}</span>
                <span class="thinking-toggle">▸</span>
              </div>
              <div class="thinking-body" style="display:none">
                <div><strong>Model:</strong> ${escapeHtml(t.model)}</div>
                <div><strong>Messages:</strong> ${t.messages}</div>
                <div><strong>Context:</strong> ${t.context_chars.toLocaleString()} chars</div>
                <div><strong>Tools:</strong> ${t.tools_enabled ? "Enabled" : "Disabled"}</div>
                <div class="thinking-analytics"></div>
              </div>`;
            thinkingEl
              .querySelector(".thinking-header")
              .addEventListener("click", () => {
                const body = thinkingEl.querySelector(".thinking-body");
                const toggle = thinkingEl.querySelector(".thinking-toggle");
                const collapsed = body.style.display === "none";
                body.style.display = collapsed ? "block" : "none";
                toggle.textContent = collapsed ? "▾" : "▸";
              });
            contentEl.insertBefore(thinkingEl, contentEl.firstChild);
          }
          // Error
          if (evt.error) {
            fullText += `\n\n**Error:** ${evt.error}`;
            contentEl.innerHTML = renderMarkdown(fullText);
          }
          // Done — with analytics
          if (evt.done) {
            state.messages.push({ role: "assistant", content: fullText });
            // Populate analytics panel if present
            if (evt.analytics) {
              const a = evt.analytics;
              const analyticsEl = contentEl.querySelector(
                ".thinking-analytics",
              );
              if (analyticsEl) {
                analyticsEl.innerHTML = `
                  <hr style="border-color: var(--border); margin: 6px 0">
                  <div><strong>⏱ Time:</strong> ${a.elapsed_sec}s</div>
                  <div><strong>📊 Tokens:</strong> ${a.total_tokens}</div>
                  <div><strong>⚡ Speed:</strong> ${a.tokens_per_sec} tok/s</div>
                  ${a.tool_calls ? `<div><strong>🔧 Tool calls:</strong> ${a.tool_calls}</div>` : ""}`;
              }
              // Update the thinking header meta with speed
              const metaEl = contentEl.querySelector(".thinking-meta");
              if (metaEl) {
                metaEl.textContent += ` • ⚡ ${a.tokens_per_sec} tok/s • ${a.elapsed_sec}s`;
              }
            }
          }
        } catch (parseErr) {
          console.warn(
            "[LocalMind] SSE parse error:",
            parseErr,
            "raw line:",
            line.substring(0, 200),
          );
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

  // Remove typing indicator if it wasn't already removed by tokens
  const leftoverTyping = assistantEl.querySelector(".typing-indicator");
  if (leftoverTyping) {
    console.log("[LocalMind] Removing leftover typing indicator");
    leftoverTyping.remove();
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

// ── Export Conversation ─────────────────────────────────────────
async function exportConversation(id, title) {
  try {
    const r = await fetch(`${API}/api/conversations/${id}/messages`);
    const d = await r.json();
    const messages = d.messages || [];
    const text = messages
      .map((m) => `[${m.role.toUpperCase()}]\n${m.content}`)
      .join("\n\n---\n\n");
    const blob = new Blob([`# ${title}\n\n${text}`], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(title || "conversation").replace(/[^a-z0-9]/gi, "_")}.md`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    console.error("Failed to export conversation", e);
  }
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

  const resultText =
    typeof result.result === "string"
      ? result.result
      : JSON.stringify(result.result, null, 2);

  // For file operations, render with syntax highlighting
  if (
    result.name === "read_file" ||
    result.name === "write_file" ||
    result.name === "run_code"
  ) {
    const ext = getFileExtension(
      result.name === "run_code" ? "output.py" : card.dataset.filePath || "",
    );
    const lang = extToLang(ext);
    body.innerHTML = `
      <div class="code-viewer">
        <div class="code-viewer-header">
          <span class="code-viewer-filename">${escapeHtml(card.dataset.filePath || result.name)}</span>
          <button class="code-copy-btn" title="Copy to clipboard">📋 Copy</button>
        </div>
        <pre><code class="${lang}">${escapeHtml(resultText.substring(0, 5000))}</code></pre>
      </div>`;
    // Highlight
    const codeEl = body.querySelector("pre code");
    if (typeof hljs !== "undefined" && codeEl) {
      hljs.highlightElement(codeEl);
    }
    // Copy button
    const copyBtn = body.querySelector(".code-copy-btn");
    if (copyBtn) {
      copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(resultText).then(() => {
          copyBtn.textContent = "✅ Copied!";
          setTimeout(() => {
            copyBtn.textContent = "📋 Copy";
          }, 2000);
        });
      });
    }
  } else {
    body.textContent = resultText.substring(0, 2000);
  }
}

// Helper: extract file extension from path
function getFileExtension(path) {
  const match = path.match(/\.(\w+)$/);
  return match ? match[1].toLowerCase() : "";
}

// Helper: map file extension to highlight.js language
function extToLang(ext) {
  const map = {
    py: "python",
    js: "javascript",
    ts: "typescript",
    html: "html",
    css: "css",
    json: "json",
    md: "markdown",
    sh: "bash",
    bat: "batch",
    yaml: "yaml",
    yml: "yaml",
    xml: "xml",
    sql: "sql",
    java: "java",
    cpp: "cpp",
    c: "c",
    h: "c",
    cs: "csharp",
    rb: "ruby",
    go: "go",
    rs: "rust",
    php: "php",
    swift: "swift",
    kt: "kotlin",
    r: "r",
  };
  return map[ext] || "";
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
  // Guard: don't speak if voice is disabled
  if (!state.voiceEnabled) return;
  // Cancel any currently playing speech first
  speechSynthesis.cancel();
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

// ── Voice Input (Web Speech API) ────────────────────────────────
let recognition = null;
let isListening = false;

function initSpeechRecognition() {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    console.warn("[LocalMind] SpeechRecognition not supported in this browser");
    if (micBtn) micBtn.title = "Voice input not supported in this browser";
    return;
  }

  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = "en-US";

  let finalTranscript = "";

  recognition.onstart = () => {
    isListening = true;
    micBtn.classList.add("mic-active");
    micBtn.title = "Listening... (click to stop)";
    finalTranscript = messageInput.value; // Preserve existing text
    console.log("[LocalMind] 🎤 Listening started");
  };

  recognition.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        finalTranscript += (finalTranscript ? " " : "") + transcript;
      } else {
        interim += transcript;
      }
    }
    // Show final + interim (interim in lighter style via placeholder feel)
    messageInput.value = finalTranscript + (interim ? " " + interim : "");
    autoResize();
  };

  recognition.onerror = (event) => {
    console.error("[LocalMind] 🎤 Speech error:", event.error);
    if (event.error === "not-allowed") {
      alert(
        "Microphone access denied. Please allow microphone access in your browser settings.",
      );
    }
    stopListening();
  };

  recognition.onend = () => {
    // Auto-restart if still in listening mode (Chrome stops after silence)
    if (isListening) {
      try {
        recognition.start();
      } catch (e) {
        stopListening();
      }
    }
  };
}

function startListening() {
  if (!recognition) {
    initSpeechRecognition();
    if (!recognition) {
      alert("Voice input is not supported in this browser. Try Chrome.");
      return;
    }
  }
  try {
    recognition.start();
  } catch (e) {
    console.error("[LocalMind] 🎤 Failed to start:", e);
  }
}

function stopListening() {
  isListening = false;
  if (recognition) {
    try {
      recognition.stop();
    } catch (e) {
      /* ignore */
    }
  }
  micBtn.classList.remove("mic-active");
  micBtn.title = "Voice input (click to speak)";
  console.log("[LocalMind] 🎤 Listening stopped");
}

function toggleMic() {
  if (isListening) {
    stopListening();
  } else {
    startListening();
  }
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
  savePromptBtn.addEventListener("click", async () => {
    // Save to localStorage as fallback
    localStorage.setItem("localmind_system_prompt", systemPromptText.value);
    // Save to backend if we have an active conversation
    if (state.currentConvId) {
      try {
        await fetch(
          `${API}/api/conversations/${state.currentConvId}/system-prompt`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ system_prompt: systemPromptText.value }),
          },
        );
      } catch {
        /* ignore */
      }
    }
    systemPromptPanel.classList.remove("open");
  });
  resetPromptBtn.addEventListener("click", async () => {
    // Load server's default prompt
    try {
      const r = await fetch(`${API}/api/default-system-prompt`);
      const d = await r.json();
      systemPromptText.value = d.system_prompt || "";
    } catch {
      systemPromptText.value = "";
    }
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

  // Voice toggle — click to toggle voice on/off, also stops current speech
  voiceToggle.addEventListener("click", () => {
    speechSynthesis.cancel(); // Stop any current speech immediately
    state.voiceEnabled = !state.voiceEnabled;
    localStorage.setItem("localmind_voice", state.voiceEnabled ? "on" : "off");
    voiceToggle.classList.toggle("active", state.voiceEnabled);
    voiceToggle.title = state.voiceEnabled
      ? "Voice On (click to mute)"
      : "Voice Off (click to unmute)";
    console.log("[LocalMind] Voice toggled:", state.voiceEnabled);
  });

  // Voice input
  micBtn.addEventListener("click", toggleMic);

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

  // Document upload
  const uploadBtn = document.getElementById("uploadBtn");
  const fileInput = document.getElementById("fileInput");
  if (uploadBtn && fileInput) {
    uploadBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", (e) => {
      if (e.target.files.length > 0) {
        uploadDocuments(e.target.files);
        fileInput.value = "";
      }
    });
  }

  // Document list toggle
  const docsToggle = document.getElementById("docsToggle");
  const docList = document.getElementById("documentList");
  if (docsToggle && docList) {
    docsToggle.addEventListener("click", () => {
      docList.classList.toggle("open");
    });
  }

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "n") {
      e.preventDefault();
      newChatBtn.click();
    }
    // Escape stops voice immediately
    if (e.key === "Escape") {
      speechSynthesis.cancel();
    }
  });
}

// ── Version Badge ───────────────────────────────────────────────
async function loadVersion() {
  try {
    const r = await fetch(`${API}/api/version`);
    const d = await r.json();
    const badge = document.getElementById("versionBadge");
    if (badge) badge.textContent = `v${d.version} #${d.build}`;
  } catch {
    /* ignore */
  }
}

// ── Document RAG ────────────────────────────────────────────────
async function uploadDocuments(files) {
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

async function loadDocuments() {
  try {
    const r = await fetch(`${API}/api/documents`);
    const d = await r.json();
    const list = document.getElementById("documentList");
    const count = document.getElementById("docCount");
    if (!list) return;

    const docs = d.documents || [];
    count.textContent = docs.length;
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
        await fetch(
          `${API}/api/documents/${encodeURIComponent(doc.filename)}`,
          {
            method: "DELETE",
          },
        );
        await loadDocuments();
      });
      list.appendChild(div);
    });
  } catch {
    /* ignore */
  }
}

// ── Utility ─────────────────────────────────────────────────────
function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text;
  return d.innerHTML;
}

// ── Document RAG ────────────────────────────────────────────────
async function uploadDocuments(files) {
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

async function loadDocuments() {
  try {
    const r = await fetch(`${API}/api/documents`);
    const d = await r.json();
    const list = document.getElementById("documentList");
    const count = document.getElementById("docCount");
    if (!list) return;

    const docs = d.documents || [];
    count.textContent = docs.length;
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
        await fetch(
          `${API}/api/documents/${encodeURIComponent(doc.filename)}`,
          {
            method: "DELETE",
          },
        );
        await loadDocuments();
      });
      list.appendChild(div);
    });
  } catch {
    /* ignore */
  }
}

// ── Hardware Dashboard ──────────────────────────────────────────
let hwInterval = null;

async function pollHardware() {
  try {
    const r = await fetch(`${API}/api/hardware`);
    const d = await r.json();

    // CPU
    const cpuBar = document.getElementById("cpuBar");
    const cpuVal = document.getElementById("cpuVal");
    if (cpuBar && d.system) {
      cpuBar.style.width = `${d.system.cpu_percent}%`;
      cpuVal.textContent = `${Math.round(d.system.cpu_percent)}%`;
      cpuBar.className = `hw-fill ${d.system.cpu_percent > 80 ? "hw-fill-red" : d.system.cpu_percent > 50 ? "hw-fill-yellow" : "hw-fill-green"}`;
    }

    // RAM
    const ramBar = document.getElementById("ramBar");
    const ramVal = document.getElementById("ramVal");
    if (ramBar && d.system) {
      ramBar.style.width = `${d.system.ram_percent}%`;
      ramVal.textContent = `${d.system.ram_used_gb}/${d.system.ram_total_gb} GB`;
      ramBar.className = `hw-fill ${d.system.ram_percent > 85 ? "hw-fill-red" : d.system.ram_percent > 60 ? "hw-fill-yellow" : "hw-fill-green"}`;
    }

    // Model / VRAM
    const vramBar = document.getElementById("vramBar");
    const vramVal = document.getElementById("vramVal");
    const modelLabel = document.getElementById("modelLabel");
    if (vramBar && d.models && d.models.length > 0) {
      const m = d.models[0];
      const pct = m.size_gb > 0 ? Math.round((m.vram_gb / m.size_gb) * 100) : 0;
      vramBar.style.width = `${pct}%`;
      vramVal.textContent = `${m.vram_gb} GB VRAM`;
      modelLabel.textContent = m.name.split(":")[0];
      vramBar.className = "hw-fill hw-fill-purple";
    } else {
      vramBar.style.width = "0%";
      vramVal.textContent = "No model loaded";
      modelLabel.textContent = "Model";
      vramBar.className = "hw-fill hw-fill-dim";
    }
  } catch {
    /* ignore */
  }
}

function startHwPolling() {
  if (hwInterval) return;
  pollHardware();
  hwInterval = setInterval(pollHardware, 3000);
}

// ── Boot ────────────────────────────────────────────────────────
init();
