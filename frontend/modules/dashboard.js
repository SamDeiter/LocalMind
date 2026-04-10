import { API } from "./state.js";

let dashboardInterval = null;
let statusFailCount = 0;
const OFFLINE_THRESHOLD = 3; // consecutive failures before showing Offline

/** Update neural topology SVG nodes and metric cards with live hardware data */
async function updateDashboardMetrics() {
  const dot = document.getElementById("brainPulse");
  const connEl = document.getElementById("brainStatus");

  try {
    const r = await fetch(`${API}/api/hardware`);
    if (!r.ok) {
      handleFail();
      return;
    }

    statusFailCount = 0;
    if (connEl) connEl.textContent = "Online";

    const hw = await r.json();
    const sys = hw.system || {};
    const models = hw.models || [];

    // ── CPU ──
    const cpuPct = sys.cpu_percent ?? 0;
    const cpuEl = document.getElementById("cpuVal");
    if (cpuEl) cpuEl.textContent = `${cpuPct}%`;

    // Update brain pulse color based on load
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

    // ⚡ Bolt: Consolidate version and memory updates to save requests
    if (hw.version) {
      const vd = hw.version;
      const versionEl = document.querySelector(".version-badge");
      if (versionEl) versionEl.textContent = `v${vd.version || "0.0.0"}`;
    }
    if (hw.memory_count !== undefined) {
      const ideasEl = document.getElementById("brainIdeas");
      if (ideasEl) ideasEl.textContent = hw.memory_count;
    }
  } catch {
    handleFail();
  }

  function handleFail() {
    statusFailCount++;
    if (statusFailCount >= OFFLINE_THRESHOLD) {
      if (dot) dot.style.backgroundColor = "#ff6b98";
      if (connEl) connEl.textContent = "Offline";
    }
  }
}

/** Initialize dashboard data feeds */
function initDashboard() {
  // ⚡ Bolt: Consolidate polling into a single call every 3s.
  // updateDashboardMetrics now handles hardware, version, memory count, and connection status.
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

async function updateStatusBar() {
  // ⚡ Bolt: This is now handled within updateDashboardMetrics to avoid redundant calls.
}
