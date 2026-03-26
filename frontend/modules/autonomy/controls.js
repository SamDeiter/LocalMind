import { API, priorityInput, welcomeScreen, chatScreen } from "../state.js";
import { showToast } from "../utils.js";
import { sendMessage } from "../chat.js";
import { pollAutonomy } from "./status.js";

export async function toggleAutonomyMode(mode) {
  try {
    const r = await fetch(`${API}/api/autonomy/mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const d = await r.json();
    if (d.ok) {
      showToast(`🛡️ Mode: ${mode}`, "info");
      pollAutonomy();
    } else {
      showToast(`❌ Mode switch failed: ${d.error || "unknown"}`, "error");
    }
  } catch (e) {
    console.error("Mode switch error:", e);
    showToast("❌ Mode switch failed", "error");
  }
}

export async function triggerReflection() {
  showToast("🔍 Thinking...", "info");
  try {
    await fetch(`${API}/api/autonomy/reflect`, { method: "POST" });
  } catch {
    showToast("❌ Reflection failed", "error");
  }
}

export async function triggerExecution() {
  showToast("⚡ Executing...", "info");
  try {
    await fetch(`${API}/api/autonomy/execute`, { method: "POST" });
  } catch {
    showToast("❌ Execution failed", "error");
  }
}

/** Execute a directive from the priority input */
export function executeDirective() {
  const input = priorityInput || document.getElementById("priorityDirectiveInput");
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;

  import("./priority.js").then(m => {
    if (m.addPriority) m.addPriority(text);
  });
  input.value = "";
}

/** Toggle the activity feed panel visibility */
export function toggleActivityFeed() {
  const feed = document.getElementById("autonomyActivityFeed") || document.getElementById("activityFeed");
  if (feed) feed.classList.toggle("open");
}

/** Set autonomy mode (supervised/autonomous) — alias for toggleAutonomyMode */
export function setAutonomyMode(mode) {
  toggleAutonomyMode(mode);
}
