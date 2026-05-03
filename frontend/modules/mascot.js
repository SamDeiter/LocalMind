/**
 * LocalMind Mascot — "Pip"
 *
 * Floating companion in the bottom-right of the shell. Reads system signals
 * from the existing utility strip + /api/jobs and drives:
 *   - a face (SVG) with a handful of mood states,
 *   - a Tamagotchi-style energy bar that drains while idle and refills with work,
 *   - rare contextual speech bubbles on real events (errors, milestones, repeats).
 *
 * Public API (additive — used by mascot_brain / mascot_quips / mascot_nudges):
 *   on(event, fn) / off(event, fn) / emit(event, payload)
 *     events: 'tick', 'signals', 'mood-change',
 *             'event:job-new', 'event:job-done', 'event:job-fail'
 *   bubble(text, mood, ms)
 *   getState()  -> frozen { mood, energy, snoozed, lastJobActivityAt, jobs }
 *   setQuipProvider(fn)  fn(mood, ctx) => Promise<string>  (fire-and-forget)
 *   requestQuip(mood, ctx, onAsync?, windowMs?)  returns immediate fallback
 *   thresholds  { sleepy_ms, bored_ms } — mutable, brain may overwrite
 */

import { API } from "./state.js";
import { escapeHtml } from "./utils.js";

// Phase A — Pip's brain. Side-effect import: the module auto-inits and
// subscribes to mascot events (job-new/done/fail, tick, mood-change).
// We then read its computeAdaptiveThresholds() each minute.
import * as brain from "./mascot_brain.js";

const LS_KEYS = {
  energy:        "lm.mascot.energy",
  energyAt:      "lm.mascot.energy_at",
  snoozeUntil:   "lm.mascot.snooze_until",
  recentTitles:  "lm.mascot.recent_titles",
  lastJobsHash:  "lm.mascot.last_jobs_hash",
  hidden:        "lm.mascot.hidden",
  lastBubbleAt:  "lm.mascot.last_bubble_at",
  templateOffered: "lm.mascot.template_offered",
};

const TICK_MS = 3000;
const POLL_MS = 12000;
const ENERGY_DRAIN_PER_MIN = 1.0;
const ENERGY_PER_NEW_JOB   = 12;
const ENERGY_PER_DONE_JOB  = 6;
const ENERGY_PER_FAIL      = -8;
const BUBBLE_COOLDOWN_MS   = 60_000;
const RECENT_TITLE_MAX     = 12;
const TEMPLATE_SUGGEST_THRESHOLD = 3;

// Mutable thresholds — Phase A's brain can overwrite these in place.
export const thresholds = {
  sleepy_ms: 5  * 60_000,
  bored_ms:  15 * 60_000,
};

const QUIPS = {
  curious:  ["ooh, what's this?", "new job — let's go.", "on it.", "interesting."],
  working:  ["working…", "humming along.", "thinking thinking thinking."],
  happy:    ["nailed it.", "ta-da!", "another one done.", "we did it."],
  excited:  ["wow! milestone unlocked.", "look at us go!", "(silently celebrating)"],
  sleepy:   ["zzz…", "wake me when there's work.", "(yawns)"],
  bored:    ["pretty quiet around here.", "got anything for me?", "I could use a job."],
  sad:      ["that didn't go great.", "I'll do better next time.", "ouch — failed."],
  alert:    ["pegged the CPU.", "things are warm in here.", "system's busy."],
  neutral:  ["hi.", "still here.", "watching."],
};

const STATE = {
  mood: "neutral",
  energy: 60,
  lastTickAt: Date.now(),
  lastJobActivityAt: Date.now(),
  jobs: [],
  knownJobIds: new Set(),
  bubbleTimer: null,
  initialized: false,
};

