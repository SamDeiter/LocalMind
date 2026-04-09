/**
 * events.js — Tab-based event binding for LocalMind 3-tab shell.
 * Replaces the old 9-nav-item sidebar system.
 */

import {
  state,
  $,
  sendBtn,
  messageInput,
  openCameraBtn,
  closeCameraBtn,
  snapBtn,
  micBtn,
  voiceBtn,
  voiceSelect,
  autoResize,
  priorityInput,
  addPriorityBtn,
} from "./state.js";
import { sendMessage, activateMode, clearMessages } from "./chat.js";
import { loadConversations } from "./conversations.js";
import { toggleMic, openCamera, closeCamera, captureFrame, clearCapturedImage } from "./media.js";
import { uploadDocuments } from "./sidebar.js";
import { toggleSettingsModal } from "./settings_ui.js";
import { submitQuickTask } from "./task_creation.js";
import { loadLearningData } from "./learning_ui.js";
import { loadProfile } from "./ai_profile.js";

// ── Tab State ────────────────────────────────────────────────────
const TABS = ["workTab", "chatTab", "systemTab"];
const TAB_BTNS = ["tabWork", "tabChat", "tabSystem"];

/** Switch the active tab. panelId: 'workTab' | 'chatTab' | 'systemTab' */
export function switchTab(panelId) {
  TABS.forEach((id) => {
    const panel = document.getElementById(id);
    if (!panel) return;
    if (id === panelId) {
      panel.classList.remove("hidden");
    } else {
      panel.classList.add("hidden");
    }
  });

  const btnMap = { workTab: "tabWork", chatTab: "tabChat", systemTab: "tabSystem" };
  TAB_BTNS.forEach((btnId) => {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    if (btnId === btnMap[panelId]) {
      btn.classList.add("tab-active");
      btn.setAttribute("aria-selected", "true");
    } else {
      btn.classList.remove("tab-active");
      btn.setAttribute("aria-selected", "false");
    }
  });

  // Lazy-load System tab sections on first open
  if (panelId === "systemTab") {
    _initSystemTabOnce();
  }
}

// ── Accordion Logic ──────────────────────────────────────────────
const _accordionInited = new Set();

function _initAccordions() {
  document.querySelectorAll("[data-accordion]").forEach((headerBtn) => {
    headerBtn.addEventListener("click", () => {
      const bodyId = headerBtn.getAttribute("data-accordion");
      const body = document.getElementById(bodyId);
      if (!body) return;
      const isOpen = !body.classList.contains("hidden");

      if (isOpen) {
        body.classList.add("hidden");
        headerBtn.setAttribute("aria-expanded", "false");
      } else {
        body.classList.remove("hidden");
        headerBtn.setAttribute("aria-expanded", "true");
        // Lazy init on first expand
        _lazyInitAccordion(bodyId);
      }
    });
  });
}

function _lazyInitAccordion(bodyId) {
  if (_accordionInited.has(bodyId)) return;
  _accordionInited.add(bodyId);

  switch (bodyId) {
    case "accordionLearningBody":
      loadLearningData?.();
      break;
    case "accordionAIProfileBody":
      loadProfile?.();
      break;
    case "accordionEditorBody":
      // Monaco is already initialized by editor.js init — just trigger resize
      window.dispatchEvent(new Event("resize"));
      break;
    default:
      break;
  }
}

let _systemTabInited = false;
function _initSystemTabOnce() {
  if (_systemTabInited) return;
  _systemTabInited = true;
  // Worker Pool accordion is open by default — mark it as inited
  _accordionInited.add("accordionWorkerPoolBody");
}

