import { API } from './state.js';

let swarmPollingInterval = null;
let swarmVisible = false;

// Helpers to get fresh DOM refs
const els = {
    dashBtn: () => document.getElementById('swarmDashBtn'),
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
            badge.className = 'text-[11px] font-bold uppercase tracking-widest bg-red-500/20 text-red-400 px-2.5 py-1 rounded-full';
        }
    }
}

export function startPolling() {
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
    // In the 3-tab shell, visibility is managed by the System tab accordion.
    // We only stop polling here; the accordion header handles show/hide.
    swarmVisible = false;
    stopPolling();

    // Legacy: update sidebar button state if it exists
    const btn = els.dashBtn();
    if (btn) {
        btn.classList.remove('bg-amber-500/10', 'text-amber-400', 'border-amber-500/20');
        btn.classList.add('text-slate-400');
    }
}

export function toggleSwarmDashboard() {
    // In the 3-tab shell, the accordion in the System tab controls visibility.
    // This function is kept for backward compatibility but only manages polling.
    swarmVisible = !swarmVisible;

    if (swarmVisible) {
        startPolling();
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
    // Initialize swarm tab bar (safe to call whenever)
    initSwarmTabs();

    // Wire sidebar button if it exists (legacy / non-shell contexts)
    const btn = els.dashBtn();
    if (btn) {
        btn.addEventListener('click', () => toggleSwarmDashboard());
    }

    // Wire scan button if present
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

    // In the 3-tab shell, polling is started when the Worker Pool accordion
    // is expanded (wired in events.js). No auto-start needed here.
}

function renderSwarmStatus(data) {
    // 1. Metrics
    const status = data.status || {};
    const metrics = data.metrics || {};
    const agents = Array.isArray(data.agents) ? data.agents : [];
    
    if (els.agentCount()) {
        els.agentCount().textContent = agents.length;
        els.agentCount().setAttribute("aria-label", `${agents.length} active worker${agents.length !== 1 ? "s" : ""}`);
    }
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
        badge.className = 'text-[11px] font-bold uppercase tracking-widest bg-emerald-500/20 text-emerald-400 px-2.5 py-1 rounded-full';
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
                        <div class="w-2 h-2 rounded-full ${agent.status === 'working' ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'}" aria-hidden="true"></div>
                        <span class="sr-only">${agent.status === 'working' ? 'Active' : 'Idle'}</span>
                        <span class="text-xs font-bold text-slate-200">Worker_${agent.id.slice(0,4)}</span>
                    </div>
                    <span class="text-[11px] font-mono text-slate-500">${agent.type || 'GPT_4o'}</span>
                </div>
                <div class="space-y-1">
                    <div class="text-xs text-slate-400 truncate">${agent.current_task || 'Awaiting task queue...'}</div>
                    <div class="w-full bg-slate-800/50 h-1 rounded-full overflow-hidden">
                        <div class="bg-cyan-500 h-full transition-all duration-1000" style="width: ${agent.status === 'working' ? '70%' : '0%'}" role="progressbar" aria-valuenow="${agent.status === 'working' ? 70 : 0}" aria-valuemin="0" aria-valuemax="100" aria-label="Worker ${agent.id.slice(0,4)} progress"></div>
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
                    <span class="material-symbols-outlined text-sm ${res.success ? 'text-emerald-400' : 'text-red-400'}" aria-hidden="true">
                        ${res.success ? 'check_circle' : 'error'}
                    </span>
                    <span class="sr-only">${res.success ? 'Success' : 'Failed'}</span>
                    <div class="flex-1 min-w-0">
                        <div class="text-[11px] text-slate-200 truncate">${res.task_id}</div>
                        <div class="text-[11px] text-slate-500 font-mono">${res.duration.toFixed(2)}s - ${res.agent_id}</div>
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
                        <span class="text-xs font-bold text-cyan-400 uppercase tracking-widest">${imp.type || 'REFACTOR'}</span>
                        <span class="text-[11px] font-mono text-slate-500">${imp.timestamp || ''}</span>
                    </div>
                    <div class="text-xs text-slate-300 leading-relaxed">${imp.description || imp.title}</div>
                    <div class="mt-2 text-[11px] font-mono text-cyan-500/60 truncate">${imp.files?.join(', ') || ''}</div>
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

// ---------------------------------------------------------------------------
// Multi-Agent Swarm Visualization
// ---------------------------------------------------------------------------

const SWARM_TABS = [
    { id: 'agents',   label: 'Agents'   },
    { id: 'tree',     label: 'Tree'     },
    { id: 'memory',   label: 'Memory'   },
    { id: 'messages', label: 'Messages' },
    { id: 'locks',    label: 'Locks'    }
];

// Panels keyed by tab id.  "agents" maps to the existing content above the
// tab bar (metrics + grid + results + improvements) which we simply leave
// visible / hidden as a group.
const PANEL_IDS = {
    agents:   null,           // special - controls existing content
    tree:     'swarmTreeView',
    memory:   'swarmMemoryView',
    messages: 'swarmMessageLog',
    locks:    'swarmLockStatus'
};

let activeSwarmTab = 'agents';

/**
 * Build the tab-bar buttons inside #swarmTabBar and wire click handlers.
 * Safe to call multiple times - it will only render once.
 */
export function initSwarmTabs() {
    const bar = document.getElementById('swarmTabBar');
    if (!bar || bar.children.length > 0) return;

    SWARM_TABS.forEach(tab => {
        const btn = document.createElement('button');
        btn.dataset.swarmTab = tab.id;
        btn.textContent = tab.label;
        btn.className = tabClass(tab.id === activeSwarmTab);
        btn.addEventListener('click', () => switchSwarmTab(tab.id));
        bar.appendChild(btn);
    });
}

function tabClass(active) {
    const base = 'px-4 py-1.5 rounded-lg text-xs font-bold uppercase tracking-widest transition-colors';
    return active
        ? `${base} bg-amber-500/20 text-amber-400 border border-amber-500/30`
        : `${base} text-slate-500 hover:text-slate-300 hover:bg-slate-800/40 border border-transparent`;
}

function switchSwarmTab(tabId) {
    activeSwarmTab = tabId;

    // Update button styles
    const bar = document.getElementById('swarmTabBar');
    if (bar) {
        [...bar.children].forEach(btn => {
            btn.className = tabClass(btn.dataset.swarmTab === tabId);
        });
    }

    // Existing "agents" content: metrics row, agent grid, results/improvements
    const agentSections = ['swarmMetrics', 'swarmAgentGrid', 'swarmResultStream', 'swarmImprovementsStream'];
    agentSections.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            // Walk up to the nearest direct child of the accordion body so we
            // toggle the whole visual section, not just the inner container.
            const accordionBody = document.getElementById('accordionWorkerPoolBody');
            const section = (accordionBody && el.closest('#accordionWorkerPoolBody > div, #accordionWorkerPoolBody > section'))
                || el.parentElement;
            if (section) section.classList.toggle('hidden', tabId !== 'agents');
        }
    });

    // New panels
    Object.entries(PANEL_IDS).forEach(([key, panelId]) => {
        if (!panelId) return;
        const panel = document.getElementById(panelId);
        if (panel) panel.classList.toggle('hidden', key !== tabId);
    });

    // Lazy-load data for the selected tab
    if (tabId === 'tree')     loadTreeTab();
    if (tabId === 'memory')   loadMemoryTab();
    if (tabId === 'messages') loadMessagesTab();
    if (tabId === 'locks')    loadLocksTab();
}