// ── Event bus ──────────────────────────────────────────────────────────────
const _listeners = new Map();
export function on(event, fn) {
  if (typeof fn !== "function") return () => {};
  if (!_listeners.has(event)) _listeners.set(event, new Set());
  _listeners.get(event).add(fn);
  return () => off(event, fn);
}
export function off(event, fn) {
  _listeners.get(event)?.delete(fn);
}
export function emit(event, payload) {
  const set = _listeners.get(event);
  if (!set || set.size === 0) return;
  for (const fn of Array.from(set)) {
    try { fn(payload); } catch (_) { /* a listener crash must not break the mascot */ }
  }
}

// ── Quip provider hook ─────────────────────────────────────────────────────
let _quipProvider = null;
export function setQuipProvider(fn) {
  _quipProvider = typeof fn === "function" ? fn : null;
}

function pickStaticQuip(mood) {
  const lines = QUIPS[mood] || QUIPS.neutral;
  return lines[Math.floor(Math.random() * lines.length)];
}

/**
 * Resolve a quip *synchronously* with an immediate fallback line, and — if a
 * provider is registered — fire-and-forget request a generated variant. The
 * `onAsync(generated)` callback runs only if the provider resolves before
 * `windowMs` elapses; the bubble is already on screen by then.
 */
export function requestQuip(mood, ctx = {}, onAsync, windowMs = 4500) {
  const fallback = pickStaticQuip(mood);
  if (_quipProvider && typeof onAsync === "function") {
    const deadline = Date.now() + windowMs;
    Promise.resolve()
      .then(() => _quipProvider(mood, ctx))
      .then((text) => {
        if (!text) return;
        if (Date.now() > deadline) return;
        onAsync(String(text).slice(0, 240));
      })
      .catch(() => {});
  }
  return fallback;
}

// ── Public state accessor ──────────────────────────────────────────────────
export function getState() {
  return Object.freeze({
    mood: STATE.mood,
    energy: STATE.energy,
    snoozed: isSnoozed(),
    lastJobActivityAt: STATE.lastJobActivityAt,
    jobs: STATE.jobs.slice(),
  });
}

// ── Persistence helpers ────────────────────────────────────────────────────
function loadEnergy() {
  const v = parseFloat(localStorage.getItem(LS_KEYS.energy));
  return Number.isFinite(v) ? Math.min(100, Math.max(0, v)) : 60;
}
function saveEnergy() {
  localStorage.setItem(LS_KEYS.energy, String(STATE.energy.toFixed(2)));
  localStorage.setItem(LS_KEYS.energyAt, String(Date.now()));
}
function loadRecentTitles() {
  try { return JSON.parse(localStorage.getItem(LS_KEYS.recentTitles) || "[]"); }
  catch (_) { return []; }
}
function pushRecentTitle(t) {
  if (!t) return;
  const arr = loadRecentTitles();
  arr.unshift({ t: String(t).slice(0, 240), at: Date.now() });
  while (arr.length > RECENT_TITLE_MAX) arr.pop();
  localStorage.setItem(LS_KEYS.recentTitles, JSON.stringify(arr));
}
function isSnoozed() {
  const until = parseInt(localStorage.getItem(LS_KEYS.snoozeUntil) || "0", 10);
  return until > Date.now();
}
function snoozeFor(ms) {
  localStorage.setItem(LS_KEYS.snoozeUntil, String(Date.now() + ms));
}

// ── Signal reading (zero extra fetches — reuse utility strip DOM) ──────────
function readSignals() {
  const num = (id) => {
    const txt = (document.getElementById(id)?.textContent || "").trim();
    const n = parseInt(txt.replace(/[^0-9]/g, ""), 10);
    return Number.isFinite(n) ? n : 0;
  };
  const onlineDot = document.getElementById("utilOnlineDot");
  return {
    cpu:     num("utilCpu"),
    ram:     num("utilRam"),
    gpu:     num("utilGpu"),
    queue:   num("utilQueue"),
    workers: num("utilWorkers"),
    online:  (document.getElementById("utilOnline")?.textContent || "").includes("online"),
    onlineDot,
  };
}

