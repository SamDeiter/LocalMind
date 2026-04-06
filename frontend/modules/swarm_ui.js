import { API } from './state.js';
import { toggleEditorPanel } from './editor.js';
import { getEditorPanel } from './state.js';
import { renderHardwareStats } from '../components/HardwareDashboard.js';
import { ActionGraph } from '../components/ActionGraph.js';

let actionGraph = null;

let swarmPollingInterval = null;
let swarmVisible = false;

// Helpers to get fresh DOM refs
const els = {
    dashBtn: () => document.getElementById('swarmDashBtn'),
    dashView: () => document.getElementById('swarmDashboardView'),
    mainScroll: () => document.getElementById('mainScrollArea'),
    agentGrid: () => document.getElementById('swarmAgentGrid'),
    resultsStream: () => document.getElementById('swarmResultStream'),
    improvementsStream: () => document.getElementById('swarmImprovementsStream'),
    agentCount: () => document.getElementById('swarmAgentCount'),
    statusBadge: () => document.getElementById('swarmStatusBadge'),
    uptimeVal: () => document.getElementById('swarmUptime'),
    tasksVal: () => document.getElementById('swarmTotalTasks'),
    failedVal: () => document.getElementById('swarmTotalFailed'),
    activeVal: () => document.getElementById('swarmActiveCount'),
    gpuUsed: () => document.getElementById('swarmGpuUsed'),
    gpuTotal: () => document.getElementById('swarmGpuTotal'),
    queueDepth: () => document.getElementById('swarmQueueDepth'),
    queuePeak: () => document.getElementById('swarmQueuePeak'),
    successRate: () => document.getElementById('swarmSuccessRate'),
    pollTime: () => document.getElementById('swarmLastPoll')
};


async function fetchHistory() {
    try {
        const res = await fetch(`${API}/api/swarm/history`);
        if (!res.ok) return;
        const data = await res.json();
        const history = data.history || [];
        console.log(`Swarm UI: Hydrating ${history.length} historical events...`);
        
        // Temporarily enable swarmVisible to allow ActionGraph to process
        const wasVisible = swarmVisible;
        swarmVisible = true; 
        
        history.forEach(event => {
            if (actionGraph) actionGraph.addEvent(event);
        });
        
        swarmVisible = wasVisible;
    } catch (err) {
        console.error('Swarm UI: History fetch failed:', err);
    }
}

async function fetchSwarmStatus() {
    try {
        const res = await fetch(`${API}/api/swarm/status`);
        if (!res.ok) throw new Error('Swarm API offline');
        const data = await res.json();
        renderSwarmStatus(data);
    } catch (err) {
        console.error('Swarm poll error:', err);
        const badge = els.statusBadge();
        if (badge) {
            badge.textContent = 'Disconnected';
            badge.className = 'text-[9px] font-bold uppercase tracking-widest bg-red-500/20 text-red-400 px-2.5 py-1 rounded-full';
        }
    }
}

function startPolling() {
    if (swarmPollingInterval) return;
    fetchSwarmStatus();
    swarmPollingInterval = setInterval(fetchSwarmStatus, 3000);
}

function stopPolling() {
    if (swarmPollingInterval) {
        clearInterval(swarmPollingInterval);
        swarmPollingInterval = null;
    }
}

/** 
 * robustly hide hive and show main nexus dashboard 
 * called by sidebar buttons to ensure we don't end up on a blank screen
 */
export function hideSwarmDashboard() {
    const view = els.dashView();
    const main = els.mainScroll();
    
    swarmVisible = false;
    
    // Always hide Hive
    if (view) view.classList.add('hidden');
    
    // Always show Nexus Dashboard (clear inline display to let Tailwind own it)
    if (main) {
        main.classList.remove('hidden');
        main.style.display = ''; // Clear any inline override, let CSS/Tailwind handle it
    }
    
    stopPolling();
    
    // Update sidebar button state
    const btn = els.dashBtn();
    if (btn) {
        btn.classList.remove('bg-amber-500/10', 'text-amber-400', 'border-amber-500/20');
        btn.classList.add('text-slate-400');
    }
}

export function toggleSwarmDashboard() {
    const view = els.dashView();
    const main = els.mainScroll();
    if (!view) return;

    swarmVisible = !swarmVisible;

    if (swarmVisible) {
        // Show hive, Hide Nexus
        view.classList.remove('hidden');
        if (main) {
            main.classList.add('hidden');
            main.style.display = ''; // Clear inline so 'hidden' class works
        }
        
        // Close editor panel if open, to prevent overlap
        const panel = getEditorPanel();
        if (panel && panel.classList.contains('visible')) {
            toggleEditorPanel();
        }
        
        startPolling();
        
        // Update sidebar button active state
        const btn = els.dashBtn();
        if (btn) {
            btn.classList.add('bg-amber-500/10', 'text-amber-400', 'border-amber-500/20');
            btn.classList.remove('text-slate-400');
        }
    } else {
        hideSwarmDashboard();
    }
}

