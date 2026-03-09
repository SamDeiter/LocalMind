/**
 * LocalMind — Frontend Application
 *
 * Handles: chat messaging (SSE streaming), tool call visualization,
 * webcam capture (WebRTC), voice output (Web Speech API), model/voice
 * selection, conversation management, and learning mode toggle.
 */

// ── Configuration ──────────────────────────────────────────────────
const API = window.location.origin;

// Default system prompt — matches server default
const DEFAULT_SYSTEM_PROMPT = `You are LocalMind, a powerful local AI assistant. You have access to tools that let you:
- Search the web for current information
- Read, write, and list files in the user's workspace
- Execute Python code
- Save and recall memories about the user
- Analyze images from the camera or screenshots
- Take screenshots of the user's screen
- Read the clipboard

IMPORTANT BEHAVIORS:
- When the user shares preferences, facts about themselves, or important context, use save_memory to remember it.
- When you need to recall something about the user, use recall_memories.
- You can NEVER delete files. You have no delete capability.
- All file operations are sandboxed to ~/LocalMind_Workspace.
- Be proactive about using your tools when they would be helpful.
- When using tools, explain what you're doing and show the results clearly.`;

// ── State ──────────────────────────────────────────────────────────
let currentConversationId = null;
let isStreaming = false; // True while SSE response is active
let voiceEnabled = false; // Text-to-speech toggle
let selectedVoice = null; // Selected SpeechSynthesis voice
let capturedImageBase64 = null; // Webcam or screenshot image data
let cameraStream = null; // Active webcam MediaStream

// ── DOM References ─────────────────────────────────────────────────
const messageInput = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendBtn");
const messagesContainer = document.getElementById("messagesContainer");
const welcomeScreen = document.getElementById("welcomeScreen");
const conversationList = document.getElementById("conversationList");
const modelSelect = document.getElementById("modelSelect");
const newChatBtn = document.getElementById("newChatBtn");
const sidebarToggle = document.getElementById("sidebarToggle");
const sidebar = document.getElementById("sidebar");
const learningToggle = document.getElementById("learningToggle");
const systemPromptBtn = document.getElementById("systemPromptBtn");
const systemPromptPanel = document.getElementById("systemPromptPanel");
const systemPromptText = document.getElementById("systemPromptText");
const resetPromptBtn = document.getElementById("resetPromptBtn");
const savePromptBtn = document.getElementById("savePromptBtn");
const voiceToggle = document.getElementById("voiceToggle");
const voiceSelect = document.getElementById("voiceSelect");
const cameraBtn = document.getElementById("cameraBtn");
const cameraModal = document.getElementById("cameraModal");
const cameraPreview = document.getElementById("cameraPreview");
const snapBtn = document.getElementById("snapBtn");
const closeCameraBtn = document.getElementById("closeCameraBtn");
const captureCanvas = document.getElementById("captureCanvas");
const imagePreview = document.getElementById("imagePreview");
const previewImg = document.getElementById("previewImg");
const removeImageBtn = document.getElementById("removeImageBtn");

// ════════════════════════════════════════════════════════════════════
// INITIALIZATION
// ════════════════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
  loadModels();
  loadConversations();
  loadMemoryStatus();
  loadVoices();
  initTooltips();
  initEventListeners();

  // Load saved system prompt from localStorage
  const savedPrompt = localStorage.getItem("localmind_system_prompt");
  if (savedPrompt) systemPromptText.value = savedPrompt;
});

// ════════════════════════════════════════════════════════════════════
// TOOLTIPS — rich, styled, accessible
// ════════════════════════════════════════════════════════════════════

/**
 * Attach descriptive tooltips to interactive elements.
 * Uses data-tooltip attributes read by CSS for styling.
 */