async function pollJobs() {
  try {
    const r = await fetch(`${API}/api/jobs?limit=10`);
    if (!r.ok) return;
    const data = await r.json();
    const list = Array.isArray(data) ? data : (data.jobs || data.items || []);
    handleJobsDelta(list);
  } catch (_) { /* offline — handled elsewhere */ }
}

function handleJobsDelta(jobs) {
  if (!Array.isArray(jobs)) return;
  const prev = STATE.knownJobIds;
  const nextSet = new Set(jobs.map((j) => j.id));

  if (STATE.initialized) {
    let newSubmitted = 0;
    let justCompleted = 0;
    let justFailed = 0;

    for (const j of jobs) {
      const wasKnown = prev.has(j.id);
      if (!wasKnown && (j.status === "pending" || j.status === "queued" || j.status === "executing")) {
        newSubmitted++;
        pushRecentTitle(j.title);
        emit("event:job-new", { job: j });
      }
      const before = STATE.jobs.find((p) => p.id === j.id);
      if (before && before.status !== j.status) {
        if (j.status === "done") {
          justCompleted++;
          emit("event:job-done", { job: j });
        }
        if (j.status === "failed" || j.status === "error" || j.status === "cancelled") {
          justFailed++;
          emit("event:job-fail", { job: j });
        }
      }
    }

    if (newSubmitted) {
      STATE.energy = Math.min(100, STATE.energy + ENERGY_PER_NEW_JOB * newSubmitted);
      STATE.lastJobActivityAt = Date.now();
      maybeBubble("curious");
      maybeOfferTemplate(jobs);
    }
    if (justCompleted) {
      STATE.energy = Math.min(100, STATE.energy + ENERGY_PER_DONE_JOB * justCompleted);
      STATE.lastJobActivityAt = Date.now();
      maybeBubble("happy");
    }
    if (justFailed) {
      STATE.energy = Math.max(0, STATE.energy + ENERGY_PER_FAIL * justFailed);
      STATE.lastJobActivityAt = Date.now();
      maybeBubble("sad");
    }
  }

  STATE.jobs = jobs;
  STATE.knownJobIds = nextSet;
  STATE.initialized = true;
}

// ── Mood resolution ────────────────────────────────────────────────────────
function resolveMood(sig) {
  if (!sig.online) return "sad";
  if (sig.cpu >= 90 || sig.ram >= 92 || sig.gpu >= 95) return "alert";
  if (sig.workers > 0 || sig.queue > 0) return "working";

  const recentMostlyFailed = STATE.jobs.slice(0, 4).filter((j) => /fail|error|cancel/i.test(j.status || "")).length >= 2;
  if (recentMostlyFailed) return "sad";

  const idleMs = Date.now() - STATE.lastJobActivityAt;
  if (idleMs > thresholds.bored_ms && STATE.energy < 25) return "bored";
  if (idleMs > thresholds.sleepy_ms) return "sleepy";

  return STATE.energy > 50 ? "happy" : "neutral";
}

// ── Tamagotchi energy ──────────────────────────────────────────────────────
function tickEnergy() {
  const now = Date.now();
  const deltaMin = (now - STATE.lastTickAt) / 60_000;
  STATE.lastTickAt = now;

  const sig = readSignals();
  if (sig.workers === 0 && sig.queue === 0) {
    STATE.energy = Math.max(0, STATE.energy - ENERGY_DRAIN_PER_MIN * deltaMin);
  } else {
    STATE.energy = Math.min(100, STATE.energy + 0.1 * deltaMin);
  }
  saveEnergy();
}

