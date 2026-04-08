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
  priorityInput,
  addPriorityBtn,
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
import { submitQuickTask } from "./task_creation.js";
import { chatScreen, overviewBtn } from "./state.js";

/** Hide all views and restore the default main scroll area. */
function hideAllViews() {
  hideSwarmDashboard();
  const views = [
    "mainScrollArea", "jobsView", "templatesView", "approvalsView",
    "chatScreen", "crossProjectHub", "learningPage", "aiProfileView",
  ];
  views.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.add("hidden");
    el.classList.remove("view-enter");
    el.style.display = "none";
  });
}

/** Show a view element with a transition animation. */
function showView(el, displayStyle = "") {
  if (!el) return;
  el.classList.remove("hidden");
  el.style.display = displayStyle || "";
  // Trigger reflow then animate
  void el.offsetWidth;
  el.classList.add("view-enter");
}

/** Reset active state on all nav buttons, then highlight the given one. */
function setActiveNav(activeId) {
  const navIds = ["overviewBtn", "jobsBtn", "templatesBtn", "approvalsBtn", "hubBtn", "aiProfileBtn", "chatBtn", "learningBtn", "swarmDashBtn"];
  navIds.forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn) return;
    if (id === activeId) {
      btn.classList.add("nav-active");
      btn.classList.remove("text-slate-400");
    } else {
      btn.classList.remove("nav-active");
      btn.classList.add("text-slate-400");
    }
  });
}

export function bindEvents() {
  // Sidebar
  sidebarToggle?.addEventListener("click", () => sidebar?.classList.toggle("collapsed"));

  // Mobile sidebar toggle
  const mobileMenuBtn = document.getElementById("mobileMenuBtn");
  const sidebarOverlay = document.getElementById("sidebarOverlay");
  const openMobileSidebar = () => {
    sidebar?.classList.remove("collapsed");
    sidebarOverlay?.classList.add("active");
  };
  const closeMobileSidebar = () => {
    sidebar?.classList.add("collapsed");
    sidebarOverlay?.classList.remove("active");
  };
  mobileMenuBtn?.addEventListener("click", () => {
    if (sidebar?.classList.contains("collapsed")) openMobileSidebar();
    else closeMobileSidebar();
  });
  sidebarOverlay?.addEventListener("click", closeMobileSidebar);

  // Sidebar quick-task Execute button
  addPriorityBtn?.addEventListener("click", () => {
    const text = priorityInput?.value?.trim() || "";
    if (!text) return;
    submitQuickTask(text);
    if (priorityInput) priorityInput.value = "";
  });

  // Nav — Dashboard
  overviewBtn?.addEventListener("click", () => {
    hideAllViews();
    showView(document.getElementById("mainScrollArea"), "flex");
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
    showView(document.getElementById("crossProjectHub"));
    loadHub();
    setActiveNav("hubBtn");
  });

  // Nav — AI Profile
  document.getElementById("aiProfileBtn")?.addEventListener("click", () => {
    hideAllViews();
    showView(document.getElementById("aiProfileView"));
    loadProfile();
    setActiveNav("aiProfileBtn");
  });

  // Nav — Chat
  document.getElementById("chatBtn")?.addEventListener("click", () => {
    hideAllViews();
    showView(document.getElementById("chatScreen"), "flex");
    setActiveNav("chatBtn");
  });

  // Nav — Learning Lab
  document.getElementById("learningBtn")?.addEventListener("click", () => {
    hideAllViews();
    showView(document.getElementById("learningPage"));
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
