/**
 * AI Learning Lab Dashboard — Sprint 8, Feature 2
 *
 * Shows what the AI has learned from the internet, lets the user trigger
 * learning cycles, and browses suggested topics.  Pure vanilla JS.
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";
import { showCardSkeletons, showStatSkeletons } from "./ui_components.js";

// ── State ──────────────────────────────────────────────────────
let _journal = [];
let _stats = {};
let _suggestions = [];
let _learning = false; // true while a learning cycle is in flight

// ── Public API ─────────────────────────────────────────────────

export function initLearningUI() {
  _injectStyles();
  _buildSkeleton();
  // Lazy-load data when the user navigates to the page
}

export async function loadLearningData() {
  const container = document.getElementById("learningPage");
  if (!container) return;

  // Show skeletons during fetch
  const statsEl = document.getElementById("learningStats");
  const suggestionsEl = document.getElementById("learningSuggestions");
  if (statsEl && !_stats.total_topics) showStatSkeletons(statsEl, 3);
  if (suggestionsEl && _suggestions.length === 0) showCardSkeletons(suggestionsEl, 6);

  try {
    const [journalRes, statsRes, suggestionsRes] = await Promise.all([
      fetch(`${API}/api/learning/journal?limit=50`),
      fetch(`${API}/api/learning/stats`),
      fetch(`${API}/api/learning/suggestions`),
    ]);

    if (journalRes.ok) {
      const jd = await journalRes.json();
      _journal = jd.journal || [];
    }
    if (statsRes.ok) {
      const sd = await statsRes.json();
      _stats = sd;
    }
    if (suggestionsRes.ok) {
      const sg = await suggestionsRes.json();
      _suggestions = sg.suggestions || [];
    }
  } catch {
    // Silently degrade — we render whatever we have
  }

  _renderDashboard();
}

export async function triggerLearning(topic) {
  if (_learning) return;
  _learning = true;
  _showLearningOverlay(topic || "auto-detect");

  try {
    const url = topic
      ? `${API}/api/learning/learn?topic=${encodeURIComponent(topic)}`
      : `${API}/api/learning/learn`;
    const res = await fetch(url, { method: "POST" });

    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    _hideLearningOverlay();
    _showSuccessCard(data);
    showToast(`Learned about "${data.topic || topic}"`, "success");
    // Refresh data
    await loadLearningData();
  } catch (err) {
    _hideLearningOverlay();
    showToast(`Learning failed: ${err.message}`, "error");
  } finally {
    _learning = false;
  }
}

// ── Skeleton (one-time DOM setup) ──────────────────────────────

function _buildSkeleton() {
  const container = document.getElementById("learningPage");
  if (!container) return;
  // The container starts empty; we render into it on loadLearningData()
  container.innerHTML = `
    <div class="learning-lab flex flex-col gap-8 p-8 relative" id="learningLabRoot">
      <!-- Header -->
      <div class="flex items-center justify-between flex-wrap gap-4">
        <div class="flex items-center gap-3">
          <div class="p-2.5 bg-primary/10 rounded-xl">
            <span class="material-symbols-outlined text-primary text-2xl">school</span>
          </div>
          <div>
            <h2 class="text-xl font-headline font-bold tracking-tight text-slate-100">AI Learning Lab</h2>
            <p class="text-xs text-outline font-mono uppercase tracking-widest mt-0.5">Autonomous internet learning engine</p>
          </div>
        </div>
        <button id="learnNowBtn"
          class="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 text-xs font-black text-white hover:shadow-2xl hover:shadow-indigo-500/30 transition-all uppercase tracking-widest"
          title="Start an AI learning cycle on an auto-detected topic">
          <span class="material-symbols-outlined text-sm">auto_awesome</span> Learn Now
        </button>
      </div>

      <!-- Stats Row -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4" id="learningStats"></div>

      <!-- Suggestions Panel -->
      <div>
        <h3 class="text-xs font-black uppercase tracking-[0.2em] text-slate-500 mb-3 flex items-center gap-2">
          <span class="w-1.5 h-1.5 rounded-full bg-violet-500 shadow-[0_0_8px_#8b5cf6]"></span>
          Suggested Topics
        </h3>
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4" id="learningSuggestions"></div>
      </div>

      <!-- Journal -->
      <div>
        <h3 class="text-xs font-black uppercase tracking-[0.2em] text-slate-500 mb-3 flex items-center gap-2">
          <span class="w-1.5 h-1.5 rounded-full bg-indigo-500 shadow-[0_0_8px_#6366f1]"></span>
          Learning Journal
        </h3>
        <div id="learningJournal" class="space-y-3"></div>
      </div>

      <!-- Learning overlay (hidden) -->
      <div id="learningOverlay" class="learning-overlay hidden">
        <div class="learning-overlay-inner">
          <div class="learning-brain-anim">
            <span class="material-symbols-outlined text-5xl text-primary learning-spin">psychology</span>
          </div>
          <p class="text-sm font-headline text-slate-200 mt-4" id="learningOverlayText">Researching...</p>
          <div class="learning-progress-bar mt-4">
            <div class="learning-progress-fill"></div>
          </div>
          <p class="text-xs text-outline mt-2 font-mono uppercase tracking-widest">Searching the web, synthesizing knowledge...</p>
        </div>
      </div>

      <!-- Success card (hidden) -->
      <div id="learningSuccessCard" class="learning-success hidden">
        <div class="flex items-center gap-3 mb-3">
          <span class="material-symbols-outlined text-emerald-400 text-2xl">check_circle</span>
          <h4 class="font-headline font-bold text-slate-100">Learning Complete</h4>
        </div>
        <div id="learningSuccessBody" class="text-sm text-slate-300"></div>
      </div>
    </div>
  `;

  // Wire the Learn Now button
  document.getElementById("learnNowBtn")?.addEventListener("click", () => {
    triggerLearning("");
  });
}

// ── Render helpers ─────────────────────────────────────────────

function _renderDashboard() {
  _renderStats();
  _renderSuggestions();
  _renderJournal();
}

function _renderStats() {
  const el = document.getElementById("learningStats");
  if (!el) return;

  const total = _stats.total_entries || 0;
  const applied = _stats.applied_count || 0;
  const topics = _stats.topics_explored || 0;
  const lastTs = _stats.last_learned;
  const lastStr = lastTs ? _timeAgo(lastTs) : "Never";

  el.innerHTML = `
    <div class="bg-slate-900/60 border border-slate-800/60 rounded-xl p-4">
      <div class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">Topics Explored</div>
      <div class="text-2xl font-bold text-indigo-400 font-mono">${topics}</div>
    </div>
    <div class="bg-slate-900/60 border border-slate-800/60 rounded-xl p-4">
      <div class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">Skills Applied</div>
      <div class="text-2xl font-bold text-emerald-400 font-mono">${applied}</div>
    </div>
    <div class="bg-slate-900/60 border border-slate-800/60 rounded-xl p-4">
      <div class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">Journal Entries</div>
      <div class="text-2xl font-bold text-violet-400 font-mono">${total}</div>
    </div>
    <div class="bg-slate-900/60 border border-slate-800/60 rounded-xl p-4">
      <div class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-1">Last Learned</div>
      <div class="text-lg font-bold text-amber-400 font-mono">${escapeHtml(lastStr)}</div>
    </div>
  `;
}

function _renderSuggestions() {
  const el = document.getElementById("learningSuggestions");
  if (!el) return;

  if (!_suggestions.length) {
    el.innerHTML = `<div class="col-span-full text-sm text-slate-500 italic py-4">No suggestions available. Check back after some learning sessions.</div>`;
    return;
  }

  el.innerHTML = _suggestions
    .slice(0, 5)
    .map((s) => {
      const diffClass = _difficultyClass(s.difficulty);
      return `
      <div class="learn-topic-card bg-surface-container/80 backdrop-blur-xl border border-outline-variant/40 rounded-2xl p-5 flex flex-col gap-3 hover:border-primary/40 transition-all group cursor-pointer"
        data-topic="${escapeHtml(s.topic)}">
        <div class="flex items-center justify-between">
          <h4 class="font-headline font-bold text-sm text-slate-100 group-hover:text-primary transition-colors">${escapeHtml(s.topic)}</h4>
          <span class="text-[11px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full ${diffClass}">${escapeHtml(s.difficulty || "medium")}</span>
        </div>
        <p class="text-xs text-slate-400 leading-relaxed">${escapeHtml(s.reasoning || "")}</p>
        <button class="learn-topic-btn mt-auto self-start flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/10 hover:bg-primary/20 text-primary text-xs font-bold uppercase tracking-wider transition-colors border border-primary/20"
          data-topic="${escapeHtml(s.topic)}">
          <span class="material-symbols-outlined text-xs">play_arrow</span> Learn This
        </button>
      </div>`;
    })
    .join("");

  // Wire clicks — entire card or button triggers learning (delegated)
  if (!el._learningWired) {
    el._learningWired = true;
    el.addEventListener("click", (e) => {
      const card = e.target.closest(".learn-topic-card");
      if (!card) return;
      const topic = card.dataset.topic;
      if (topic) triggerLearning(topic);
    });
  }
}

function _renderJournal() {
  const el = document.getElementById("learningJournal");
  if (!el) return;

  if (!_journal.length) {
    el.innerHTML = `
      <div class="flex flex-col items-center justify-center py-12 text-center gap-3">
        <span class="material-symbols-outlined text-4xl text-slate-600">menu_book</span>
        <p class="text-sm text-slate-500">No learning sessions yet.</p>
        <p class="text-xs text-slate-600">Click <strong class="text-primary">"Learn Now"</strong> to get started.</p>
      </div>`;
    return;
  }

  el.innerHTML = _journal
    .map((entry, idx) => {
      const time = entry.timestamp ? _formatTimestamp(entry.timestamp) : "Unknown time";
      const appliedBadge = entry.applied
        ? `<span class="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-widest bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded-full"><span class="material-symbols-outlined text-xs">check_circle</span> Applied</span>`
        : `<span class="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-widest bg-slate-500/20 text-slate-400 px-2 py-0.5 rounded-full"><span class="material-symbols-outlined text-xs">lightbulb</span> Knowledge</span>`;
      const toolBadge = entry.tool_name
        ? `<span class="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-widest bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded-full"><span class="material-symbols-outlined text-xs">build</span> ${escapeHtml(entry.tool_name)}</span>`
        : "";
      const sourceBadge = entry.source
        ? `<span class="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-widest bg-cyan-500/20 text-cyan-400 px-2 py-0.5 rounded-full">${escapeHtml(entry.source)}</span>`
        : "";

      const summaryTrunc = (entry.summary || "").length > 180;
      const summaryShort = summaryTrunc
        ? escapeHtml(entry.summary.slice(0, 180)) + "..."
        : escapeHtml(entry.summary || "");
      const summaryFull = escapeHtml(entry.summary || "");

      return `
      <div class="learning-journal-entry border-l-2 border-primary/40 pl-5 py-4 hover:border-primary transition-colors relative group">
        <div class="absolute left-[-5px] top-6 w-2 h-2 rounded-full bg-primary shadow-[0_0_8px_rgba(99,102,241,0.5)]"></div>
        <div class="flex items-center gap-3 flex-wrap mb-2">
          <h4 class="font-headline font-bold text-sm text-slate-100">${escapeHtml(entry.topic || "Untitled")}</h4>
          ${appliedBadge}
          ${toolBadge}
          ${sourceBadge}
        </div>
        <p class="text-xs text-outline font-mono mb-2">${escapeHtml(time)}</p>
        <div class="text-xs text-slate-400 leading-relaxed">
          <span class="journal-summary-short-${idx}">${summaryShort}</span>
          ${summaryTrunc ? `<span class="journal-summary-full-${idx} hidden">${summaryFull}</span>
          <button class="journal-expand-btn text-primary hover:text-primary/80 text-xs font-bold ml-1 transition-colors" data-idx="${idx}">Show more</button>` : ""}
        </div>
      </div>`;
    })
    .join("");

  // Wire expand/collapse buttons
  el.querySelectorAll(".journal-expand-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = btn.dataset.idx;
      const shortEl = el.querySelector(`.journal-summary-short-${idx}`);
      const fullEl = el.querySelector(`.journal-summary-full-${idx}`);
      if (!shortEl || !fullEl) return;
      const expanded = !fullEl.classList.contains("hidden");
      if (expanded) {
        fullEl.classList.add("hidden");
        shortEl.classList.remove("hidden");
        btn.textContent = "Show more";
      } else {
        shortEl.classList.add("hidden");
        fullEl.classList.remove("hidden");
        btn.textContent = "Show less";
      }
    });
  });
}

// ── Learning overlay animation ─────────────────────────────────

function _showLearningOverlay(topic) {
  const overlay = document.getElementById("learningOverlay");
  const text = document.getElementById("learningOverlayText");
  if (!overlay) return;
  if (text) text.textContent = `Researching "${topic}"...`;
  overlay.classList.remove("hidden");
  // Reset progress bar animation
  const fill = overlay.querySelector(".learning-progress-fill");
  if (fill) {
    fill.style.animation = "none";
    // Trigger reflow
    void fill.offsetWidth;
    fill.style.animation = "";
  }
}

function _hideLearningOverlay() {
  const overlay = document.getElementById("learningOverlay");
  if (overlay) overlay.classList.add("hidden");
}

function _showSuccessCard(data) {
  const card = document.getElementById("learningSuccessCard");
  const body = document.getElementById("learningSuccessBody");
  if (!card || !body) return;

  const findings = (data.findings || []).map((f) => `<li>${escapeHtml(f)}</li>`).join("");
  body.innerHTML = `
    <p class="mb-2"><strong class="text-slate-100">Topic:</strong> ${escapeHtml(data.topic || "Unknown")}</p>
    ${data.summary ? `<p class="mb-2">${escapeHtml(data.summary)}</p>` : ""}
    ${findings ? `<ul class="list-disc list-inside space-y-1 text-xs text-slate-400">${findings}</ul>` : ""}
    ${data.applied ? `<p class="mt-2 text-emerald-400 text-xs font-bold">New skill applied as tool: ${escapeHtml(data.tool_name || "")}</p>` : ""}
  `;

  card.classList.remove("hidden");
  // Auto-hide after 8 seconds
  setTimeout(() => {
    card.classList.add("hidden");
  }, 8000);
}

// ── Utility ────────────────────────────────────────────────────

function _difficultyClass(difficulty) {
  switch ((difficulty || "").toLowerCase()) {
    case "easy":
      return "bg-emerald-500/20 text-emerald-400";
    case "hard":
      return "bg-red-500/20 text-red-400";
    case "medium":
    default:
      return "bg-amber-500/20 text-amber-400";
  }
}

function _timeAgo(ts) {
  const now = Date.now() / 1000;
  const diff = now - ts;
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function _formatTimestamp(ts) {
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "Unknown";
  }
}

// ── Injected CSS ───────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById("learning-ui-styles")) return;
  const style = document.createElement("style");
  style.id = "learning-ui-styles";
  style.textContent = `
    /* Learning overlay */
    .learning-overlay {
      position: absolute;
      inset: 0;
      z-index: 50;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(2, 6, 23, 0.92);
      backdrop-filter: blur(12px);
      border-radius: 1rem;
    }
    .learning-overlay.hidden { display: none; }
    .learning-overlay-inner {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      max-width: 320px;
    }

    /* Spinning brain */
    @keyframes learningSpin {
      0%   { transform: rotateY(0deg)   scale(1);   filter: drop-shadow(0 0 12px rgba(99,102,241,0.4)); }
      25%  { transform: rotateY(90deg)  scale(1.1);  filter: drop-shadow(0 0 20px rgba(99,102,241,0.7)); }
      50%  { transform: rotateY(180deg) scale(1);    filter: drop-shadow(0 0 12px rgba(139,92,246,0.4)); }
      75%  { transform: rotateY(270deg) scale(1.1);  filter: drop-shadow(0 0 20px rgba(139,92,246,0.7)); }
      100% { transform: rotateY(360deg) scale(1);    filter: drop-shadow(0 0 12px rgba(99,102,241,0.4)); }
    }
    .learning-spin {
      animation: learningSpin 2s ease-in-out infinite;
    }
    .learning-brain-anim {
      width: 80px;
      height: 80px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      background: rgba(99,102,241,0.08);
      border: 2px solid rgba(99,102,241,0.25);
      box-shadow: 0 0 40px rgba(99,102,241,0.15);
    }

    /* Progress bar */
    .learning-progress-bar {
      width: 200px;
      height: 4px;
      background: rgba(99,102,241,0.15);
      border-radius: 2px;
      overflow: hidden;
    }
    @keyframes learningProgressFill {
      0%   { width: 0%; }
      20%  { width: 25%; }
      60%  { width: 65%; }
      80%  { width: 80%; }
      100% { width: 95%; }
    }
    .learning-progress-fill {
      height: 100%;
      background: linear-gradient(90deg, #6366f1, #8b5cf6);
      border-radius: 2px;
      animation: learningProgressFill 12s ease-out forwards;
    }

    /* Success card */
    .learning-success {
      position: absolute;
      bottom: 2rem;
      right: 2rem;
      z-index: 55;
      max-width: 380px;
      background: rgba(30, 41, 59, 0.95);
      backdrop-filter: blur(16px);
      border: 1px solid rgba(16, 185, 129, 0.3);
      border-radius: 1rem;
      padding: 1.25rem;
      box-shadow: 0 20px 60px rgba(0,0,0,0.4), 0 0 30px rgba(16,185,129,0.1);
      animation: learningSuccessIn 0.4s ease-out;
    }
    .learning-success.hidden { display: none; }
    @keyframes learningSuccessIn {
      from { opacity: 0; transform: translateY(20px) scale(0.95); }
      to   { opacity: 1; transform: translateY(0) scale(1); }
    }

    /* Journal entry hover */
    .learning-journal-entry {
      transition: border-color 0.2s, background 0.2s;
    }
    .learning-journal-entry:hover {
      background: rgba(99,102,241,0.03);
    }
  `;
  document.head.appendChild(style);
}
