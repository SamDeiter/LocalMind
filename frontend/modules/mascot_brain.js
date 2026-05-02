// frontend/modules/mascot_brain.js
//
// On-device behavior profile, multi-armed bandit, and adaptive thresholds
// for the LocalMind mascot ("Pip"). Pure ESM, no DOM, no Node.
//
// Public surface (consumed by mascot.js + a panel renderer):
//   recordEvent(type, payload)
//   recordSuggestion(type) -> id
//   recordSuggestionOutcome(id, outcome)
//   shouldFireSuggestion(type) -> bool
//   computeAdaptiveThresholds() -> {sleepy_ms, bored_ms}
//   snapshot() -> object  (contract: see SNAPSHOT_SHAPE below)
//   reset()
//   init()

import { on as mascotOn, getState as mascotGetState } from "./mascot.js";

// -----------------------------------------------------------------------------
// Constants & defaults
// -----------------------------------------------------------------------------

const STATE_VERSION = 1;
const LS_STATE_KEY = "lm.mascot.brain.state";
const LS_EVENTS_KEY = "lm.mascot.brain.events";
const LS_FLUSH_KEY = "lm.mascot.brain.last_flush_at";

const RECENT_EVENTS_MAX = 50;
const GAPS_MAX = 50;
const OUTCOMES_MAX = 200;
const FLUSH_MIN_INTERVAL_MS = 30 * 1000;

const DEFAULT_THRESHOLDS = {
  sleepy_ms: 5 * 60 * 1000,
  bored_ms: 15 * 60 * 1000,
};
const SLEEPY_MIN_MS = 2 * 60 * 1000;
const SLEEPY_MAX_MS = 30 * 60 * 1000;
const BORED_MIN_MS = 10 * 60 * 1000;
const BORED_MAX_MS = 90 * 60 * 1000;

const HISTOGRAM_DECAY_PER_DAY = 0.98;
const HISTOGRAM_EWMA_ALPHA = 0.9; // bucket = bucket * 0.9 + 1.0

const BANDIT_HALFLIFE_DAYS = 14;
const BANDIT_COLD_START_THRESHOLD = 3;
const BANDIT_EPSILON_FLOOR = 0.05;
const TIMEOUT_AS_DISMISS_WEIGHT = 0.5;

const STRING_TRIM_MAX = 240;

// -----------------------------------------------------------------------------
// In-memory state
// -----------------------------------------------------------------------------

function makeDefaultState() {
  return {
    state_version: STATE_VERSION,
    hist: new Array(24).fill(0),
    hist_last_decay_at: Date.now(),
    gaps: [], // array of gap minutes (numbers), bounded GAPS_MAX
    last_activity_at: 0, // ms timestamp of previous job-* event for gap calc
    bandit: {}, // type -> {shown, last_shown_at, outcomes: [{at, outcome}]}
    pending_suggestions: {}, // id -> {type, at}
    totals: { jobs_seen: 0, jobs_done: 0, jobs_failed: 0 },
  };
}

let state = makeDefaultState();
let recentEvents = []; // newest first
let lastFlushAt = 0;

// -----------------------------------------------------------------------------
// Tiny utilities (defensive)
// -----------------------------------------------------------------------------

function safeNow() {
  try {
    return Date.now();
  } catch (_) {
    return 0;
  }
}

function clamp(x, lo, hi) {
  if (!Number.isFinite(x)) return lo;
  if (x < lo) return lo;
  if (x > hi) return hi;
  return x;
}

function trimString(s) {
  if (typeof s !== "string") return s;
  if (s.length <= STRING_TRIM_MAX) return s;
  return s.slice(0, STRING_TRIM_MAX);
}

function trimPayloadStrings(obj, depth) {
  if (depth > 3) return obj;
  if (obj == null) return obj;
  if (typeof obj === "string") return trimString(obj);
  if (Array.isArray(obj)) {
    return obj.map((v) => trimPayloadStrings(v, depth + 1));
  }
  if (typeof obj === "object") {
    const out = {};
    for (const k of Object.keys(obj)) {
      try {
        out[k] = trimPayloadStrings(obj[k], depth + 1);
      } catch (_) {
        // skip
      }
    }
    return out;
  }
  return obj;
}

