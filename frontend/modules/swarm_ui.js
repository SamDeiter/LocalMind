/**
 * swarm_ui.js — Hive Mind Dashboard UI Module
 * Real-time visualization of the multi-agent swarm.
 * Polls /api/swarm/status every 3s when visible.
 */

const API_BASE = window.location.origin;
let swarmPollInterval = null;
let swarmVisible = false;

// ── DOM References ──────────────────────────────────────────────
const els = {
    dashBtn: () => document.getElementById('swarmDashBtn'),
    dashView: () => document.getElementById('swarmDashboardView'),
    mainScroll: () => document.getElementById('mainScrollArea'),
    statusBadge: () => document.getElementById('swarmStatusBadge'),
    activeCount: () => document.getElementById('swarmActiveCount'),
    gpuUsed: () => document.getElementById('swarmGpuUsed'),
    gpuTotal: () => document.getElementById('swarmGpuTotal'),
    queueDepth: () => document.getElementById('swarmQueueDepth'),
    successRate: () => document.getElementById('swarmSuccessRate'),
    agentGrid: () => document.getElementById('swarmAgentGrid'),
    resultStream: () => document.getElementById('swarmResultStream'),
    scanBtn: () => document.getElementById('swarmScanBtn'),
    agentCountBadge: () => document.getElementById('swarmAgentCount'),
    // Heartbeat row
    uptime: () => document.getElementById('swarmUptime'),
    totalTasks: () => document.getElementById('swarmTotalTasks'),
    totalFailed: () => document.getElementById('swarmTotalFailed'),
    lastPoll: () => document.getElementById('swarmLastPoll'),
    heartbeat: () => document.getElementById('swarmHeartbeat'),
};

// ── Agent Type Icons & Colors ───────────────────────────────────
const AGENT_STYLES = {
    scanner: { icon: 'search', color: 'cyan', label: 'Scanner' },
    tester:  { icon: 'science', color: 'emerald', label: 'Tester' },
    researcher: { icon: 'travel_explore', color: 'blue', label: 'Researcher' },
    llm:     { icon: 'psychology', color: 'violet', label: 'LLM' },
    base:    { icon: 'smart_toy', color: 'slate', label: 'Agent' },
};

// ── Toggle Dashboard ────────────────────────────────────────────
export function initSwarmUI() {
    const btn = els.dashBtn();
    if (!btn) return;

    btn.addEventListener('click', toggleSwarmDashboard);

    // Scan button
    const scanBtn = els.scanBtn();
    if (scanBtn) {
        scanBtn.addEventListener('click', triggerFullScan);
    }

    // When other sidebar buttons are clicked, close swarm panel
    const otherNavBtns = ['overviewBtn', 'editorToggle', 'activityToggle', 'memoryToggleBtn'];
    otherNavBtns.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', hideSwarmDashboard);
    });

    // Start background agent count polling (even when hidden)
    setInterval(updateAgentCountBadge, 10000);
    updateAgentCountBadge();
}

/** Close the swarm dashboard and restore the main view. */
export function hideSwarmDashboard() {
    if (!swarmVisible) return;
    const view = els.dashView();
    const main = els.mainScroll();
    swarmVisible = false;
    if (view) view.classList.add('hidden');
    if (main) main.classList.remove('hidden');
    stopPolling();
}

function toggleSwarmDashboard() {
    const view = els.dashView();
    const main = els.mainScroll();
    if (!view) return;

    swarmVisible = !swarmVisible;

    if (swarmVisible) {
        // Also hide the editor panel if it's open
        const editorPanel = document.getElementById('editorPanelContainer');
        if (editorPanel && !editorPanel.classList.contains('hidden')) {
            editorPanel.classList.add('hidden');
        }

        view.classList.remove('hidden');
        if (main) main.classList.add('hidden');
        startPolling();
        fetchSwarmStatus(); // Immediate first load
    } else {
        view.classList.add('hidden');
        if (main) main.classList.remove('hidden');
        stopPolling();
    }
}

// ── Polling ─────────────────────────────────────────────────────
function startPolling() {
    stopPolling();
    swarmPollInterval = setInterval(fetchSwarmStatus, 3000);
}

function stopPolling() {
    if (swarmPollInterval) {
        clearInterval(swarmPollInterval);
        swarmPollInterval = null;
    }
}

// ── Fetch & Render ──────────────────────────────────────────────
async function fetchSwarmStatus() {
    try {
        const resp = await fetch(`${API_BASE}/api/swarm/status`);
        if (!resp.ok) return;
        const data = await resp.json();
        renderSwarmStatus(data);
    } catch (err) {
        console.debug('Swarm status fetch failed:', err);
    }
}

