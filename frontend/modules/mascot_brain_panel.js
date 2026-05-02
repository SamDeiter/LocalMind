// Mascot brain panel — read-only inspector for Pip's on-device learning state.
//
// Slotted into the Settings page. The Settings page completely re-renders on
// every tab visit, so we MUST scope every listener to the rootEl passed into
// bindMascotSection() — never to `document`. We also do NOT poll or auto-
// refresh: a single render call must produce a complete, statically-correct
// DOM. The user gets a fresh snapshot the next time they visit the tab.

import { snapshot, reset } from "./mascot_brain.js";
import { getState } from "./mascot.js";
import { escapeHtml } from "./utils.js";

// ---------- helpers ----------

function safeSnapshot() {
  try {
    const snap = snapshot();
    return snap && typeof snap === "object" ? snap : null;
  } catch (_err) {
    return null;
  }
}

function safeMascotState() {
  try {
    const s = getState();
    return s && typeof s === "object" ? s : null;
  } catch (_err) {
    return null;
  }
}

function msToMinutes(ms) {
  if (typeof ms !== "number" || !isFinite(ms) || ms <= 0) return null;
  return ms / 60000;
}

function fmtMinutes(ms) {
  const m = msToMinutes(ms);
  if (m === null) return "—";
  return `${m.toFixed(1)} min`;
}

function fmtPct(value) {
  if (typeof value !== "number" || !isFinite(value)) return "—";
  // Accept either 0–1 or 0–100; normalize anything <= 1 as a fraction.
  const pct = value <= 1 ? value * 100 : value;
  return `${Math.round(pct)}%`;
}

function fmtNum(n, fallback = "0") {
  if (typeof n !== "number" || !isFinite(n)) return fallback;
  return String(Math.round(n));
}

function fmt1(n) {
  if (typeof n !== "number" || !isFinite(n)) return "—";
  return n.toFixed(1);
}

