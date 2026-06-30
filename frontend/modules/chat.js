/**
 * Chat engine — sendMessage, message rendering, markdown, approval cards.
 * Also: model management (checkHealth, loadModels, resolveModel, activateMode).
 *
 * SSE streaming logic lives in ./streaming.js
 * Tool card rendering lives in ./tools.js
 */

import {
  API,
  state,
  MODE_MODELS,
  messageInput,
  sendBtn,
  stopBtn,
  messagesContainer,
  welcomeScreen,
  systemPromptText,
  loadingStatus,
  modelSelect,
  editorState,
  scrollToBottom,
  resetAutoScroll,
  autoResize,
} from "./state.js";
import { escapeHtml, getLang } from "./utils.js";
import { loadConversations } from "./conversations.js";
import { clearCapturedImage } from "./media.js";
import { loadMemories } from "./sidebar.js";
import { streamChat } from "./streaming.js";
import { createToolCallCard, updateToolResult, highlightCode } from "./tools.js";
import { speakText, isTTSEnabled, renderTTSButton } from "./tts.js";
import { updateTokenStream, updateTokenAnalytics, resetTokenPanel } from "./token_panel.js";

// Re-export so external consumers that imported from chat.js still work
export { createToolCallCard, updateToolResult, highlightCode } from "./tools.js";
export { streamChat } from "./streaming.js";

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

  // Switch to chat view if not already visible
  const chatScreen = document.getElementById("chatScreen");
  if (chatScreen && chatScreen.classList.contains("hidden")) {
    // Trigger the chat nav button to properly hide other views
    const chatBtn = document.getElementById("chatBtn");
    if (chatBtn) chatBtn.click();
  }

  state.messages.push({ role: "user", content: text });
  appendMessage("user", text);
  scrollToBottom();
  autoResize();

  const assistantEl = appendMessage("assistant", "");
  addTypingIndicator(assistantEl);
  let typingRemoved = false;

  state.streaming = true;
  sendBtn.disabled = true;
  sendBtn.style.display = "none";
  resetAutoScroll();
  if (stopBtn) {
    stopBtn.style.display = "";
    stopBtn.focus();
  }
  state.abortController = new AbortController();

  resetTokenPanel();

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

  const contentEl = assistantEl.querySelector(".message-content");

  const removeTyping = () => {
    if (!typingRemoved) {
      const dots = assistantEl.querySelector(".typing-dots");
      if (dots) dots.remove();
      typingRemoved = true;
    }
  };

  try {
    const fullText = await streamChat(
      body,
      {
        onToken(_token, fullText) {
          removeTyping();
          if (contentEl) {
            contentEl.innerHTML = renderMarkdown(fullText);
            highlightCode();
          }
          updateTokenStream(fullText.length);
          scrollToBottom();
        },

        onToolCall(tc) {
          removeTyping();
          // If this is a propose_action call, the approval card will be
          // rendered by the approval_request event instead.
          if (tc.name !== "propose_action") {
            const card = createToolCallCard(tc);
            if (contentEl) contentEl.appendChild(card);
          }
          scrollToBottom();
        },

        onApproval(req) {
          removeTyping();
          // Render an inline approval card for the user.
          const card = document.createElement("div");
          card.className = "approval-card";
          const riskClass = { LOW: "approval-risk-low", MEDIUM: "approval-risk-medium", HIGH: "approval-risk-high" };
          const riskCls = riskClass[req.risk_level] || "approval-risk-medium";
          const icons = {
            install_package: "📦",
            download_file: "📥",
            use_cloud_model: "☁️",
            web_submit: "🌐",
            system_command: "🔧",
          };
          const icon = icons[req.action_type] || "⚡";
          card.innerHTML = `
            <div class="approval-header">
              <span>${icon} Action Request</span>
              <span class="approval-risk ${riskCls}">${req.risk_level || "MEDIUM"}</span>
            </div>
            <div class="approval-body">
              <div class="approval-desc">${escapeHtml(req.description || "")}</div>
              <div class="approval-reason"><em>${escapeHtml(req.reason || "")}</em></div>
              ${req.estimated_cost ? `<div class="approval-cost">Cost: ${escapeHtml(req.estimated_cost)}</div>` : ""}
              ${req.alternatives ? `<div class="approval-alt">Alt: ${escapeHtml(req.alternatives)}</div>` : ""}
            </div>
            <div class="approval-actions">
              <button class="approval-btn approve" data-decision="true">✅ Approve</button>
              <button class="approval-btn deny" data-decision="false">❌ Deny</button>
            </div>
          `;
          if (contentEl) contentEl.appendChild(card);
          // Wire the buttons — they call POST /api/approve/:id
          card.querySelectorAll(".approval-btn").forEach((btn) => {
            btn.addEventListener("click", async () => {
              const approved = btn.dataset.decision === "true";
              // Find the request_id from the pending approvals endpoint
              try {
                const res = await fetch(`${window.location.origin}/api/approvals/pending`);
                const data = await res.json();
                const pending = data.pending || [];
                if (pending.length > 0) {
                  const latestId = pending[pending.length - 1].request_id;
                  await fetch(`${window.location.origin}/api/approve/${latestId}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ approved }),
                  });
                  // Update card visual
                  card.classList.add(approved ? "approved" : "denied");
                  card.querySelector(".approval-actions").innerHTML = approved
                    ? '<span class="approval-resolved">✅ Approved</span>'
                    : '<span class="approval-resolved">❌ Denied</span>';
                }
              } catch (err) {
                console.error("[LocalMind] Approval error:", err);
              }
            });
          });
          scrollToBottom();
        },

        onToolResult(result) {
          removeTyping();
          updateToolResult(contentEl, result);
          scrollToBottom();
        },

        onThinking(thinking) {
          removeTyping();
          const mode = thinking.provider === "react_agent" ? "Agent" : "Chat";
          const badge = document.createElement("div");
          badge.className = "agent-mode-badge";
          badge.innerHTML = `<strong>${mode}</strong> &middot; ${thinking.model || "model"} &middot; ${thinking.tier || "auto"}`;
          if (contentEl) contentEl.prepend(badge);
        },

        onEstimate(_estimate) {
          // Currently just logged in streaming.js
        },

        onAnalytics(a) {
          removeTyping();
          updateTokenAnalytics(a);
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
        },

        onDone(evt) {
          if (evt.conversation_id) {
            state.currentConvId = evt.conversation_id;
          }
        },

        onError(error) {
          removeTyping();
          if (contentEl) {
            contentEl.innerHTML = `<div class="flex items-center gap-2 text-red-400 text-sm"><span class="material-symbols-outlined text-base">error</span> ${escapeHtml(error)}</div>`;
          }
        },

        onReconnecting(attempt, maxAttempts) {
          if (contentEl) {
            contentEl.innerHTML = `<div class="flex items-center gap-2 text-amber-400 text-sm"><span class="material-symbols-outlined text-base animate-spin">progress_activity</span> Reconnecting\u2026 (attempt ${attempt}/${maxAttempts})</div>`;
          }
        },
      },
      state.abortController.signal,
    );

    state.messages.push({ role: "assistant", content: fullText });

    // Auto-play TTS for new AI responses when enabled
    if (isTTSEnabled() && fullText) speakText(fullText);

    // Append per-message TTS play button to the assistant bubble
    const ttsHtml = renderTTSButton(fullText);
    if (ttsHtml && contentEl) {
      contentEl.insertAdjacentHTML("beforeend", ttsHtml);
    }

    // Sync conversation state without clearing the visible streamed content.
    // loadConversation + renderMessages would wipe the DOM and re-render,
    // which flashes away the streamed response. Instead, just update the
    // sidebar conversation list so the title/timestamp refresh.
    await loadConversations();

    // Refresh memory badge — auto-save heuristic may have saved new memories
    // during this chat turn, so update the sidebar count + list
    await loadMemories();
  } catch (e) {
    if (e.name === "AbortError") {
      console.log("[LocalMind] Request aborted by user");
    } else {
      console.error("[LocalMind] Stream error:", e);
      if (contentEl) {
        contentEl.innerHTML = `<div class="flex items-center gap-2 text-red-400 text-sm"><span class="material-symbols-outlined text-base">cloud_off</span> Connection error: ${escapeHtml(e.message)}</div>`;
      }
    }
  } finally {
    state.streaming = false;
    sendBtn.disabled = false;
    sendBtn.style.display = "";
    state.abortController = null;
    if (stopBtn) stopBtn.style.display = "none";
    messageInput.focus();
  }
}

// ── Message Rendering ───────────────────────────────────────────
export function clearMessages() {
  if (messagesContainer) {
    // Remove only message elements
    const messages = messagesContainer.querySelectorAll(".message");
    messages.forEach((m) => m.remove());
  }
}

export function renderMessages() {
  if (!messagesContainer) return;
  // messagesContainer.innerHTML = ""; // Managed by clearMessages
  const chatScreen = document.getElementById("chatScreen");
  // Dashboard UI is no longer obscured when chatting
  if (chatScreen) {
    if (state.messages.length > 0) {
      chatScreen.classList.remove("hidden");
      chatScreen.style.display = "flex";
    } else {
      chatScreen.style.display = "none";
    }
  }
  state.messages.forEach((m) => {
    createMessageEl(m.role, m.content);
  });
  highlightCode();
  scrollToBottom();
}

export function appendMessage(role, content) {
  if (welcomeScreen) welcomeScreen.style.display = "none";
  // Hide chat empty state on first message
  const emptyState = document.getElementById("chatEmptyState");
  if (emptyState) emptyState.style.display = "none";
  return createMessageEl(role, content);
}

export function createMessageEl(role, content) {
  const wrapper = document.createElement("div");
  const isUser = role === "user";
  wrapper.className = `message ${role}-message flex w-full mb-3 ${isUser ? "justify-end" : "justify-start"}`;

  const contentDiv = document.createElement("div");
  contentDiv.className = `message-content max-w-[75%] px-4 py-3 rounded-2xl text-sm leading-relaxed ${
    isUser
      ? "bg-indigo-500/12 border border-indigo-500/20 text-slate-200 rounded-tr-sm"
      : "bg-slate-800/50 border border-slate-700/25 text-slate-300 rounded-tl-sm"
  }`;

  contentDiv.innerHTML = role === "assistant" ? renderMarkdown(content) : escapeHtml(content);

  // Add per-message TTS play button to assistant messages
  if (role === "assistant" && content) {
    const ttsBtn = renderTTSButton(content);
    if (ttsBtn) contentDiv.insertAdjacentHTML("beforeend", ttsBtn);
  }

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

// ── Markdown ────────────────────────────────────────────────────
export function renderMarkdown(text) {
  if (!text) return "";
  try {
    return marked.parse(text, { breaks: true, gfm: true });
  } catch {
    return escapeHtml(text);
  }
}