// ---------------------------------------------------------------------------
// 1. Tree Visualization
// ---------------------------------------------------------------------------

let _selectedTreeJobId = null;

async function loadTreeTab() {
    const container = document.getElementById('swarmTreeView');
    if (!container) return;
    if (!_selectedTreeJobId) {
        container.innerHTML = `
            <div class="mb-4 flex items-center gap-2">
                <input id="swarmTreeJobInput" type="text" placeholder="Enter Job ID..."
                    class="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-amber-500/50 w-64" />
                <button id="swarmTreeLoadBtn"
                    class="px-4 py-1.5 rounded-lg text-xs font-bold uppercase tracking-widest bg-amber-500/20 text-amber-400 border border-amber-500/30 hover:bg-amber-500/30 transition-colors">
                    Load Tree
                </button>
            </div>
            <div id="swarmTreeContent" class="text-xs text-slate-500 italic">Enter a job ID to view its delegation tree.</div>`;
        document.getElementById('swarmTreeLoadBtn')?.addEventListener('click', () => {
            const val = document.getElementById('swarmTreeJobInput')?.value?.trim();
            if (val) { _selectedTreeJobId = val; loadTreeTab(); }
        });
        return;
    }
    container.innerHTML = '<div class="text-xs text-slate-400 animate-pulse">Loading tree...</div>';
    try {
        const res = await fetch(`${API}/api/swarm/tree/${_selectedTreeJobId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const treeData = await res.json();
        container.innerHTML = '';
        const header = document.createElement('div');
        header.className = 'flex items-center gap-2 mb-4';
        header.innerHTML = `
            <button id="swarmTreeBack" class="text-slate-400 hover:text-white text-xs underline">&#8592; Change Job</button>
            <span class="text-xs text-slate-500 font-mono">Job: ${_selectedTreeJobId}</span>`;
        container.appendChild(header);
        document.getElementById('swarmTreeBack')?.addEventListener('click', () => { _selectedTreeJobId = null; loadTreeTab(); });
        const treeEl = document.createElement('div');
        treeEl.id = 'swarmTreeContent';
        container.appendChild(treeEl);
        renderJobTree(treeData, treeEl);
    } catch (err) {
        container.innerHTML = `<div class="text-xs text-red-400">Failed to load tree: ${err.message}</div>`;
    }
}

/**
 * Render a collapsible job delegation tree.
 * @param {Object} treeData  - { title, status, delegation_type, children?: [...] }
 * @param {HTMLElement} container
 */
export function renderJobTree(treeData, container) {
    if (!treeData || !container) return;
    const ul = document.createElement('ul');
    ul.className = 'space-y-1 pl-4 border-l border-slate-700/50';
    appendTreeNode(ul, treeData);
    container.innerHTML = '';
    container.appendChild(ul);
}

function statusBadge(status) {
    const map = {
        completed: 'bg-emerald-500/20 text-emerald-400',
        running:   'bg-blue-500/20 text-blue-400',
        failed:    'bg-red-500/20 text-red-400',
        cancelled: 'bg-slate-600/20 text-slate-400'
    };
    const cls = map[(status || '').toLowerCase()] || 'bg-slate-600/20 text-slate-400';
    return `<span class="text-[11px] font-bold uppercase tracking-widest ${cls} px-2 py-0.5 rounded-full">${status || 'unknown'}</span>`;
}

function appendTreeNode(ul, node) {
    const li = document.createElement('li');
    li.className = 'py-1';

    const hasChildren = Array.isArray(node.children) && node.children.length > 0;
    const toggle = hasChildren
        ? `<button class="swarm-tree-toggle text-slate-500 hover:text-slate-300 mr-1 text-xs select-none" aria-expanded="true">&#9660;</button>`
        : `<span class="inline-block w-4 mr-1"></span>`;

    li.innerHTML = `
        <div class="flex items-center gap-2 group">
            ${toggle}
            <span class="text-xs text-slate-200 font-semibold">${node.title || 'Untitled'}</span>
            ${statusBadge(node.status)}
            ${node.delegation_type ? `<span class="text-[11px] text-slate-500 font-mono">${node.delegation_type}</span>` : ''}
        </div>`;

    if (hasChildren) {
        const childUl = document.createElement('ul');
        childUl.className = 'space-y-1 pl-4 border-l border-slate-700/50 mt-1';
        node.children.forEach(child => appendTreeNode(childUl, child));
        li.appendChild(childUl);

        const toggleBtn = li.querySelector('.swarm-tree-toggle');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                const expanded = childUl.style.display !== 'none';
                childUl.style.display = expanded ? 'none' : '';
                toggleBtn.innerHTML = expanded ? '&#9654;' : '&#9660;';
                toggleBtn.setAttribute('aria-expanded', String(!expanded));
            });
        }
    }
    ul.appendChild(li);
}

