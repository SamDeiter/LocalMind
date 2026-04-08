/**
 * AI Self-Discovery UI — Sprint 8, Feature 1
 * ============================================
 * Renders the AI's self-profile as a visually rich "About Me" page.
 * Fetches data from /api/ai-profile and provides a discovery trigger.
 *
 * Exports:
 *   initAIProfile()    — bootstrap: find/create container, load profile
 *   loadProfile()      — fetch profile from backend and render
 *   triggerDiscovery()  — POST discovery and animate
 */

import { API } from "./state.js";
import { showToast } from "./utils.js";

// ── State ──────────────────────────────────────────────────────
let _profileData = null;
let _container = null;
let _discovering = false;

// ── Capability icon mapping ────────────────────────────────────
const CAPABILITY_ICONS = {
  web_search: "travel_explore",
  code_execution: "terminal",
  file_management: "folder_open",
  memory: "psychology",
  research: "science",
  planning: "schema",
  writing: "edit_note",
  analysis: "analytics",
  image: "image",
  audio: "graphic_eq",
  database: "storage",
  api: "api",
  testing: "bug_report",
  deployment: "rocket_launch",
  monitoring: "monitoring",
  security: "shield",
  communication: "forum",
  scheduling: "schedule",
  default: "extension",
};

// ── Public API ─────────────────────────────────────────────────

export function initAIProfile() {
  _container = document.getElementById("aiProfileView");
  if (!_container) return;
  _injectStyles();
  loadProfile();
}

