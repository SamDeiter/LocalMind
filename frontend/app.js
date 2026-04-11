/**
 * LocalMind v4 — Entry Point
 * Nav rail + sidebar + main workspace shell
 */

import { checkHealth, loadModels } from "./modules/chat.js";
import { loadConversations } from "./modules/conversations.js";
import { populateVoices, initSpeechRecognition } from "./modules/media.js";
import {
  startHwPolling,
  loadMemories,
  loadDocuments,
  loadVersion,
} from "./modules/sidebar.js";
import { initEditorEnhancements } from "./modules/editor.js";
import { bindEvents } from "./modules/events.js";
import { initNavRail } from "./modules/nav_rail.js";
import { initSettingsUI } from "./modules/settings_ui.js";
import { initDashboard } from "./modules/dashboard.js";
import { initLiveReload } from "./modules/live_reload.js";
import { initSwarmUI } from "./modules/swarm_ui.js";
import { initJobsUI } from "./modules/jobs_ui.js";
import { initTemplatesUI } from "./modules/templates_ui.js";
import { initApprovalsUI } from "./modules/approvals_ui.js";
import { initTaskCreation } from "./modules/task_creation.js";
import { initGeneratedTools } from "./modules/tools_generated.js";
import { initPWA } from "./modules/pwa.js";
import { initMonitoring } from "./modules/monitoring_ui.js";
import { initEvalUI } from "./modules/eval_ui.js";
import { initTTS } from "./modules/tts.js";
import { initTokenPanel } from "./modules/token_panel.js";
import { initLearningUI } from "./modules/learning_ui.js";
import { initAIProfile } from "./modules/ai_profile.js";
import { initOnboarding } from "./modules/onboarding.js";
import { initPlaceholderRotation } from "./modules/chat_ux.js";

async function init() {
  // ── Phase 1: Parallel network fetches + sync DOM setup ──────────
  // Fire off all independent network requests concurrently instead of
  // waiting for each to complete sequentially (~60% faster startup).
  const networkFetches = [
    checkHealth(),
    loadModels(),
    loadConversations(),
    loadDocuments(),
    loadMemories(),
    loadVersion(),
  ];

  // Sync DOM init (no network) — runs while fetches are in flight
  populateVoices();
  initSpeechRecognition();
  bindEvents();
  initNavRail();
  initEditorEnhancements();
  initSettingsUI();
  initPlaceholderRotation();

  // ── Phase 2: Feature modules (sync, DOM-only) ──────────────────
  initDashboard();
  initLiveReload();
  initSwarmUI();
  initJobsUI();
  initTemplatesUI();
  initApprovalsUI();
  initTaskCreation();
  initGeneratedTools();
  initPWA();
  initMonitoring();
  initTTS();
  initTokenPanel();
  initLearningUI();
  initAIProfile();
  initOnboarding();
  startHwPolling();

  // Wait for all network fetches to settle (don't block on failures)
  await Promise.allSettled(networkFetches);

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
