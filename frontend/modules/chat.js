/**
 * Chat engine — sendMessage, SSE streaming, message rendering, tool cards, markdown.
 * Also: model management (checkHealth, loadModels, resolveModel, activateMode).
 */

import {
  API,
  state,
  MODE_MODELS,
  messageInput,
  sendBtn,
  messagesContainer,
  welcomeScreen,
  systemPromptText,
  loadingStatus,
  modelSelect,
  editorState,
  scrollToBottom,
  autoResize,
} from "./state.js";
import { escapeHtml, getLang, getFileExtension, extToLang } from "./utils.js";
import { loadConversation, loadConversations } from "./conversations.js";
import { speak, clearCapturedImage } from "./media.js";

// ── Model & Health ──────────────────────────────────────────────
export async function checkHealth() {
  try {
    const r = await fetch(`${API}/api/health`);
    const d = await r.json();
    if (loadingStatus) {
      loadingStatus.textContent = d.status === "ok" ? "Connected to Ollama" : "Ollama not found";
    }
  } catch {
    if (loadingStatus) loadingStatus.textContent = "Cannot reach server";
  }
}

export function resolveModel() {
  if (state.mode === "auto") return "auto";
  return MODE_MODELS[state.mode] || "auto";
}

export function activateMode(mode) {
  state.mode = mode;
  state.model = MODE_MODELS[mode] || "auto";
  localStorage.setItem("localmind_mode", mode);
  document.querySelectorAll(".mode-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
  });
}

export async function loadModels() {
  try {
    const r = await fetch(`${API}/api/models`);
    const d = await r.json();
    if (!modelSelect) return;
    modelSelect.innerHTML = '<option value="auto">🤖 Auto-Route</option>';
    (d.models || []).forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = m.name;
      modelSelect.appendChild(opt);
    });
  } catch {
    /* offline */
  }
}

// ── Send Message ────────────────────────────────────────────────
export async function sendMessage() {
  const text = messageInput.value.trim();
  if (!text || state.streaming) return;

  messageInput.value = "";
  autoResize();

  if (welcomeScreen) welcomeScreen.style.display = "none";

  state.messages.push({ role: "user", content: text });
  appendMessage("user", text);
  scrollToBottom();
  autoResize();

  const assistantEl = appendMessage("assistant", "");
  addTypingIndicator(assistantEl);
  let typingRemoved = false;

  state.streaming = true;
  sendBtn.disabled = true;
  state.abortController = new AbortController();

  const body = {
    model: resolveModel(),
    message: text,
    conversation_id: state.currentConvId || undefined,
    system_prompt: systemPromptText.value || undefined,
  };

  // Auto-inject editor context if a file is open
  if (editorState.monacoEditor && editorState.currentPath) {
    const editorCode = editorState.monacoEditor.getValue();
    if (editorCode && editorCode.trim()) {
      const lang = getLang(editorState.currentPath.split("/").pop());
      const snippet =
        editorCode.length > 2000 ? editorCode.substring(0, 2000) + "\n... (truncated)" : editorCode;
      body.editor_context = `File: ${editorState.currentPath} (${lang})\n\`\`\`${lang}\n${snippet}\n\`\`\``;
    }
  }

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
    console.log("[LocalMind] Fetch response status:", resp.status, resp.statusText);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";
    let chunkCount = 0;
    const contentEl = assistantEl.querySelector(".message-content");

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        console.log("[LocalMind] Stream ended. Total chunks:", chunkCount);
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const raw = line.slice(6).trim();
        if (!raw || raw === "[DONE]") continue;

        let evt;
        try {
          evt = JSON.parse(raw);
        } catch {
          continue;
        }
        chunkCount++;

        if (!typingRemoved) {
          const dots = assistantEl.querySelector(".typing-dots");
          if (dots) dots.remove();
          typingRemoved = true;
        }

        if (evt.token) {
          fullText += evt.token;
          if (contentEl) {
            contentEl.innerHTML = renderMarkdown(fullText);
            highlightCode();
          }
          scrollToBottom();
        } else if (evt.tool_call) {
          const card = createToolCallCard(evt.tool_call);
          if (contentEl) contentEl.appendChild(card);
          scrollToBottom();
        } else if (evt.tool_result) {
          updateToolResult(contentEl, evt.tool_result);
          scrollToBottom();
        } else if (evt.thinking) {
          // Thinking/routing info — could display a subtle indicator
          console.log("[LocalMind] Thinking:", evt.thinking);
        } else if (evt.task_estimate) {
          console.log("[LocalMind] Task estimate:", evt.task_estimate);
        } else if (evt.analytics) {
          const a = evt.analytics;
          const panel = document.createElement("div");
          panel.className = "thinking-panel";
          panel.innerHTML = `
            <div class="thinking-header" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'">
              <span>🧠 Thinking</span>
              <span class="thinking-meta">${a.model || "model"} • ${a.total_tokens || "?"} tok • ${a.tokens_per_sec?.toFixed(1) || "?"} tok/s • ${a.elapsed_sec}s</span>
              <span class="thinking-toggle">▼</span>
            </div>
            <div class="thinking-body" style="display:none">
              <strong>Tool calls:</strong> ${a.tool_calls || 0}
            </div>`;
          if (contentEl) contentEl.appendChild(panel);
        } else if (evt.done) {
          if (evt.conversation_id && !state.currentConvId) {
            state.currentConvId = evt.conversation_id;
          }
        } else if (evt.error) {
          if (contentEl) {
            contentEl.innerHTML = `<div style="color: var(--error)">❌ ${escapeHtml(evt.error)}</div>`;
          }
        }
      }
    }

    state.messages.push({ role: "assistant", content: fullText });

    // TTS
    if (state.voiceEnabled && fullText) speak(fullText);

    // Refresh conversation list
    if (state.currentConvId) {
      await loadConversation(state.currentConvId);
      // Re-render messages from loaded conversation
      renderMessages();
    }
    await loadConversations();
  } catch (e) {
    if (e.name === "AbortError") {
      console.log("[LocalMind] Request aborted by user");
    } else {
      console.error("[LocalMind] Stream error:", e);
      const contentEl = assistantEl.querySelector(".message-content");
      if (contentEl) {
        contentEl.innerHTML = `<div style="color: var(--error)">❌ Connection error: ${escapeHtml(e.message)}</div>`;
      }
    }
  } finally {
    state.streaming = false;
    sendBtn.disabled = false;
    state.abortController = null;
  }
}

