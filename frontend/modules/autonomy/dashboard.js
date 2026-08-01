import { brainState } from "./constants.js";
export function updateBrainUptime() {
  const el = document.getElementById("brainUptime");
  if (el && brainState.bootTime)
    el.textContent = `${((Date.now() - brainState.bootTime) / 3600000).toFixed(1)}h`;
}
export function updateSuccessRate() {
  const el = document.getElementById("brainSuccessRate");
  if (el)
    el.textContent = brainState.proposalCount
      ? `${Math.round((brainState.executedCount / brainState.proposalCount) * 100)}%`
      : "100%";
}
