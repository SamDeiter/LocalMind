/**
 * LocalMind v2 — Entry Point (mission control shell)
 * Six-tab IA: Home / Jobs / Artifacts / Knowledge / Operations / Settings.
 */

import { API } from "./modules/state.js";
import { checkHealth, loadModels } from "./modules/chat.js";
import { loadConversations } from "./modules/conversations.js";
import { populateVoices, initSpeechRecognition } from "./modules/media.js";
import {
  startHwPolling,
  loadVersion,
} from "./modules/sidebar.js";
import { initEditorEnhancements } from "./modules/editor.js";
import { bindEvents } from "./modules/events.js";
import { initNavRail } from "./modules/nav_rail.js";
import { initModeToggle } from "./modules/mode_toggle.js";

// v2 page modules
import { initHome } from "./modules/home.js";
import { initJobsUI } from "./modules/jobs_ui.js";
import { initJobDetail } from "./modules/job_detail.js";
import { initTaskCreation } from "./modules/task_creation.js";

// Phase-1 stub pages (lazy-inited by nav_rail.switchNav too; we keep a no-op import chain here to ensure the bundler can resolve them)
// Intentionally NOT calling their init on boot — they're lazy per-tab.

import { initLiveReload } from "./modules/live_reload.js";
import { initPWA } from "./modules/pwa.js";
import { initTTS } from "./modules/tts.js";
import { initPlaceholderRotation } from "./modules/chat_ux.js";
import { initMascot } from "./modules/mascot.js";

async function init() {
  // Parallel network fetches (no dependencies between them)
  const networkFetches = [
    checkHealth(),
    loadModels(),
    loadConversations(),
    loadVersion(),
  ];

  // Synchronous DOM wiring — runs while fetches are in flight
  populateVoices();
  initSpeechRecognition();
  bindEvents();
  initNavRail();
  initModeToggle();
  initEditorEnhancements();
  initPlaceholderRotation();

  // v2 feature modules — Home + Jobs + Job Detail overlay + the New Job drawer contents
  initHome();
  initJobsUI();
  initJobDetail();
  initTaskCreation();

  // Background services
  initLiveReload();
  initPWA();
  initTTS();
  startHwPolling();
  initMascot();

  await Promise.allSettled(networkFetches);

  // Topbar model indicator — uses the models list from checkHealth/loadModels
  refreshModelIndicator();

  // Service worker disabled in dev to prevent cache staleness.
  // Any pre-existing registration is actively killed by /sw.js (the kill switch).
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.getRegistrations().then((regs) => {
      for (const r of regs) r.unregister().catch(() => {});
    }).catch(() => {});
  }
}

async function refreshModelIndicator() {
  try {
    const r = await fetch(`${API}/api/models`);
    if (!r.ok) return;
    const data = await r.json();
    const list = Array.isArray(data) ? data : (data.models || []);
    const active = list.find((m) => m.loaded || m.active) || list[0];
    const name = active?.name || active?.id || "auto";
    const loc  = active?.location || (active?.provider ? active.provider : "local");
    const n = document.getElementById("modelIndicatorName");
    const l = document.getElementById("modelIndicatorLoc");
    if (n) n.textContent = name;
    if (l) l.textContent = loc;
  } catch (_) { /* offline */ }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
