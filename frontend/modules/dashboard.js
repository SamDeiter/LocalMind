import { API } from "./state.js";

let dashboardInterval = null;
let statusFailCount = 0;
const OFFLINE_THRESHOLD = 3; // consecutive failures before showing Offline

/** Update neural topology SVG nodes and metric cards with live hardware data */
async function updateDashboardMetrics() {
  try {
    const r = await fetch(`${API}/api/hardware`);
    if (!r.ok) return;
    const hw = await r.json();
    const sys = hw.system || {};
    const models = hw.models || [];

    // ⚡ Bolt: Use consolidated metrics from hardware response
    // to reduce redundant polling requests to /api/version and /api/memories.
    const version = hw.version || {};
    const memoryCount = hw.memory_count ?? 0;

    // ── CPU ──
    const cpuPct = sys.cpu_percent ?? 0;
    const cpuEl = document.getElementById("cpuVal");
    if (cpuEl) cpuEl.textContent = `${cpuPct}%`;

    // Update brain pulse color based on load
    const brainPulse = document.getElementById("brainPulse");
    if (brainPulse) {
      if (cpuPct > 80) brainPulse.style.backgroundColor = "#ff6b98";
      else if (cpuPct > 50) brainPulse.style.backgroundColor = "#f59e0b";
      else brainPulse.style.backgroundColor = "#4fdbc8";
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

    // ⚡ Bolt: Update status bar metrics using consolidated data
    const brainStatus = document.getElementById("brainStatus");
    const versionBadgeEl = document.querySelector(".version-badge");
    const brainIdeas = document.getElementById("brainIdeas");

    statusFailCount = 0;
    if (brainPulse) brainPulse.style.backgroundColor = "#4fdbc8";
    if (brainStatus) brainStatus.textContent = "Online";
    if (versionBadgeEl) versionBadgeEl.textContent = `v${version.version || "0.0.0"}`;
    if (brainIdeas) brainIdeas.textContent = memoryCount;

  } catch {
    // Silently fail — hardware API may not be available
  }
}

/** Update status bar with connection info */
async function updateStatusBar() {
  const dot = document.getElementById("brainPulse");
  const connEl = document.getElementById("brainStatus");
  try {
    // Check server health — we can still use /api/version as a minimal heartbeat
    const vr = await fetch(`${API}/api/version`);
    if (vr.ok) {
      statusFailCount = 0;
      if (dot) dot.style.backgroundColor = "#4fdbc8";
      if (connEl) connEl.textContent = "Online";
    } else {
      statusFailCount++;
      if (statusFailCount >= OFFLINE_THRESHOLD) {
        if (dot) dot.style.backgroundColor = "#ff6b98";
        if (connEl) connEl.textContent = "Degraded";
      }
    }

  } catch {
    statusFailCount++;
    if (statusFailCount >= OFFLINE_THRESHOLD) {
      const dot = document.getElementById("brainPulse");
      const connEl = document.getElementById("brainStatus");
      if (dot) dot.style.backgroundColor = "#ff6b98";
      if (connEl) connEl.textContent = "Offline";
    }
  }
}

/** Initialize dashboard data feeds */
function initDashboard() {
  updateDashboardMetrics();
  updateStatusBar();
  dashboardInterval = setInterval(() => {
    updateDashboardMetrics();
    updateStatusBar();
  }, 3000);
}

/** Stop dashboard updates */
function stopDashboard() {
  if (dashboardInterval) {
    clearInterval(dashboardInterval);
    dashboardInterval = null;
  }
}

export { initDashboard, stopDashboard, updateDashboardMetrics, updateStatusBar };
