/**
 * "Pip's research" panel — sits inside the Knowledge tab.
 *
 * Two surfaces in one card:
 *   1. Overnight research scheduler toggle (Off / every Nh).
 *   2. Triage list of LocalMind change candidates (lane, change, hook, why, source).
 *
 * Self-contained: builds its own DOM under a host element, fetches its own
 * data, and re-renders on every visit (Settings/Knowledge re-init on switch).
 */

import { API } from "./state.js";
import { escapeHtml } from "./utils.js";

const STATUSES = ["new", "accepted", "rejected", "implemented"];
const STATUS_LABEL = {
  new:         "New",
  accepted:    "Accepted",
  rejected:    "Rejected",
  implemented: "Implemented",
};

let _state = {
  candidates: [],
  countsByStatus: {},
  filter: "new",
  scheduler: null,
};

export function mountResearchPanel(host) {
  if (!host) return;
  host.innerHTML = `
    <section class="lm-research-panel" aria-label="Pip's research">
      <header class="lm-research-panel__head">
        <div class="lm-research-panel__title-group">
          <h2 class="lm-research-panel__title">
            <span class="material-symbols-outlined" aria-hidden="true">smart_toy</span>
            Pip's research
          </h2>
          <p class="lm-research-panel__subtitle" id="researchPanelSubtitle">Loading…</p>
        </div>
        <div class="lm-research-panel__sched" id="researchSchedBox">
          <span class="lm-mute lm-fs-12">Auto-research:</span>
          <label class="lm-research-panel__toggle">
            <input type="checkbox" id="researchSchedEnabled"/>
            <span>Enabled</span>
          </label>
          <select id="researchSchedInterval" aria-label="Interval">
            <option value="2">every 2h</option>
            <option value="4">every 4h</option>
            <option value="8" selected>every 8h</option>
            <option value="12">every 12h</option>
            <option value="24">every 24h</option>
          </select>
          <select id="researchSchedLane" aria-label="Lane">
            <option value="random">Random lane</option>
            <option value="agentic">Agentic</option>
            <option value="local_llm">Local LLM</option>
            <option value="memory_rag">Memory &amp; RAG</option>
            <option value="evaluation">Evaluation</option>
          </select>
          <button type="button" id="researchSchedRunNow" class="lm-btn lm-btn--ghost lm-btn--sm">
            Run now
          </button>
        </div>
      </header>

      <div class="lm-research-panel__filter-row" role="tablist" aria-label="Status filter">
        ${STATUSES.map((s) => `
          <button type="button" role="tab" class="lm-chip" data-status-filter="${s}">
            ${escapeHtml(STATUS_LABEL[s])} <span class="lm-research-panel__filter-count" data-status-count="${s}">0</span>
          </button>
        `).join("")}
      </div>

      <ul class="lm-research-panel__list" id="researchCandidates"></ul>
      <div class="lm-research-panel__empty lm-mute" id="researchCandidatesEmpty" hidden>
        No change candidates yet. Click Pip → "Make LocalMind smarter" or
        enable auto-research above.
      </div>

      <details class="lm-research-panel__brain">
        <summary>Pip's brain — what I've learned about your usage</summary>
        <div id="researchPipBrainHost"></div>
      </details>
    </section>
  `;

  _bind(host);
  _refresh();
  _mountBrain(host);
}

function _mountBrain(host) {
  const slot = host.querySelector("#researchPipBrainHost");
  if (!slot) return;
  // Lazy-load the brain panel renderer so the Knowledge tab boots fast.
  Promise.all([
    import("./mascot_brain_panel.js"),
  ])
    .then(([panel]) => {
      try {
        slot.innerHTML = panel.renderMascotSection();
        const inner = slot.querySelector("#settings-mascot");
        if (inner && typeof panel.bindMascotSection === "function") {
          panel.bindMascotSection(inner);
        }
      } catch (e) {
        console.warn("[research_panel] brain mount failed:", e);
        slot.innerHTML = `<div class="lm-mute lm-fs-12">Brain inspector unavailable.</div>`;
      }
    })
    .catch((err) => {
      console.warn("[research_panel] brain panel import failed:", err);
    });
}

