/**
 * Legacy autonomy dashboard rendering stub for compatibility.
 */

import { brainState } from "./constants.js";

export function updateBrainUptime() {
  const element = document.getElementById("brainUptime");
  if (!element || brainState.bootTime === null) return;
  const elapsedHours = (Date.now() - brainState.bootTime) / 3600000;
  element.textContent = elapsedHours.toFixed(1) + "h";
}

export function updateSuccessRate() {
  const element = document.getElementById("brainSuccessRate");
  if (!element) return;
  if (brainState.proposalCount === 0) {
    element.textContent = "100%";
    return;
  }
  const rate = Math.round((brainState.executedCount / brainState.proposalCount) * 100);
  element.textContent = rate + "%";
}
