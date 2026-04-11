/**
 * events.js — Event bindings for LocalMind (accordions, chat input, media, settings).
 * Navigation is handled by nav_rail.js.
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

// ── Legacy Tab Compat ────────────────────────────────────────────
// Old 3-tab shell replaced by nav_rail.js. Keep switchTab as no-op
// so any callers don't crash.
export function switchTab(_panelId) {
  // Navigation is now handled by nav_rail.js switchNav()
}

// ── Accordion Logic ──────────────────────────────────────────────
const _accordionInited = new Set();

function _initAccordions() {
  document.querySelectorAll("[data-accordion]").forEach((headerEl) => {
    const toggle = () => {
      const bodyId = headerEl.getAttribute("data-accordion");
      const body = document.getElementById(bodyId);
      if (!body) return;
      const isOpen = headerEl.getAttribute("aria-expanded") === "true";
      if (isOpen) {
        body.classList.add("hidden");
        headerEl.setAttribute("aria-expanded", "false");
      } else {
        body.classList.remove("hidden");
        headerEl.setAttribute("aria-expanded", "true");
        _lazyInitAccordion(bodyId);
      }
    };
    headerEl.addEventListener("click", toggle);
    // Support Enter/Space for div[role=button] accordion headers
    if (headerEl.tagName !== "BUTTON") {
      headerEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
      });
    }
  });
}

function _lazyInitAccordion(bodyId) {
  if (_accordionInited.has(bodyId)) return;
  _accordionInited.add(bodyId);

  switch (bodyId) {
    case "accordionWorkerPoolBody":
      // Start swarm polling when the Worker Pool section is first expanded
      import("./swarm_ui.js").then(m => {
        m.initSwarmTabs?.();
        m.startPolling?.();
      }).catch(() => {});
      break;
    case "accordionLearningBody":
      loadLearningData?.();
      break;
    case "accordionAIProfileBody":
      loadProfile?.();
      break;
    case "accordionEvalsBody":
      import("./eval_ui.js").then(m => m.initEvalUI()).catch(() => {});
      break;
    case "accordionMonitoringBody":
      import("./monitoring_ui.js").then(m => m.initMonitoring()).catch(() => {});
      break;
    case "accordionEditorBody":
      // Monaco is already initialized by editor.js init — just trigger resize
      window.dispatchEvent(new Event("resize"));
      break;
    default:
      break;
  }
}

// ── Main Event Binding ───────────────────────────────────────────
export function bindEvents() {
  // Tab buttons are now hidden shims — nav_rail.js handles navigation.

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
  document.getElementById("chatSidebarToggle")?.addEventListener("click", () => {
    document.getElementById("chatSidebar")?.classList.toggle("open");
    document.getElementById("sidebarBackdrop")?.classList.toggle("visible");
  });

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

  // ── Backdrop ─────────────────────────────────────────────────
  document.getElementById("sidebarBackdrop")?.addEventListener("click", () => {
    document.getElementById("chatSidebar")?.classList.remove("open");
    document.getElementById("sidebarBackdrop")?.classList.remove("visible");
  });
}