function initTooltips() {
  const tips = {
    newChatBtn:
      "Start a new conversation\nClears the current chat and begins fresh",
    sidebarToggle:
      "Show or hide the sidebar\nContains your conversation history",
    voiceToggle:
      "Enable or disable voice output\nThe AI will read its responses aloud",
    systemPromptBtn:
      "Customize system prompt\nTell the AI how to behave and what persona to use",
    cameraBtn:
      "Open camera to capture an image\nThe AI can see and analyze what your camera sees",
    sendBtn: "Send your message\nYou can also press Enter to send",
    learningToggle:
      "Toggle memory learning on or off\nWhen off, the AI reads memories but does not save new ones",
  };

  for (const [id, text] of Object.entries(tips)) {
    const el = document.getElementById(id);
    if (el) {
      el.setAttribute("data-tooltip", text);
    }
  }
}

// ════════════════════════════════════════════════════════════════════
// EVENT LISTENERS
// ════════════════════════════════════════════════════════════════════

function initEventListeners() {
  // Send message on button click or Enter key
  sendBtn.addEventListener("click", sendMessage);
  messageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Auto-resize textarea as user types
  messageInput.addEventListener("input", autoResizeInput);

  // New chat
  newChatBtn.addEventListener("click", startNewChat);

  // Sidebar toggle (mobile)
  sidebarToggle.addEventListener("click", () => {
    sidebar.classList.toggle("open");
  });

  // System prompt panel
  systemPromptBtn.addEventListener("click", () => {
    systemPromptPanel.classList.toggle("open");
  });
  resetPromptBtn.addEventListener("click", () => {
    systemPromptText.value = DEFAULT_SYSTEM_PROMPT;
    localStorage.removeItem("localmind_system_prompt");
  });
  savePromptBtn.addEventListener("click", () => {
    localStorage.setItem("localmind_system_prompt", systemPromptText.value);
    systemPromptPanel.classList.remove("open");
  });

  // Learning toggle
  learningToggle.addEventListener("change", async () => {
    try {
      await fetch(`${API}/api/memory/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: learningToggle.checked }),
      });
    } catch (err) {
      console.error("Failed to toggle learning:", err);
    }
  });

  // Voice output toggle
  voiceToggle.addEventListener("click", () => {
    voiceEnabled = !voiceEnabled;
    voiceToggle.classList.toggle("active", voiceEnabled);
    if (!voiceEnabled) speechSynthesis.cancel();
  });

  // Voice selection
  voiceSelect.addEventListener("change", () => {
    const voices = speechSynthesis.getVoices();
    selectedVoice = voices.find((v) => v.name === voiceSelect.value) || null;
  });

  // Camera
  cameraBtn.addEventListener("click", openCamera);
  snapBtn.addEventListener("click", captureFrame);
  closeCameraBtn.addEventListener("click", closeCamera);
  removeImageBtn.addEventListener("click", removeImage);

  // Welcome screen feature cards — click to populate input
  document.querySelectorAll(".feature-card").forEach((card) => {
    card.addEventListener("click", () => {
      messageInput.value = card.getAttribute("data-prompt");
      messageInput.focus();
      autoResizeInput();
    });
  });

  // Close sidebar when clicking outside on mobile
  document.addEventListener("click", (e) => {
    if (
      window.innerWidth <= 768 &&
      sidebar.classList.contains("open") &&
      !sidebar.contains(e.target) &&
      e.target !== sidebarToggle
    ) {
      sidebar.classList.remove("open");
    }
  });
}

// ════════════════════════════════════════════════════════════════════
// MODELS
// ════════════════════════════════════════════════════════════════════

/** Fetch available Ollama models and populate the dropdown. */
async function loadModels() {
  try {
    const resp = await fetch(`${API}/api/models`);
    const data = await resp.json();
    modelSelect.innerHTML = "";

    if (data.models && data.models.length > 0) {
      data.models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        modelSelect.appendChild(opt);
      });
    } else {
      modelSelect.innerHTML = '<option value="">No models found</option>';
    }
  } catch (err) {
    modelSelect.innerHTML = '<option value="">Ollama not running</option>';
    console.error("Failed to load models:", err);
  }
}

// ════════════════════════════════════════════════════════════════════
// CONVERSATIONS
// ════════════════════════════════════════════════════════════════════

/** Load conversation list from backend and render sidebar. */
async function loadConversations() {
  try {
    const resp = await fetch(`${API}/api/conversations`);
    const data = await resp.json();
    conversationList.innerHTML = "";

    if (data.conversations.length === 0) {
      conversationList.innerHTML = `
                <div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px;">
                    No conversations yet.<br>Start chatting!
                </div>`;
      return;
    }

    data.conversations.forEach((conv) => {
      const item = document.createElement("div");
      item.className = `conversation-item${conv.id === currentConversationId ? " active" : ""}`;
      item.innerHTML = `
                <span>💬</span>
                <span class="conv-title" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                    ${escapeHtml(conv.title)}
                </span>
                <button class="delete-btn" data-tooltip="Delete this conversation" title="">🗑️</button>
            `;

      // Click to load conversation
      item.addEventListener("click", (e) => {
        if (!e.target.classList.contains("delete-btn")) {
          loadConversation(conv.id);
        }
      });

      // Delete button
      item.querySelector(".delete-btn").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (confirm("Delete this conversation?")) {
          await fetch(`${API}/api/conversations/${conv.id}`, {
            method: "DELETE",
          });
          if (currentConversationId === conv.id) {
            currentConversationId = null;
            showWelcome();
          }
          loadConversations();
        }
      });

      conversationList.appendChild(item);
    });
  } catch (err) {
    console.error("Failed to load conversations:", err);
  }
}

/** Load and display messages for a specific conversation. */
async function loadConversation(convId) {
  currentConversationId = convId;
  welcomeScreen.style.display = "none";

  try {
    const resp = await fetch(`${API}/api/conversations/${convId}/messages`);
    const data = await resp.json();
    renderMessages(data.messages);
    loadConversations(); // Refresh active state in sidebar
  } catch (err) {
    console.error("Failed to load conversation:", err);
  }

  // Close sidebar on mobile after selection
  sidebar.classList.remove("open");
}

/** Start a fresh conversation. */
function startNewChat() {
  currentConversationId = null;
  showWelcome();
  sidebar.classList.remove("open");
}

/** Show the welcome screen (no active conversation). */
function showWelcome() {
  // Remove all messages except the welcome screen
  const msgs = messagesContainer.querySelectorAll(".message");
  msgs.forEach((m) => m.remove());
  welcomeScreen.style.display = "flex";
}

// ════════════════════════════════════════════════════════════════════
// CHAT — Send + Stream
// ════════════════════════════════════════════════════════════════════

/** Send user message and stream the AI response via SSE. */
async function sendMessage() {
  const text = messageInput.value.trim();
  if (!text || isStreaming) return;

  const model = modelSelect.value;
  const systemPrompt = systemPromptText.value || DEFAULT_SYSTEM_PROMPT;

  // Show user message in UI
  welcomeScreen.style.display = "none";
  appendMessage("user", text);
  messageInput.value = "";
  autoResizeInput();

  isStreaming = true;
  sendBtn.disabled = true;

  // Build request body
  const body = {
    message: text,
    model: model,
    system_prompt: systemPrompt,
    conversation_id: currentConversationId,
  };

  // Attach image if one was captured
  if (capturedImageBase64) {
    body.image = capturedImageBase64;
    removeImage();
  }

  try {
    const resp = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!resp.ok) {
      throw new Error(`Server error: ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let assistantEl = null; // The assistant message element
    let contentEl = null; // The text content div
    let fullText = ""; // Accumulated response text

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      const lines = chunk.split("\n");

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6).trim();
        if (!jsonStr) continue;

        try {
          const data = JSON.parse(jsonStr);

          // Update conversation ID (auto-created on first message)
          if (data.conversation_id && !currentConversationId) {
            currentConversationId = data.conversation_id;
          }

          // Tool call started
          if (data.tool_call) {
            if (!assistantEl) {
              assistantEl = appendMessage("assistant", "");
              contentEl = assistantEl.querySelector(".message-content");
            }
            appendToolCard(
              contentEl,
              data.tool_call.name,
              data.tool_call.arguments,
              "running",
            );
          }

          // Tool result received
          if (data.tool_result) {
            updateToolCard(
              contentEl,
              data.tool_result.name,
              data.tool_result.result,
              "success",
            );
          }

          // Text token (streaming response)
          if (data.token) {
            if (!assistantEl) {
              assistantEl = appendMessage("assistant", "");
              contentEl = assistantEl.querySelector(".message-content");
            }
            fullText += data.token;
            // Render markdown incrementally
            contentEl.innerHTML = renderMarkdown(fullText);
            highlightCodeBlocks(contentEl);
            addCopyButtons(contentEl);
            scrollToBottom();
          }

          // Stream finished
          if (data.done) {
            // Speak the response if voice is enabled
            if (voiceEnabled && fullText) {
              speak(fullText);
            }
            loadConversations();
          }

          // Error from server
          if (data.error) {
            if (!assistantEl) {
              assistantEl = appendMessage("assistant", "");
              contentEl = assistantEl.querySelector(".message-content");
            }
            contentEl.innerHTML = `<span style="color:var(--error);">⚠️ ${escapeHtml(data.error)}</span>`;
          }
        } catch (parseErr) {
          // Skip malformed SSE lines
        }
      }
    }
  } catch (err) {
    appendMessage(
      "assistant",
      `<span style="color:var(--error);">⚠️ Connection failed: ${escapeHtml(err.message)}</span>`,
    );
    console.error("Chat error:", err);
  } finally {
    isStreaming = false;
    sendBtn.disabled = false;
  }
}