// ── Clippy-style template suggestion (bandit-gated by Pip's brain) ────────
function maybeOfferTemplate(jobs) {
  if (isSnoozed()) return;
  const offered = parseInt(localStorage.getItem(LS_KEYS.templateOffered) || "0", 10);
  if (Date.now() - offered < 6 * 60 * 60 * 1000) return;

  const titles = loadRecentTitles().map((x) => x.t);
  if (titles.length < TEMPLATE_SUGGEST_THRESHOLD) return;

  const sample = titles.slice(0, 6).map((t) => t.toLowerCase());
  const candidate = findSharedSubstring(sample, 4);
  if (!candidate) return;
  const matches = sample.filter((t) => t.includes(candidate)).length;
  if (matches < TEMPLATE_SUGGEST_THRESHOLD) return;

  // Brain gate — if the user has dismissed too many template suggestions
  // recently, the bandit downweights this type and we skip. Cold-start
  // and the ε-floor guarantee we still fire occasionally.
  let shouldFire = true;
  let suggestionId = null;
  try {
    if (typeof brain.shouldFireSuggestion === "function") {
      shouldFire = !!brain.shouldFireSuggestion("template");
    }
  } catch (_) { /* fall through to default-fire */ }
  if (!shouldFire) return;

  try {
    if (typeof brain.recordSuggestion === "function") {
      suggestionId = brain.recordSuggestion("template");
    }
  } catch (_) { /* swallow */ }

  localStorage.setItem(LS_KEYS.templateOffered, String(Date.now()));
  bubble(
    `I noticed "${candidate}" keeps coming up — want this saved as a template?`,
    "curious",
    8000,
  );

  // Best-effort outcome: record a 'timeout' if no follow-up bubble click
  // happens before the bubble auto-hides. (The bubble doesn't surface a
  // Yes/No today; this is the dismissed-by-default path until that lands.)
  if (suggestionId) {
    setTimeout(() => {
      try {
        if (typeof brain.recordSuggestionOutcome === "function") {
          brain.recordSuggestionOutcome(suggestionId, "timeout");
        }
      } catch (_) { /* swallow */ }
    }, 9000);
  }
}

function findSharedSubstring(strs, minLen) {
  if (strs.length < 2) return null;
  const a = strs[0];
  let best = null;
  for (let len = Math.min(40, a.length); len >= minLen; len--) {
    for (let i = 0; i + len <= a.length; i++) {
      const sub = a.slice(i, i + len).trim();
      if (sub.length < minLen) continue;
      if (/^[\s\-_:]+$/.test(sub)) continue;
      let hits = 0;
      for (const s of strs) if (s.includes(sub)) hits++;
      if (hits >= TEMPLATE_SUGGEST_THRESHOLD) {
        if (!best || sub.length > best.length) best = sub;
      }
    }
    if (best) return best;
  }
  return best;
}

// ── Speech bubble ──────────────────────────────────────────────────────────
function maybeBubble(mood) {
  if (isSnoozed()) return;
  const last = parseInt(localStorage.getItem(LS_KEYS.lastBubbleAt) || "0", 10);
  if (Date.now() - last < BUBBLE_COOLDOWN_MS) return;
  const ctx = buildQuipCtx(mood);
  const text = requestQuip(mood, ctx, swapBubbleText);
  bubble(text, mood, 5000);
}

function buildQuipCtx(mood) {
  const sig = readSignals();
  const titles = loadRecentTitles().slice(0, 3).map((x) => x.t);
  return {
    mood,
    queue: sig.queue,
    workers: sig.workers,
    cpu: sig.cpu,
    energy: Math.round(STATE.energy),
    hour: new Date().getHours(),
    last_titles: titles,
  };
}

function swapBubbleText(text) {
  const root = document.getElementById("lmMascotBubble");
  if (!root) return;
  if (!root.classList.contains("is-visible")) return;
  root.textContent = String(text).slice(0, 240);
}

export function bubble(text, mood = "neutral", ms = 5000) {
  if (isSnoozed()) return;
  const root = document.getElementById("lmMascotBubble");
  if (!root) return;
  localStorage.setItem(LS_KEYS.lastBubbleAt, String(Date.now()));
  root.innerHTML = escapeHtml(text);
  root.dataset.mood = mood;
  root.classList.add("is-visible");
  if (STATE.bubbleTimer) clearTimeout(STATE.bubbleTimer);
  STATE.bubbleTimer = setTimeout(() => root.classList.remove("is-visible"), ms);
}

