/**
 * LocalMind v3 — Entry Point
 * Thin boot file: imports modules and calls init.
 */

import { checkHealth, loadModels } from "./modules/chat.js";
import { loadConversations } from "./modules/conversations.js";
import { populateVoices, initSpeechRecognition } from "./modules/media.js";
import { startHwPolling, loadMemories, loadDocuments, loadVersion, loadProposals, connectActivityFeed, initDashboardPanels } from "./modules/sidebar.js";
import { toggleEditorPanel, initEditorEnhancements } from "./modules/editor.js";
import { bindEvents } from "./modules/events.js";
import { initResearchPanel, initGlobalSearch } from "./modules/research_ui.js";

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

  // Restore editor panel if it was open

  if (localStorage.getItem("localmind_editor") === "on") {
    setTimeout(toggleEditorPanel, 500);
  }
}

init();
