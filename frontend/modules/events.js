/**
 * Event binding — wires all DOM events to their module handlers.
 */

import {
  state,
  $,
  sidebar,
  sidebarToggle,
  newChatBtn,
  learningToggle,
  modelSelect,
  sendBtn,
  messageInput,
  openCameraBtn,
  closeCameraBtn,
  snapBtn,
  removeImageBtn,
  micBtn,
  voiceBtn,
  voiceSelect,
  autoResize,
} from "./state.js";
import { sendMessage, activateMode, clearMessages } from "./chat.js";
import { loadConversations } from "./conversations.js";
import { toggleMic, openCamera, closeCamera, captureFrame, clearCapturedImage } from "./media.js";
import { uploadDocuments, toggleMemoryList } from "./sidebar.js";

export function bindEvents() {
  // Sidebar
  sidebarToggle?.addEventListener("click", () => sidebar?.classList.toggle("collapsed"));
  newChatBtn?.addEventListener("click", () => {
    state.currentConvId = null;
    state.messages = [];
    clearMessages();
    loadConversations();
  });

  // Send button + Enter
  sendBtn?.addEventListener("click", sendMessage);
  messageInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  messageInput?.addEventListener("input", autoResize);

  // Learning toggle
  learningToggle?.addEventListener("change", async () => {
    try {
      await fetch(`${window.location.origin}/api/memory/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: learningToggle.checked }),
      });
    } catch {
      /* ignore */
    }
  });

  // Model dropdown
  modelSelect?.addEventListener("change", () => {
    state.model = modelSelect.value;
  });

  // Voice
  micBtn?.addEventListener("click", toggleMic);
  voiceBtn?.addEventListener("click", () => {
    state.voiceEnabled = !state.voiceEnabled;
    localStorage.setItem("localmind_voice", state.voiceEnabled ? "on" : "off");
    if (voiceBtn) voiceBtn.classList.toggle("active", state.voiceEnabled);
  });
  // Ensure voice button UI matches default-off state
  if (voiceBtn) voiceBtn.classList.remove("active");
  voiceSelect?.addEventListener("change", () => {
    /* voice stored by index */
  });

  // Camera
  openCameraBtn?.addEventListener("click", openCamera);
  closeCameraBtn?.addEventListener("click", closeCamera);
  snapBtn?.addEventListener("click", captureFrame);
  removeImageBtn?.addEventListener("click", clearCapturedImage);

  // Mode buttons
  document.querySelectorAll(".mode-btn").forEach((b) => {
    b.addEventListener("click", () => activateMode(b.dataset.mode));
  });

  // System prompt
  const spBtn = $("#systemPromptBtn");
  const spEditor = $("#systemPromptEditor");
  spBtn?.addEventListener("click", () => {
    if (spEditor) spEditor.style.display = spEditor.style.display === "none" ? "" : "none";
  });
  $("#saveSystemPrompt")?.addEventListener("click", async () => {
    const text = $("#systemPromptText")?.value || "";
    try {
      await fetch(`${window.location.origin}/api/system-prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text }),
      });
    } catch {
      /* ignore */
    }
    if (spEditor) spEditor.style.display = "none";
  });

  // Doc upload
  const docUpload = $("#docUploadInput");
  docUpload?.addEventListener("change", () => {
    if (docUpload.files.length) uploadDocuments(Array.from(docUpload.files));
  });
  $("#uploadDocsBtn")?.addEventListener("click", () => docUpload?.click());

  // Memory
  $("#memoryToggleBtn")?.addEventListener("click", toggleMemoryList);

  // Stop button
  $("#stopBtn")?.addEventListener("click", () => {
    if (state.abortController) state.abortController.abort();
  });

  // Suggested prompts
  document.querySelectorAll(".prompt-pill").forEach((p) => {
    p.addEventListener("click", () => {
      if (messageInput) {
        messageInput.value = p.textContent;
        sendMessage();
      }
    });
  });
}