// ════════════════════════════════════════════════════════════════════
// MESSAGE RENDERING
// ════════════════════════════════════════════════════════════════════

/** Render a list of messages from the server into the chat area. */
function renderMessages(messages) {
  // Clear everything except the welcome screen
  const existing = messagesContainer.querySelectorAll(".message");
  existing.forEach((m) => m.remove());

  messages.forEach((msg) => {
    if (msg.role === "system") return; // Don't render system messages
    appendMessage(msg.role, msg.content);
  });
  scrollToBottom();
}

/**
 * Append a message bubble to the chat.
 * @param {'user'|'assistant'} role
 * @param {string} content — raw text (user) or HTML/markdown (assistant)
 * @returns {HTMLElement} The created message element
 */
function appendMessage(role, content) {
  const msgEl = document.createElement("div");
  msgEl.className = `message ${role}`;

  const avatar = role === "user" ? "👤" : "🧠";
  const rendered =
    role === "assistant" ? renderMarkdown(content) : escapeHtml(content);

  msgEl.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-body">
            <div class="message-content">${rendered}</div>
        </div>
    `;

  messagesContainer.appendChild(msgEl);

  // Post-process code highlighting for assistant messages
  if (role === "assistant") {
    const contentEl = msgEl.querySelector(".message-content");
    highlightCodeBlocks(contentEl);
    addCopyButtons(contentEl);
  }

  scrollToBottom();
  return msgEl;
}

// ════════════════════════════════════════════════════════════════════
// TOOL CALL CARDS — collapsible cards showing tool usage
// ════════════════════════════════════════════════════════════════════

/** Map tool names to descriptive icons and labels. */
const TOOL_META = {
  web_search: { icon: "🔍", label: "Web Search" },
  read_file: { icon: "📄", label: "Read File" },
  write_file: { icon: "📝", label: "Write File" },
  list_files: { icon: "📁", label: "List Files" },
  run_code: { icon: "▶️", label: "Run Code" },
  save_memory: { icon: "💾", label: "Save Memory" },
  recall_memories: { icon: "🔮", label: "Recall Memories" },
  analyze_image: { icon: "👁️", label: "Analyze Image" },
  take_screenshot: { icon: "📸", label: "Take Screenshot" },
  clipboard_read: { icon: "📋", label: "Read Clipboard" },
};

/**
 * Append a collapsible tool call card to the message content area.
 * Shows the tool name and arguments; expands to show results.
 */
function appendToolCard(parentEl, toolName, args, status) {
  const meta = TOOL_META[toolName] || { icon: "🔧", label: toolName };
  const statusLabel = status === "running" ? "⏳ Running..." : "✅ Done";

  const card = document.createElement("div");
  card.className = `tool-call-card ${status}`;
  card.setAttribute("data-tool", toolName);

  card.innerHTML = `
        <div class="tool-call-header"
             data-tooltip="${meta.label}: Click to expand and see details">
            <span class="tool-icon">${meta.icon}</span>
            <span class="tool-name">${meta.label}</span>
            <span class="tool-status">${statusLabel}</span>
        </div>
        <div class="tool-call-body">
            <strong>Input:</strong>\n${JSON.stringify(args, null, 2)}\n\n<strong>Output:</strong>\nWaiting...
        </div>
    `;

  // Click header to expand/collapse
  card.querySelector(".tool-call-header").addEventListener("click", () => {
    card.classList.toggle("expanded");
  });

  parentEl.appendChild(card);
  scrollToBottom();
}

/** Update a tool card with results once the tool finishes. */
function updateToolCard(parentEl, toolName, result, status) {
  const cards = parentEl.querySelectorAll(
    `.tool-call-card[data-tool="${toolName}"]`,
  );
  const card = cards[cards.length - 1]; // Get the most recent card for this tool
  if (!card) return;

  card.className = `tool-call-card ${status}`;
  const statusEl = card.querySelector(".tool-status");
  statusEl.textContent = status === "success" ? "✅ Done" : "❌ Error";

  const body = card.querySelector(".tool-call-body");
  const existingText = body.textContent.split("\n\nOutput:\n")[0];
  body.innerHTML = `${existingText}\n\n<strong>Output:</strong>\n${escapeHtml(result)}`;
}

// ════════════════════════════════════════════════════════════════════
// MARKDOWN + CODE HIGHLIGHTING
// ════════════════════════════════════════════════════════════════════

/** Convert markdown text to HTML using marked.js. */
function renderMarkdown(text) {
  if (!text) return "";
  try {
    return marked.parse(text, { breaks: true, gfm: true });
  } catch {
    return escapeHtml(text);
  }
}

/** Apply syntax highlighting to all code blocks in an element. */
function highlightCodeBlocks(el) {
  el.querySelectorAll("pre code").forEach((block) => {
    try {
      hljs.highlightElement(block);
    } catch {}
  });
}

/** Add copy buttons to all code blocks. */
function addCopyButtons(el) {
  el.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".code-copy-btn")) return; // Already has one

    const btn = document.createElement("button");
    btn.className = "code-copy-btn";
    btn.textContent = "Copy";
    btn.setAttribute("data-tooltip", "Copy code to clipboard");

    btn.addEventListener("click", async () => {
      const code = pre.querySelector("code")?.textContent || "";
      await navigator.clipboard.writeText(code);
      btn.textContent = "✓ Copied";
      setTimeout(() => {
        btn.textContent = "Copy";
      }, 2000);
    });

    pre.style.position = "relative";
    pre.appendChild(btn);
  });
}

// ════════════════════════════════════════════════════════════════════
// VOICE OUTPUT — Web Speech API with selectable voices
// ════════════════════════════════════════════════════════════════════

/**
 * Load available speech synthesis voices into the dropdown.
 * Voices load asynchronously, so we listen for the voiceschanged event.
 */
function loadVoices() {
  function populateVoices() {
    const voices = speechSynthesis.getVoices();
    voiceSelect.innerHTML = '<option value="">Default Voice</option>';

    voices.forEach((voice) => {
      const opt = document.createElement("option");
      opt.value = voice.name;
      opt.textContent = `${voice.name} (${voice.lang})`;
      voiceSelect.appendChild(opt);
    });
  }

  // Voices may already be loaded, or load asynchronously
  populateVoices();
  speechSynthesis.addEventListener("voiceschanged", populateVoices);
}

/**
 * Speak text aloud using the Web Speech API.
 * Strips markdown formatting for cleaner speech output.
 * @param {string} text — markdown-formatted text to speak
 */
function speak(text) {
  speechSynthesis.cancel(); // Stop any current speech

  // Strip markdown for cleaner voice output
  const clean = text
    .replace(/```[\s\S]*?```/g, "code block omitted") // Remove code blocks
    .replace(/`[^`]+`/g, "") // Remove inline code
    .replace(/[#*_~>\[\]()]/g, "") // Remove markdown chars
    .replace(/\n+/g, ". ") // Newlines to pauses
    .trim();

  if (!clean) return;

  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;
  utterance.volume = 0.9;

  if (selectedVoice) {
    utterance.voice = selectedVoice;
  }

  speechSynthesis.speak(utterance);
}

// ════════════════════════════════════════════════════════════════════
// CAMERA — WebRTC webcam capture
// ════════════════════════════════════════════════════════════════════

/** Open the camera modal and start the webcam stream. */
async function openCamera() {
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: 640, height: 480 },
      audio: false,
    });
    cameraPreview.srcObject = cameraStream;
    cameraModal.style.display = "flex";
  } catch (err) {
    alert("Camera access denied or unavailable. Check browser permissions.");
    console.error("Camera error:", err);
  }
}

