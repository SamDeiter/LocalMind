/**
 * Autonomy Module Index
 * Consolidates all autonomy-related UI logic.
 */

export * from "./constants.js";
export * from "./status.js";
export * from "./activity.js";
export * from "./dashboard.js";
export * from "./proposals.js";
export * from "./controls.js";
export * from "./priority.js";
export * from "./digest.js";

import { pollAutonomy } from "./status.js";
import { connectActivityFeed } from "./activity.js";
import { loadPriorities } from "./priority.js";
import { loadDigest, exportDigest } from "./digest.js";
import { renderTaskPipeline } from "./proposals.js";
import { updateSuccessRate } from "./dashboard.js";
import { toggleAutonomyMode, triggerReflection, triggerExecution } from "./controls.js";
import { priorityInput } from "../state.js";

export function initAutonomyUI() {
  console.log("Initializing Autonomy UI...");

  // Wire mode switches
  const supBtn = document.getElementById("modeSupervisedBtn");
  const autoBtn = document.getElementById("modeAutonomousBtn");
  supBtn?.addEventListener("click", () => toggleAutonomyMode("supervised"));
  autoBtn?.addEventListener("click", () => toggleAutonomyMode("autonomous"));

  // Wire Tab Buttons if any
  const digestBtn = document.getElementById("tabDigestBtn");
  if (digestBtn) {
    digestBtn.addEventListener("click", loadDigest);
  }
  const exportBtn = document.getElementById("exportDigestBtn");
  if (exportBtn) {
    exportBtn.addEventListener("click", exportDigest);
  }

  // Wire Reflect + Execute buttons
  const reflectBtn = document.getElementById("brainReflectBtn");
  if (reflectBtn) {
    reflectBtn.addEventListener("click", triggerReflection);
  }
  const executeBtn = document.getElementById("brainExecuteBtn");
  if (executeBtn) {
    executeBtn.addEventListener("click", triggerExecution);
  }

  // Wire directive input ENTER key
  if (priorityInput) {
      priorityInput.addEventListener("keydown", (e) => {
          if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              import("./controls.js").then(m => m.executeDirective());
          }
      });
  }

  // Initial load
  loadPriorities();
  loadDigest();
  updateSuccessRate();
  renderTaskPipeline();
  connectActivityFeed();
  
  // Set up polling
  setInterval(pollAutonomy, 5000);
}
