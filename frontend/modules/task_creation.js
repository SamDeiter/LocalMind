/**
 * task_creation.js — New Job drawer (4-step flow)
 * =================================================
 * Progressive drawer-based job creation for the v2 mission control shell.
 *
 * Steps:
 *   1. Goal         — describe what the agent should do
 *   2. Constraints  — tools, file context, priority, budget
 *   3. Review plan  — agent-proposed plan (if available) + edit
 *   4. Launch       — submit
 *
 * Mounts into #taskCreationArea (inside the #newJobDrawer).
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

const AVAILABLE_TOOLS = [
  { id: "web_search",    label: "Web Search",      icon: "language" },
  { id: "read_file",     label: "Read File",       icon: "description" },
  { id: "write_file",    label: "Write File",      icon: "edit_document" },
  { id: "run_code",      label: "Run Code",        icon: "terminal" },
  { id: "save_memory",   label: "Save Memory",     icon: "bookmark_add" },
  { id: "recall_memories", label: "Recall Memory", icon: "bookmark" },
  { id: "git_status",    label: "Git",             icon: "merge_type" },
  { id: "browser",       label: "Browser",         icon: "public" },
  { id: "analyze_image", label: "Vision",          icon: "visibility" },
];

const PRIORITIES = [
  { id: "low",    label: "Low" },
  { id: "normal", label: "Normal" },
  { id: "high",   label: "High" },
];

const PRIORITY_TO_INT = { low: -1, normal: 0, high: 1 };

function _priorityToInt(p) {
  if (typeof p === "number") return p;
  return PRIORITY_TO_INT[p] ?? 0;
}

// Module-local state for the in-progress draft
const _draft = {
  step: 0,
  goal: "",
  tools: new Set(["web_search", "read_file", "write_file"]),
  priority: "normal",
  timeBudgetMin: 30,
  notes: "",
  plan: null,    // set after step 3 agent call
};

// ── Public API ──────────────────────────────────────────────────

export function initTaskCreation() {
  const target = document.getElementById("taskCreationArea");
  if (!target) return;

  target.innerHTML = _render();
  _bindEvents(target);
  _goto(0);
}

// ── Render ──────────────────────────────────────────────────────

function _render() {
  return /* html */ `
    <div class="lm-stepper" id="njStepper" role="list">
      ${_stepBadge(1, "Goal")}
      <div class="lm-step__connector"></div>
      ${_stepBadge(2, "Constraints")}
      <div class="lm-step__connector"></div>
      ${_stepBadge(3, "Review plan")}
      <div class="lm-step__connector"></div>
      ${_stepBadge(4, "Launch")}
    </div>

    <!-- ── Step 1: Goal ────────────────────────────────────────── -->
    <section class="lm-drawer__step" data-step="0">
      <div class="lm-field-group">
        <div class="lm-field">
          <label class="lm-label" for="njGoal">What should LocalMind do?</label>
          <textarea id="njGoal" class="lm-input" rows="6"
            placeholder="Describe the outcome, not the steps. E.g.: 'Summarize the 8 newest papers on retrieval-augmented planning and draft a team memo.'"></textarea>
          <div class="lm-field__hint">Be specific about the outcome. The agent plans the steps.</div>
        </div>

        <div class="lm-field">
          <label class="lm-label">Examples</label>
          <div class="lm-home__chips">
            <button type="button" class="lm-chip" data-example="research">
              <span class="material-symbols-outlined" aria-hidden="true">manage_search</span>
              Research topic
            </button>
            <button type="button" class="lm-chip" data-example="summarize">
              <span class="material-symbols-outlined" aria-hidden="true">summarize</span>
              Summarize files
            </button>
            <button type="button" class="lm-chip" data-example="code">
              <span class="material-symbols-outlined" aria-hidden="true">code</span>
              Fix a bug
            </button>
            <button type="button" class="lm-chip" data-example="memo">
              <span class="material-symbols-outlined" aria-hidden="true">edit_note</span>
              Draft memo
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- ── Step 2: Constraints ────────────────────────────────── -->
    <section class="lm-drawer__step" data-step="1" hidden>
      <div class="lm-field-group">
        <div class="lm-field">
          <label class="lm-label">Tools the agent can use</label>
          <div class="lm-home__chips" id="njTools">
            ${AVAILABLE_TOOLS.map((t) => `
              <label class="lm-chip" data-tool="${t.id}">
                <input type="checkbox" value="${t.id}" ${_draft.tools.has(t.id) ? "checked" : ""} style="accent-color:var(--lm-accent);">
                <span class="material-symbols-outlined" aria-hidden="true">${t.icon}</span>
                ${escapeHtml(t.label)}
              </label>
            `).join("")}
          </div>
        </div>

        <div class="lm-field-group" style="grid-template-columns:1fr 1fr;display:grid;gap:var(--lm-space-4);">
          <div class="lm-field">
            <label class="lm-label" for="njPriority">Priority</label>
            <select class="lm-input" id="njPriority">
              ${PRIORITIES.map((p) => `
                <option value="${p.id}" ${p.id === _draft.priority ? "selected" : ""}>${escapeHtml(p.label)}</option>
              `).join("")}
            </select>
          </div>
          <div class="lm-field">
            <label class="lm-label" for="njTimeBudget">Time budget (min)</label>
            <input class="lm-input" type="number" id="njTimeBudget" min="5" max="600" step="5" value="${_draft.timeBudgetMin}"/>
          </div>
        </div>

        <div class="lm-field">
          <label class="lm-label" for="njNotes">Additional context (optional)</label>
          <textarea id="njNotes" class="lm-input" rows="3" placeholder="Files, URLs, constraints the agent should know about."></textarea>
        </div>
      </div>
    </section>

    <!-- ── Step 3: Review plan ────────────────────────────────── -->
    <section class="lm-drawer__step" data-step="2" hidden>
      <div class="lm-review">
        <div class="lm-review__heading">Your goal</div>
        <div class="lm-review__text" id="njReviewGoal">—</div>

        <div class="lm-review__heading" style="margin-top:var(--lm-space-5);">Proposed plan</div>
        <div id="njPlanArea" class="lm-review__text">
          <div class="lm-home__empty">Generating plan…</div>
        </div>

        <div class="lm-review__heading" style="margin-top:var(--lm-space-5);">Constraints</div>
        <div class="lm-review__text" id="njReviewConstraints">—</div>
      </div>
    </section>

    <!-- ── Step 4: Launch ─────────────────────────────────────── -->
    <section class="lm-drawer__step" data-step="3" hidden>
      <div class="lm-review">
        <div class="lm-review__heading">Ready to launch</div>
        <p class="lm-review__text">
          LocalMind will start immediately. You'll see the job under
          <strong>Jobs → Running</strong> and receive a notification on completion.
        </p>

        <div class="lm-field-group" style="margin-top:var(--lm-space-5);">
          <div class="lm-field">
            <label class="lm-label" style="display:flex;align-items:center;gap:var(--lm-space-2);">
              <input type="checkbox" id="njAutoApprove" style="accent-color:var(--lm-accent);">
              Auto-approve low-risk steps
            </label>
            <div class="lm-field__hint">Skip confirmation for read-only or sandboxed tool calls.</div>
          </div>
        </div>
      </div>
    </section>

    <!-- ── Footer nav ─────────────────────────────────────────── -->
    <footer class="lm-drawer__footer">
      <button type="button" class="lm-btn lm-btn--ghost" id="njBackBtn" disabled>
        <span class="material-symbols-outlined" aria-hidden="true">chevron_left</span>
        Back
      </button>
      <div style="flex:1;"></div>
      <button type="button" class="lm-btn lm-btn--ghost" data-close-drawer>Cancel</button>
      <button type="button" class="lm-btn lm-btn--primary" id="njNextBtn">
        <span id="njNextLabel">Next</span>
        <span class="material-symbols-outlined" aria-hidden="true">chevron_right</span>
      </button>
    </footer>
  `;
}

