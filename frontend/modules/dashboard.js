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
  } catch {
    // Silently fail — hardware API may not be available
  }
}

/** Update status bar with connection + engine info */
async function updateStatusBar() {
  const dot = document.getElementById("brainPulse");
  const connEl = document.getElementById("brainStatus");
  try {
    // Check autonomy status
    const r = await fetch(`${API}/api/autonomy/status`);
    if (r.ok) {
      const s = await r.json();
      statusFailCount = 0; // Reset on success
      if (dot) dot.style.backgroundColor = "#4fdbc8";
      if (connEl) connEl.textContent = s.status ? `Active: ${s.status}` : "Online";
    } else {
      statusFailCount++;
      if (statusFailCount >= OFFLINE_THRESHOLD) {
        if (dot) dot.style.backgroundColor = "#ff6b98";
        if (connEl) connEl.textContent = "Degraded";
      }
    }

    // Version
    try {
      const vr = await fetch(`${API}/api/version`);
      if (vr.ok) {
        const vd = await vr.json();
        const versionEl = document.querySelector(".version-badge");
        if (versionEl) versionEl.textContent = `v${vd.version || "0.0.0"}`;
      }
    } catch {
      /* ignore */
    }

    // Ideas count for reasoning grid
    try {
      const mr = await fetch(`${API}/api/memories`);
      if (mr.ok) {
        const memories = await mr.json();
        const ideasEl = document.getElementById("brainIdeas");
        if (ideasEl) ideasEl.textContent = Array.isArray(memories) ? memories.length : "0";
      }
    } catch {
      /* ignore */
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