// ── Main Event Binding ───────────────────────────────────────────
export function bindEvents() {
  // ── Tab Buttons ──────────────────────────────────────────────
  document.getElementById("tabWork")?.addEventListener("click", () => switchTab("workTab"));
  document.getElementById("tabChat")?.addEventListener("click", () => switchTab("chatTab"));
  document.getElementById("tabSystem")?.addEventListener("click", () => switchTab("systemTab"));

  // ── Accordions ───────────────────────────────────────────────
  _initAccordions();

  // ── Work Tab: Task Input ─────────────────────────────────────
  addPriorityBtn?.addEventListener("click", () => {
    const text = priorityInput?.value?.trim() || "";
    if (!text) return;
    submitQuickTask(text);
    if (priorityInput) priorityInput.value = "";
  });

  priorityInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = priorityInput?.value?.trim() || "";
      if (!text) return;
      submitQuickTask(text);
      priorityInput.value = "";
    }
  });

  // Work tab attach/camera buttons → reuse media module
  document.getElementById("workAttachBtn")?.addEventListener("click", () => {
    const docUpload = document.getElementById("docUploadInput");
    docUpload?.click();
  });
  document.getElementById("workCameraBtn")?.addEventListener("click", openCamera);
  document.getElementById("workRemoveImageBtn")?.addEventListener("click", clearCapturedImage);

  // ── Chat Tab: Message Input ──────────────────────────────────
  sendBtn?.addEventListener("click", () => sendMessage());

  messageInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  messageInput?.addEventListener("input", autoResize);

  // Chat tab: new conversation inline button
  document.getElementById("newChatInlineBtn")?.addEventListener("click", () => {
    clearMessages?.();
    loadConversations?.();
  });

  // ── Voice & Camera ───────────────────────────────────────────
  micBtn?.addEventListener("click", toggleMic);
  voiceBtn?.addEventListener("click", () => {
    state.voiceEnabled = !state.voiceEnabled;
    localStorage.setItem("localmind_voice", state.voiceEnabled ? "on" : "off");
    if (voiceBtn) voiceBtn.classList.toggle("active", state.voiceEnabled);
  });
  if (voiceBtn) voiceBtn.classList.remove("active");
  voiceSelect?.addEventListener("change", () => { /* voice stored by index */ });

  openCameraBtn?.addEventListener("click", openCamera);
  closeCameraBtn?.addEventListener("click", closeCamera);
  snapBtn?.addEventListener("click", captureFrame);

  // ── Mode Buttons ─────────────────────────────────────────────
  document.querySelectorAll(".mode-btn").forEach((b) => {
    b.addEventListener("click", () => activateMode(b.dataset.mode));
  });

  // ── Settings ─────────────────────────────────────────────────
  const spBtn = $("#systemPromptBtn");
  spBtn?.addEventListener("click", () => toggleSettingsModal(true));
  $("#saveSystemPrompt")?.addEventListener("click", async () => {
    const text = $("#systemPromptText")?.value || "";
    try {
      await fetch(`${window.location.origin}/api/system-prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text }),
      });
    } catch { /* ignore */ }
  });

  // ── Doc Upload ───────────────────────────────────────────────
  const docUpload = $("#docUploadInput");
  docUpload?.addEventListener("change", () => {
    if (docUpload.files.length) uploadDocuments(Array.from(docUpload.files));
  });
  $("#uploadDocsBtn")?.addEventListener("click", () => docUpload?.click());

  // ── Stop ─────────────────────────────────────────────────────
  $("#stopBtn")?.addEventListener("click", () => {
    if (state.abortController) state.abortController.abort();
  });

  // ── Prompt Pills ─────────────────────────────────────────────
  document.querySelectorAll(".prompt-pill").forEach((p) => {
    p.addEventListener("click", () => {
      if (messageInput) {
        messageInput.value = p.textContent;
        sendMessage();
      }
    });
  });

  // ── Learning toggle (legacy, may not exist in new HTML) ──────
  const learningToggle = $("#learningToggle");
  learningToggle?.addEventListener("change", async () => {
    try {
      await fetch(`${window.location.origin}/api/memory/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: learningToggle.checked }),
      });
    } catch { /* ignore */ }
  });

  // ── Model select ─────────────────────────────────────────────
  const modelSelect = $("#modelSelect");
  modelSelect?.addEventListener("change", () => {
    state.model = modelSelect.value;
  });

  // ── Thinking terminal close ──────────────────────────────────
  // (terminal shows/hides based on task activity — no change needed)
}