function _bind(host) {
  host.querySelector("#researchSchedRunNow")?.addEventListener("click", async () => {
    const btn = host.querySelector("#researchSchedRunNow");
    btn.disabled = true;
    btn.textContent = "Running…";
    try {
      const r = await fetch(`${API}/api/research/scheduler/run-now`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await r.json();
      // Wait a moment for the job to register, then refresh candidate list.
      setTimeout(_refresh, 1500);
    } catch (e) {
      console.warn("Run now failed:", e);
    } finally {
      btn.disabled = false;
      btn.textContent = "Run now";
    }
  });

  host.querySelector("#researchSchedEnabled")?.addEventListener("change", _saveScheduler);
  host.querySelector("#researchSchedInterval")?.addEventListener("change", _saveScheduler);
  host.querySelector("#researchSchedLane")?.addEventListener("change", _saveScheduler);

  host.addEventListener("click", async (e) => {
    const filterBtn = e.target.closest("[data-status-filter]");
    if (filterBtn) {
      _state.filter = filterBtn.dataset.statusFilter;
      _renderList(host);
      _paintFilter(host);
      return;
    }
    const setBtn = e.target.closest("[data-set-status]");
    if (setBtn) {
      const id = setBtn.dataset.id;
      const next = setBtn.dataset.setStatus;
      try {
        await fetch(`${API}/api/research/candidates/${id}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: next }),
        });
        _refresh();
      } catch (err) {
        console.warn("Status update failed:", err);
      }
      return;
    }

    const proposeBtn = e.target.closest("[data-propose]");
    if (proposeBtn) {
      const id = proposeBtn.dataset.propose;
      const card = proposeBtn.closest(".lm-research-card");
      const slot = card?.querySelector(".lm-research-card__proposal");
      proposeBtn.disabled = true;
      proposeBtn.textContent = "Generating…";
      if (slot) {
        slot.hidden = false;
        slot.innerHTML = `<div class="lm-mute lm-fs-12">Asking the model for a concrete code change…</div>`;
      }
      try {
        const r = await fetch(`${API}/api/research/candidates/${id}/propose`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        });
        const data = await r.json();
        if (data.ok && data.proposal) {
          if (slot) slot.innerHTML = _renderProposal(data.proposal);
          // Status auto-flipped server-side; refresh so the chip updates.
          _refresh();
        } else {
          if (slot) {
            slot.innerHTML = `<div class="lm-status--failed lm-fs-12">
              ${escapeHtml(data.error || "Proposal generation failed.")}
            </div>`;
          }
        }
      } catch (err) {
        console.warn("Propose failed:", err);
        if (slot) {
          slot.innerHTML = `<div class="lm-status--failed lm-fs-12">
            ${escapeHtml(String(err.message || err))}
          </div>`;
        }
      } finally {
        proposeBtn.disabled = false;
        proposeBtn.textContent = "Generate proposal";
      }
    }
  });
}

function _renderProposal(p) {
  if (!p || typeof p !== "object") return "";
  const title = p.title || p.summary || "Proposal";
  const body  = p.description || p.body || p.content || "";
  const file  = p.file || p.target_file || p.path || "";
  const diff  = p.diff || p.patch || "";
  return `
    <article class="lm-research-card__proposal-body">
      <header class="lm-research-card__proposal-head">
        <span class="material-symbols-outlined" aria-hidden="true">build</span>
        <strong>${escapeHtml(String(title))}</strong>
      </header>
      ${file ? `<div class="lm-mono lm-fs-12 lm-mute">${escapeHtml(String(file))}</div>` : ""}
      ${body ? `<p>${escapeHtml(String(body))}</p>` : ""}
      ${diff ? `<pre class="lm-research-card__proposal-diff"><code>${escapeHtml(String(diff))}</code></pre>` : ""}
    </article>
  `;
}

async function _saveScheduler() {
  const root = document.querySelector(".lm-research-panel");
  if (!root) return;
  const enabled = root.querySelector("#researchSchedEnabled").checked;
  const interval = parseInt(root.querySelector("#researchSchedInterval").value, 10) || 8;
  const lane = root.querySelector("#researchSchedLane").value || "random";
  try {
    await fetch(`${API}/api/research/scheduler`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, interval_hours: interval, lane }),
    });
  } catch (e) {
    console.warn("Scheduler save failed:", e);
  }
}

async function _refresh() {
  const root = document.querySelector(".lm-research-panel");
  if (!root) return;

  // Pull both in parallel.
  let candidates = [], countsByStatus = {}, sched = null;
  try {
    const [c, s] = await Promise.all([
      fetch(`${API}/api/research/candidates`).then((r) => (r.ok ? r.json() : null)),
      fetch(`${API}/api/research/scheduler`).then((r) => (r.ok ? r.json() : null)),
    ]);
    if (c) {
      candidates     = c.candidates || [];
      countsByStatus = c.counts_by_status || {};
    }
    if (s) sched = s;
  } catch (e) {
    console.warn("Research panel fetch failed:", e);
  }

  _state.candidates = candidates;
  _state.countsByStatus = countsByStatus;
  _state.scheduler = sched;

  _paintScheduler(root);
  _paintFilter(root);
  _renderList(root);

  const subtitle = root.querySelector("#researchPanelSubtitle");
  if (subtitle) {
    const total = candidates.length;
    const newCount = countsByStatus.new || 0;
    if (total === 0) {
      subtitle.textContent = "Lane jobs that produce 'change candidates' will land here for triage.";
    } else {
      subtitle.textContent = `${total} candidate${total === 1 ? "" : "s"} · ${newCount} new`;
    }
  }
}

function _paintScheduler(root) {
  const sched = _state.scheduler;
  if (!sched) return;
  const en = root.querySelector("#researchSchedEnabled");
  const iv = root.querySelector("#researchSchedInterval");
  const ln = root.querySelector("#researchSchedLane");
  if (en) en.checked = !!sched.enabled;
  if (iv) {
    const v = String(sched.interval_hours ?? 8);
    if ([...iv.options].some((o) => o.value === v)) iv.value = v;
  }
  if (ln) {
    const v = sched.lane || "random";
    if ([...ln.options].some((o) => o.value === v)) ln.value = v;
  }
}

function _paintFilter(root) {
  for (const btn of root.querySelectorAll("[data-status-filter]")) {
    const k = btn.dataset.statusFilter;
    const active = k === _state.filter;
    btn.classList.toggle("lm-chip--active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  }
  for (const sp of root.querySelectorAll("[data-status-count]")) {
    const k = sp.dataset.statusCount;
    sp.textContent = String(_state.countsByStatus[k] || 0);
  }
}

function _renderList(root) {
  const list  = root.querySelector("#researchCandidates");
  const empty = root.querySelector("#researchCandidatesEmpty");
  if (!list || !empty) return;

  const filtered = _state.candidates.filter((c) => c.status === _state.filter);
  if (!filtered.length) {
    list.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  list.innerHTML = filtered.map(_renderCard).join("");
}

function _renderCard(c) {
  const id = c.memory_id;
  const lane = c.lane || "?";
  const change = c.change || c.raw_content || "";
  const hook = c.hook || "—";
  const why = c.why || "—";
  const url = c.source_url || "";
  const created = c.created_at || "";

  const statusButtons = STATUSES
    .filter((s) => s !== c.status)
    .map((s) => `
      <button type="button" class="lm-btn lm-btn--ghost lm-btn--xs"
              data-set-status="${s}" data-id="${id}" title="Mark as ${STATUS_LABEL[s]}">
        ${escapeHtml(STATUS_LABEL[s])}
      </button>
    `).join("");

  // "Generate proposal" only makes sense once the user has accepted the
  // candidate. Keep it visible for `implemented` too so the user can
  // re-generate if they want a second opinion.
  const showPropose = c.status === "accepted" || c.status === "implemented";
  const proposeButton = showPropose
    ? `<button type="button" class="lm-btn lm-btn--primary lm-btn--xs"
               data-propose="${id}" title="Generate a code change proposal from this candidate">
         <span class="material-symbols-outlined" aria-hidden="true">build</span>
         Generate proposal
       </button>`
    : "";

  return `
    <li class="lm-research-card" data-status="${escapeHtml(c.status)}">
      <header class="lm-research-card__head">
        <span class="lm-chip lm-chip--static lm-research-card__lane">${escapeHtml(lane)}</span>
        <span class="lm-chip lm-chip--static lm-research-card__status lm-research-card__status--${escapeHtml(c.status)}">
          ${escapeHtml(STATUS_LABEL[c.status] || c.status)}
        </span>
        <span class="lm-mute lm-mono lm-fs-11" style="margin-left:auto;">${escapeHtml(created)}</span>
      </header>
      <div class="lm-research-card__change">${escapeHtml(change)}</div>
      <div class="lm-research-card__meta">
        <span><strong>Hook:</strong> ${escapeHtml(hook)}</span>
        <span><strong>Why:</strong> ${escapeHtml(why)}</span>
      </div>
      <div class="lm-research-card__proposal" hidden></div>
      <footer class="lm-research-card__foot">
        ${url ? `<a class="lm-research-card__src" href="${escapeHtml(url)}" target="_blank" rel="noopener">
          <span class="material-symbols-outlined" aria-hidden="true">open_in_new</span>
          source paper
        </a>` : `<span class="lm-mute lm-fs-12">no source URL</span>`}
        <div class="lm-research-card__actions">${proposeButton}${statusButtons}</div>
      </footer>
    </li>
  `;
}