// ---------------------------------------------------------------------------
// 2. Shared Memory Viewer
// ---------------------------------------------------------------------------

let _selectedMemoryJobId = null;

async function loadMemoryTab() {
    const container = document.getElementById('swarmMemoryView');
    if (!container) return;
    if (!_selectedMemoryJobId) {
        container.innerHTML = `
            <div class="mb-4 flex items-center gap-2">
                <input id="swarmMemJobInput" type="text" placeholder="Enter Job ID..."
                    class="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-amber-500/50 w-64" />
                <button id="swarmMemLoadBtn"
                    class="px-4 py-1.5 rounded-lg text-xs font-bold uppercase tracking-widest bg-amber-500/20 text-amber-400 border border-amber-500/30 hover:bg-amber-500/30 transition-colors">
                    Load Memory
                </button>
            </div>
            <div class="text-xs text-slate-500 italic">Enter a job ID to view shared memory.</div>`;
        document.getElementById('swarmMemLoadBtn')?.addEventListener('click', () => {
            const val = document.getElementById('swarmMemJobInput')?.value?.trim();
            if (val) { _selectedMemoryJobId = val; loadMemoryTab(); }
        });
        return;
    }
    container.innerHTML = '<div class="text-xs text-slate-400 animate-pulse">Loading memory...</div>';
    try {
        const res = await fetch(`${API}/api/swarm/memory/${_selectedMemoryJobId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        container.innerHTML = '';
        const header = document.createElement('div');
        header.className = 'flex items-center gap-2 mb-4';
        header.innerHTML = `
            <button id="swarmMemBack" class="text-slate-400 hover:text-white text-xs underline">&#8592; Change Job</button>
            <span class="text-xs text-slate-500 font-mono">Job: ${_selectedMemoryJobId}</span>`;
        container.appendChild(header);
        document.getElementById('swarmMemBack')?.addEventListener('click', () => { _selectedMemoryJobId = null; loadMemoryTab(); });
        const tableEl = document.createElement('div');
        container.appendChild(tableEl);
        renderSharedMemory(_selectedMemoryJobId, tableEl, data);
    } catch (err) {
        container.innerHTML = `<div class="text-xs text-red-400">Failed to load memory: ${err.message}</div>`;
    }
}

/**
 * Render shared memory key-value table.
 * @param {string} jobId
 * @param {HTMLElement} container
 * @param {Object} [prefetchedData] - optional pre-fetched data to avoid a duplicate request
 */
export async function renderSharedMemory(jobId, container, prefetchedData) {
    if (!container) return;
    let entries;
    if (prefetchedData) {
        entries = Array.isArray(prefetchedData) ? prefetchedData : (prefetchedData.entries || []);
    } else {
        try {
            const res = await fetch(`${API}/api/swarm/memory/${jobId}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            entries = Array.isArray(data) ? data : (data.entries || []);
        } catch (err) {
            container.innerHTML = `<div class="text-xs text-red-400">Error: ${err.message}</div>`;
            return;
        }
    }

    if (entries.length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-500 italic">No shared memory entries.</div>';
        return;
    }

    const truncate = (v, max = 100) => {
        const s = typeof v === 'string' ? v : JSON.stringify(v);
        return s.length > max ? s.slice(0, max) + '...' : s;
    };

    container.innerHTML = `
        <div class="overflow-x-auto">
            <table class="w-full text-xs">
                <thead>
                    <tr class="text-left text-[11px] font-bold uppercase tracking-widest text-slate-500 border-b border-slate-700">
                        <th class="pb-2 pr-4">Key</th>
                        <th class="pb-2 pr-4">Value</th>
                        <th class="pb-2 pr-4">Version</th>
                        <th class="pb-2 pr-4">Written By</th>
                        <th class="pb-2">Updated At</th>
                    </tr>
                </thead>
                <tbody>
                    ${entries.map(e => `
                        <tr class="border-b border-slate-800/40 hover:bg-slate-800/30 transition-colors">
                            <td class="py-2 pr-4 text-slate-200 font-mono">${e.key || ''}</td>
                            <td class="py-2 pr-4 text-slate-400 font-mono max-w-xs truncate" title="${truncate(e.value, 500)}">${truncate(e.value)}</td>
                            <td class="py-2 pr-4 text-slate-500">${e.version ?? ''}</td>
                            <td class="py-2 pr-4 text-slate-400">${e.written_by || ''}</td>
                            <td class="py-2 text-slate-500 font-mono">${e.updated_at || ''}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>`;
}

// ---------------------------------------------------------------------------
// 3. Message Log
// ---------------------------------------------------------------------------

let _selectedMsgJobId = null;

async function loadMessagesTab() {
    const container = document.getElementById('swarmMessageLog');
    if (!container) return;
    if (!_selectedMsgJobId) {
        container.innerHTML = `
            <div class="mb-4 flex items-center gap-2">
                <input id="swarmMsgJobInput" type="text" placeholder="Enter Job ID..."
                    class="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-amber-500/50 w-64" />
                <button id="swarmMsgLoadBtn"
                    class="px-4 py-1.5 rounded-lg text-xs font-bold uppercase tracking-widest bg-amber-500/20 text-amber-400 border border-amber-500/30 hover:bg-amber-500/30 transition-colors">
                    Load Messages
                </button>
            </div>
            <div class="text-xs text-slate-500 italic">Enter a job ID to view agent messages.</div>`;
        document.getElementById('swarmMsgLoadBtn')?.addEventListener('click', () => {
            const val = document.getElementById('swarmMsgJobInput')?.value?.trim();
            if (val) { _selectedMsgJobId = val; loadMessagesTab(); }
        });
        return;
    }
    container.innerHTML = '<div class="text-xs text-slate-400 animate-pulse">Loading messages...</div>';
    try {
        const res = await fetch(`${API}/api/swarm/messages/${_selectedMsgJobId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        container.innerHTML = '';
        const header = document.createElement('div');
        header.className = 'flex items-center gap-2 mb-4';
        header.innerHTML = `
            <button id="swarmMsgBack" class="text-slate-400 hover:text-white text-xs underline">&#8592; Change Job</button>
            <span class="text-xs text-slate-500 font-mono">Job: ${_selectedMsgJobId}</span>`;
        container.appendChild(header);
        document.getElementById('swarmMsgBack')?.addEventListener('click', () => { _selectedMsgJobId = null; loadMessagesTab(); });
        const logEl = document.createElement('div');
        container.appendChild(logEl);
        renderMessageLog(_selectedMsgJobId, logEl, data);
    } catch (err) {
        container.innerHTML = `<div class="text-xs text-red-400">Failed to load messages: ${err.message}</div>`;
    }
}