/** Capture a frame from the webcam and attach it to the message. */
function captureFrame() {
  const video = cameraPreview;
  captureCanvas.width = video.videoWidth;
  captureCanvas.height = video.videoHeight;

  const ctx = captureCanvas.getContext("2d");
  ctx.drawImage(video, 0, 0);

  // Convert to base64 JPEG (smaller than PNG)
  const dataUrl = captureCanvas.toDataURL("image/jpeg", 0.85);
  capturedImageBase64 = dataUrl.split(",")[1]; // Remove data:image/jpeg;base64, prefix

  // Show preview
  previewImg.src = dataUrl;
  imagePreview.style.display = "flex";

  closeCamera();
}

/** Close the camera modal and stop the webcam stream. */
function closeCamera() {
  if (cameraStream) {
    cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
  }
  cameraPreview.srcObject = null;
  cameraModal.style.display = "none";
}

/** Remove the captured image from the message. */
function removeImage() {
  capturedImageBase64 = null;
  imagePreview.style.display = "none";
  previewImg.src = "";
}

// ════════════════════════════════════════════════════════════════════
// MEMORY STATUS
// ════════════════════════════════════════════════════════════════════

/** Load the current learning mode status from the server. */
async function loadMemoryStatus() {
  try {
    const resp = await fetch(`${API}/api/memory/status`);
    const data = await resp.json();
    learningToggle.checked = data.learning_enabled;
  } catch {
    // Server not running — default to enabled in UI
  }
}

// ════════════════════════════════════════════════════════════════════
// UTILITIES
// ════════════════════════════════════════════════════════════════════

/** Escape HTML special characters to prevent XSS. */
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

/** Auto-resize the input textarea based on content. */
function autoResizeInput() {
  messageInput.style.height = "auto";
  messageInput.style.height = Math.min(messageInput.scrollHeight, 150) + "px";
}

/** Scroll the messages container to the bottom. */
function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}