// ── Message Rendering ───────────────────────────────────────────
export function clearMessages() {
  if (messagesContainer) messagesContainer.innerHTML = "";
  if (welcomeScreen) welcomeScreen.style.display = "";
}

export function renderMessages() {
  if (!messagesContainer) return;
  messagesContainer.innerHTML = "";
  if (welcomeScreen) {
    welcomeScreen.style.display = state.messages.length ? "none" : "";
  }
  state.messages.forEach((m) => {
    createMessageEl(m.role, m.content);
  });
  highlightCode();
  scrollToBottom();
}

export function appendMessage(role, content) {
  if (welcomeScreen) welcomeScreen.style.display = "none";
  return createMessageEl(role, content);
}

export function createMessageEl(role, content) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}-message`;
  const contentDiv = document.createElement("div");
  contentDiv.className = "message-content";
  contentDiv.innerHTML = role === "assistant" ? renderMarkdown(content) : escapeHtml(content);
  wrapper.appendChild(contentDiv);
  if (messagesContainer) messagesContainer.appendChild(wrapper);
  return wrapper;
}

export function addTypingIndicator(el) {
  const dots = document.createElement("div");
  dots.className = "typing-dots";
  dots.innerHTML = "<span></span><span></span><span></span>";
  el.querySelector(".message-content")?.appendChild(dots);
}

// ── Tool Cards ──────────────────────────────────────────────────
export function createToolCallCard(tc) {
  const card = document.createElement("div");
  card.className = "tool-call-card";
  card.dataset.toolCallId = tc.id || tc.tool_call_id || "";

  const iconMap = {
    web_search: "🔍",
    run_code: "💻",
    read_file: "📖",
    write_file: "✏️",
    list_files: "📂",
  };
  const icon = iconMap[tc.name] || "🔧";

  card.innerHTML = `
    <div class="tool-call-header">
      <span class="tool-icon">${icon}</span>
      <span class="tool-name">${escapeHtml(tc.name)}</span>
      <span class="tool-status">⏳ Running...</span>
    </div>
    <div class="tool-call-args">
      <pre>${escapeHtml(JSON.stringify(tc.arguments || tc.args || {}, null, 2))}</pre>
    </div>
  `;
  return card;
}

export function updateToolResult(container, result) {
  if (!container) return;
  const id = result.tool_call_id || result.id;
  const card = container.querySelector(`.tool-call-card[data-tool-call-id="${id}"]`);

  if (card) {
    const status = card.querySelector(".tool-status");
    const success = result.success !== false;
    if (status) {
      status.textContent = success ? "✅ Done" : "❌ Failed";
      status.className = `tool-status ${success ? "success" : "error"}`;
    }

    // Show result
    const resultDiv = document.createElement("div");
    resultDiv.className = "tool-result";

    const output = result.result || result.output || result.error || "";
    const outputStr = typeof output === "object" ? JSON.stringify(output, null, 2) : String(output);

    // Check if output looks like file content
    if (result.name === "read_file" && result.path) {
      const ext = getFileExtension(result.path);
      const lang = extToLang(ext);
      resultDiv.innerHTML = `
        <div class="code-viewer">
          <div class="code-viewer-header">
            <span class="code-viewer-filename">${escapeHtml(result.path)}</span>
            <button class="code-copy-btn" onclick="navigator.clipboard.writeText(this.closest('.code-viewer').querySelector('code').textContent)">Copy</button>
          </div>
          <pre><code class="language-${lang}">${escapeHtml(outputStr)}</code></pre>
        </div>`;
    } else if (outputStr.length > 200) {
      resultDiv.innerHTML = `
        <div class="code-viewer">
          <div class="code-viewer-header">
            <span class="code-viewer-filename">Output</span>
            <button class="code-copy-btn" onclick="navigator.clipboard.writeText(this.closest('.code-viewer').querySelector('code').textContent)">Copy</button>
          </div>
          <pre><code>${escapeHtml(outputStr)}</code></pre>
        </div>`;
    } else {
      resultDiv.innerHTML = `<pre class="tool-output">${escapeHtml(outputStr)}</pre>`;
    }

    card.appendChild(resultDiv);
    highlightCode();
  }
}

// ── Markdown ────────────────────────────────────────────────────
export function renderMarkdown(text) {
  if (!text) return "";
  try {
    return marked.parse(text, { breaks: true, gfm: true });
  } catch {
    return escapeHtml(text);
  }
}

export function highlightCode() {
  try {
    document.querySelectorAll("pre code:not(.hljs)").forEach((block) => {
      hljs.highlightElement(block);
    });
  } catch {
    /* hljs not loaded */
  }
}