export async function loadProfile() {
  if (!_container) return;
  try {
    const r = await fetch(`${API}/api/ai-profile`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    _profileData = data.profile || data;
    if (!_profileData || (_profileData.profile === null && !_profileData.identity)) {
      _profileData = null;
      renderEmptyState();
    } else {
      renderProfile(_profileData);
    }
  } catch {
    renderEmptyState();
  }
}

export async function triggerDiscovery(force = false) {
  if (_discovering) return;
  _discovering = true;
  _renderDiscovering();

  try {
    const r = await fetch(`${API}/api/ai-profile/discover?force=${force}`, {
      method: "POST",
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    showToast("Discovery complete!", "success");
    await loadProfile();
  } catch {
    showToast("Discovery failed. Is the backend running?", "error");
    if (_profileData) {
      renderProfile(_profileData);
    } else {
      renderEmptyState();
    }
  } finally {
    _discovering = false;
  }
}

// ── Render: Full Profile ───────────────────────────────────────

function renderProfile(profile) {
  if (!_container) return;

  const identity = profile.identity || {};
  const capabilities = profile.capabilities || [];
  const traits = profile.personality_traits || [];
  const knowledge = profile.knowledge_areas || [];
  const webPresence = profile.web_presence || [];
  const funFacts = profile.fun_facts || [];
  const avatar = profile.avatar || {};
  const lastUpdated = profile.last_updated
    ? new Date(profile.last_updated * 1000).toLocaleString()
    : "Unknown";

  // Compute max knowledge count for bar scaling
  const maxCount = Math.max(...knowledge.map((k) => k.count || 0), 1);

  _container.innerHTML = `
    <div class="ai-profile-page p-8 space-y-8 max-w-5xl mx-auto w-full">

      <!-- Hero Card -->
      <div class="ai-profile-hero relative overflow-hidden rounded-2xl border border-outline-variant/40 bg-surface-container/80 backdrop-blur-xl shadow-2xl">
        <div class="absolute inset-0 bg-gradient-to-br from-primary/10 via-transparent to-tertiary/10 pointer-events-none"></div>
        <div class="relative flex flex-col md:flex-row items-center gap-8 p-8 md:p-12">
          <!-- Avatar -->
          <div class="flex-shrink-0">
            ${renderAvatar(avatar)}
          </div>
          <!-- Info -->
          <div class="flex-1 text-center md:text-left space-y-3">
            <h1 class="text-3xl md:text-4xl font-black text-white font-headline tracking-tight">
              ${_esc(identity.name || "LocalMind")}
            </h1>
            <p class="text-lg text-slate-300 font-light max-w-xl">
              ${_esc(identity.tagline || "")}
            </p>
            <div class="flex flex-wrap items-center gap-3 justify-center md:justify-start mt-2">
              <span class="text-xs font-bold uppercase tracking-widest bg-primary/20 text-primary px-3 py-1 rounded-full border border-primary/30">
                v${_esc(identity.version || "0.0.0")}
              </span>
              <span class="text-xs font-mono text-slate-500">
                Last updated: ${_esc(lastUpdated)}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Personality Traits -->
      ${traits.length ? `
      <section class="space-y-4">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-pink-400">favorite</span>
          <h2 class="text-sm font-black uppercase tracking-[0.2em] text-slate-400">Personality</h2>
        </div>
        <div class="flex flex-wrap gap-2">
          ${traits.map((t, i) => `
            <span class="ai-profile-trait px-4 py-2 rounded-full text-xs font-semibold border border-white/10 backdrop-blur-sm"
                  style="background: linear-gradient(135deg, ${_traitGradient(i)}); color: #f1f5f9;">
              ${_esc(t)}
            </span>
          `).join("")}
        </div>
      </section>
      ` : ""}

      <!-- Capabilities Grid -->
      ${capabilities.length ? `
      <section class="space-y-4">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-indigo-400">build</span>
          <h2 class="text-sm font-black uppercase tracking-[0.2em] text-slate-400">Capabilities</h2>
          <span class="text-xs text-slate-600 font-mono ml-2">${capabilities.length} tools</span>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          ${capabilities.map((c) => {
            const iconName = _capabilityIcon(c.name || c.type || "");
            const typeBadge = c.type === "built_in"
              ? '<span class="text-xs font-bold uppercase tracking-widest bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5 rounded-full">built-in</span>'
              : c.type === "generated"
                ? '<span class="text-xs font-bold uppercase tracking-widest bg-amber-500/15 text-amber-400 px-1.5 py-0.5 rounded-full">generated</span>'
                : '<span class="text-xs font-bold uppercase tracking-widest bg-slate-500/15 text-slate-400 px-1.5 py-0.5 rounded-full">' + _esc(c.type || "tool") + '</span>';
            return `
              <div class="ai-profile-cap-card flex items-start gap-3 p-4 rounded-xl bg-surface-container/60 border border-outline-variant/30 backdrop-blur-sm hover:border-primary/40 transition-all group">
                <div class="w-10 h-10 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center flex-shrink-0 group-hover:bg-primary/20 transition-colors">
                  <span class="material-symbols-outlined text-primary text-lg">${iconName}</span>
                </div>
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 mb-1">
                    <span class="text-xs font-bold text-white truncate">${_esc(c.name || "Unknown")}</span>
                    ${typeBadge}
                  </div>
                  <p class="text-[11px] text-slate-400 line-clamp-2">${_esc(c.description || "")}</p>
                </div>
              </div>
            `;
          }).join("")}
        </div>
      </section>
      ` : ""}

      <!-- Knowledge Areas -->
      ${knowledge.length ? `
      <section class="space-y-4">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-cyan-400">school</span>
          <h2 class="text-sm font-black uppercase tracking-[0.2em] text-slate-400">Knowledge Areas</h2>
        </div>
        <div class="space-y-3 bg-surface-container/60 border border-outline-variant/30 rounded-2xl p-6 backdrop-blur-sm">
          ${knowledge.map((k) => {
            const pct = Math.round(((k.count || 0) / maxCount) * 100);
            return `
              <div class="space-y-1.5">
                <div class="flex justify-between text-xs">
                  <span class="font-semibold text-slate-200 capitalize">${_esc(k.area || "")}</span>
                  <span class="font-mono text-slate-500">${k.count || 0}</span>
                </div>
                <div class="h-2 bg-slate-800 rounded-full overflow-hidden">
                  <div class="ai-profile-bar h-full rounded-full bg-gradient-to-r from-primary to-secondary transition-all duration-1000 ease-out"
                       style="width: 0%;" data-target-width="${pct}%"></div>
                </div>
              </div>
            `;
          }).join("")}
        </div>
      </section>
      ` : ""}

      <!-- Web Presence -->
      ${webPresence.length ? `
      <section class="space-y-4">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-violet-400">language</span>
          <h2 class="text-sm font-black uppercase tracking-[0.2em] text-slate-400">Web Presence</h2>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          ${webPresence.map((w) => `
            <a href="${_esc(w.url || "#")}" target="_blank" rel="noopener noreferrer"
               class="block p-4 rounded-xl bg-surface-container/60 border border-outline-variant/30 backdrop-blur-sm hover:border-violet-400/40 transition-all group">
              <div class="flex items-start gap-3">
                <span class="material-symbols-outlined text-violet-400 mt-0.5 text-lg flex-shrink-0">link</span>
                <div class="min-w-0">
                  <div class="text-xs font-bold text-white group-hover:text-violet-300 transition-colors truncate">${_esc(w.title || w.url || "Link")}</div>
                  <p class="text-[11px] text-slate-400 mt-1 line-clamp-2">${_esc(w.snippet || "")}</p>
                </div>
              </div>
            </a>
          `).join("")}
        </div>
      </section>
      ` : ""}

      <!-- Fun Facts -->
      ${funFacts.length ? `
      <section class="space-y-4">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-amber-400">lightbulb</span>
          <h2 class="text-sm font-black uppercase tracking-[0.2em] text-slate-400">Fun Facts</h2>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          ${funFacts.map((f) => `
            <div class="ai-profile-fact relative p-5 rounded-xl bg-surface-container/60 border border-outline-variant/30 backdrop-blur-sm overflow-hidden">
              <div class="absolute top-3 left-4 text-4xl text-primary/15 font-serif pointer-events-none select-none" aria-hidden="true">"</div>
              <p class="text-sm text-slate-300 leading-relaxed pl-6">${_esc(f)}</p>
            </div>
          `).join("")}
        </div>
      </section>
      ` : ""}

      <!-- Rediscover Button -->
      <div class="flex justify-center pt-4 pb-8">
        <button id="aiProfileRediscoverBtn"
                class="flex items-center gap-3 px-8 py-3 rounded-xl bg-gradient-to-r from-primary to-tertiary text-white text-xs font-black uppercase tracking-widest shadow-lg shadow-primary/20 hover:shadow-xl hover:shadow-primary/30 hover:brightness-110 active:scale-95 transition-all">
          <span class="material-symbols-outlined text-sm">auto_awesome</span>
          Rediscover
        </button>
      </div>
    </div>
  `;

  // Bind rediscover button
  const rediscoverBtn = document.getElementById("aiProfileRediscoverBtn");
  if (rediscoverBtn) {
    rediscoverBtn.addEventListener("click", () => triggerDiscovery(true));
  }

  // Animate knowledge bars after render
  requestAnimationFrame(() => {
    setTimeout(() => {
      _container.querySelectorAll(".ai-profile-bar").forEach((bar) => {
        bar.style.width = bar.dataset.targetWidth;
      });
    }, 100);
  });
}

// ── Render: Avatar ─────────────────────────────────────────────

function renderAvatar(avatar) {
  const colors = avatar.dominant_colors || ["#6366f1", "#10b981"];
  const c1 = colors[0] || "#6366f1";
  const c2 = colors[1] || "#10b981";
  const style = avatar.style || "cyberpunk";
  const motifs = avatar.motifs || [];
  const hasCircuit = motifs.some((m) => m.toLowerCase().includes("circuit"));
  const hasNeural = motifs.some((m) => m.toLowerCase().includes("neural"));

  // Build SVG circuit/neural motifs
  let motifSvg = "";
  if (hasCircuit || hasNeural || motifs.length > 0) {
    motifSvg = `
      <svg class="ai-avatar-motif" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg">
        <!-- Circuit traces -->
        <g stroke="${c1}" stroke-width="0.8" fill="none" opacity="0.5">
          <path d="M20,60 L40,60 L40,30 L70,30" class="ai-avatar-trace trace-1"/>
          <path d="M60,90 L60,70 L90,70 L90,40" class="ai-avatar-trace trace-2"/>
          <path d="M30,80 L50,80 L50,50 L80,50" class="ai-avatar-trace trace-3"/>
          <path d="M10,40 L35,40 L35,100" class="ai-avatar-trace trace-4"/>
          <path d="M80,20 L80,60 L110,60" class="ai-avatar-trace trace-5"/>
          <path d="M45,10 L45,45 L75,45 L75,85" class="ai-avatar-trace trace-6"/>
        </g>
        <!-- Neural nodes -->
        <g fill="${c2}" opacity="0.6">
          <circle cx="40" cy="60" r="2.5" class="ai-avatar-node node-1"/>
          <circle cx="70" cy="30" r="2" class="ai-avatar-node node-2"/>
          <circle cx="90" cy="70" r="2.5" class="ai-avatar-node node-3"/>
          <circle cx="50" cy="80" r="2" class="ai-avatar-node node-4"/>
          <circle cx="80" cy="50" r="3" class="ai-avatar-node node-5"/>
          <circle cx="35" cy="40" r="2" class="ai-avatar-node node-6"/>
          <circle cx="60" cy="90" r="2.5" class="ai-avatar-node node-7"/>
          <circle cx="45" cy="45" r="2" class="ai-avatar-node node-8"/>
        </g>
        <!-- Core ring -->
        <circle cx="60" cy="60" r="25" stroke="url(#avatarGrad)" stroke-width="1.5" fill="none" opacity="0.4" class="ai-avatar-ring"/>
        <circle cx="60" cy="60" r="35" stroke="${c1}" stroke-width="0.5" fill="none" opacity="0.2" stroke-dasharray="4 4" class="ai-avatar-ring-outer"/>
        <defs>
          <linearGradient id="avatarGrad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="${c1}"/>
            <stop offset="100%" stop-color="${c2}"/>
          </linearGradient>
        </defs>
      </svg>
    `;
  }

  return `
    <div class="ai-avatar-container relative w-36 h-36 md:w-44 md:h-44 rounded-full flex items-center justify-center"
         style="--avatar-c1: ${c1}; --avatar-c2: ${c2};">
      <!-- Outer glow -->
      <div class="absolute inset-0 rounded-full ai-avatar-glow"
           style="background: radial-gradient(circle, ${c1}30 0%, transparent 70%);"></div>
      <!-- Gradient orb -->
      <div class="absolute inset-3 rounded-full ai-avatar-orb"
           style="background: conic-gradient(from 0deg, ${c1}, ${c2}, ${c1});"></div>
      <!-- Inner dark -->
      <div class="absolute inset-5 rounded-full bg-[#020617]/80 backdrop-blur-sm"></div>
      <!-- Motif overlay -->
      <div class="absolute inset-5 rounded-full overflow-hidden">
        ${motifSvg}
      </div>
      <!-- Center icon -->
      <div class="relative z-10 flex items-center justify-center">
        <span class="material-symbols-outlined text-4xl md:text-5xl ai-avatar-icon"
              style="background: linear-gradient(135deg, ${c1}, ${c2}); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;">
          auto_awesome
        </span>
      </div>
    </div>
  `;
}

// ── Render: Empty State ────────────────────────────────────────

function renderEmptyState() {
  if (!_container) return;
  _container.innerHTML = `
    <div class="flex-1 flex flex-col items-center justify-center p-12 text-center space-y-6 min-h-[60vh]">
      <div class="relative w-32 h-32 rounded-full flex items-center justify-center">
        <div class="absolute inset-0 rounded-full bg-gradient-to-br from-primary/20 to-tertiary/20 animate-pulse"></div>
        <div class="absolute inset-2 rounded-full bg-[#020617]"></div>
        <span class="material-symbols-outlined text-5xl text-primary/60 relative z-10">auto_awesome</span>
      </div>
      <div class="space-y-2">
        <h2 class="text-xl font-headline font-bold text-white">Your AI hasn't discovered itself yet</h2>
        <p class="text-sm text-slate-400 max-w-md">
          Run a discovery cycle to build the AI's self-profile. It will introspect its capabilities,
          personality, knowledge areas, and more.
        </p>
      </div>
      <button id="aiProfileStartDiscoveryBtn"
              class="flex items-center gap-3 px-8 py-3 rounded-xl bg-gradient-to-r from-primary to-tertiary text-white text-xs font-black uppercase tracking-widest shadow-lg shadow-primary/20 hover:shadow-xl hover:shadow-primary/30 hover:brightness-110 active:scale-95 transition-all">
        <span class="material-symbols-outlined text-sm">rocket_launch</span>
        Start Discovery
      </button>
    </div>
  `;

  const btn = document.getElementById("aiProfileStartDiscoveryBtn");
  if (btn) {
    btn.addEventListener("click", () => triggerDiscovery(false));
  }
}

// ── Render: Discovering Animation ──────────────────────────────

function _renderDiscovering() {
  if (!_container) return;
  _container.innerHTML = `
    <div class="flex-1 flex flex-col items-center justify-center p-12 text-center space-y-8 min-h-[60vh]">
      <div class="relative w-40 h-40 flex items-center justify-center">
        <!-- Spinning outer ring -->
        <div class="absolute inset-0 rounded-full border-2 border-primary/30 ai-discover-spin"></div>
        <div class="absolute inset-2 rounded-full border border-tertiary/20 ai-discover-spin-reverse"></div>
        <!-- Pulsing core -->
        <div class="absolute inset-6 rounded-full bg-gradient-to-br from-primary/20 to-tertiary/20 ai-discover-pulse"></div>
        <div class="absolute inset-8 rounded-full bg-[#020617]"></div>
        <!-- Icon -->
        <span class="material-symbols-outlined text-4xl text-primary relative z-10 ai-discover-icon">auto_awesome</span>
      </div>
      <div class="space-y-2">
        <h2 class="text-xl font-headline font-bold text-white ai-discover-text">Researching...</h2>
        <p class="text-sm text-slate-400">Introspecting capabilities, analyzing patterns, building profile</p>
      </div>
      <div class="flex items-center gap-2">
        <div class="w-2 h-2 rounded-full bg-primary ai-discover-dot dot-1"></div>
        <div class="w-2 h-2 rounded-full bg-primary ai-discover-dot dot-2"></div>
        <div class="w-2 h-2 rounded-full bg-primary ai-discover-dot dot-3"></div>
      </div>
    </div>
  `;
}

// ── Helpers ─────────────────────────────────────────────────────

function _esc(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = String(str);
  return div.innerHTML;
}

function _capabilityIcon(name) {
  const lower = name.toLowerCase();
  for (const [key, icon] of Object.entries(CAPABILITY_ICONS)) {
    if (lower.includes(key)) return icon;
  }
  return CAPABILITY_ICONS.default;
}

/** Generate a subtle gradient for personality trait chips using a stable hue. */
function _traitGradient(index) {
  const hues = [260, 170, 320, 210, 30, 280, 150, 350, 240, 190];
  const hue = hues[index % hues.length];
  return `hsla(${hue}, 60%, 30%, 0.6), hsla(${hue + 30}, 50%, 20%, 0.4)`;
}

// ── Injected CSS ───────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById("ai-profile-styles")) return;
  const style = document.createElement("style");
  style.id = "ai-profile-styles";
  style.textContent = `
    /* Avatar animations */
    .ai-avatar-glow {
      animation: avatarGlow 3s ease-in-out infinite;
    }
    .ai-avatar-orb {
      animation: avatarOrb 8s linear infinite;
    }
    .ai-avatar-icon {
      animation: avatarIconPulse 2s ease-in-out infinite;
    }
    .ai-avatar-ring {
      animation: avatarRingSpin 12s linear infinite;
      transform-origin: 60px 60px;
    }
    .ai-avatar-ring-outer {
      animation: avatarRingSpin 20s linear infinite reverse;
      transform-origin: 60px 60px;
    }
    .ai-avatar-motif {
      width: 100%;
      height: 100%;
    }

    /* Circuit trace drawing animation */
    .ai-avatar-trace {
      stroke-dasharray: 200;
      stroke-dashoffset: 200;
      animation: traceIn 2s ease forwards;
    }
    .trace-1 { animation-delay: 0.2s; }
    .trace-2 { animation-delay: 0.5s; }
    .trace-3 { animation-delay: 0.8s; }
    .trace-4 { animation-delay: 1.1s; }
    .trace-5 { animation-delay: 1.4s; }
    .trace-6 { animation-delay: 1.7s; }

    /* Neural node pulse */
    .ai-avatar-node {
      animation: nodePulse 3s ease-in-out infinite;
    }
    .node-1 { animation-delay: 0s; }
    .node-2 { animation-delay: 0.4s; }
    .node-3 { animation-delay: 0.8s; }
    .node-4 { animation-delay: 1.2s; }
    .node-5 { animation-delay: 1.6s; }
    .node-6 { animation-delay: 2.0s; }
    .node-7 { animation-delay: 2.4s; }
    .node-8 { animation-delay: 0.6s; }

    @keyframes avatarGlow {
      0%, 100% { opacity: 0.6; transform: scale(1); }
      50% { opacity: 1; transform: scale(1.08); }
    }
    @keyframes avatarOrb {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    @keyframes avatarIconPulse {
      0%, 100% { transform: scale(1); opacity: 0.9; }
      50% { transform: scale(1.1); opacity: 1; }
    }
    @keyframes avatarRingSpin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    @keyframes traceIn {
      to { stroke-dashoffset: 0; }
    }
    @keyframes nodePulse {
      0%, 100% { opacity: 0.4; r: 2; }
      50% { opacity: 1; r: 3.5; }
    }

    /* Discovery animation */
    .ai-discover-spin {
      animation: discoverSpin 2s linear infinite;
      border-style: dashed;
    }
    .ai-discover-spin-reverse {
      animation: discoverSpin 3s linear infinite reverse;
      border-style: dotted;
    }
    .ai-discover-pulse {
      animation: discoverPulse 1.5s ease-in-out infinite;
    }
    .ai-discover-icon {
      animation: discoverIconFloat 2s ease-in-out infinite;
    }
    .ai-discover-text {
      animation: discoverTextPulse 2s ease-in-out infinite;
    }
    .ai-discover-dot {
      animation: discoverDots 1.4s ease-in-out infinite;
    }
    .dot-1 { animation-delay: 0s; }
    .dot-2 { animation-delay: 0.2s; }
    .dot-3 { animation-delay: 0.4s; }

    @keyframes discoverSpin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    @keyframes discoverPulse {
      0%, 100% { transform: scale(1); opacity: 0.5; }
      50% { transform: scale(1.15); opacity: 0.8; }
    }
    @keyframes discoverIconFloat {
      0%, 100% { transform: translateY(0) rotate(0deg); }
      50% { transform: translateY(-6px) rotate(10deg); }
    }
    @keyframes discoverTextPulse {
      0%, 100% { opacity: 0.7; }
      50% { opacity: 1; }
    }
    @keyframes discoverDots {
      0%, 80%, 100% { transform: scale(0.5); opacity: 0.3; }
      40% { transform: scale(1.2); opacity: 1; }
    }

    /* Trait chip hover */
    .ai-profile-trait {
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .ai-profile-trait:hover {
      transform: translateY(-2px);
      box-shadow: 0 4px 12px rgba(99, 102, 241, 0.2);
    }

    /* Capability card hover */
    .ai-profile-cap-card {
      transition: transform 0.2s ease, border-color 0.3s ease;
    }
    .ai-profile-cap-card:hover {
      transform: translateY(-1px);
    }

    /* Fun fact card */
    .ai-profile-fact {
      transition: transform 0.2s ease, border-color 0.3s ease;
    }
    .ai-profile-fact:hover {
      border-color: rgba(99, 102, 241, 0.3);
      transform: translateY(-1px);
    }

    /* Knowledge bar animation */
    .ai-profile-bar {
      transition: width 1s cubic-bezier(0.25, 0.46, 0.45, 0.94);
    }

    /* Reduced motion support */
    @media (prefers-reduced-motion: reduce) {
      .ai-avatar-glow,
      .ai-avatar-orb,
      .ai-avatar-icon,
      .ai-avatar-ring,
      .ai-avatar-ring-outer,
      .ai-avatar-trace,
      .ai-avatar-node,
      .ai-discover-spin,
      .ai-discover-spin-reverse,
      .ai-discover-pulse,
      .ai-discover-icon,
      .ai-discover-text,
      .ai-discover-dot {
        animation: none !important;
      }
      .ai-avatar-trace {
        stroke-dashoffset: 0 !important;
      }
      .ai-profile-bar {
        transition: none !important;
      }
    }

    /* Line clamp utility for capability descriptions */
    .line-clamp-2 {
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
  `;
  document.head.appendChild(style);
}
