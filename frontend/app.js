/**
 * LocalMind v3 — Entry Point
 * Thin boot file: imports modules and calls init.
 */

import { checkHealth, loadModels } from "./modules/chat.js";
import { loadConversations } from "./modules/conversations.js";
import { populateVoices, initSpeechRecognition } from "./modules/media.js";
import { startHwPolling, loadMemories, loadDocuments, loadVersion } from "./modules/sidebar.js";
import { toggleEditorPanel, initEditorEnhancements } from "./modules/editor.js";
import { bindEvents } from "./modules/events.js";

async function init() {
  checkHealth();
  loadModels();
  loadConversations();
  loadDocuments();
  loadMemories();
  populateVoices();
  initSpeechRecognition();
  startHwPolling();
  bindEvents();
  loadVersion();
  initEditorEnhancements();

  // Restore editor panel if it was open
  if (localStorage.getItem("localmind_editor") === "on") {
    setTimeout(toggleEditorPanel, 500);
  }
}

init();