export function initSwarmUI() {
    const btn = els.dashBtn();
    if (!btn) {
        console.warn('swarmDashBtn not found in DOM');
        return;
    }

    // Connect toggle
    btn.addEventListener('click', () => {
        toggleSwarmDashboard();
    });

    // Initialize Neural Action Graph
    if (!actionGraph) {
        try {
            actionGraph = new ActionGraph('actionGraphCanvas');
            // Hydrate from history immediately
            fetchHistory();
            const resetBtn = document.getElementById('resetGraphBtn');
            if (resetBtn) {
                resetBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (actionGraph) actionGraph.reset();
                });
            }
        } catch (e) {
            console.error('Swarm UI: ActionGraph failed to load:', e);
        }
    }

    // Other nav buttons should clear the Swarm dashboard to prevent "blank screen" overlaps
    // NOTE: editorToggle is handled in events.js to avoid triple-binding
    const otherNavBtns = ['overviewBtn', 'activityToggle', 'memoryToggleBtn', 'newChatBtn'];
    otherNavBtns.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('click', () => {
                hideSwarmDashboard();
            });
        }
    });

    // Scan button
    const scanBtn = document.getElementById('swarmScanBtn');
    if (scanBtn) {
        scanBtn.addEventListener('click', async () => {
            scanBtn.disabled = true;
            scanBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1 animate-spin">refresh</span> Scanning...';
            try {
                await fetch(`${API}/api/swarm/scan`, { method: 'POST' });
            } finally {
                setTimeout(() => {
                    scanBtn.disabled = false;
                    scanBtn.innerHTML = '<span class="material-symbols-outlined text-xs align-middle mr-1">radar</span> Full Scan';
                }, 2000);
            }
        });
    }
}

/**
 * Handle a real-time event from the SSE stream for the action graph
 */
export function handleSwarmEvent(event) {
    if (actionGraph && swarmVisible) {
        actionGraph.addEvent(event);
    }
}

