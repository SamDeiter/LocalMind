/**
 * Token Visualization Panel — live token counters, breakdown bar, speed gauge,
 * session totals, and model badge.
 *
 * Exports:
 *   initTokenPanel()           – create DOM and attach to the header area
 *   updateTokenStream(count)   – called per-token during streaming
 *   updateTokenAnalytics(a)    – called with full analytics object at stream end
 *   resetTokenPanel()          – reset for a new message
 *   getSessionStats()          – return cumulative session statistics
 */

import { API } from "./state.js";

// ── Session-level accumulators (in-memory only) ────────────────
let _sessionTokens = 0;
let _sessionMessages = 0;
let _sessionSpeedSum = 0; // sum of tokens_per_sec across messages

// ── Per-message state ──────────────────────────────────────────
let _currentTokens = 0;
let _speedHistory = []; // recent tok/s readings for sparkline
let _streamStartTime = 0;
let _lastTokenTime = 0;
let _recentTokenTimes = []; // timestamps for live speed calc

// ── DOM refs (set in initTokenPanel) ───────────────────────────
let _panel = null;
let _liveCounter = null;
let _breakdownBar = null;
let _speedValue = null;
let _sparkline = null;
let _sessionTotal = null;
let _modelBadge = null;
let _toggleBtn = null;

// ── Colours for breakdown segments ─────────────────────────────
const SEGMENT_COLORS = {
  system: "#6366f1",       // primary
  tools: "#10b981",        // secondary
  instructions: "#8b5cf6", // tertiary
  input: "#f59e0b",        // amber
  output: "#94a3b8",       // slate
};