function renderSwarmStatus(data) {
    // Flash heartbeat dot to prove live update
    const hb = els.heartbeat();
    if (hb) {
        hb.classList.remove('bg-emerald-400', 'bg-slate-600');
        hb.classList.add(data.running ? 'bg-emerald-400' : 'bg-slate-600');
    }

    // Status badge
    const badge = els.statusBadge();
    if (badge) {
        if (data.running) {
            badge.textContent = 'ACTIVE';
            badge.className = badge.className.replace(/bg-\w+-\d+\/\d+/g, '').replace(/text-\w+-\d+/g, '');
            badge.classList.add('bg-emerald-500/20', 'text-emerald-400');
        } else {
            badge.textContent = 'OFFLINE';
            badge.classList.add('bg-red-500/20', 'text-red-400');
        }
    }

    // Heartbeat row
    const uptime = data.uptime || 0;
    const mins = Math.floor(uptime / 60);
    const secs = uptime % 60;
    setTextSafe(els.uptime(), mins > 0 ? `${mins}m ${secs}s` : `${secs}s`);
    setTextSafe(els.totalTasks(), data.metrics?.tasks_processed ?? 0);
    setTextSafe(els.totalFailed(), data.metrics?.tasks_failed ?? 0);
    setTextSafe(els.lastPoll(), new Date().toLocaleTimeString());

    // Metrics
    const agents = data.agents || {};
    const metrics = data.metrics || {};
    const queue = data.queue || {};

    setTextSafe(els.activeCount(), data.metrics?.tasks_processed ?? 0);
    setTextSafe(els.gpuUsed(), agents.by_type?.gpu?.active || 0);
    setTextSafe(els.gpuTotal(), agents.by_type?.gpu?.total || 3);
    setTextSafe(els.queueDepth(), queue.total_queued || 0);
    setTextSafe(els.successRate(), metrics.success_rate !== null && metrics.success_rate !== undefined ? `${metrics.success_rate}%` : '--');

    // Agent grid — pass data directly, no second fetch
    renderAgentGrid(data.agent_details || []);

    // Recent results
    renderResultStream(data.recent_results || []);
}

function renderAgentGrid(agents) {
    const grid = els.agentGrid();
    if (!grid) return;

    if (!agents || agents.length === 0) {
        grid.innerHTML = `<div class="col-span-3 text-xs text-slate-500 italic p-4">No agents registered yet</div>`;
        return;
    }

    grid.innerHTML = agents.map(agent => {
        const style = AGENT_STYLES[agent.agent_type] || AGENT_STYLES.base;
        const isActive = agent.is_running;
        const totalDone = agent.tasks_completed + agent.tasks_failed;

        return `
            <div class="bg-slate-900/40 border ${
                isActive ? `border-cyan-500/40` : (totalDone > 0 ? 'border-slate-700/50' : 'border-slate-800/30')
            } rounded-lg p-3 transition-all ${
                isActive ? `shadow-[0_0_10px_-4px] shadow-cyan-500/30` : ''
            }">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-2">
                        <span class="material-symbols-outlined text-${style.color}-400 text-sm">${style.icon}</span>
                        <span class="text-[10px] font-bold uppercase tracking-wider text-slate-300">${style.label}</span>
                    </div>
                    <span class="w-2 h-2 rounded-full ${
                        isActive ? `bg-${style.color}-400 animate-pulse` : (totalDone > 0 ? 'bg-slate-500' : 'bg-slate-700')
                    }"></span>
                </div>
                <div class="text-[9px] font-mono text-slate-500 truncate mb-2">${agent.agent_id}</div>
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2 text-[9px]">
                        <span class="text-emerald-400 font-mono font-bold">${agent.tasks_completed} ✓</span>
                        ${agent.tasks_failed > 0 ? `<span class="text-red-400 font-mono">${agent.tasks_failed} ✗</span>` : ''}
                    </div>
                    ${agent.current_task ? `<span class="text-[8px] text-amber-400 truncate max-w-[80px]">▶ ${agent.current_task}</span>` : ''}
                </div>
            </div>
        `;
    }).join('');
}



function renderResultStream(results) {
    const stream = els.resultStream();
    if (!stream) return;

    if (results.length === 0) {
        stream.innerHTML = `<div class="text-xs text-slate-500 italic p-4">Waiting for tasks...</div>`;
        return;
    }

    stream.innerHTML = results.reverse().map(r => {

        return `
            <div class="flex items-center gap-3 px-3 py-2 rounded-lg ${r.success ? 'bg-emerald-500/5' : 'bg-red-500/5'} border border-slate-800/30">
                <span class="${r.success ? 'text-emerald-400' : 'text-red-400'} text-xs">${r.success ? '✅' : '❌'}</span>
                <span class="text-[10px] font-mono text-slate-400 w-24 truncate">${r.type}</span>
                <span class="text-[10px] text-slate-500 w-16">${r.duration}s</span>
                <span class="text-[10px] font-mono text-slate-600 truncate flex-1">${r.agent || '--'}</span>
                ${r.error ? `<span class="text-[9px] text-red-400/80 truncate max-w-[200px]">${r.error}</span>` : ''}
            </div>
        `;
    }).join('');
}

// ── Actions ─────────────────────────────────────────────────────
async function triggerFullScan() {
    const btn = els.scanBtn();
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Scanning...';
    }
    try {
        const resp = await fetch(`${API_BASE}/api/swarm/scan`, { method: 'POST' });
        const data = await resp.json();
        console.log('Scan submitted:', data);
    } catch (err) {
        console.error('Scan trigger failed:', err);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1">radar</span> Full Scan';
        }
    }
}

// ── Sidebar Badge ───────────────────────────────────────────────
async function updateAgentCountBadge() {
    try {
        const resp = await fetch(`${API_BASE}/api/swarm/status`);
        if (!resp.ok) return;
        const data = await resp.json();
        const badge = els.agentCountBadge();
        if (badge) {
            const active = data.agents?.active || 0;
            badge.textContent = active;
            badge.classList.toggle('hidden', active === 0);
        }
    } catch {
        // Silent fail for background polling
    }
}

// ── Helpers ─────────────────────────────────────────────────────
function setTextSafe(el, val) {
    if (el) el.textContent = val;
}
