import { brainState } from "./constants.js";
export async function pollAutonomy() {
  try {
    const res = await fetch("/api/autonomy/status");
    const data = await res.json();
    brainState.proposalCount = data.reflection?.proposals_logged ?? brainState.proposalCount;
    brainState.executedCount = data.execution?.proposals_executed ?? brainState.executedCount;
    if (data.start_time) brainState.bootTime = data.start_time * 1000;
    const ind = document.getElementById("autonomyIndicator");
    if (ind) ind.className = data.enabled ? "autonomy-active" : "autonomy-paused";
    ["memoryCount", "docCount", "proposalCount"].forEach((id) => {
      const el = document.getElementById(id);
      const val =
        data[
          id === "memoryCount"
            ? "memories_count"
            : id === "docCount"
              ? "documents_count"
              : "proposals_count"
        ];
      if (el && val !== undefined) el.textContent = String(val);
    });
    if (data.recent_events?.length > 0) brainState.caughtUp = true;
  } catch (err) {
    console.warn("Poll autonomy failed", err);
  }
}