function fmtTimeHM(ts) {
  if (typeof ts !== "number" || !isFinite(ts) || ts <= 0) return "—";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return "—";
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${hh}:${mm}`;
  } catch (_err) {
    return "—";
  }
}

function pad2(n) {
  const s = String(n);
  return s.length < 2 ? "0" + s : s;
}

// ---------- section renderers ----------

function renderHeader() {
  return `
    <div class="lm-settings__section-header">
      <div class="lm-settings__section-title">Mascot — Pip's Brain</div>
      <div class="lm-settings__section-subtitle">What Pip has learned about your usage. All on-device, never leaves your machine.</div>
    </div>
  `;
}

function renderLiveSnapshot(snap, mascotState) {
  const mood = mascotState && typeof mascotState.mood === "string" ? mascotState.mood : "—";
  const energyRaw = mascotState && typeof mascotState.energy === "number" ? mascotState.energy : null;
  const energyPct = energyRaw === null
    ? "—"
    : `${Math.round(energyRaw <= 1 ? energyRaw * 100 : energyRaw)}%`;
  const snoozed = mascotState && mascotState.snoozed ? "Yes" : "No";

  const sleepyMs = snap && typeof snap.sleepy_ms === "number" ? snap.sleepy_ms : null;
  const boredMs = snap && typeof snap.bored_ms === "number" ? snap.bored_ms : null;

  const gaps = (snap && snap.gaps_minutes && typeof snap.gaps_minutes === "object") ? snap.gaps_minutes : {};
  const p50 = typeof gaps.p50 === "number" ? gaps.p50 : null;
  const p90 = typeof gaps.p90 === "number" ? gaps.p90 : null;
  const sampleCount = typeof gaps.count === "number"
    ? gaps.count
    : (typeof gaps.samples === "number" ? gaps.samples : (typeof gaps.n === "number" ? gaps.n : 0));

  const totals = (snap && snap.totals && typeof snap.totals === "object") ? snap.totals : {};
  const seen = totals.jobs_seen != null ? totals.jobs_seen : (totals.seen != null ? totals.seen : 0);
  const done = totals.jobs_done != null ? totals.jobs_done : (totals.done != null ? totals.done : 0);
  const failed = totals.jobs_failed != null ? totals.jobs_failed : (totals.failed != null ? totals.failed : 0);

  return `
    <div class="lm-settings__form">
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Current mood</div>
        <div class="lm-settings__row-control">
          <span class="lm-chip lm-chip--static">${escapeHtml(String(mood))}</span>
        </div>
      </div>
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Energy</div>
        <div class="lm-settings__row-control">
          <span class="lm-mono">${escapeHtml(energyPct)}</span>
        </div>
      </div>
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Snoozed</div>
        <div class="lm-settings__row-control">
          <span class="lm-mono">${escapeHtml(snoozed)}</span>
        </div>
      </div>
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Adaptive thresholds</div>
        <div class="lm-settings__row-control">
          <span class="lm-mute">sleepy</span>
          <span class="lm-mono">${escapeHtml(fmtMinutes(sleepyMs))}</span>
          <span class="lm-mute"> · bored</span>
          <span class="lm-mono">${escapeHtml(fmtMinutes(boredMs))}</span>
        </div>
      </div>
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Inter-job gap</div>
        <div class="lm-settings__row-control">
          <span class="lm-mute">p50</span>
          <span class="lm-mono">${escapeHtml(fmt1(p50))} min</span>
          <span class="lm-mute"> · p90</span>
          <span class="lm-mono">${escapeHtml(fmt1(p90))} min</span>
          <span class="lm-mute"> · samples</span>
          <span class="lm-mono">${escapeHtml(fmtNum(sampleCount))}</span>
        </div>
      </div>
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Totals</div>
        <div class="lm-settings__row-control">
          <span class="lm-mute">seen</span>
          <span class="lm-mono">${escapeHtml(fmtNum(seen))}</span>
          <span class="lm-mute"> · done</span>
          <span class="lm-mono">${escapeHtml(fmtNum(done))}</span>
          <span class="lm-mute"> · failed</span>
          <span class="lm-mono">${escapeHtml(fmtNum(failed))}</span>
        </div>
      </div>
    </div>
  `;
}

function renderHourlyActivity(snap) {
  const top = (snap && Array.isArray(snap.hist_top_hours)) ? snap.hist_top_hours : [];
  if (top.length === 0) {
    return `
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Hourly activity</div>
        <div class="lm-settings__row-control">
          <span class="lm-mute">No activity yet</span>
        </div>
      </div>
    `;
  }
  const pills = top.slice(0, 3).map((entry) => {
    let hour = null;
    let weight = null;
    if (entry && typeof entry === "object") {
      hour = typeof entry.hour === "number" ? entry.hour : null;
      weight = typeof entry.weight === "number"
        ? entry.weight
        : (typeof entry.score === "number" ? entry.score : (typeof entry.value === "number" ? entry.value : null));
    } else if (Array.isArray(entry) && entry.length >= 2) {
      hour = typeof entry[0] === "number" ? entry[0] : null;
      weight = typeof entry[1] === "number" ? entry[1] : null;
    }
    const label = hour === null ? "—" : `${pad2(hour)}:00`;
    const w = weight === null ? "—" : weight.toFixed(2);
    return `<span class="lm-chip lm-chip--static">${escapeHtml(label)} · ${escapeHtml(w)}</span>`;
  }).join(" ");
  return `
    <div class="lm-settings__row">
      <div class="lm-settings__row-label">Hourly activity</div>
      <div class="lm-settings__row-control">${pills}</div>
    </div>
  `;
}

function renderBanditWinrates(snap) {
  const rates = (snap && snap.bandit_winrates && typeof snap.bandit_winrates === "object")
    ? snap.bandit_winrates
    : null;
  const entries = rates ? Object.entries(rates) : [];
  if (entries.length === 0) {
    return `
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Suggestion win-rates</div>
        <div class="lm-settings__row-control">
          <span class="lm-mute">No suggestions tracked yet</span>
        </div>
      </div>
    `;
  }
  const rows = entries.map(([type, info]) => {
    const obj = info && typeof info === "object" ? info : {};
    const winRate = typeof obj.win_rate === "number"
      ? obj.win_rate
      : (typeof obj.rate === "number" ? obj.rate : null);
    const acted = typeof obj.acted === "number" ? obj.acted : 0;
    const dismissed = typeof obj.dismissed === "number" ? obj.dismissed : 0;
    const timeout = typeof obj.timeout === "number"
      ? obj.timeout
      : (typeof obj.timed_out === "number" ? obj.timed_out : 0);
    const pctText = winRate === null ? "—" : fmtPct(winRate);
    return `
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">${escapeHtml(String(type))}</div>
        <div class="lm-settings__row-control">
          <span class="lm-mono">${escapeHtml(pctText)}</span>
          <span class="lm-mute"> (acted ${escapeHtml(fmtNum(acted))} · dismissed ${escapeHtml(fmtNum(dismissed))} · timeout ${escapeHtml(fmtNum(timeout))})</span>
        </div>
      </div>
    `;
  }).join("");
  return rows;
}

function renderEventRow(ev) {
  if (!ev || typeof ev !== "object") return "";
  const ts = typeof ev.ts === "number" ? ev.ts : (typeof ev.t === "number" ? ev.t : (typeof ev.time === "number" ? ev.time : 0));
  const type = typeof ev.type === "string" ? ev.type : (typeof ev.kind === "string" ? ev.kind : "event");
  const title = typeof ev.title === "string" && ev.title.length > 0 ? ev.title : null;
  const time = fmtTimeHM(ts);
  const tail = title ? ` · ${escapeHtml(title)}` : "";
  return `<div class="lm-mono">${escapeHtml(time)} · ${escapeHtml(type)}${tail}</div>`;
}

function renderRecentEvents(snap) {
  const events = (snap && Array.isArray(snap.recent_events)) ? snap.recent_events : [];
  if (events.length === 0) {
    return `
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Recent events</div>
        <div class="lm-settings__row-control">
          <span class="lm-mute">No events recorded yet</span>
        </div>
      </div>
    `;
  }
  // Most recent first if input isn't already.
  const sorted = events.slice().sort((a, b) => {
    const ta = (a && typeof a.ts === "number") ? a.ts : 0;
    const tb = (b && typeof b.ts === "number") ? b.ts : 0;
    return tb - ta;
  });
  const top5 = sorted.slice(0, 5).map(renderEventRow).join("");
  const full = sorted.slice(0, 50).map(renderEventRow).join("");
  return `
    <div class="lm-settings__row">
      <div class="lm-settings__row-label">Recent events</div>
      <div class="lm-settings__row-control">
        ${top5}
        <details>
          <summary><span class="lm-mute">Show full event log (last 50)</span></summary>
          ${full}
        </details>
      </div>
    </div>
  `;
}

function renderActions() {
  return `
    <div class="lm-settings__actions">
      <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" data-action="reset">Reset Pip's brain</button>
      <span class="lm-mute">Read-only inspector. Stats refresh next time you open this tab.</span>
    </div>
  `;
}

function renderError(message) {
  return `
    <div class="lm-settings__form">
      <div class="lm-settings__row">
        <div class="lm-settings__row-label">Status</div>
        <div class="lm-settings__row-control">
          <span class="lm-mute">${escapeHtml(message)}</span>
        </div>
      </div>
    </div>
  `;
}

// ---------- public API ----------

export function renderMascotSection() {
  let body = "";
  try {
    const snap = safeSnapshot();
    const mascotState = safeMascotState();
    const liveBlock = renderLiveSnapshot(snap, mascotState);
    const hourly = renderHourlyActivity(snap);
    const bandit = renderBanditWinrates(snap);
    const events = renderRecentEvents(snap);
    body = `
      ${liveBlock}
      <div class="lm-settings__form">
        ${hourly}
        ${bandit}
        ${events}
      </div>
      ${renderActions()}
    `;
  } catch (_err) {
    body = `
      ${renderError("Pip's brain is unavailable right now. Try again next time you open this tab.")}
      ${renderActions()}
    `;
  }
  return `
    <section class="lm-settings__section" id="settings-mascot" data-section="mascot">
      ${renderHeader()}
      ${body}
    </section>
  `;
}

export function bindMascotSection(rootEl) {
  if (!rootEl || typeof rootEl.querySelector !== "function") return;
  const resetBtn = rootEl.querySelector('[data-action="reset"]');
  if (resetBtn) {
    resetBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      let confirmed = false;
      try {
        confirmed = window.confirm("Reset Pip's brain? Histogram, bandit stats, and event log will be cleared.");
      } catch (_err) {
        confirmed = false;
      }
      if (!confirmed) return;
      try {
        reset();
      } catch (_err) {
        // Swallow — we still re-render to reflect whatever state survived.
      }
      try {
        rootEl.innerHTML = renderMascotSection();
        bindMascotSection(rootEl);
      } catch (_err) {
        // If re-render fails, leave DOM as-is rather than throwing into the caller.
      }
    });
  }
}
