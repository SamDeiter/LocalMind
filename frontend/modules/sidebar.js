/**
 * sidebar.js — Hardware polling for the utility footer + version badge.
 *
 * The v1 sidebar's memory viewer and document uploader lived here too; in
 * v2 the Knowledge tab owns both surfaces (see knowledge_ui.js), and the
 * old DOM containers were removed from the shell. Only the always-on
 * footer poll and the version badge remain.
 */

import { API } from "./state.js";

let hwInterval = null;

function setText(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v;
}

export async function pollHardware() {
  try {
    const r = await fetch(`${API}/api/hardware`);
    if (!r.ok) throw new Error("HTTP " + r.status);
    const h = await r.json();
    const sys = h.system || {};

    const cpu = sys.cpu_percent ?? h.cpu;
    const ram = sys.ram_percent ?? h.ram ?? h.memory;
    const gpu = sys.gpu_percent ?? h.gpu;

    setText("utilCpu", cpu != null ? `${Math.round(cpu)}%` : "—");
    setText("utilRam", ram != null ? `${Math.round(ram)}%` : "—");
    setText("utilGpu", gpu != null ? `${Math.round(gpu)}%` : "—");

    const dot = document.getElementById("utilOnlineDot");
    if (dot) dot.style.background = "var(--lm-status-ok)";
    setText("utilOnline", "online");
  } catch (_) {
    const dot = document.getElementById("utilOnlineDot");
    if (dot) dot.style.background = "var(--lm-status-failed)";
    setText("utilOnline", "offline");
  }

  try {
    const r = await fetch(`${API}/api/health`);
    if (r.ok) {
      const h = await r.json();
      setText("utilQueue",   String(h.queued_jobs ?? h.queue_depth ?? 0));
      setText("utilWorkers", String(h.active_jobs ?? "0"));
    }
  } catch (_) { /* silent */ }
}

export function startHwPolling() {
  if (hwInterval) return;
  pollHardware();
  hwInterval = setInterval(() => {
    if (!document.hidden) pollHardware();
  }, 3000);
}

export async function loadVersion() {
  try {
    const r = await fetch(`${API}/api/version`);
    if (!r.ok) return;
    const d = await r.json();
    const badge = document.querySelector(".version-badge");
    if (badge && d.version) {
      badge.textContent = `v${d.version} #${d.build || "?"}`;
      badge.title = `LocalMind v${d.version} build #${d.build} — ${d.codename || ""}`;
    }
  } catch (_) { /* badge stays at placeholder */ }
}
