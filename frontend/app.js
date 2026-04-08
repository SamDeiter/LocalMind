/**
 * LocalMind v3 — Entry Point
 * Thin boot file: imports modules and calls init.
 */

import { checkHealth, loadModels } from "./modules/chat.js";
import { loadConversations } from "./modules/conversations.js";
import { populateVoices, initSpeechRecognition } from "./modules/media.js";
import {
  startHwPolling,
  loadMemories,
  loadDocuments,
  loadVersion,
  loadProposals,
  connectActivityFeed,
  initDashboardPanels,
} from "./modules/sidebar.js";
import { toggleEditorPanel, initEditorEnhancements } from "./modules/editor.js";
import { bindEvents } from "./modules/events.js";
import { initResearchPanel, initGlobalSearch } from "./modules/research_ui.js";
import { initSettingsUI } from "./modules/settings_ui.js";
import { initDashboard } from "./modules/dashboard.js";
import { initLiveReload } from "./modules/live_reload.js";
import { initSwarmUI } from "./modules/swarm_ui.js";
import { initJobsUI } from "./modules/jobs_ui.js";
import { initTemplatesUI } from "./modules/templates_ui.js";
import { initApprovalsUI } from "./modules/approvals_ui.js";
import { initTaskCreation } from "./modules/task_creation.js";
import { initOnboarding } from "./modules/onboarding.js";

async function init() {
  checkHealth();
  loadModels();
  loadConversations();
  setTimeout(loadDocuments, 500);
  loadMemories();
  populateVoices();
  initSpeechRecognition();
  startHwPolling();
  bindEvents();
  loadVersion();
  loadProposals();
  connectActivityFeed();
  initEditorEnhancements();
  initDashboardPanels();
  initResearchPanel();
  initGlobalSearch();
  initSettingsUI();
  initDashboard();
  initLiveReload();
  initSwarmUI();
  initJobsUI();
  initTemplatesUI();
  initApprovalsUI();
  initTaskCreation();
  initOnboarding();

  // Restore editor panel if it was open

  if (localStorage.getItem("localmind_editor") === "on") {
    setTimeout(toggleEditorPanel, 500);
  }

  // Register Service Worker for PWA/Cache
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
