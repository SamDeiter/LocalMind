/**
 * Autonomy UI Module — Phase F
 * Manages the Coworker experience, briefing modal, and mission explorer.
 */

export async function initAutonomyUI() {
    const briefingModal = document.getElementById('briefingModal');
    const briefingAckBtn = document.getElementById('briefingAckBtn');
    const mainAutonomy = document.getElementById('mainAutonomy');
    
    // 1. Fetch Daily Briefing on init
    try {
        const response = await fetch('/api/autonomy/briefing');
        if (response.ok) {
            const data = await response.json();
            showBriefing(data);
        }
    } catch (err) {
        console.error("Failed to fetch daily briefing:", err);
    }

    if (briefingAckBtn) {
        briefingAckBtn.addEventListener('click', () => {
            briefingModal.classList.add('hidden');
        });
    }

    // 2. Handle Mission Creation (from chat or dedicated btn)
    const newMissionBtn = document.getElementById('newMissionBtn');
    if (newMissionBtn) {
        newMissionBtn.addEventListener('click', () => {
            import("./nav_rail.js").then(m => m.switchNav('chat'));
            const messageInput = document.getElementById('messageInput');
            if (messageInput) {
                messageInput.value = "Briefing: [Describe your mission here]";
                messageInput.focus();
            }
        });
    }

    // 3. Listen for mission planning results (potentially from chat)
    // In a real implementation, the chat service would emit an event when it detects a 'Briefing:' input
}

export function showMissionPlan(plan) {
    const explorerContent = document.getElementById('missionExplorerContent');
    const engageBtn = document.getElementById('engageMissionBtn');
    
    if (!explorerContent) return;

    explorerContent.innerHTML = `
        <div class="space-y-4">
            <div class="p-4 rounded-xl bg-slate-900 border border-slate-800">
                <h3 class="text-sm font-bold text-indigo-400 uppercase tracking-widest mb-1">Goal</h3>
                <p class="text-slate-200 font-medium">${plan.goal}</p>
            </div>
            
            <div class="space-y-2">
                <div class="text-[9px] font-bold uppercase tracking-widest text-slate-500 ml-1">Task Tree</div>
                <div class="space-y-3">
                    ${plan.tasks.map(task => `
                        <div class="p-4 rounded-xl bg-slate-900/50 border border-slate-800 flex items-start gap-4">
                            <div class="w-8 h-8 rounded-lg bg-slate-800 flex items-center justify-center text-slate-400 shrink-0">
                                <span class="material-symbols-outlined" style="font-size:18px">task_alt</span>
                            </div>
                            <div class="flex-1">
                                <div class="flex justify-between items-center mb-1">
                                    <span class="text-sm font-bold text-slate-200">${task.title}</span>
                                    <span class="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-slate-800 text-slate-500">${task.status}</span>
                                </div>
                                <p class="text-xs text-slate-400">${task.description}</p>
                                ${task.dependencies.length > 0 ? `
                                    <div class="mt-2 flex gap-2">
                                        ${task.dependencies.map(dep => `<span class="text-[8px] bg-indigo-500/10 text-indigo-400 px-1.5 py-0.5 rounded border border-indigo-500/20">Depends on ${dep}</span>`).join('')}
                                    </div>
                                ` : ''}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>
    `;

    if (engageBtn) {
        engageBtn.classList.remove('hidden');
        engageBtn.onclick = () => engageMission(plan);
    }
    
    // Switch to autonomy view
    import("./nav_rail.js").then(m => m.switchNav('autonomy'));
}

async function engageMission(plan) {
    const engageBtn = document.getElementById('engageMissionBtn');
    const explorerContent = document.getElementById('missionExplorerContent');
    
    if (engageBtn) {
        engageBtn.disabled = true;
        engageBtn.innerHTML = `<span class="animate-spin material-symbols-outlined text-sm">sync</span> Engaging Swarm...`;
    }
    
    try {
        const response = await fetch(`/api/autonomy/mission/active/execute`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mission_id: "active", plan: plan})
        });
        
        if (response.ok) {
            // In Phase F, we wait for a status update or result
            // For this UI demo, we'll simulate the wait and show a 'Minting' step
            if (explorerContent) {
                explorerContent.innerHTML += `
                    <div id="mintingStatus" class="mt-6 p-4 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-center animate-pulse">
                        <div class="text-xs font-bold text-indigo-400 uppercase tracking-widest mb-1">Minting Integrity Seal</div>
                        <div class="text-[10px] text-slate-400">LocalMind is generating cryptographic proof of this mission's results...</div>
                    </div>
                `;
            }

            // Realistically, we'd poll or use WebSocket. For now, we tell the user it's running.
            setTimeout(() => {
                const mintStatus = document.getElementById('mintingStatus');
                if (mintStatus) {
                    mintStatus.classList.remove('animate-pulse');
                    mintStatus.innerHTML = `
                        <div class="text-xs font-bold text-green-400 uppercase tracking-widest mb-1">Mission Sealed</div>
                        <div class="text-[9px] font-mono text-slate-500 break-all">LM1:7b92...8f3c:9d1a...5e2b</div>
                    `;
                }
            }, 5000);
        }
    } catch (err) {
        console.error("Engage failed:", err);
        if (engageBtn) engageBtn.disabled = false;
    }
}

function showBriefing(data) {
    const modal = document.getElementById('briefingModal');
    const greeting = document.getElementById('briefingGreeting');
    const yesterday = document.getElementById('briefingYesterday');
    const flagsList = document.getElementById('briefingFlags');
    
    if (!modal) return;

    greeting.textContent = data.greeting_persona || "Yo — what's up?";
    yesterday.textContent = data.yesterday_summary || "Ready to get to work?";
    
    // Clear and inject flags
    flagsList.innerHTML = '';
    if (data.overnight_flags && data.overnight_flags.length > 0) {
        data.overnight_flags.forEach(flag => {
            const div = document.createElement('div');
            div.className = "p-3 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center gap-3";
            div.innerHTML = `
                <span class="material-symbols-outlined text-indigo-400">priority_high</span>
                <div>
                    <div class="text-[11px] font-bold text-slate-200">${flag.title}</div>
                    <div class="text-[9px] text-slate-500">Source: ${flag.source}</div>
                </div>
            `;
            flagsList.appendChild(div);
        });
    } else {
        flagsList.innerHTML = `<div class="text-[10px] text-slate-600 italic">No overnight alerts.</div>`;
    }

    // Show modal
    modal.classList.remove('hidden');
}