function uuidish() {
  try {
    if (
      typeof crypto !== "undefined" &&
      crypto &&
      typeof crypto.randomUUID === "function"
    ) {
      return crypto.randomUUID();
    }
  } catch (_) {
    // fall through
  }
  // Fallback: timestamp + random hex
  const r1 = Math.floor(Math.random() * 0xffffffff)
    .toString(16)
    .padStart(8, "0");
  const r2 = Math.floor(Math.random() * 0xffffffff)
    .toString(16)
    .padStart(8, "0");
  return `${safeNow().toString(16)}-${r1}-${r2}`;
}

function lsGet(key) {
  try {
    if (typeof localStorage === "undefined") return null;
    return localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function lsSet(key, value) {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(key, value);
  } catch (_) {
    // quota exceeded or unavailable: swallow
  }
}

function lsRemove(key) {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.removeItem(key);
  } catch (_) {
    // swallow
  }
}

// -----------------------------------------------------------------------------
// Persistence
// -----------------------------------------------------------------------------

function loadFromStorage() {
  try {
    const raw = lsGet(LS_STATE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        const def = makeDefaultState();
        state = {
          state_version: STATE_VERSION,
          hist:
            Array.isArray(parsed.hist) && parsed.hist.length === 24
              ? parsed.hist.map((n) => (Number.isFinite(n) ? n : 0))
              : def.hist,
          hist_last_decay_at: Number.isFinite(parsed.hist_last_decay_at)
            ? parsed.hist_last_decay_at
            : safeNow(),
          gaps:
            Array.isArray(parsed.gaps)
              ? parsed.gaps
                  .filter((n) => Number.isFinite(n))
                  .slice(-GAPS_MAX)
              : [],
          last_activity_at: Number.isFinite(parsed.last_activity_at)
            ? parsed.last_activity_at
            : 0,
          bandit:
            parsed.bandit && typeof parsed.bandit === "object"
              ? parsed.bandit
              : {},
          pending_suggestions:
            parsed.pending_suggestions &&
            typeof parsed.pending_suggestions === "object"
              ? parsed.pending_suggestions
              : {},
          totals:
            parsed.totals && typeof parsed.totals === "object"
              ? {
                  jobs_seen: parsed.totals.jobs_seen | 0,
                  jobs_done: parsed.totals.jobs_done | 0,
                  jobs_failed: parsed.totals.jobs_failed | 0,
                }
              : def.totals,
        };
      }
    }
  } catch (_) {
    state = makeDefaultState();
  }

  try {
    const rawEvents = lsGet(LS_EVENTS_KEY);
    if (rawEvents) {
      const parsed = JSON.parse(rawEvents);
      if (Array.isArray(parsed)) {
        recentEvents = parsed.slice(0, RECENT_EVENTS_MAX);
      }
    }
  } catch (_) {
    recentEvents = [];
  }

  try {
    const rawFlush = lsGet(LS_FLUSH_KEY);
    if (rawFlush) {
      const n = Number(rawFlush);
      if (Number.isFinite(n)) lastFlushAt = n;
    }
  } catch (_) {
    lastFlushAt = 0;
  }
}

function flushToStorage(force) {
  try {
    const now = safeNow();
    if (!force && now - lastFlushAt < FLUSH_MIN_INTERVAL_MS) return;
    lastFlushAt = now;

    const blob = {
      state_version: STATE_VERSION,
      hist: state.hist,
      hist_last_decay_at: state.hist_last_decay_at,
      gaps: state.gaps,
      last_activity_at: state.last_activity_at,
      bandit: state.bandit,
      pending_suggestions: state.pending_suggestions,
      totals: state.totals,
    };
    lsSet(LS_STATE_KEY, JSON.stringify(blob));
    lsSet(LS_EVENTS_KEY, JSON.stringify(recentEvents));
    lsSet(LS_FLUSH_KEY, String(now));
  } catch (_) {
    // swallow
  }
}