// ── Pip-initiated jobs ─────────────────────────────────────────────────────
const LOOKUP_TOPICS = [
  "deep-sea biology",
  "obscure chess endgames",
  "paper engineering and pop-up books",
  "the history of typography",
  "lost programming languages",
  "unusual units of measurement",
  "weird musical instruments",
  "cartography mistakes that became real places",
  "how submarines navigate without GPS",
  "the science of how cats land on their feet",
  "bizarre mathematical conjectures",
  "the linguistics of constructed languages",
];
const LEARN_TOPICS = [
  "tying a bowline knot you'll actually remember",
  "reading a basic chess opening",
  "the touch-typing home row in 10 minutes",
  "writing a one-page personal mission statement",
  "keyboard shortcuts in your code editor",
  "a 5-move morning mobility routine",
  "one Excel/Sheets formula that pays for itself weekly",
  "the basics of color theory for non-designers",
  "how to make pour-over coffee that doesn't taste burnt",
  "a beginner box-breathing technique for stress",
];

function _pickTopic(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function _runResearchLane(laneKey) {
  const root = document.getElementById("lmMascotBubble");
  if (root) bubble("hunting a paper for LocalMind…", "curious", 4000);

  fetch(`${API}/api/research/lane`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lane: laneKey || "random" }),
  })
    .then((r) => (r.ok ? r.json() : r.json().then((j) => Promise.reject(new Error(j.detail || `HTTP ${r.status}`)))))
    .then((data) => {
      const paperTitle = (data?.paper?.title || "a paper").slice(0, 80);
      bubble(`reading "${paperTitle}" for ${data?.lane_label}…`, "working", 5500);
      import("./nav_rail.js").then((m) => m.switchNav?.("jobs")).catch(() => {});
      if (data?.job_id) {
        import("./job_detail.js").then((m) => m.openJobDetail?.(data.job_id)).catch(() => {});
      }
    })
    .catch((e) => {
      console.warn("[mascot] research lane failed:", e);
      bubble(`hmm — ${String(e.message || e).slice(0, 120)}`, "sad", 6000);
    });
}

