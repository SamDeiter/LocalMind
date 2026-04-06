/**
 * HardwareDashboard Component
 * 
 * Renders the live GPU/VRAM metrics with color-coded status based on adaptive scaling.
 */

export function renderHardwareStats(status) {
    const hw = status.hardware || {};
    const gpuUtil = hw.gpu_util || 0;
    const vramPct = hw.vram_pct || 0;
    const vramUsed = hw.vram_used ? (hw.vram_used / 1024).toFixed(1) : '0.0'; // GB
    const vramTotal = hw.vram_total ? (hw.vram_total / 1024).toFixed(1) : '0.0'; // GB
    const adaptiveStatus = hw.adaptive_status || 'safe';

    let statusColor = 'text-emerald-400';
    let statusText = 'SAFE';
    let pulseClass = '';

    if (adaptiveStatus === 'critical') {
        statusColor = 'text-red-500';
        statusText = 'CRITICAL (Throttled)';
        pulseClass = 'animate-pulse';
    } else if (adaptiveStatus === 'warning') {
        statusColor = 'text-amber-500';
        statusText = 'HIGH (Adaptive)';
    }

    return `
        <div class="bg-slate-900/60 border border-slate-800/60 rounded-xl p-4 relative overflow-hidden group">
            <div class="absolute top-0 right-0 p-2">
                <div class="w-1.5 h-1.5 rounded-full ${statusColor === 'text-emerald-400' ? 'bg-emerald-500' : (statusColor === 'text-amber-500' ? 'bg-amber-500' : 'bg-red-500')} ${pulseClass}"></div>
            </div>
            
            <div class="text-[9px] font-bold uppercase tracking-widest text-slate-500 mb-2 flex justify-between items-center">
                <span>Hardware System</span>
                <span class="${statusColor} text-[8px] font-black">${statusText}</span>
            </div>

            <div class="space-y-3">
                <!-- GPU Load -->
                <div>
                    <div class="flex justify-between text-[10px] mb-1">
                        <span class="text-slate-400">GPU Core</span>
                        <span class="font-mono ${gpuUtil > 80 ? 'text-amber-400' : 'text-slate-200'}">${gpuUtil.toFixed(0)}%</span>
                    </div>
                    <div class="h-1 bg-slate-800 rounded-full overflow-hidden">
                        <div class="h-full bg-gradient-to-r from-cyan-500 to-blue-500 transition-all duration-500" style="width: ${gpuUtil}%"></div>
                    </div>
                </div>

                <!-- VRAM usage -->
                <div>
                    <div class="flex justify-between text-[10px] mb-1">
                        <span class="text-slate-400">VRAM</span>
                        <span class="font-mono ${vramPct > 80 ? 'text-amber-400' : 'text-slate-200'}">${vramUsed} / ${vramTotal} GB</span>
                    </div>
                    <div class="h-1 bg-slate-800 rounded-full overflow-hidden">
                        <div class="h-full bg-gradient-to-r from-violet-500 to-fuchsia-500 transition-all duration-500" style="width: ${vramPct}%"></div>
                    </div>
                </div>
            </div>
            
            <!-- Tooltip-style info on hover -->
            <div class="absolute inset-0 bg-slate-900/95 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none">
                <div class="text-[10px] text-slate-300 text-center px-4">
                    <p class="font-bold text-white mb-1">Adaptive Swarm Control</p>
                    <p>Automatically throttles workers if VRAM usage exceeds 85% to prevent system crashes.</p>
                </div>
            </div>
        </div>
    `;
}