// -----------------------------------------------------------------------------
// Histogram
// -----------------------------------------------------------------------------

function applyHistogramDecay(now) {
  try {
    const last = state.hist_last_decay_at || now;
    const days = Math.max(0, (now - last) / (24 * 60 * 60 * 1000));
    if (days <= 0) {
      state.hist_last_decay_at = now;
      return;
    }
    const factor = Math.pow(HISTOGRAM_DECAY_PER_DAY, days);
    for (let i = 0; i < 24; i++) {
      const v = state.hist[i] * factor;
      state.hist[i] = Number.isFinite(v) ? v : 0;
    }
    state.hist_last_decay_at = now;
  } catch (_) {
    // swallow
  }
}

function bumpHistogramForNow() {
  try {
    const now = safeNow();
    applyHistogramDecay(now);
    let hour = 0;
    try {
      hour = new Date(now).getHours();
    } catch (_) {
      hour = 0;
    }
    if (hour < 0 || hour > 23) hour = 0;
    state.hist[hour] = state.hist[hour] * HISTOGRAM_EWMA_ALPHA + 1.0;
  } catch (_) {
    // swallow
  }
}

function topHours(limit) {
  try {
    const total = state.hist.reduce(
      (a, b) => a + (Number.isFinite(b) ? b : 0),
      0
    );
    const arr = state.hist.map((v, hour) => ({
      hour,
      raw: Number.isFinite(v) ? v : 0,
    }));
    arr.sort((a, b) => b.raw - a.raw);
    const top = arr.slice(0, limit);
    return top.map((e) => ({
      hour: e.hour,
      weight: total > 0 ? e.raw / total : 0,
    }));
  } catch (_) {
    return [];
  }
}

// -----------------------------------------------------------------------------
// Inter-job gap tracking
// -----------------------------------------------------------------------------

function recordActivityForGap(now) {
  try {
    if (state.last_activity_at && now > state.last_activity_at) {
      const gapMin = (now - state.last_activity_at) / 60000;
      if (Number.isFinite(gapMin) && gapMin >= 0) {
        state.gaps.push(gapMin);
        if (state.gaps.length > GAPS_MAX) {
          state.gaps.splice(0, state.gaps.length - GAPS_MAX);
        }
      }
    }
    state.last_activity_at = now;
  } catch (_) {
    // swallow
  }
}

function percentile(sorted, p) {
  if (!sorted.length) return 0;
  const idx = clamp(Math.floor((sorted.length - 1) * p), 0, sorted.length - 1);
  return sorted[idx];
}

function gapsP() {
  try {
    const samples = state.gaps.length;
    if (samples === 0) return { p50: 0, p90: 0, samples: 0 };
    const sorted = state.gaps.slice().sort((a, b) => a - b);
    return {
      p50: percentile(sorted, 0.5),
      p90: percentile(sorted, 0.9),
      samples,
    };
  } catch (_) {
    return { p50: 0, p90: 0, samples: 0 };
  }
}

// -----------------------------------------------------------------------------
// Bandit math
// -----------------------------------------------------------------------------

function ensureBanditEntry(type) {
  if (!state.bandit[type]) {
    state.bandit[type] = {
      shown: 0,
      last_shown_at: 0,
      outcomes: [],
    };
  }
  const entry = state.bandit[type];
  if (typeof entry.shown !== "number") entry.shown = 0;
  if (typeof entry.last_shown_at !== "number") entry.last_shown_at = 0;
  if (!Array.isArray(entry.outcomes)) entry.outcomes = [];
  return entry;
}

function decayWeight(at, now) {
  const ageMs = Math.max(0, now - at);
  const ageDays = ageMs / (24 * 60 * 60 * 1000);
  return Math.exp(-Math.LN2 * (ageDays / BANDIT_HALFLIFE_DAYS));
}

