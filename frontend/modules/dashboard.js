/**
 * dashboard.js — Live data feed for the LocalMind dashboard
 * Connects neural topology nodes + metrics bars + status bar
 * to /api/hardware and /api/autonomy/status endpoints.
 */

const DASH_API = window.location.origin;
let dashboardInterval = null;

/** Update neural topology SVG nodes and metric cards with live hardware data */
async function updateDashboardMetrics() {
  try {
    const r = await fetch(`${DASH_API}/api/hardware`);
    if (!r.ok) return;
    const hw = await r.json();
    const sys = hw.system || {};
    const models = hw.models || [];

    // ── CPU ──
    const cpuPct = sys.cpu_percent ?? 0;
    const cpuEl = document.getElementById("metricCpu");
    const cpuBar = document.getElementById("metricCpuBar");
    const nodeCpuVal = document.getElementById("nodeCpuVal");
    if (cpuEl) cpuEl.textContent = `${cpuPct}%`;
    if (cpuBar) cpuBar.style.width = `${Math.min(cpuPct, 100)}%`;
    if (nodeCpuVal) nodeCpuVal.textContent = `${cpuPct}%`;

    // Color CPU node based on load
    const nodeCpu = document.getElementById("nodeCpu");
    if (nodeCpu) {
      if (cpuPct > 80) nodeCpu.setAttribute("fill", "#ff6b98");
      else if (cpuPct > 50) nodeCpu.setAttribute("fill", "#f59e0b");
      else nodeCpu.setAttribute("fill", "#00eefc");
    }

    // ── RAM ──
    const ramUsed = sys.ram_used_gb ?? 0;
    const ramTotal = sys.ram_total_gb ?? 0;
    const ramPct = ramTotal > 0 ? (ramUsed / ramTotal * 100) : 0;
    const ramEl = document.getElementById("metricRam");
    const ramBar = document.getElementById("metricRamBar");
    const nodeRamVal = document.getElementById("nodeRamVal");
    if (ramEl) ramEl.textContent = `${ramUsed.toFixed(1)} / ${ramTotal.toFixed(0)}GB`;
    if (ramBar) ramBar.style.width = `${ramPct}%`;
    if (nodeRamVal) nodeRamVal.textContent = `${ramUsed.toFixed(1)}GB`;

    // ── VRAM (from loaded models) ──
    let vramUsed = 0;
    let vramTotal = 0;
    let modelName = "No model";
    if (models.length > 0) {
      vramUsed = models.reduce((sum, m) => sum + (m.vram_gb || 0), 0);
      vramTotal = vramUsed; // Ollama doesn't report total VRAM, just what's allocated
      modelName = models[0].name || "Unknown";
    }
    const vramEl = document.getElementById("metricVram");
    const vramBar = document.getElementById("metricVramBar");
    const nodeGpuVal = document.getElementById("nodeGpuVal");
    // Use a rough estimate: most GPUs are 4-24GB, show percentage of a reasonable max
    const estimatedVramMax = 24; // Will show proportional bar
    const vramPct = estimatedVramMax > 0 ? (vramUsed / estimatedVramMax * 100) : 0;
    if (vramEl) vramEl.textContent = vramUsed > 0 ? `${vramUsed.toFixed(1)}GB` : "--";
    if (vramBar) vramBar.style.width = `${Math.min(vramPct, 100)}%`;
    if (nodeGpuVal) nodeGpuVal.textContent = vramUsed > 0 ? `${vramUsed.toFixed(1)}GB` : "--";

    // Color GPU node
    const nodeGpu = document.getElementById("nodeGpu");
    if (nodeGpu) {
      if (vramPct > 80) nodeGpu.setAttribute("fill", "#ff6b98");
      else if (vramPct > 50) nodeGpu.setAttribute("fill", "#f59e0b");
      else nodeGpu.setAttribute("fill", "#00eefc");
    }

    // Update status bar model
    const statusModel = document.getElementById("statusModel");
    if (statusModel) statusModel.textContent = modelName;

  } catch {
    // Silently fail — hardware API may not be available
  }
}

/** Update status bar with connection + engine info */
async function updateStatusBar() {
  try {
    const dot = document.getElementById("statusDot");
    const connEl = document.getElementById("statusConnection");
    const engineEl = document.getElementById("statusEngine");
    const versionEl = document.getElementById("statusVersion");

    // Check autonomy status
    const r = await fetch(`${DASH_API}/api/autonomy/status`);
    if (r.ok) {
      const s = await r.json();
      if (dot) dot.classList.add("connected");
      if (connEl) connEl.textContent = "Connected";
      if (engineEl) engineEl.textContent = `Engine: ${s.status || "Idle"}`;

      // Update memory count in neural topology
      const nodeMemVal = document.getElementById("nodeMemVal");
      if (nodeMemVal && s.memory_count !== undefined) {
        nodeMemVal.textContent = s.memory_count;
      }
    } else {
      if (dot) dot.classList.remove("connected");
      if (connEl) connEl.textContent = "Disconnected";
    }

    // Version
    try {
      const vr = await fetch(`${DASH_API}/api/version`);
      if (vr.ok) {
        const vd = await vr.json();
        if (versionEl) versionEl.textContent = `v${vd.version || "0.0.0"}`;
      }
    } catch { /* ignore */ }

    // Memory count for neural node
    try {
      const mr = await fetch(`${DASH_API}/api/memories`);
      if (mr.ok) {
        const memories = await mr.json();
        const nodeMemVal = document.getElementById("nodeMemVal");
        if (nodeMemVal) nodeMemVal.textContent = Array.isArray(memories) ? memories.length : "0";
      }
    } catch { /* ignore */ }

  } catch {
    const dot = document.getElementById("statusDot");
    const connEl = document.getElementById("statusConnection");
    if (dot) dot.classList.remove("connected");
    if (connEl) connEl.textContent = "Offline";
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

// Auto-start when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initDashboard);
} else {
  initDashboard();
}

window.dashboardModule = { initDashboard, stopDashboard, updateDashboardMetrics, updateStatusBar };
