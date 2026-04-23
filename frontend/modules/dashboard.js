import { API } from "./state.js";

let dashboardInterval = null;
let statusFailCount = 0;
const OFFLINE_THRESHOLD = 3; // consecutive failures before showing Offline

/** Update neural topology SVG nodes and metric cards with live hardware data
 * ⚡ Bolt: Consolidated polling. This now handles hardware, status, version, and memories
 * in a single request to /api/hardware, reducing network overhead by ~66%.
 */
async function updateDashboardMetrics() {
  const dot = document.getElementById("brainPulse");
  const connEl = document.getElementById("brainStatus");

  try {
    const r = await fetch(`${API}/api/hardware`);
    if (!r.ok) {
      statusFailCount++;
      handleStatusFailure(dot, connEl);
      return;
    }

    statusFailCount = 0;
    const hw = await r.json();
    const sys = hw.system || {};
    const models = hw.models || [];
    const vd = hw.version || {};

    // ── Status & Version ──
    if (dot) dot.style.backgroundColor = "#4fdbc8";
    if (connEl) connEl.textContent = "Online";
    const versionEl = document.querySelector(".version-badge");
    if (versionEl) versionEl.textContent = `v${vd.version || "0.0.0"}`;

    // ── CPU ──
    const cpuPct = sys.cpu_percent ?? 0;
    const cpuEl = document.getElementById("cpuVal");
    if (cpuEl) cpuEl.textContent = `${cpuPct}%`;

    // Update brain pulse color based on load (only if online)
    if (dot) {
      if (cpuPct > 80) dot.style.backgroundColor = "#ff6b98";
      else if (cpuPct > 50) dot.style.backgroundColor = "#f59e0b";
      else dot.style.backgroundColor = "#4fdbc8";
    }

    // ── RAM ──
    const ramUsed = sys.ram_used_gb ?? 0;
    const ramTotal = sys.ram_total_gb ?? 0;
    const ramEl = document.getElementById("ramVal");
    if (ramEl) ramEl.textContent = `${ramUsed.toFixed(1)} / ${ramTotal.toFixed(0)}GB`;

    // ── VRAM (from loaded models) ──
    let vramUsed = 0;
    let modelName = "No model";
    if (models.length > 0) {
      vramUsed = models.reduce((sum, m) => sum + (m.vram_gb || 0), 0);
      modelName = models[0].name || "Unknown";
    }
    const vramEl = document.getElementById("vramVal");
    if (vramEl) vramEl.textContent = vramUsed > 0 ? `${vramUsed.toFixed(1)}GB` : "N/A";

    // Update status badge with model
    const versionBadge = document.getElementById("versionBadge");
    if (versionBadge && modelName !== "No model") versionBadge.textContent = modelName;

    // ── Memories (Ideas) ──
    const ideasEl = document.getElementById("brainIdeas");
    if (ideasEl) ideasEl.textContent = hw.memory_count ?? "0";
  } catch {
    statusFailCount++;
    handleStatusFailure(dot, connEl);
  }
}

/** Helper to handle offline/degraded status UI */
function handleStatusFailure(dot, connEl) {
  if (statusFailCount >= OFFLINE_THRESHOLD) {
    if (dot) dot.style.backgroundColor = "#ff6b98";
    if (connEl) connEl.textContent = statusFailCount > OFFLINE_THRESHOLD + 2 ? "Offline" : "Degraded";
  }
}

/** Update status bar with connection info
 * @deprecated ⚡ Bolt: Consolidated into updateDashboardMetrics
 */
async function updateStatusBar() {
  // Logic moved to updateDashboardMetrics to save requests
}

/** Initialize dashboard data feeds */
function initDashboard() {
  updateDashboardMetrics();
  dashboardInterval = setInterval(updateDashboardMetrics, 3000);
}

/** Stop dashboard updates */
function stopDashboard() {
  if (dashboardInterval) {
    clearInterval(dashboardInterval);
    dashboardInterval = null;
  }
}

export { initDashboard, stopDashboard, updateDashboardMetrics, updateStatusBar };