function decayedCounts(type) {
  const entry = ensureBanditEntry(type);
  const now = safeNow();
  let acted = 0;
  let dismissed = 0;
  let timeoutCount = 0;
  let actedRaw = 0;
  let dismissedRaw = 0;
  let timeoutRaw = 0;
  for (const o of entry.outcomes) {
    if (!o || !Number.isFinite(o.at)) continue;
    const w = decayWeight(o.at, now);
    if (o.outcome === "acted") {
      acted += w;
      actedRaw += 1;
    } else if (o.outcome === "dismissed") {
      dismissed += w;
      dismissedRaw += 1;
    } else if (o.outcome === "timeout") {
      // half a dismiss
      dismissed += w * TIMEOUT_AS_DISMISS_WEIGHT;
      timeoutCount += w;
      timeoutRaw += 1;
    }
  }
  return {
    decayed_acted: acted,
    decayed_dismissed: dismissed,
    decayed_timeout: timeoutCount,
    raw_acted: actedRaw,
    raw_dismissed: dismissedRaw,
    raw_timeout: timeoutRaw,
  };
}

// Gamma(k, 1) sampler for integer-ish shape via Marsaglia & Tsang for k>=1,
// and the (Math.random()^(1/a)) trick can be unstable; use Marsaglia/Tsang.
function gammaSample(k) {
  if (!Number.isFinite(k) || k <= 0) k = 1;
  if (k < 1) {
    // Boost: Gamma(k) = Gamma(k+1) * U^(1/k)
    const u = Math.random();
    return gammaSample(k + 1) * Math.pow(u || 1e-12, 1 / k);
  }
  const d = k - 1 / 3;
  const c = 1 / Math.sqrt(9 * d);
  for (let i = 0; i < 1000; i++) {
    let x;
    let v;
    do {
      // Box-Muller for standard normal
      const u1 = Math.random() || 1e-12;
      const u2 = Math.random();
      x = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
      v = 1 + c * x;
    } while (v <= 0);
    v = v * v * v;
    const u = Math.random();
    if (u < 1 - 0.0331 * x * x * x * x) return d * v;
    if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
  }
  // Fallback (shouldn't hit)
  return d;
}

function betaSample(a, b) {
  try {
    if (!Number.isFinite(a) || a <= 0) a = 1;
    if (!Number.isFinite(b) || b <= 0) b = 1;
    const x = gammaSample(a);
    const y = gammaSample(b);
    const denom = x + y;
    if (denom <= 0 || !Number.isFinite(denom)) return 0.5;
    return x / denom;
  } catch (_) {
    return 0.5;
  }
}

// -----------------------------------------------------------------------------
// Public: recordEvent
// -----------------------------------------------------------------------------

export function recordEvent(type, payload) {
  try {
    const now = safeNow();
    const entry = { at: now, type: trimString(String(type || "unknown")) };
    let safePayload = null;
    try {
      safePayload = trimPayloadStrings(payload, 0);
    } catch (_) {
      safePayload = null;
    }

    // Common shortcut: surface job title at top level for snapshot consumers.
    if (
      safePayload &&
      typeof safePayload === "object" &&
      safePayload.job &&
      typeof safePayload.job === "object" &&
      typeof safePayload.job.title === "string"
    ) {
      entry.title = trimString(safePayload.job.title);
    } else if (safePayload && typeof safePayload.title === "string") {
      entry.title = trimString(safePayload.title);
    }

    if (safePayload != null) entry.payload = safePayload;

    recentEvents.unshift(entry);
    if (recentEvents.length > RECENT_EVENTS_MAX) {
      recentEvents.length = RECENT_EVENTS_MAX;
    }
  } catch (_) {
    // swallow
  }
}

// -----------------------------------------------------------------------------
// Public: recordSuggestion / recordSuggestionOutcome / shouldFireSuggestion
// -----------------------------------------------------------------------------

