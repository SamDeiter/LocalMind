import { API, priorityInput, welcomeScreen, chatScreen } from "../state.js";
import { showToast } from "../utils.js";
import { sendMessage } from "../chat.js";
import { pollAutonomy } from "./status.js";

export async function toggleAutonomyMode(mode) {
  try {
    const r = await fetch(`${API}/api/autonomy/mode?mode=${mode}`, { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      showToast(`🛡️ Mode: ${mode}`, "info");
      pollAutonomy();
    }
  } catch {
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

export function executeDirective() {
  if (!priorityInput) return;
  const text = priorityInput.value.trim();
  if (!text) return;

  if (welcomeScreen) welcomeScreen.style.display = "none";
  if (chatScreen) chatScreen.style.display = "flex";

  sendMessage();
}
