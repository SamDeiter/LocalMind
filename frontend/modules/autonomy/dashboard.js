/**
 * modules/autonomy/dashboard.js — Brain dashboard rendering.
 */

import { brainState } from "./constants.js";

export function updateBrainUptime() {
  const el = document.getElementById("brainUptime");
  if (!el || brainState.bootTime === null) return;
  const diffMs = Date.now() - brainState.bootTime;
  const hours = diffMs / 3600000;
  el.textContent = `${hours.toFixed(1)}h`;
}

export function updateSuccessRate() {
  const el = document.getElementById("brainSuccessRate");
  if (!el) return;
  if (brainState.proposalCount === 0) {
    el.textContent = "100%";
    return;
  }
  const pct = Math.round((brainState.executedCount / brainState.proposalCount) * 100);
  el.textContent = `${pct}%`;
}