/**
 * Render chronological agent message log.
 * @param {string} jobId
 * @param {HTMLElement} container
 * @param {Object} [prefetchedData]
 */
export async function renderMessageLog(jobId, container, prefetchedData) {
    if (!container) return;
    let messages;
    if (prefetchedData) {
        messages = Array.isArray(prefetchedData) ? prefetchedData : (prefetchedData.messages || []);
    } else {
        try {
            const res = await fetch(`${API}/api/swarm/messages/${jobId}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            messages = Array.isArray(data) ? data : (data.messages || []);
        } catch (err) {
            container.innerHTML = `<div class="text-xs text-red-400">Error: ${err.message}</div>`;
            return;
        }
    }

    if (messages.length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-500 italic">No messages recorded.</div>';
        return;
    }

    const typeBadge = (type) => {
        const colors = {
            delegate:  'bg-violet-500/20 text-violet-400',
            result:    'bg-emerald-500/20 text-emerald-400',
            error:     'bg-red-500/20 text-red-400',
            broadcast: 'bg-amber-500/20 text-amber-400',
            request:   'bg-blue-500/20 text-blue-400'
        };
        const cls = colors[(type || '').toLowerCase()] || 'bg-slate-600/20 text-slate-400';
        return `<span class="text-[11px] font-bold uppercase tracking-widest ${cls} px-2 py-0.5 rounded-full">${type || 'info'}</span>`;
    };

    container.innerHTML = `
        <div class="space-y-2 max-h-96 overflow-y-auto custom-scrollbar">
            ${messages.map(m => `
                <div class="flex items-start gap-3 p-3 bg-slate-900/40 border border-slate-800/40 rounded-lg hover:bg-slate-800/30 transition-colors">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-1">
                            <span class="text-xs font-bold text-slate-200">${m.from || 'unknown'}</span>
                            <span class="text-[11px] text-slate-600">&#8594;</span>
                            <span class="text-xs text-slate-400">${m.to || 'broadcast'}</span>
                            ${typeBadge(m.type)}
                        </div>
                        <div class="text-xs text-slate-300 truncate">${m.subject || ''}</div>
                    </div>
                    <span class="text-[11px] text-slate-600 font-mono whitespace-nowrap">${m.timestamp || ''}</span>
                </div>
            `).join('')}
        </div>`;
}

// ---------------------------------------------------------------------------
// 4. Lock Status
// ---------------------------------------------------------------------------

async function loadLocksTab() {
    const container = document.getElementById('swarmLockStatus');
    if (!container) return;
    container.innerHTML = '<div class="text-xs text-slate-400 animate-pulse">Loading locks...</div>';
    try {
        const res = await fetch(`${API}/api/swarm/locks`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderLockStatus(container, data);
    } catch (err) {
        container.innerHTML = `<div class="text-xs text-red-400">Failed to load locks: ${err.message}</div>`;
    }
}

/**
 * Render active resource locks table.
 * @param {HTMLElement} container
 * @param {Object} [prefetchedData]
 */
export async function renderLockStatus(container, prefetchedData) {
    if (!container) return;
    let locks;
    if (prefetchedData) {
        locks = Array.isArray(prefetchedData) ? prefetchedData : (prefetchedData.locks || []);
    } else {
        try {
            const res = await fetch(`${API}/api/swarm/locks`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            locks = Array.isArray(data) ? data : (data.locks || []);
        } catch (err) {
            container.innerHTML = `<div class="text-xs text-red-400">Error: ${err.message}</div>`;
            return;
        }
    }

    if (locks.length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-500 italic">No active locks.</div>';
        return;
    }

    container.innerHTML = `
        <div class="overflow-x-auto">
            <table class="w-full text-xs">
                <thead>
                    <tr class="text-left text-[11px] font-bold uppercase tracking-widest text-slate-500 border-b border-slate-700">
                        <th class="pb-2 pr-4">Resource</th>
                        <th class="pb-2 pr-4">Lock Type</th>
                        <th class="pb-2 pr-4">Held By</th>
                        <th class="pb-2 pr-4">Acquired</th>
                        <th class="pb-2">Expires</th>
                    </tr>
                </thead>
                <tbody>
                    ${locks.map(l => `
                        <tr class="border-b border-slate-800/40 hover:bg-slate-800/30 transition-colors">
                            <td class="py-2 pr-4 text-slate-200 font-mono">${l.resource || ''}</td>
                            <td class="py-2 pr-4">
                                <span class="text-[11px] font-bold uppercase tracking-widest ${l.lock_type === 'exclusive' ? 'bg-red-500/20 text-red-400' : 'bg-blue-500/20 text-blue-400'} px-2 py-0.5 rounded-full">
                                    ${l.lock_type || 'shared'}
                                </span>
                            </td>
                            <td class="py-2 pr-4 text-slate-400">${l.held_by || ''}</td>
                            <td class="py-2 pr-4 text-slate-500 font-mono">${l.acquired || ''}</td>
                            <td class="py-2 text-slate-500 font-mono">${l.expires || ''}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>`;
}