// ════════════════════════════════════════════════════════════════
// initTokenPanel — build DOM, attach toggle button to header
// ════════════════════════════════════════════════════════════════
export function initTokenPanel() {
  // --- Toggle button (placed near the system metrics in header) ---
  const metricsArea = document.querySelector("header .hidden.md\\:flex");
  if (metricsArea && !document.getElementById("tokenPanelToggle")) {
    _toggleBtn = document.createElement("button");
    _toggleBtn.id = "tokenPanelToggle";
    _toggleBtn.title = "Toggle Token Panel";
    _toggleBtn.className =
      "flex items-center gap-2 p-2 px-3 rounded-lg bg-slate-900/40 border border-slate-800/60 " +
      "hover:bg-primary/20 hover:border-primary/40 transition-all cursor-pointer select-none";
    _toggleBtn.innerHTML =
      '<span class="material-symbols-outlined text-amber-400 text-[12px]">token</span>' +
      '<span class="text-[11px] font-bold uppercase tracking-wider text-slate-500">Tokens</span>';
    _toggleBtn.addEventListener("click", _togglePanel);
    metricsArea.appendChild(_toggleBtn);
  }

  // --- Floating panel ---
  if (document.getElementById("tokenVizPanel")) return;

  _panel = document.createElement("div");
  _panel.id = "tokenVizPanel";
  _panel.className =
    "fixed top-16 right-4 w-80 z-[100] rounded-2xl " +
    "bg-[#1e293b]/95 backdrop-blur-xl border border-[#334155]/40 shadow-2xl shadow-black/40 " +
    "text-white font-body overflow-hidden transition-all duration-300 ease-in-out " +
    "opacity-0 pointer-events-none translate-y-2 scale-95";
  _panel.style.transformOrigin = "top right";

  _panel.innerHTML = `
    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-[#334155]/40 bg-[#0f172a]/60">
      <div class="flex items-center gap-2">
        <span class="material-symbols-outlined text-amber-400 text-base">token</span>
        <span class="text-xs font-headline font-bold uppercase tracking-widest text-slate-300">Token Panel</span>
      </div>
      <button id="tokenPanelClose" class="text-slate-500 hover:text-slate-300 transition-colors" title="Close panel">
        <span class="material-symbols-outlined text-sm">close</span>
      </button>
    </div>

    <!-- Live Counter -->
    <div class="px-4 pt-4 pb-2">
      <div class="flex items-baseline justify-between">
        <div class="flex items-baseline gap-2">
          <span id="tokenLiveCount" class="text-3xl font-headline font-bold tabular-nums text-white transition-all duration-150"
            style="text-shadow: 0 0 20px rgba(99,102,241,0.3)">0</span>
          <span class="text-xs uppercase tracking-widest text-slate-500 font-bold">tokens</span>
        </div>
        <span id="tokenModelBadge"
          class="text-[11px] font-mono font-bold uppercase tracking-wider bg-primary/15 text-primary border border-primary/20 px-2 py-0.5 rounded-full max-w-[140px] truncate">
          --
        </span>
      </div>
    </div>

    <!-- Speed Gauge -->
    <div class="px-4 py-2 flex items-center gap-3">
      <div class="flex items-baseline gap-1.5">
        <span id="tokenSpeedValue" class="text-lg font-headline font-bold tabular-nums text-emerald-400">0.0</span>
        <span class="text-[11px] uppercase tracking-widest text-slate-500 font-bold">tok/s</span>
      </div>
      <svg id="tokenSparkline" class="flex-1 h-6" viewBox="0 0 120 24" preserveAspectRatio="none">
        <polyline fill="none" stroke="#10b981" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
          points="" id="tokenSparklinePath" opacity="0.7"/>
      </svg>
    </div>

    <!-- Breakdown Bar -->
    <div class="px-4 py-2">
      <div class="text-[11px] uppercase tracking-widest text-slate-500 font-bold mb-1.5">Prompt Breakdown</div>
      <div id="tokenBreakdownBar" class="flex h-3 rounded-full overflow-hidden bg-slate-800/60 border border-slate-700/30">
        <!-- segments injected dynamically -->
      </div>
      <div id="tokenBreakdownLegend" class="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-[11px] text-slate-400">
        <!-- legend items injected dynamically -->
      </div>
    </div>

    <!-- Session Totals -->
    <div class="px-4 py-3 mt-1 border-t border-[#334155]/40 bg-[#0f172a]/40 flex items-center justify-between">
      <div class="text-[11px] uppercase tracking-widest text-slate-500 font-bold">Session</div>
      <div class="flex items-center gap-4">
        <div class="text-right">
          <span id="tokenSessionTotal" class="text-sm font-headline font-bold tabular-nums text-slate-200">0</span>
          <span class="text-xs uppercase tracking-wider text-slate-500 ml-1">total</span>
        </div>
        <div class="text-right">
          <span id="tokenSessionMsgs" class="text-sm font-headline font-bold tabular-nums text-slate-200">0</span>
          <span class="text-xs uppercase tracking-wider text-slate-500 ml-1">msgs</span>
        </div>
        <div class="text-right">
          <span id="tokenSessionAvgSpeed" class="text-sm font-headline font-bold tabular-nums text-emerald-400">0.0</span>
          <span class="text-xs uppercase tracking-wider text-slate-500 ml-1">avg tok/s</span>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(_panel);

  // Cache DOM refs
  _liveCounter = _panel.querySelector("#tokenLiveCount");
  _breakdownBar = _panel.querySelector("#tokenBreakdownBar");
  _speedValue = _panel.querySelector("#tokenSpeedValue");
  _sparkline = _panel.querySelector("#tokenSparklinePath");
  _sessionTotal = _panel.querySelector("#tokenSessionTotal");
  _modelBadge = _panel.querySelector("#tokenModelBadge");

  // Close button
  _panel.querySelector("#tokenPanelClose").addEventListener("click", _hidePanel);
}

// ════════════════════════════════════════════════════════════════
// updateTokenStream — called per-token during SSE streaming
// ════════════════════════════════════════════════════════════════
export function updateTokenStream(approximateTokenCount) {
  if (!_panel) return;

  const now = performance.now();

  if (_streamStartTime === 0) {
    _streamStartTime = now;
    _lastTokenTime = now;
  }

  // Approximate token count (text length / 4)
  const estimated = Math.ceil(approximateTokenCount / 4);
  _currentTokens = estimated;

  // Animate counter
  if (_liveCounter) {
    _liveCounter.textContent = _formatNumber(estimated);
    _liveCounter.style.textShadow = "0 0 24px rgba(99,102,241,0.5)";
    setTimeout(() => {
      if (_liveCounter) _liveCounter.style.textShadow = "0 0 20px rgba(99,102,241,0.3)";
    }, 100);
  }

  // Compute live speed from recent tokens
  _recentTokenTimes.push({ time: now, count: estimated });
  // Keep only last 2 seconds of data
  const windowMs = 2000;
  _recentTokenTimes = _recentTokenTimes.filter((t) => now - t.time < windowMs);

  if (_recentTokenTimes.length >= 2) {
    const first = _recentTokenTimes[0];
    const last = _recentTokenTimes[_recentTokenTimes.length - 1];
    const dtSec = (last.time - first.time) / 1000;
    const dTokens = last.count - first.count;
    if (dtSec > 0) {
      const speed = dTokens / dtSec;
      _updateSpeed(speed);
    }
  }
}

// ════════════════════════════════════════════════════════════════
// updateTokenAnalytics — called at end with full analytics obj
// ════════════════════════════════════════════════════════════════
export function updateTokenAnalytics(analytics) {
  if (!_panel) return;

  const totalTokens = analytics.total_tokens || _currentTokens || 0;
  const tokPerSec = analytics.tokens_per_sec || 0;
  const model = analytics.model || "--";

  // Update live counter with accurate final value
  if (_liveCounter) {
    _animateCounter(_liveCounter, _currentTokens, totalTokens, 400);
  }
  _currentTokens = totalTokens;

  // Model badge
  if (_modelBadge) {
    _modelBadge.textContent = model;
    _modelBadge.title = model;
  }

  // Speed
  _updateSpeed(tokPerSec);

  // Session accumulators
  _sessionTokens += totalTokens;
  _sessionMessages += 1;
  _sessionSpeedSum += tokPerSec;

  _updateSessionDisplay();

  // Fetch token breakdown from backend for this message
  _fetchBreakdown(model);
}

// ════════════════════════════════════════════════════════════════
// resetTokenPanel — prepare for a new message
// ════════════════════════════════════════════════════════════════
export function resetTokenPanel() {
  _currentTokens = 0;
  _speedHistory = [];
  _streamStartTime = 0;
  _lastTokenTime = 0;
  _recentTokenTimes = [];

  if (_liveCounter) _liveCounter.textContent = "0";
  if (_speedValue) _speedValue.textContent = "0.0";
  if (_sparkline) _sparkline.setAttribute("points", "");
  if (_breakdownBar) _breakdownBar.innerHTML = "";

  const legend = _panel?.querySelector("#tokenBreakdownLegend");
  if (legend) legend.innerHTML = "";
}

// ════════════════════════════════════════════════════════════════
// getSessionStats
// ════════════════════════════════════════════════════════════════
export function getSessionStats() {
  return {
    totalTokens: _sessionTokens,
    messageCount: _sessionMessages,
    avgSpeed: _sessionMessages > 0 ? _sessionSpeedSum / _sessionMessages : 0,
  };
}

// ────────────────────────────────────────────────────────────────
// Internal helpers
// ────────────────────────────────────────────────────────────────

function _togglePanel() {
  if (!_panel) return;
  const isHidden = _panel.classList.contains("pointer-events-none");
  if (isHidden) {
    _showPanel();
  } else {
    _hidePanel();
  }
}

function _showPanel() {
  if (!_panel) return;
  _panel.classList.remove("opacity-0", "pointer-events-none", "translate-y-2", "scale-95");
  _panel.classList.add("opacity-100", "pointer-events-auto", "translate-y-0", "scale-100");
  if (_toggleBtn) _toggleBtn.classList.add("bg-primary/20", "border-primary/40");
}

function _hidePanel() {
  if (!_panel) return;
  _panel.classList.add("opacity-0", "pointer-events-none", "translate-y-2", "scale-95");
  _panel.classList.remove("opacity-100", "pointer-events-auto", "translate-y-0", "scale-100");
  if (_toggleBtn) _toggleBtn.classList.remove("bg-primary/20", "border-primary/40");
}

function _updateSpeed(tokPerSec) {
  if (_speedValue) {
    _speedValue.textContent = tokPerSec.toFixed(1);
    // Color-code: green > 20, yellow 5-20, red < 5
    if (tokPerSec >= 20) {
      _speedValue.className = "text-lg font-headline font-bold tabular-nums text-emerald-400";
    } else if (tokPerSec >= 5) {
      _speedValue.className = "text-lg font-headline font-bold tabular-nums text-amber-400";
    } else {
      _speedValue.className = "text-lg font-headline font-bold tabular-nums text-red-400";
    }
  }

  // Sparkline
  _speedHistory.push(tokPerSec);
  if (_speedHistory.length > 30) _speedHistory.shift();
  _drawSparkline();
}

function _drawSparkline() {
  if (!_sparkline || _speedHistory.length < 2) return;

  const max = Math.max(..._speedHistory, 1);
  const w = 120;
  const h = 24;
  const step = w / (_speedHistory.length - 1);

  const points = _speedHistory
    .map((v, i) => {
      const x = (i * step).toFixed(1);
      const y = (h - (v / max) * (h - 2) - 1).toFixed(1);
      return `${x},${y}`;
    })
    .join(" ");

  _sparkline.setAttribute("points", points);
}

function _updateSessionDisplay() {
  if (!_panel) return;

  const totalEl = _panel.querySelector("#tokenSessionTotal");
  const msgsEl = _panel.querySelector("#tokenSessionMsgs");
  const avgEl = _panel.querySelector("#tokenSessionAvgSpeed");

  if (totalEl) totalEl.textContent = _formatNumber(_sessionTokens);
  if (msgsEl) msgsEl.textContent = _sessionMessages.toString();
  if (avgEl) {
    const avg = _sessionMessages > 0 ? _sessionSpeedSum / _sessionMessages : 0;
    avgEl.textContent = avg.toFixed(1);
  }
}

function _formatNumber(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 10_000) return (n / 1_000).toFixed(1) + "K";
  return n.toLocaleString();
}

function _animateCounter(el, from, to, durationMs) {
  const start = performance.now();
  const diff = to - from;

  function tick(now) {
    const elapsed = now - start;
    const progress = Math.min(elapsed / durationMs, 1);
    // Ease-out cubic
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = Math.round(from + diff * eased);
    el.textContent = _formatNumber(current);
    if (progress < 1) requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}

function _renderBreakdown(breakdown) {
  if (!_breakdownBar || !_panel) return;

  const total = breakdown.total || 1;
  const segments = ["system", "tools", "instructions", "input", "output"];

  _breakdownBar.innerHTML = "";
  const legend = _panel.querySelector("#tokenBreakdownLegend");
  if (legend) legend.innerHTML = "";

  for (const key of segments) {
    const value = breakdown[key] || 0;
    if (value === 0) continue;

    const pct = ((value / total) * 100).toFixed(1);
    const color = SEGMENT_COLORS[key] || "#64748b";

    // Bar segment
    const seg = document.createElement("div");
    seg.className = "transition-all duration-500 ease-out";
    seg.style.width = pct + "%";
    seg.style.backgroundColor = color;
    seg.title = `${key}: ${value} tokens (${pct}%)`;
    _breakdownBar.appendChild(seg);

    // Legend
    if (legend) {
      const item = document.createElement("div");
      item.className = "flex items-center gap-1";
      item.innerHTML =
        `<span class="w-2 h-2 rounded-full inline-block" style="background:${color}"></span>` +
        `<span>${key}</span>` +
        `<span class="text-slate-500 ml-0.5">${value}</span>`;
      legend.appendChild(item);
    }
  }
}

async function _fetchBreakdown(model) {
  try {
    // Use an empty string for now — the backend estimates based on last prompt
    const resp = await fetch(`${API}/api/token-estimate?text=&model=${encodeURIComponent(model || "")}`);
    if (resp.ok) {
      const data = await resp.json();
      // Only render if we got meaningful data; otherwise synthesise from current info
      if (data.total > 0) {
        _renderBreakdown(data);
        return;
      }
    }
  } catch {
    // silent — breakdown is optional
  }

  // Fallback: render a simple output-only bar
  _renderBreakdown({ output: _currentTokens, total: _currentTokens });
}