function _stepBadge(num, label) {
  return `
    <div class="lm-step" data-step-num="${num - 1}" role="listitem">
      <div class="lm-step__num">${num}</div>
      <div class="lm-step__label">${escapeHtml(label)}</div>
    </div>
  `;
}

// ── Events ──────────────────────────────────────────────────────

function _bindEvents(root) {
  // Example chips → prefill goal
  root.querySelectorAll("[data-example]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const field = root.querySelector("#njGoal");
      if (!field) return;
      const presets = {
        research:  "Research and summarize the most relevant work on ___ from the last 12 months.",
        summarize: "Read the files I dropped in and produce a one-page summary with action items.",
        code:      "Investigate the bug described in ___ and produce a minimal fix with a test.",
        memo:      "Draft a 300-word memo for my team about ___ in a direct, declarative tone.",
      };
      field.value = presets[btn.dataset.example] || "";
      field.focus();
    });
  });

  // Tool toggles
  root.querySelectorAll("[data-tool] input").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) _draft.tools.add(cb.value);
      else _draft.tools.delete(cb.value);
    });
  });

  // Priority + time
  root.querySelector("#njPriority")?.addEventListener("change", (e) => {
    _draft.priority = e.target.value;
  });
  root.querySelector("#njTimeBudget")?.addEventListener("change", (e) => {
    const v = parseInt(e.target.value, 10);
    if (!isNaN(v)) _draft.timeBudgetMin = Math.max(5, Math.min(600, v));
  });
  root.querySelector("#njNotes")?.addEventListener("input", (e) => {
    _draft.notes = e.target.value;
  });

  // Nav buttons
  root.querySelector("#njBackBtn")?.addEventListener("click", () => _goto(_draft.step - 1));
  root.querySelector("#njNextBtn")?.addEventListener("click", _onNext);

  // Goal field → sync into draft
  root.querySelector("#njGoal")?.addEventListener("input", (e) => {
    _draft.goal = e.target.value;
  });
}