function renderSwarmStatus(data) {
    if (!data) return;

    // 1. Core Metrics (Mapping from HiveCoordinator.get_status())
    const metrics = data.metrics || {};
    const queue = data.queue || {};
    // agent_details is a list of objects returned by BaseAgent.get_status()
    const agents = Array.isArray(data.agent_details) ? data.agent_details : [];
    
    // Header Metrics
    if (els.agentCount()) els.agentCount().textContent = agents.length;
    const workerTotal = document.getElementById('swarmWorkerTotal');
    if (workerTotal) workerTotal.textContent = `${agents.length} worker${agents.length !== 1 ? 's' : ''}`;
    if (els.uptimeVal()) els.uptimeVal().textContent = formatUptime(data.uptime || 0);
    if (els.tasksVal()) els.tasksVal().textContent = metrics.tasks_processed || 0;
    if (els.failedVal()) els.failedVal().textContent = metrics.tasks_failed || 0;
    if (els.activeVal()) els.activeVal().textContent = agents.filter(a => a.is_running).length;
    
    // Queue & GPU Slots
    if (els.gpuUsed()) {
        const gpuActive = agents.filter(a => a.agent_type === 'llm' && a.is_running).length;
        els.gpuUsed().textContent = gpuActive;
    }
    if (els.gpuTotal()) {
        els.gpuTotal().textContent = agents.filter(a => a.agent_type === 'llm').length || 0;
    }
    
    if (els.queueDepth()) els.queueDepth().textContent = queue.total_queued || 0;
    if (els.queuePeak()) els.queuePeak().textContent = queue.peak_depth || 0;
    
    if (els.successRate()) {
        const rate = metrics.success_rate;
        els.successRate().textContent = (rate !== undefined ? rate : '--') + (rate !== undefined ? '%' : '');
    }

    if (els.statusBadge()) {
        const badge = els.statusBadge();
        const state = data.running ? 'ACTIVE' : 'IDLE';
        badge.textContent = state;
        badge.className = `text-[9px] font-bold uppercase tracking-widest px-2.5 py-1 rounded-full ${data.running ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-500/20 text-slate-400'}`;
    }

    if (els.pollTime()) {
        els.pollTime().textContent = new Date().toLocaleTimeString();
    }

    // New: Render Hardware Stats
    const hwContainer = document.getElementById('hardwareStatsContainer');
    if (hwContainer) {
        hwContainer.innerHTML = renderHardwareStats(data);
    }

    // 2. Agents Grid (Worker List) — capped with scroll container
    const grid = els.agentGrid();
    if (grid) {
        // Apply scrollable max-height to the grid wrapper
        const gridWrapper = grid.parentElement;
        if (gridWrapper && !gridWrapper.dataset.scrollApplied) {
            gridWrapper.style.maxHeight = '320px';
            gridWrapper.style.overflowY = 'auto';
            gridWrapper.classList.add('custom-scrollbar');
            gridWrapper.dataset.scrollApplied = 'true';
        }
        if (agents.length === 0) {
            grid.innerHTML = '<div class="col-span-full text-xs text-slate-500 italic p-4 text-center">No hive workers connected...</div>';
        } else {
            grid.innerHTML = agents.map(agent => {
                const shortId = (agent.agent_id || 'err').slice(-4);
                const taskText = agent.current_task
                    ? (agent.current_task.length > 40 ? agent.current_task.slice(0, 37) + '...' : agent.current_task)
                    : 'Awaiting task queue...';
                return `
                <div class="bg-slate-900/40 border border-slate-800/40 rounded-xl p-3 transition-all hover:bg-slate-800/60 group" style="min-height:0">
                    <div class="flex items-center justify-between mb-2">
                        <div class="flex items-center gap-2 min-w-0">
                            <div class="w-2 h-2 rounded-full flex-shrink-0 ${agent.is_running ? 'bg-cyan-400 animate-pulse' : 'bg-slate-600'}"></div>
                            <span class="text-xs font-bold text-slate-200 truncate">Worker_${shortId}</span>
                        </div>
                        <span class="text-[9px] font-mono text-slate-500 flex-shrink-0 ml-2">${(agent.agent_type || 'CPU').toUpperCase()}</span>
                    </div>
                    <div class="space-y-1">
                        <div class="text-[10px] text-slate-400 truncate" title="${agent.current_task || ''}">${taskText}</div>
                        <div class="w-full bg-slate-800/50 h-1 rounded-full overflow-hidden">
                            <div class="bg-cyan-500 h-full transition-all duration-1000" style="width: ${agent.is_running ? '70%' : '0%'}"></div>
                        </div>
                    </div>
                </div>`;
            }).join('');
        }
    }

    // 3. Result Stream (Step Execution Log)
    const results = els.resultsStream();
    if (results) {
        const recentResults = data.recent_results || [];
        if (recentResults.length === 0) {
            results.innerHTML = '<div class="text-xs text-slate-500 italic p-4 text-center">Waiting for task results...</div>';
        } else {
            results.innerHTML = recentResults.map(res => `
                <div class="flex items-center gap-3 p-3 border-b border-white/5 last:border-0 hover:bg-white/5 transition-colors">
                    <span class="material-symbols-outlined text-sm ${res.success ? 'text-emerald-400' : 'text-red-400'}">
                        ${res.success ? 'check_circle' : 'error'}
                    </span>
                    <div class="flex-1 min-w-0">
                        <div class="text-[11px] text-slate-200 truncate font-mono">${res.task_id}</div>
                        <div class="text-[9px] text-slate-500 font-mono">${res.duration}s - ${res.agent || 'Hive'}</div>
                    </div>
                </div>
            `).join('');
        }
    }

    // 4. Improvements Stream (Brain Updates)
    const improvements = data.recent_improvements || [];
    const impStream = els.improvementsStream();
    if (impStream) {
        if (improvements.length === 0) {
            impStream.innerHTML = '<div class="text-xs text-slate-500 italic p-4 text-center">No brain optimizations verified yet...</div>';
        } else {
            impStream.innerHTML = improvements.map(imp => `
                <div class="p-3 bg-cyan-500/5 border border-cyan-500/10 rounded-lg group">
                    <div class="flex items-center justify-between mb-1">
                        <span class="text-[10px] font-bold text-cyan-400 uppercase tracking-widest">${imp.category || 'REFACTOR'}</span>
                        <span class="text-[9px] font-mono text-slate-500">${formatTimestamp(imp.completed_at || imp.timestamp)}</span>
                    </div>
                    <div class="text-xs text-slate-300 leading-relaxed">${imp.description || imp.title}</div>
                </div>
            `).join('');
        }
    }
}

function formatUptime(sec) {
    if (sec < 60) return `${sec}s`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
    return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

/** Safely parse timestamps — handles unix epoch (seconds), ms epoch, and ISO strings */
function formatTimestamp(ts) {
    if (!ts && ts !== 0) return '--';
    let d;
    if (typeof ts === 'number') {
        // If it looks like seconds (< year 2100 in seconds), multiply to ms
        d = ts < 1e12 ? new Date(ts * 1000) : new Date(ts);
    } else if (typeof ts === 'string') {
        d = new Date(ts);
    } else {
        return '--';
    }
    return isNaN(d.getTime()) ? '--' : d.toLocaleTimeString();
}