function _runMascotJob(kind) {
  let title, description;
  if (kind === "lookup") {
    const topic = _pickTopic(LOOKUP_TOPICS);
    title = `Pip: Look up ${topic}`;
    description = `Find one genuinely surprising fact about "${topic}". Output: a 3–4 sentence summary in plain language, plus the source URL. Use web_search if available. Keep it punchy — no hedging, no padding.`;
  } else if (kind === "learn") {
    const topic = _pickTopic(LEARN_TOPICS);
    title = `Pip: Teach me ${topic}`;
    description = `Produce a tight 10-minute primer on "${topic}". Output sections: WHAT (one sentence), WHY (one sentence), QUICK START (5 numbered steps), ONE PRACTICE EXERCISE (one sentence), and ONE LINK to a high-quality free resource. Use web_search if needed. Aim for under 250 words total.`;
  } else {
    return;
  }

  const root = document.getElementById("lmMascotBubble");
  if (root) bubble("on it…", "curious", 3500);

  fetch(`${API}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, description, mode: "quick" }),
  })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((job) => {
      bubble("queued — I'll ping you when it's ready.", "happy", 4500);
      // Hand off: open the new job's detail overlay on completion. The
      // existing job-event polling will fire 'event:job-done' which we use
      // to celebrate. For the immediate jump, switch to Jobs tab so the
      // user can watch progress.
      import("./nav_rail.js").then((m) => m.switchNav?.("jobs")).catch(() => {});
      if (job?.id) {
        import("./job_detail.js").then((m) => m.openJobDetail?.(job.id)).catch(() => {});
      }
    })
    .catch((e) => {
      console.warn("[mascot] job submit failed:", e);
      bubble("hmm — couldn't queue that. Backend offline?", "sad", 5000);
    });
}

// ── Render ─────────────────────────────────────────────────────────────────
function ensureMounted() {
  if (document.getElementById("lmMascot")) return;
  const wrap = document.createElement("div");
  wrap.id = "lmMascot";
  wrap.className = "lm-mascot";
  wrap.dataset.mood = "neutral";
  wrap.setAttribute("role", "img");
  wrap.setAttribute("aria-label", "LocalMind mascot");
  wrap.innerHTML = `
    <div class="lm-mascot__bubble" id="lmMascotBubble" aria-live="polite"></div>
    <button class="lm-mascot__body" id="lmMascotBody" type="button" aria-label="LocalMind mascot — click to interact">
      <svg class="lm-mascot__face" viewBox="0 0 64 64" aria-hidden="true">
        <defs>
          <radialGradient id="lmMascotGlow" cx="50%" cy="40%" r="60%">
            <stop offset="0%" stop-color="#7A7CF3" stop-opacity="0.55"/>
            <stop offset="100%" stop-color="#6366F1" stop-opacity="0"/>
          </radialGradient>
        </defs>
        <circle class="lm-mascot__halo" cx="32" cy="32" r="28" fill="url(#lmMascotGlow)"/>
        <rect class="lm-mascot__head" x="10" y="10" width="44" height="44" rx="11" ry="11"/>
        <circle class="lm-mascot__antenna-tip" cx="32" cy="6" r="2.4"/>
        <line class="lm-mascot__antenna" x1="32" y1="10" x2="32" y2="8"/>
        <g class="lm-mascot__eyes">
          <circle class="lm-mascot__eye lm-mascot__eye--l" cx="24" cy="30" r="3.4"/>
          <circle class="lm-mascot__eye lm-mascot__eye--r" cx="40" cy="30" r="3.4"/>
        </g>
        <path class="lm-mascot__mouth" d="M24 42 Q32 47 40 42"/>
      </svg>
      <div class="lm-mascot__energy" aria-hidden="true">
        <div class="lm-mascot__energy-fill" id="lmMascotEnergyFill"></div>
      </div>
    </button>
    <div class="lm-mascot__menu" id="lmMascotMenu" hidden>
      <div class="lm-mascot__menu-section">Make LocalMind smarter</div>
      <button type="button" data-action="lane:random">
        <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
        Read a paper · any lane
      </button>
      <button type="button" data-action="lane:agentic">
        <span class="material-symbols-outlined" aria-hidden="true">smart_toy</span>
        Agentic patterns
      </button>
      <button type="button" data-action="lane:local_llm">
        <span class="material-symbols-outlined" aria-hidden="true">memory</span>
        Local LLM ops
      </button>
      <button type="button" data-action="lane:memory_rag">
        <span class="material-symbols-outlined" aria-hidden="true">hub</span>
        Memory &amp; RAG
      </button>
      <button type="button" data-action="lane:evaluation">
        <span class="material-symbols-outlined" aria-hidden="true">fact_check</span>
        Evaluation
      </button>
      <div class="lm-mascot__menu-divider"></div>
      <div class="lm-mascot__menu-section">Casual</div>
      <button type="button" data-action="lookup">
        <span class="material-symbols-outlined" aria-hidden="true">travel_explore</span>
        Look up something cool
      </button>
      <button type="button" data-action="learn">
        <span class="material-symbols-outlined" aria-hidden="true">school</span>
        Teach me a new skill
      </button>
      <div class="lm-mascot__menu-divider"></div>
      <button type="button" data-action="snooze-1h">Snooze 1h</button>
      <button type="button" data-action="snooze-8h">Snooze 8h</button>
      <button type="button" data-action="hide">Hide mascot</button>
    </div>
  `;
  document.body.appendChild(wrap);

  const body = wrap.querySelector("#lmMascotBody");
  const menu = wrap.querySelector("#lmMascotMenu");
  body.addEventListener("click", (e) => {
    e.preventDefault();
    // Shift/alt-click → casual poke (random quip). Plain click → menu.
    if (e.shiftKey || e.altKey) {
      poke();
      return;
    }
    menu.hidden = !menu.hidden;
  });
  body.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    menu.hidden = !menu.hidden;
  });
  menu.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const a = btn.dataset.action;
    if (a === "snooze-1h") snoozeFor(60 * 60_000);
    else if (a === "snooze-8h") snoozeFor(8 * 60 * 60_000);
    else if (a === "hide") {
      localStorage.setItem(LS_KEYS.hidden, "1");
      wrap.style.display = "none";
    } else if (a === "lookup")   _runMascotJob("lookup");
    else if (a === "learn")     _runMascotJob("learn");
    else if (a && a.startsWith("lane:")) _runResearchLane(a.slice(5));
    menu.hidden = true;
  });

  document.addEventListener("click", (e) => {
    if (!wrap.contains(e.target)) menu.hidden = true;
  });
}

function poke() {
  STATE.energy = Math.min(100, STATE.energy + 4);
  saveEnergy();
  const moods = ["curious", "happy", "sleepy", "excited", "working"];
  const sig = readSignals();
  let mood;
  if (sig.workers > 0 || sig.queue > 0) mood = "working";
  else if (STATE.energy < 20) mood = "sleepy";
  else mood = moods[Math.floor(Math.random() * moods.length)];
  const ctx = buildQuipCtx(mood);
  const text = requestQuip(mood, ctx, swapBubbleText);
  bubble(text, mood, 4000);
}

function render() {
  const wrap = document.getElementById("lmMascot");
  if (!wrap) return;
  if (localStorage.getItem(LS_KEYS.hidden) === "1") {
    wrap.style.display = "none";
    return;
  }
  const sig = readSignals();
  emit("signals", sig);
  const mood = resolveMood(sig);
  if (mood !== STATE.mood) {
    emit("mood-change", { from: STATE.mood, to: mood });
  }
  STATE.mood = mood;
  wrap.dataset.mood = mood;
  wrap.dataset.snoozed = isSnoozed() ? "1" : "0";

  const fill = document.getElementById("lmMascotEnergyFill");
  if (fill) {
    const pct = Math.max(0, Math.min(100, STATE.energy));
    fill.style.width = `${pct}%`;
    fill.dataset.level =
      pct < 20 ? "low" : pct < 60 ? "mid" : "high";
  }
}

// ── Boot ───────────────────────────────────────────────────────────────────
export function initMascot() {
  requestAnimationFrame(() => {
    ensureMounted();
    STATE.energy = loadEnergy();
    STATE.lastTickAt = Date.now();
    STATE.lastJobActivityAt = Date.now();

    pollJobs().finally(() => {
      STATE.initialized = true;
      render();
    });

    setInterval(() => {
      tickEnergy();
      render();
      emit("tick");
    }, TICK_MS);

    setInterval(() => { pollJobs(); }, POLL_MS);

    // Adaptive thresholds — Pip's brain learns the user's typical inter-job
    // gap and shifts what counts as "sleepy" / "bored" accordingly. Sync
    // once a minute (computeAdaptiveThresholds() returns defaults when the
    // user has fewer than 5 samples).
    setInterval(() => {
      try {
        if (typeof brain.computeAdaptiveThresholds === "function") {
          const t = brain.computeAdaptiveThresholds();
          if (t && Number.isFinite(t.sleepy_ms)) thresholds.sleepy_ms = t.sleepy_ms;
          if (t && Number.isFinite(t.bored_ms))  thresholds.bored_ms  = t.bored_ms;
        }
      } catch (_) { /* swallow — keep defaults */ }
    }, 60_000);

    if (!localStorage.getItem(LS_KEYS.energy)) {
      setTimeout(() => bubble("hi! I'm Pip. I'll keep an eye on things.", "curious", 6000), 1500);
    }
  });
}