async function _onNext() {
  const nextBtn = document.getElementById("njNextBtn");

  switch (_draft.step) {
    case 0: {
      const v = (document.getElementById("njGoal")?.value || "").trim();
      if (!v) {
        showToast?.("Describe the goal first", "warn");
        return;
      }
      _draft.goal = v;
      _goto(1);
      return;
    }
    case 1: {
      _goto(2);
      _populateReview();
      _generatePlan().catch(() => { /* non-blocking */ });
      return;
    }
    case 2: {
      _goto(3);
      return;
    }
    case 3: {
      // Launch
      if (nextBtn) nextBtn.disabled = true;
      try {
        await _submitJob();
        showToast?.("Job launched", "ok");
        _closeDrawer();
        _resetDraft();
      } catch (e) {
        showToast?.(`Could not launch: ${e.message || e}`, "error");
      } finally {
        if (nextBtn) nextBtn.disabled = false;
      }
      return;
    }
  }
}

function _goto(step) {
  _draft.step = step;

  document.querySelectorAll("[data-step]").forEach((el) => {
    el.hidden = String(el.dataset.step) !== String(step);
  });

  document.querySelectorAll(".lm-step").forEach((el) => {
    const n = parseInt(el.dataset.stepNum, 10);
    el.classList.toggle("lm-step--active", n === step);
    el.classList.toggle("lm-step--done", n < step);
  });

  const back = document.getElementById("njBackBtn");
  const nextLabel = document.getElementById("njNextLabel");
  if (back) back.disabled = step === 0;
  if (nextLabel) {
    nextLabel.textContent = step === 3 ? "Launch" : (step === 2 ? "Continue" : "Next");
  }
}

function _populateReview() {
  const goal = document.getElementById("njReviewGoal");
  const cons = document.getElementById("njReviewConstraints");
  if (goal) goal.textContent = _draft.goal || "—";
  if (cons) {
    const toolList = [..._draft.tools].join(", ") || "none";
    cons.innerHTML = `
      <div><span class="lm-mute">Tools:</span> <span class="lm-mono">${escapeHtml(toolList)}</span></div>
      <div><span class="lm-mute">Priority:</span> <span class="lm-mono">${escapeHtml(_draft.priority)}</span></div>
      <div><span class="lm-mute">Time budget:</span> <span class="lm-mono">${_draft.timeBudgetMin} min</span></div>
    `;
  }
}

async function _generatePlan() {
  const area = document.getElementById("njPlanArea");
  if (!area) return;
  try {
    const res = await fetch(`${API}/api/jobs/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        goal: _draft.goal,
        tools: [..._draft.tools],
        priority: _priorityToInt(_draft.priority),
      }),
    });
    if (!res.ok) throw new Error("plan endpoint returned " + res.status);
    const plan = await res.json();
    _draft.plan = plan;
    const steps = plan.steps || plan.plan || [];
    if (!Array.isArray(steps) || steps.length === 0) {
      area.innerHTML = `<div class="lm-home__empty">Agent will plan on first tool call.</div>`;
      return;
    }
    area.innerHTML = `
      <ol style="margin:0;padding-left:var(--lm-space-5);display:flex;flex-direction:column;gap:var(--lm-space-2);">
        ${steps.map((s) => `<li>${escapeHtml(s.description || s.title || String(s))}</li>`).join("")}
      </ol>
    `;
  } catch (_) {
    // No plan endpoint available — skip gracefully
    area.innerHTML = `<div class="lm-home__empty">Agent will plan on first tool call.</div>`;
  }
}

async function _submitJob() {
  const autoApprove = document.getElementById("njAutoApprove")?.checked || false;
  const body = {
    title: _draft.goal.slice(0, 120),
    description: _draft.goal,
    prompt: _draft.goal,
    tools: [..._draft.tools],
    priority: _priorityToInt(_draft.priority),
    time_budget_minutes: _draft.timeBudgetMin,
    notes: _draft.notes,
    auto_approve: autoApprove,
  };
  const res = await fetch(`${API}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || "HTTP " + res.status);
  }
  return await res.json();
}

function _closeDrawer() {
  const drawer = document.getElementById("newJobDrawer");
  if (drawer) {
    drawer.dataset.open = "false";
    drawer.setAttribute("aria-hidden", "true");
  }
}

function _resetDraft() {
  _draft.step = 0;
  _draft.goal = "";
  _draft.notes = "";
  _draft.plan = null;
  const goal = document.getElementById("njGoal");
  const notes = document.getElementById("njNotes");
  if (goal) goal.value = "";
  if (notes) notes.value = "";
  _goto(0);
}

/**
 * Quick-submit shim for legacy call sites (events.js priority input etc.).
 * Opens the New Job drawer with the goal prefilled so the user still sees
 * the Constraints / Review / Launch steps before committing.
 */
export function submitQuickTask(text) {
  const goal = String(text || "").trim();
  if (!goal) return;

  _draft.goal = goal;
  _goto(0);

  const drawer = document.getElementById("newJobDrawer");
  if (drawer) {
    drawer.dataset.open = "true";
    drawer.setAttribute("aria-hidden", "false");
  }

  const goalEl = document.getElementById("njGoal");
  if (goalEl) {
    goalEl.value = goal;
    goalEl.focus();
  }
}
