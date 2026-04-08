import { API } from './state.js';

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
 * robustly hide worker pool and show main dashboard
 * called by sidebar buttons to ensure we don't end up on a blank screen
 */
export function hideSwarmDashboard() {
    const view = els.dashView();
    const main = els.mainScroll();
    
    swarmVisible = false;
    
    // Always hide Worker Pool
    if (view) view.classList.add('hidden');

    // Always show Main Dashboard
    if (main) {
        main.classList.remove('hidden');
        main.style.display = 'flex'; // Ensure flex layout is restored
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
        // Show Worker Pool, Hide Main Dashboard
        view.classList.remove('hidden');
        if (main) main.classList.add('hidden');
        
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
    btn.addEventListener('click', (e) => {
        // If clicking on text or icon, ensure the button handles it
        toggleSwarmDashboard();
    });

    // Other nav buttons should clear the Swarm dashboard to prevent "blank screen" overlaps
    const otherNavBtns = ['overviewBtn', 'editorToggle', 'activityToggle', 'memoryToggleBtn', 'newChatBtn', 'jobsBtn', 'templatesBtn', 'approvalsBtn'];
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

function renderSwarmStatus(data) {
    // 1. Metrics
    const status = data.status || {};
    const metrics = data.metrics || {};
    const agents = Array.isArray(data.agents) ? data.agents : [];
    
    if (els.agentCount()) els.agentCount().textContent = agents.length;
    if (els.uptimeVal()) els.uptimeVal().textContent = formatUptime(status.uptime_seconds || 0);
    if (els.tasksVal()) els.tasksVal().textContent = metrics.total_tasks_completed || 0;
    if (els.failedVal()) els.failedVal().textContent = metrics.total_tasks_failed || 0;
    if (els.activeVal()) els.activeVal().textContent = metrics.total_tasks_completed || 0; // matching "Tasks Done"
    
    if (els.gpuUsed()) els.gpuUsed().textContent = agents.filter(a => a.status === 'working').length;
    if (els.gpuTotal()) els.gpuTotal().textContent = agents.length;
    
    if (els.queueDepth()) els.queueDepth().textContent = metrics.queue_depth || 0;
    if (els.queuePeak()) els.queuePeak().textContent = metrics.peak_queue_depth || 0;
    
    if (els.successRate()) {
        const total = metrics.total_tasks_completed + metrics.total_tasks_failed;
        const rate = total > 0 ? (metrics.total_tasks_completed / total * 100).toFixed(1) + '%' : '--';
        els.successRate().textContent = rate;
    }

    if (els.statusBadge()) {
        const badge = els.statusBadge();
        badge.textContent = status.state || 'Active';
        badge.className = 'text-[9px] font-bold uppercase tracking-widest bg-emerald-500/20 text-emerald-400 px-2.5 py-1 rounded-full';
    }

    if (els.pollTime()) {
        els.pollTime().textContent = new Date().toLocaleTimeString();
    }

    // 2. Agents Grid
    const grid = els.agentGrid();
    if (grid) {
        grid.innerHTML = agents.map(agent => `
            <div class="bg-slate-900/40 border border-slate-800/40 rounded-xl p-4 transition-all hover:bg-slate-800/60 group">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center gap-2">
                        <div class="w-2 h-2 rounded-full ${agent.status === 'working' ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'}"></div>
                        <span class="text-xs font-bold text-slate-200">Worker_${agent.id.slice(0,4)}</span>
                    </div>
                    <span class="text-[9px] font-mono text-slate-500">${agent.type || 'GPT_4o'}</span>
                </div>
                <div class="space-y-1">
                    <div class="text-[10px] text-slate-400 truncate">${agent.current_task || 'Awaiting task queue...'}</div>
                    <div class="w-full bg-slate-800/50 h-1 rounded-full overflow-hidden">
                        <div class="bg-cyan-500 h-full transition-all duration-1000" style="width: ${agent.status === 'working' ? '70%' : '0%'}"></div>
                    </div>
                </div>
            </div>
        `).join('');
    }

    // 3. Result Stream
    const results = els.resultsStream();
    if (results) {
        if (!data.recent_results || data.recent_results.length === 0) {
            results.innerHTML = '<div class="text-xs text-slate-500 italic p-4">Waiting for tasks...</div>';
        } else {
            results.innerHTML = data.recent_results.map(res => `
                <div class="flex items-center gap-3 p-3 border-b border-white/5 last:border-0 hover:bg-white/5 transition-colors">
                    <span class="material-symbols-outlined text-sm ${res.success ? 'text-emerald-400' : 'text-red-400'}">
                        ${res.success ? 'check_circle' : 'error'}
                    </span>
                    <div class="flex-1 min-w-0">
                        <div class="text-[11px] text-slate-200 truncate">${res.task_id}</div>
                        <div class="text-[9px] text-slate-500 font-mono">${res.duration.toFixed(2)}s - ${res.agent_id}</div>
                    </div>
                </div>
            `).join('');
        }
    }

    // 4. Improvements Stream
    const improvements = els.improvementsStream();
    if (improvements) {
        if (!data.recent_improvements || data.recent_improvements.length === 0) {
            improvements.innerHTML = '<div class="text-xs text-slate-500 italic p-4">No verified improvements merged yet...</div>';
        } else {
            improvements.innerHTML = data.recent_improvements.map(imp => `
                <div class="p-3 bg-cyan-500/5 border border-cyan-500/10 rounded-lg group">
                    <div class="flex items-center justify-between mb-1">
                        <span class="text-[10px] font-bold text-cyan-400 uppercase tracking-widest">${imp.type || 'REFACTOR'}</span>
                        <span class="text-[9px] font-mono text-slate-500">${imp.timestamp || ''}</span>
                    </div>
                    <div class="text-xs text-slate-300 leading-relaxed">${imp.description || imp.title}</div>
                    <div class="mt-2 text-[9px] font-mono text-cyan-500/60 truncate">${imp.files?.join(', ') || ''}</div>
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