export function recordSuggestion(type) {
  try {
    const t = String(type || "unknown");
    const entry = ensureBanditEntry(t);
    const id = uuidish();
    const now = safeNow();
    entry.shown = (entry.shown | 0) + 1;
    entry.last_shown_at = now;
    state.pending_suggestions[id] = { type: t, at: now };
    return id;
  } catch (_) {
    return uuidish();
  }
}

export function recordSuggestionOutcome(suggestion_id, outcome) {
  try {
    if (!suggestion_id) return;
    const pending = state.pending_suggestions[suggestion_id];
    if (!pending) return; // unknown id -> no-op
    const valid = ["acted", "dismissed", "timeout"];
    if (!valid.includes(outcome)) return;
    const entry = ensureBanditEntry(pending.type);
    const now = safeNow();
    entry.outcomes.push({ at: now, outcome });
    if (entry.outcomes.length > OUTCOMES_MAX) {
      entry.outcomes.splice(0, entry.outcomes.length - OUTCOMES_MAX);
    }
    delete state.pending_suggestions[suggestion_id];
  } catch (_) {
    // swallow
  }
}

export function shouldFireSuggestion(type) {
  try {
    const t = String(type || "unknown");
    const entry = ensureBanditEntry(t);

    // ε-floor exploration
    if (Math.random() < BANDIT_EPSILON_FLOOR) return true;

    // Cold-start
    if ((entry.shown | 0) < BANDIT_COLD_START_THRESHOLD) return true;

    const { decayed_acted, decayed_dismissed } = decayedCounts(t);
    const a = decayed_acted + 1;
    const b = decayed_dismissed + 1;
    const draw = betaSample(a, b);
    return draw > 0.5;
  } catch (_) {
    return false;
  }
}

// -----------------------------------------------------------------------------
// Public: computeAdaptiveThresholds
// -----------------------------------------------------------------------------

export function computeAdaptiveThresholds() {
  try {
    const samples = state.gaps.length;
    if (samples < 5) {
      return {
        sleepy_ms: DEFAULT_THRESHOLDS.sleepy_ms,
        bored_ms: DEFAULT_THRESHOLDS.bored_ms,
      };
    }
    const sorted = state.gaps.slice().sort((a, b) => a - b);
    const p50 = percentile(sorted, 0.5);
    const p90 = percentile(sorted, 0.9);
    const sleepy_ms = clamp(p50 * 60_000, SLEEPY_MIN_MS, SLEEPY_MAX_MS);
    const bored_ms = clamp(p90 * 60_000, BORED_MIN_MS, BORED_MAX_MS);
    return { sleepy_ms, bored_ms };
  } catch (_) {
    return {
      sleepy_ms: DEFAULT_THRESHOLDS.sleepy_ms,
      bored_ms: DEFAULT_THRESHOLDS.bored_ms,
    };
  }
}

// -----------------------------------------------------------------------------
// Public: snapshot
// -----------------------------------------------------------------------------

