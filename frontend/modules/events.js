/**
 * Event binding — wires all DOM events to their module handlers.
 */

import {
  state,
  $,
  sidebar,
  sidebarToggle,
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
import { toggleEditorPanel } from "./editor.js";
import { toggleSettingsModal } from "./settings_ui.js";
import { hideSwarmDashboard } from "./swarm_ui.js";
import { showJobsView } from "./jobs_ui.js";
import { showTemplatesView } from "./templates_ui.js";
import { showApprovalsView } from "./approvals_ui.js";
import { loadHub } from "./hub.js";
import { loadLearningData } from "./learning_ui.js";
import { loadProfile } from "./ai_profile.js";
import { chatScreen, overviewBtn } from "./state.js";

/** Hide all views and restore the default main scroll area. */
function hideAllViews() {
  hideSwarmDashboard();
  const mainScroll = document.getElementById("mainScrollArea");
  const jobsView = document.getElementById("jobsView");
  const templatesView = document.getElementById("templatesView");
  const approvalsView = document.getElementById("approvalsView");
  const chat = document.getElementById("chatScreen");
  const hubView = document.getElementById("crossProjectHub");
  const learningPage = document.getElementById("learningPage");
  const aiProfileView = document.getElementById("aiProfileView");
  if (mainScroll) { mainScroll.classList.add("hidden"); mainScroll.style.display = "none"; }
  if (jobsView) jobsView.classList.add("hidden");
  if (templatesView) templatesView.classList.add("hidden");
  if (approvalsView) approvalsView.classList.add("hidden");
  if (hubView) hubView.classList.add("hidden");
  if (learningPage) learningPage.classList.add("hidden");
  if (aiProfileView) aiProfileView.classList.add("hidden");
  if (chat) { chat.classList.add("hidden"); chat.style.display = "none"; }
}

/** Reset active state on all nav buttons, then highlight the given one. */
function setActiveNav(activeId) {
  const navIds = ["overviewBtn", "jobsBtn", "templatesBtn", "approvalsBtn", "hubBtn", "aiProfileBtn", "chatBtn", "learningBtn", "swarmDashBtn"];
  navIds.forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn) return;
    if (id === activeId) {
      btn.classList.add("text-slate-100", "bg-slate-800/40", "border", "border-slate-700/30");
      btn.classList.remove("text-slate-400");
    } else {
      btn.classList.remove("text-slate-100", "bg-slate-800/40", "border", "border-slate-700/30");
      btn.classList.add("text-slate-400");
    }
  });
}

export function bindEvents() {
  // Sidebar
  sidebarToggle?.addEventListener("click", () => sidebar?.classList.toggle("collapsed"));

  // Nav — Dashboard
  overviewBtn?.addEventListener("click", () => {
    hideAllViews();
    const mainScroll = document.getElementById("mainScrollArea");
    if (mainScroll) { mainScroll.classList.remove("hidden"); mainScroll.style.display = "flex"; }
    setActiveNav("overviewBtn");
  });

  // Nav — Jobs
  document.getElementById("jobsBtn")?.addEventListener("click", () => {
    hideAllViews();
    showJobsView();
    setActiveNav("jobsBtn");
  });

  // Nav — Templates
  document.getElementById("templatesBtn")?.addEventListener("click", () => {
    hideAllViews();
    showTemplatesView();
    setActiveNav("templatesBtn");
  });

  // Nav — Approvals
  document.getElementById("approvalsBtn")?.addEventListener("click", () => {
    hideAllViews();
    showApprovalsView();
    setActiveNav("approvalsBtn");
  });

  // Nav — Hub
  document.getElementById("hubBtn")?.addEventListener("click", () => {
    hideAllViews();
    const hubView = document.getElementById("crossProjectHub");
    if (hubView) hubView.classList.remove("hidden");
    loadHub();
    setActiveNav("hubBtn");
  });

  // Nav — AI Profile
  document.getElementById("aiProfileBtn")?.addEventListener("click", () => {
    hideAllViews();
    const aiProfileView = document.getElementById("aiProfileView");
    if (aiProfileView) aiProfileView.classList.remove("hidden");
    loadProfile();
    setActiveNav("aiProfileBtn");
  });

  // Nav — Chat
  document.getElementById("chatBtn")?.addEventListener("click", () => {
    hideAllViews();
    const chat = document.getElementById("chatScreen");
    if (chat) { chat.classList.remove("hidden"); chat.style.display = "flex"; }
    setActiveNav("chatBtn");
  });

  // Nav — Learning Lab
  document.getElementById("learningBtn")?.addEventListener("click", () => {
    hideAllViews();
    const learningPage = document.getElementById("learningPage");
    if (learningPage) learningPage.classList.remove("hidden");
    loadLearningData();
    setActiveNav("learningBtn");
  });

  // Unified Main Input: Always use sendMessage which switches to Chat Mode
  sendBtn?.addEventListener("click", () => {
    sendMessage();
  });

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
  spBtn?.addEventListener("click", () => toggleSettingsModal(true));
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
  });

  // Doc upload
  const docUpload = $("#docUploadInput");
  docUpload?.addEventListener("change", () => {
    if (docUpload.files.length) uploadDocuments(Array.from(docUpload.files));
  });
  $("#uploadDocsBtn")?.addEventListener("click", () => docUpload?.click());

  // Memory
  $("#memoryToggleBtn")?.addEventListener("click", toggleMemoryList);

  // Obsidian Specific Hooks
  $("#editorToggle")?.addEventListener("click", () => {
    hideSwarmDashboard();
    toggleEditorPanel();
  });

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
