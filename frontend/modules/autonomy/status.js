/**
 * modules/autonomy/status.js — Status polling logic.
 */

import { brainState } from "./constants.js";

export async function pollAutonomy() {
  try {
    const res = await fetch("/api/autonomy/status");
    const data = await res.json();

    // Update brainState from server response
    if (data.reflection) {
      brainState.proposalCount = data.reflection.proposals_logged || 0;
    }
    if (data.execution) {
      brainState.executedCount = data.execution.proposals_executed || 0;
    }
    if (data.start_time) {
      brainState.bootTime = data.start_time * 1000;
    }

    // Set indicator class
    const indicator = document.getElementById("autonomyIndicator");
    if (indicator) {
      if (data.enabled) {
        indicator.className = "autonomy-active";
      } else {
        indicator.className = "autonomy-paused";
      }
    }

    // Update sidebar counts
    const memoryCount = document.getElementById("memoryCount");
    if (memoryCount && data.memories_count !== undefined) {
      memoryCount.textContent = String(data.memories_count);
    }
    const docCount = document.getElementById("docCount");
    if (docCount && data.documents_count !== undefined) {
      docCount.textContent = String(data.documents_count);
    }
    const proposalCount = document.getElementById("proposalCount");
    if (proposalCount && data.proposals_count !== undefined) {
      proposalCount.textContent = String(data.proposals_count);
    }

    // Set caughtUp after processing recent events
    if (!brainState.caughtUp) {
      if (data.recent_events) {
        brainState.caughtUp = true;
      }
    }
  } catch (err) {
    // Handle gracefully as per tests
  }
}