export function snapshot() {
  try {
    const gaps = gapsP();
    const thresholds = computeAdaptiveThresholds();
    const bandit_winrates = {};
    for (const type of Object.keys(state.bandit)) {
      const entry = ensureBanditEntry(type);
      let actedRaw = 0;
      let dismissedRaw = 0;
      let timeoutRaw = 0;
      for (const o of entry.outcomes) {
        if (!o) continue;
        if (o.outcome === "acted") actedRaw++;
        else if (o.outcome === "dismissed") dismissedRaw++;
        else if (o.outcome === "timeout") timeoutRaw++;
      }
      const denom = actedRaw + dismissedRaw + timeoutRaw;
      const win_rate = denom > 0 ? actedRaw / denom : 0;
      bandit_winrates[type] = {
        shown: entry.shown | 0,
        acted: actedRaw,
        dismissed: dismissedRaw,
        timeout: timeoutRaw,
        win_rate,
      };
    }

    return {
      state_version: STATE_VERSION,
      hist: state.hist.slice(),
      hist_top_hours: topHours(3),
      gaps_minutes: {
        p50: gaps.p50,
        p90: gaps.p90,
        samples: gaps.samples | 0,
      },
      thresholds,
      bandit_winrates,
      recent_events: recentEvents.slice(0, RECENT_EVENTS_MAX),
      totals: {
        jobs_seen: state.totals.jobs_seen | 0,
        jobs_done: state.totals.jobs_done | 0,
        jobs_failed: state.totals.jobs_failed | 0,
      },
    };
  } catch (_) {
    return {
      state_version: STATE_VERSION,
      hist: new Array(24).fill(0),
      hist_top_hours: [],
      gaps_minutes: { p50: 0, p90: 0, samples: 0 },
      thresholds: {
        sleepy_ms: DEFAULT_THRESHOLDS.sleepy_ms,
        bored_ms: DEFAULT_THRESHOLDS.bored_ms,
      },
      bandit_winrates: {},
      recent_events: [],
      totals: { jobs_seen: 0, jobs_done: 0, jobs_failed: 0 },
    };
  }
}

// -----------------------------------------------------------------------------
// Public: reset
// -----------------------------------------------------------------------------

export function reset() {
  try {
    lsRemove(LS_STATE_KEY);
    lsRemove(LS_EVENTS_KEY);
    lsRemove(LS_FLUSH_KEY);
    state = makeDefaultState();
    recentEvents = [];
    lastFlushAt = 0;
    recordEvent("reset", { at: safeNow() });
    flushToStorage(true);
  } catch (_) {
    // swallow
  }
}

// -----------------------------------------------------------------------------
// Public: init (subscribes to mascot events; idempotent)
// -----------------------------------------------------------------------------

let initialized = false;

export function init() {
  if (initialized) return;
  initialized = true;
  try {
    loadFromStorage();
  } catch (_) {
    // swallow
  }

  const safeOn = (event, handler) => {
    try {
      if (typeof mascotOn === "function") {
        mascotOn(event, (payload) => {
          try {
            handler(payload);
          } catch (_) {
            // swallow listener errors
          }
        });
      }
    } catch (_) {
      // swallow
    }
  };

  safeOn("event:job-new", (payload) => {
    const now = safeNow();
    const job = payload && payload.job ? payload.job : null;
    recordEvent("job-new", payload);
    bumpHistogramForNow();
    recordActivityForGap(now);
    state.totals.jobs_seen = (state.totals.jobs_seen | 0) + 1;
    void job;
  });

  safeOn("event:job-done", (payload) => {
    const now = safeNow();
    recordEvent("job-done", payload);
    recordActivityForGap(now);
    state.totals.jobs_done = (state.totals.jobs_done | 0) + 1;
  });

  safeOn("event:job-fail", (payload) => {
    const now = safeNow();
    recordEvent("job-fail", payload);
    recordActivityForGap(now);
    state.totals.jobs_failed = (state.totals.jobs_failed | 0) + 1;
  });

  safeOn("mood-change", (payload) => {
    recordEvent("mood-change", payload);
  });

  safeOn("signals", (payload) => {
    // Track lightly; do not bloat events. Only record if explicitly snoozing
    // signal arrives — leave generic signals out of the ring buffer.
    void payload;
  });

  safeOn("tick", () => {
    try {
      flushToStorage(false);
    } catch (_) {
      // swallow
    }
  });

  // Initial flush is fine (force=true) to persist any cleanup from load.
  try {
    flushToStorage(true);
  } catch (_) {
    // swallow
  }

  // Reference mascotGetState so the import is alive (also useful for future
  // contextual decisions). Calling defensively without using the result.
  try {
    if (typeof mascotGetState === "function") {
      void mascotGetState();
    }
  } catch (_) {
    // swallow
  }
}

// Auto-init on module load (per spec). MUST NOT throw.
try {
  init();
} catch (_) {
  // swallow
}
