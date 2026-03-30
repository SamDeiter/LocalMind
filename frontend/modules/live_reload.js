/**
 * live_reload.js — Dev helper
 * Automatically reloads the page when the backend restarts.
 */

import { API } from "./state.js";

let _isOffline = false;
let _checkInterval = null;

/** Start monitoring the backend for restarts */
export function initLiveReload() {
  // Only run on localhost
  if (!["localhost", "127.0.0.1"].includes(window.location.hostname)) {
    return;
  }

  console.log("🚀 Live-Reload monitoring active");

  _checkInterval = setInterval(async () => {
    try {
      const r = await fetch(`${API}/api/version`, { cache: "no-store" });
      if (r.ok) {
        if (_isOffline) {
          console.log("♻️ Backend back online — reloading...");
          window.location.reload();
        }
        _isOffline = false;
      } else {
        _isOffline = true;
      }
    } catch {
      // Backend is likely restarting
      _isOffline = true;
    }
  }, 2000);
}

export function stopLiveReload() {
  if (_checkInterval) clearInterval(_checkInterval);
}
