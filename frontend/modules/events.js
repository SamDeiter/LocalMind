/**
 * events.js — Global event bindings for LocalMind v2.
 *
 * v1's chat composer, mode pills, voice/camera buttons, and document
 * uploader lived here. The v2 IA dropped all those DOM nodes, so this
 * module is now intentionally tiny: it just wires the topbar quick-action
 * icons and a no-op switchTab shim for any legacy caller.
 */

import { showToast } from "./utils.js";

// Old 3-tab shell replaced by nav_rail.js — keep switchTab as a no-op
// so any straggling caller doesn't crash.
export function switchTab(_panelId) { /* no-op */ }

export function bindEvents() {
  // Topbar quick actions.
  document.getElementById("profileBtn")?.addEventListener("click", () => {
    import("./nav_rail.js").then((m) => m.switchNav?.("settings")).catch(() => {});
  });
  document.getElementById("helpBtn")?.addEventListener("click", () => {
    showToast(
      "Tips: ⌘K search · click any job card to open detail · right-click Pip for snooze.",
      "info",
    );
  });
  document.getElementById("notificationsBtn")?.addEventListener("click", () => {
    import("./nav_rail.js").then((m) => m.switchNav?.("ops")).catch(() => {});
  });
}
