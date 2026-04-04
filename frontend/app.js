/**
 * LocalMind v3 — Entry Point
 * Thin boot file: imports modules and calls init.
 */

import { initErrorBoundary } from "./modules/errors.js";
import { checkHealth, loadModels } from "./modules/chat.js";
import { loadConversations } from "./modules/conversations.js";
import { populateVoices, initSpeechRecognition } from "./modules/media.js";
import { startHwPolling, loadMemories, loadDocuments, loadVersion, loadProposals, connectActivityFeed, initDashboardPanels } from "./modules/sidebar.js";
import { toggleEditorPanel, initEditorEnhancements } from "./modules/editor.js";
import { bindEvents } from "./modules/events.js";
import { initResearchPanel, initGlobalSearch } from "./modules/research_ui.js";
import { initSettingsUI } from "./modules/settings_ui.js";
import { initDashboard } from "./modules/dashboard.js";
import { initLiveReload } from "./modules/live_reload.js";
import { initSwarmUI } from "./modules/swarm_ui.js";
import { onStatus as onWsStatus, getConnectionStatus, getTransport } from "./modules/ws_client.js";

async function init() {
  initErrorBoundary();

  const safeInit = (fn, name) => {
    try { fn(); } catch (e) { console.error(`${name} failed:`, e); }
  };

  // Core init — must succeed for basic functionality
  try {
    await checkHealth();
  } catch (e) { console.error("Health check failed:", e); }

  loadModels();
  loadConversations();
  bindEvents();

  // Sidebar data — load in background, failures are non-fatal
  setTimeout(loadDocuments, 500);
  safeInit(loadMemories, "Load memories");
  safeInit(loadVersion, "Load version");
  safeInit(loadProposals, "Load proposals");
  safeInit(startHwPolling, "HW polling");
  safeInit(connectActivityFeed, "Activity feed");

  // Media — can fail gracefully (e.g. no mic/speaker)
  safeInit(populateVoices, "Populate voices");
  safeInit(initSpeechRecognition, "Speech recognition");

  // Enhancement UIs — wrap each so one failure doesn't block the rest
  safeInit(initEditorEnhancements, "Editor enhancements");
  safeInit(initDashboardPanels, "Dashboard panels");
  safeInit(initResearchPanel, "Research panel");
  safeInit(initGlobalSearch, "Global search");
  safeInit(initSettingsUI, "Settings UI");
  safeInit(initDashboard, "Dashboard");
  safeInit(initLiveReload, "Live reload");
  safeInit(initSwarmUI, "Swarm UI");

  // Log ws_client transport for diagnostics
  onWsStatus((status, transport) => {
    if (status === "connected") {
      console.info(`Activity feed connected via ${transport}`);
    }
  });

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
