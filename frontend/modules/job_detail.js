/**
 * job_detail.js — Job Detail page (Phase 2)
 *
 * Mounts an overlay inside #mainJobs with three panes:
 *   left:   plan / timeline (steps with status)
 *   center: current output (streaming text + artifacts)
 *   right:  inspector (metadata, tools, resources, evidence/QA)
 *
 * Surfaces:
 *   - Inline approvals gate when status is reviewing / waiting_approval
 *   - Evidence panel with sources + QA when status is done
 *   - Cancel / Delete / Save-as-Template actions
 *
 * Deep link: #/jobs/:id
 * Data:      GET /api/jobs/:id, POST /approve|reject|cancel, DELETE, SSE on /api/jobs/activity
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

let _rendered = false;
let _currentJobId = null;
let _currentJob = null;
let _sse = null;

// ── Public API ──────────────────────────────────────────────────

export function initJobDetail() {
  const parent = document.getElementById("mainJobs");
  if (!parent) return;
  if (_rendered) return;
  _rendered = true;

  const section = document.createElement("section");
  section.id = "jobDetailPanel";
  section.className = "lm-job-detail";
  section.setAttribute("role", "region");
  section.setAttribute("aria-label", "Job detail");
  section.hidden = true;
  section.innerHTML = _skeleton();
  parent.appendChild(section);

  _bindStaticEvents(section);

  // Listen for hash → open/close
  window.addEventListener("hashchange", _syncFromHash);
  _syncFromHash();
}

/** Open the detail view for a given job id. Also updates the URL hash. */
export function openJobDetail(jobId) {
  if (!jobId) return;
  _currentJobId = jobId;

  const panel = document.getElementById("jobDetailPanel");
  if (!panel) return;
  panel.hidden = false;

  // Make sure Jobs tab is active
  import("./nav_rail.js").then((m) => m.switchNav?.("jobs")).catch(() => {});

  // Update hash without re-triggering navigation
  const target = `#/jobs/${encodeURIComponent(jobId)}`;
  if (location.hash !== target) {
    history.replaceState(null, "", target);
  }

  _loadDetail(jobId);
  _connectSSE();
}

/** Close the detail view and return to the list. */
export function closeJobDetail() {
  _currentJobId = null;
  _currentJob = null;

  const panel = document.getElementById("jobDetailPanel");
  if (panel) panel.hidden = true;

  _disconnectSSE();

  const target = `#/jobs`;
  if (location.hash !== target) history.replaceState(null, "", target);
}

// ── Shell ───────────────────────────────────────────────────────

function _skeleton() {
  return /* html */ `
    <header class="lm-job-detail__header">
      <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="jdBack">
        <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span> Back to Jobs
      </button>

      <div class="lm-job-detail__title-group">
        <div class="lm-page__eyebrow lm-mono" id="jdJobId">—</div>
        <h1 class="lm-page__title" id="jdTitle">Loading…</h1>
        <div class="lm-job-detail__meta" id="jdMeta"></div>
      </div>

      <div class="lm-page__actions" id="jdActions">
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm lm-job-detail__inspector-toggle" id="jdInspectorToggle" aria-label="Toggle inspector panel" aria-pressed="false">
          <span class="material-symbols-outlined" aria-hidden="true">info</span>
        </button>
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="jdSaveTemplate" hidden>
          <span class="material-symbols-outlined" aria-hidden="true">bookmark_add</span> Save as template
        </button>
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="jdCancel" hidden>
          <span class="material-symbols-outlined" aria-hidden="true">stop</span> Cancel
        </button>
        <button type="button" class="lm-btn lm-btn--danger lm-btn--sm" id="jdDelete">
          <span class="material-symbols-outlined" aria-hidden="true">delete</span> Delete
        </button>
      </div>
    </header>

    <!-- Approval gate (hidden unless status == reviewing / waiting_approval) -->
    <section class="lm-approval" id="jdApproval" hidden>
      <div class="lm-approval__icon">
        <span class="material-symbols-outlined" aria-hidden="true">pending_actions</span>
      </div>
      <div class="lm-approval__body">
        <div class="lm-approval__heading">Agent is waiting for your approval</div>
        <div class="lm-approval__text" id="jdApprovalText">—</div>
      </div>
      <div class="lm-approval__actions">
        <button type="button" class="lm-btn lm-btn--ghost" id="jdReject">Reject</button>
        <button type="button" class="lm-btn lm-btn--primary" id="jdApprove">Approve</button>
      </div>
    </section>

    <!-- Three-pane body -->
    <div class="lm-job-detail__body">

      <!-- Left: plan / timeline -->
      <aside class="lm-job-detail__pane lm-job-detail__pane--left" aria-label="Plan">
        <div class="lm-label">Plan</div>
        <ol class="lm-timeline" id="jdTimeline">
          ${_timelineSkeleton(4)}
        </ol>
      </aside>

      <!-- Center: output -->
      <section class="lm-job-detail__pane lm-job-detail__pane--center" aria-label="Output">
        <div class="lm-job-detail__tabs" role="tablist" aria-label="Detail tabs">
          <button type="button" class="lm-jobs__tab" role="tab" aria-selected="true"  data-tab="output">Output</button>
          <button type="button" class="lm-jobs__tab" role="tab" aria-selected="false" data-tab="logs">Logs</button>
          <button type="button" class="lm-jobs__tab" role="tab" aria-selected="false" data-tab="artifacts">Artifacts</button>
          <button type="button" class="lm-jobs__tab" role="tab" aria-selected="false" data-tab="evidence">Evidence</button>
        </div>

        <div class="lm-job-detail__tab-body" data-tab-body="output">
          <article class="lm-output" id="jdOutput">
            <div class="lm-home__empty">Waiting for output…</div>
          </article>
        </div>

        <div class="lm-job-detail__tab-body" data-tab-body="logs" hidden>
          <pre class="lm-logs" id="jdLogs"></pre>
        </div>

        <div class="lm-job-detail__tab-body" data-tab-body="artifacts" hidden>
          <div class="lm-artifacts-list" id="jdArtifacts">
            <div class="lm-home__empty">No artifacts yet.</div>
          </div>
        </div>

        <div class="lm-job-detail__tab-body" data-tab-body="evidence" hidden>
          <div class="lm-evidence" id="jdEvidence">
            <div class="lm-home__empty">No evidence recorded yet.</div>
          </div>
        </div>
      </section>

      <!-- Right: inspector -->
      <aside class="lm-job-detail__pane lm-job-detail__pane--right" aria-label="Inspector">
        <div class="lm-inspector-group">
          <div class="lm-label">Status</div>
          <div id="jdStatusChip">—</div>
        </div>

        <div class="lm-inspector-group">
          <div class="lm-label">Model</div>
          <div class="lm-mono" id="jdModel">—</div>
        </div>

        <div class="lm-inspector-group">
          <div class="lm-label">Tools</div>
          <div id="jdTools" class="lm-inspector-chips">—</div>
        </div>

        <div class="lm-inspector-group">
          <div class="lm-label">Created</div>
          <div class="lm-mono" id="jdCreated">—</div>
        </div>

        <div class="lm-inspector-group">
          <div class="lm-label">Last update</div>
          <div class="lm-mono" id="jdUpdated">—</div>
        </div>

        <div class="lm-inspector-group">
          <div class="lm-label">Duration</div>
          <div class="lm-mono" id="jdDuration">—</div>
        </div>

        <div class="lm-inspector-group">
          <div class="lm-label">Priority</div>
          <div class="lm-mono" id="jdPriority">normal</div>
        </div>

        <div class="lm-inspector-group" id="jdCostGroup" hidden>
          <div class="lm-label">Cost</div>
          <div class="lm-mono" id="jdCost">—</div>
        </div>
      </aside>
    </div>
  `;
}

function _timelineSkeleton(n) {
  let out = "";
  for (let i = 0; i < n; i++) {
    out += `<li class="lm-timeline__step">
      <div class="lm-timeline__dot"></div>
      <div class="lm-skeleton" style="height:12px;width:70%;"></div>
    </li>`;
  }
  return out;
}

// ── Events (static, bound once) ─────────────────────────────────

function _bindStaticEvents(root) {
  root.querySelector("#jdBack")?.addEventListener("click", closeJobDetail);

  root.querySelector("#jdCancel")?.addEventListener("click", _cancelJob);
  root.querySelector("#jdDelete")?.addEventListener("click", _deleteJob);
  root.querySelector("#jdSaveTemplate")?.addEventListener("click", _saveAsTemplate);
  root.querySelector("#jdApprove")?.addEventListener("click", () => _review("approve"));
  root.querySelector("#jdReject")?.addEventListener("click", () => _review("reject"));

  // Inspector toggle (mobile only — CSS hides the button on desktop)
  root.querySelector("#jdInspectorToggle")?.addEventListener("click", () => {
    const panel = document.getElementById("jobDetailPanel");
    if (!panel) return;
    const hidden = panel.dataset.inspector === "hidden";
    if (hidden) {
      panel.removeAttribute("data-inspector");
    } else {
      panel.dataset.inspector = "hidden";
    }
    root.querySelector("#jdInspectorToggle")?.setAttribute("aria-pressed", String(!hidden));
  });

  // Tab switching
  root.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => _switchTab(btn.dataset.tab));
  });

  // Escape → close
  root.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeJobDetail();
  });
}

function _switchTab(tab) {
  document.querySelectorAll("#jobDetailPanel [data-tab]").forEach((b) => {
    const active = b.dataset.tab === tab;
    b.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("#jobDetailPanel [data-tab-body]").forEach((b) => {
    b.hidden = b.dataset.tabBody !== tab;
  });
}

// ── Hash sync ───────────────────────────────────────────────────

function _syncFromHash() {
  const m = (location.hash || "").match(/^#\/jobs\/(.+)$/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    if (id !== _currentJobId) openJobDetail(id);
  } else {
    if (_currentJobId !== null) closeJobDetail();
  }
}

// ── Data ────────────────────────────────────────────────────────

async function _loadDetail(jobId) {
  try {
    const r = await fetch(`${API}/api/jobs/${encodeURIComponent(jobId)}`);
    if (!r.ok) {
      if (r.status === 404) {
        showToast?.("Job not found", "error");
        closeJobDetail();
      }
      return;
    }
    const job = await r.json();
    _currentJob = job;
    _render(job);
  } catch (e) {
    console.warn("[job_detail] load failed", e);
  }
}

function _connectSSE() {
  _disconnectSSE();
  try {
    _sse = new EventSource(`${API}/api/jobs/activity`);
    _sse.onmessage = (evt) => {
      if (!_currentJobId) return;
      try {
        const data = JSON.parse(evt.data || "{}");
        const matchId = data.job_id || data.id;
        if (matchId && String(matchId) === String(_currentJobId)) {
          _loadDetail(_currentJobId);
        }
      } catch (_) {
        // Plain ping — still refresh if it's our job
        _loadDetail(_currentJobId);
      }
    };
    _sse.onerror = () => {
      _disconnectSSE();
    };
  } catch (_) { /* SSE optional */ }
}

function _disconnectSSE() {
  if (_sse) {
    try { _sse.close(); } catch (_) {}
    _sse = null;
  }
}

// ── Render ──────────────────────────────────────────────────────

function _render(job) {
  const status = (job.status || job.state || "").toLowerCase();

  // Header
  _setText("jdJobId", job.id || job.job_id || "");
  _setText("jdTitle", job.title || job.name || "(untitled)");
  _setHTML("jdMeta", _renderHeaderMeta(job));

  // Inspector
  _setHTML("jdStatusChip", _statusChip(status));
  _setText("jdModel", job.model || job.active_model || "auto");
  _setHTML("jdTools", _renderToolChips(job.tools || job.allowed_tools));
  _setText("jdCreated",  _fmtTime(job.created_at));
  _setText("jdUpdated",  _fmtTime(job.updated_at || job.last_update));
  _setText("jdDuration", _duration(job));
  _setText("jdPriority", String(job.priority ?? "normal"));

  const costEl = document.getElementById("jdCostGroup");
  if (costEl) {
    if (job.cost != null) {
      costEl.hidden = false;
      _setText("jdCost", `$${Number(job.cost).toFixed(4)}`);
    } else {
      costEl.hidden = true;
    }
  }

  // Actions
  _toggle("jdCancel",       _isCancellable(status));
  _toggle("jdSaveTemplate", status === "done" || status === "completed");

  // Approval gate
  _renderApprovalGate(job, status);

  // Plan
  _renderTimeline(job);

  // Output / Logs / Artifacts / Evidence
  _renderOutput(job);
  _renderLogs(job);
  _renderArtifacts(job);
  _renderEvidence(job);
}

function _renderHeaderMeta(job) {
  const bits = [];
  if (job.created_at) bits.push(`<span>${escapeHtml(_fmtTime(job.created_at))}</span>`);
  if (job.mode)       bits.push(`<span class="lm-mute">·</span><span>${escapeHtml(job.mode)}</span>`);
  if (job.template_id || job.template) {
    bits.push(`<span class="lm-mute">·</span><span class="lm-mono">${escapeHtml(job.template_id || job.template)}</span>`);
  }
  return bits.join(" ");
}

function _renderApprovalGate(job, status) {
  const gate = document.getElementById("jdApproval");
  if (!gate) return;
  const awaiting = status === "reviewing" || status === "waiting_approval" || status === "waiting";
  gate.hidden = !awaiting;
  if (!awaiting) return;

  const txt = document.getElementById("jdApprovalText");
  const prompt = job.pending_action || job.approval_prompt || job.review_prompt
              || job.next_step?.description || job.next_step?.title
              || "Agent wants to take the next step. Review the plan and approve to continue.";
  if (txt) txt.textContent = prompt;
}

function _renderTimeline(job) {
  const el = document.getElementById("jdTimeline");
  if (!el) return;
  const steps = _extractSteps(job);
  if (!steps.length) {
    el.innerHTML = `<li class="lm-home__empty">No plan yet.</li>`;
    return;
  }
  el.innerHTML = steps.map(_timelineRow).join("");
}

function _timelineRow(step) {
  const status = (step.status || step.state || "pending").toLowerCase();
  const variant = _stepVariant(status);
  const title   = step.title || step.description || step.name || step.step || String(step);
  const sub     = step.detail || step.tool || step.output_summary || "";
  return `
    <li class="lm-timeline__step lm-timeline__step--${variant}">
      <div class="lm-timeline__dot" aria-hidden="true"></div>
      <div class="lm-timeline__body">
        <div class="lm-timeline__title">${escapeHtml(title)}</div>
        ${sub ? `<div class="lm-timeline__sub lm-mute">${escapeHtml(sub)}</div>` : ""}
      </div>
    </li>
  `;
}

function _renderOutput(job) {
  const el = document.getElementById("jdOutput");
  if (!el) return;

  // Prefer explicit top-level output; fall back to walking node outputs.
  let out = job.output || job.result || job.summary || job.final_output;
  if (!out) {
    const pieces = [];
    for (const node of Array.isArray(job.nodes) ? job.nodes : []) {
      const raw = node.output_json;
      if (!raw) continue;
      let parsed = raw;
      if (typeof raw === "string") {
        try { parsed = JSON.parse(raw); } catch (_) { parsed = { result: raw }; }
      }
      const text = parsed?.result ?? parsed?.output ?? parsed?.text ??
                   (typeof parsed === "string" ? parsed : JSON.stringify(parsed, null, 2));
      if (text) {
        const header = node.title ? `## ${node.title}\n\n` : "";
        pieces.push(header + String(text));
      }
    }
    if (pieces.length) out = pieces.join("\n\n---\n\n");
    else if (job.result_summary) out = job.result_summary;
  }

  if (!out) {
    el.innerHTML = `<div class="lm-home__empty">Waiting for output…</div>`;
    return;
  }
  let text = typeof out === "string" ? out : JSON.stringify(out, null, 2);
  text = _normalizeMarkdown(text);

  if (typeof window !== "undefined" && window.marked) {
    try {
      if (typeof window.marked.setOptions === "function") {
        window.marked.setOptions({ gfm: true, breaks: true });
      }
      el.innerHTML = window.marked.parse(text);
      if (window.hljs) {
        el.querySelectorAll("pre code").forEach((block) => {
          try { window.hljs.highlightElement(block); } catch (_) {}
        });
      }
      return;
    } catch (_) { /* fallthrough */ }
  }
  el.innerHTML = `<pre class="lm-output__pre">${escapeHtml(text)}</pre>`;
}

/**
 * Normalize loose agent markdown so `marked` renders it with proper separation:
 *  - Insert a blank line before lines that start with "**Label:**" so each
 *    becomes its own paragraph instead of gluing to the prior one.
 *  - Collapse runs of 3+ blank lines.
 */
function _normalizeMarkdown(src) {
  if (!src) return "";
  const lines = String(src).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  const labelLine = /^\s*\*\*[^*\n]+:\*\*\s*/;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const prev = out.length ? out[out.length - 1] : "";
    if (labelLine.test(line) && prev.trim() !== "") {
      out.push("");
    }
    out.push(line);
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n");
}

function _renderLogs(job) {
  const el = document.getElementById("jdLogs");
  if (!el) return;
  // Backend exposes the execution trail as `audit` (list of {timestamp, actor, action, detail, node_id}).
  const logs = job.logs || job.trace || job.events || job.audit || [];
  if (!Array.isArray(logs) || logs.length === 0) {
    el.textContent = "";
    el.innerHTML = `<div class="lm-home__empty" style="padding:var(--lm-space-4);">No log entries.</div>`;
    return;
  }
  // Audit events come newest-first; show oldest-first for a readable log.
  const ordered = [...logs].reverse();
  el.textContent = ordered
    .map((l) => {
      if (typeof l === "string") return l;
      const t = l.time || l.timestamp || "";
      const lvl = l.level || l.actor || l.action || "info";
      const msg = l.message || l.text || l.detail || (l.action && !l.detail ? l.action : "") || JSON.stringify(l);
      return `${t ? `[${t}] ` : ""}${String(lvl).padEnd(10)} ${msg}`;
    })
    .join("\n");
}

function _renderArtifacts(job) {
  const el = document.getElementById("jdArtifacts");
  if (!el) return;
  const files = job.outputs || job.files || job.artifacts || [];
  if (!Array.isArray(files) || files.length === 0) {
    el.innerHTML = `<div class="lm-home__empty">No artifacts yet.</div>`;
    return;
  }
  const jobId = job.id || job.job_id;
  el.innerHTML = files.map((f) => _artifactRow(jobId, f)).join("");
}

function _artifactRow(jobId, f) {
  const name = f.name || f.filename || f.path || "file";
  const size = f.size ? _fmtSize(f.size) : "";
  const kind = f.kind || f.type || _inferKind(name);
  const fid  = f.id || f.file_id || name;
  const href = `${API}/api/jobs/${encodeURIComponent(jobId)}/files/${encodeURIComponent(fid)}`;
  return `
    <a class="lm-artifact-row" href="${href}" target="_blank" rel="noopener">
      <span class="material-symbols-outlined" aria-hidden="true">${_iconFor(kind)}</span>
      <span class="lm-artifact-row__name">${escapeHtml(name)}</span>
      <span class="lm-artifact-row__kind lm-mute">${escapeHtml(kind)}</span>
      <span class="lm-artifact-row__size lm-mono lm-mute">${escapeHtml(size)}</span>
      <span class="material-symbols-outlined lm-mute" aria-hidden="true">download</span>
    </a>
  `;
}

function _renderEvidence(job) {
  const el = document.getElementById("jdEvidence");
  if (!el) return;

  // Top-level review summary (backend emits `result_summary`, e.g. "Review PASSED (score=1.00)").
  const resultSummary = job.result_summary || "";
  const reviewCount   = job.review_count ?? null;
  const maxReviews    = job.max_reviews ?? null;

  // Pull tool & review events out of the audit trail (newest-first).
  const audit = Array.isArray(job.audit) ? job.audit : [];
  const toolEvents   = audit.filter((a) => {
    const act = String(a.action || "").toLowerCase();
    return act.includes("tool") || a.tool || a.tool_name;
  });
  const reviewEvents = audit.filter((a) => {
    const act = String(a.action || "").toLowerCase();
    return act.includes("review") || act === "qa" || act === "revise";
  });

  // Legacy fields (kept as a fallback if a backend ever populates them).
  const sources   = job.sources || job.citations || [];
  const checks    = job.safety_checks || job.checks || [];
  const qa        = job.qa || job.quality || {};
  const toolTrace = job.tool_calls || job.tool_trace || [];

  const hasAny =
    resultSummary ||
    reviewCount != null ||
    toolEvents.length ||
    reviewEvents.length ||
    sources.length ||
    checks.length ||
    Object.keys(qa).length ||
    toolTrace.length;

  if (!hasAny) {
    el.innerHTML = `<div class="lm-home__empty">No evidence recorded yet.</div>`;
    return;
  }

  el.innerHTML = `
    ${resultSummary || reviewCount != null ? `
      <div class="lm-evidence__group">
        <div class="lm-label">Review</div>
        ${resultSummary ? `<div class="lm-evidence__summary">${escapeHtml(resultSummary)}</div>` : ""}
        ${reviewCount != null ? `
          <div class="lm-mute lm-mono" style="margin-top:4px;">
            ${escapeHtml(String(reviewCount))}${maxReviews != null ? ` / ${escapeHtml(String(maxReviews))}` : ""} review${reviewCount === 1 ? "" : "s"}
          </div>
        ` : ""}
      </div>
    ` : ""}

    ${reviewEvents.length ? `
      <div class="lm-evidence__group">
        <div class="lm-label">Review events (${reviewEvents.length})</div>
        <ol class="lm-evidence__trace">
          ${[...reviewEvents].reverse().map((e) => `
            <li>
              <span class="lm-mono">${escapeHtml(e.action || e.actor || "review")}</span>
              ${e.detail ? `<span class="lm-mute">— ${escapeHtml(String(e.detail))}</span>` : ""}
              ${e.timestamp ? `<span class="lm-mute lm-mono" style="margin-left:auto;">${escapeHtml(String(e.timestamp))}</span>` : ""}
            </li>
          `).join("")}
        </ol>
      </div>
    ` : ""}

    ${toolEvents.length ? `
      <div class="lm-evidence__group">
        <div class="lm-label">Tool events (${toolEvents.length})</div>
        <ol class="lm-evidence__trace">
          ${[...toolEvents].reverse().map((t) => `
            <li>
              <span class="lm-mono">${escapeHtml(t.tool || t.tool_name || t.action || "tool")}</span>
              ${t.detail ? `<span class="lm-mute">— ${escapeHtml(String(t.detail))}</span>` : ""}
            </li>
          `).join("")}
        </ol>
      </div>
    ` : ""}

    ${Object.keys(qa).length ? `
      <div class="lm-evidence__group">
        <div class="lm-label">Quality</div>
        <div class="lm-evidence__grid">
          ${Object.entries(qa).map(([k, v]) => `
            <div class="lm-evidence__metric">
              <div class="lm-evidence__metric-val">${escapeHtml(_fmtMetric(v))}</div>
              <div class="lm-evidence__metric-label">${escapeHtml(k)}</div>
            </div>
          `).join("")}
        </div>
      </div>
    ` : ""}

    ${sources.length ? `
      <div class="lm-evidence__group">
        <div class="lm-label">Sources</div>
        <ul class="lm-evidence__sources">
          ${sources.map((s) => `
            <li>
              ${s.url ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title || s.url)}</a>`
                     : escapeHtml(s.title || String(s))}
              ${s.snippet ? `<div class="lm-mute">${escapeHtml(s.snippet)}</div>` : ""}
            </li>
          `).join("")}
        </ul>
      </div>
    ` : ""}

    ${checks.length ? `
      <div class="lm-evidence__group">
        <div class="lm-label">Safety checks</div>
        <ul class="lm-evidence__checks">
          ${checks.map((c) => `
            <li>
              ${_checkIcon(c)}
              <span>${escapeHtml(c.name || c.check || String(c))}</span>
              ${c.detail ? `<span class="lm-mute">— ${escapeHtml(c.detail)}</span>` : ""}
            </li>
          `).join("")}
        </ul>
      </div>
    ` : ""}

    ${toolTrace.length ? `
      <div class="lm-evidence__group">
        <div class="lm-label">Tool calls (${toolTrace.length})</div>
        <ol class="lm-evidence__trace">
          ${toolTrace.map((t) => `
            <li>
              <span class="lm-mono">${escapeHtml(t.tool || t.name || "tool")}</span>
              ${t.duration_ms != null ? `<span class="lm-mute">${Math.round(t.duration_ms)}ms</span>` : ""}
              ${t.success === false ? `<span class="lm-status lm-status--failed"><span class="lm-status__dot"></span>failed</span>` : ""}
            </li>
          `).join("")}
        </ol>
      </div>
    ` : ""}
  `;
}

// ── Actions ─────────────────────────────────────────────────────

async function _cancelJob() {
  if (!_currentJobId) return;
  const btn = document.getElementById("jdCancel");
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`${API}/api/jobs/${encodeURIComponent(_currentJobId)}/cancel`, { method: "POST" });
    if (r.ok) {
      showToast?.("Cancel requested", "ok");
      _loadDetail(_currentJobId);
    } else {
      showToast?.(`Cancel failed (HTTP ${r.status})`, "error");
    }
  } catch (_) {
    showToast?.("Cancel failed", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function _deleteJob() {
  if (!_currentJobId) return;
  if (!confirm("Delete this job? This cannot be undone.")) return;
  const btn = document.getElementById("jdDelete");
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`${API}/api/jobs/${encodeURIComponent(_currentJobId)}`, { method: "DELETE" });
    if (r.ok) {
      showToast?.("Job deleted", "ok");
      closeJobDetail();
    } else {
      showToast?.(`Delete failed (HTTP ${r.status})`, "error");
    }
  } catch (_) {
    showToast?.("Delete failed", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function _saveAsTemplate() {
  if (!_currentJob) return;
  const name = prompt("Name this template:", _currentJob.title || "");
  if (!name) return;
  try {
    const r = await fetch(`${API}/api/templates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, job_id: _currentJob.id || _currentJob.job_id }),
    });
    if (r.ok) showToast?.("Template saved", "ok");
    else     showToast?.(`Save failed (HTTP ${r.status})`, "error");
  } catch (_) {
    showToast?.("Save failed", "error");
  }
}

async function _review(action) {
  if (!_currentJobId) return;
  const approveBtn = document.getElementById("jdApprove");
  const rejectBtn  = document.getElementById("jdReject");
  if (approveBtn) approveBtn.disabled = true;
  if (rejectBtn)  rejectBtn.disabled  = true;
  try {
    const r = await fetch(`${API}/api/jobs/${encodeURIComponent(_currentJobId)}/${action}`, { method: "POST" });
    if (r.ok) {
      showToast?.(action === "approve" ? "Approved" : "Rejected", "ok");
      _loadDetail(_currentJobId);
    } else {
      showToast?.(`${action} failed (HTTP ${r.status})`, "error");
    }
  } catch (_) {
    showToast?.(`${action} failed`, "error");
  } finally {
    if (approveBtn) approveBtn.disabled = false;
    if (rejectBtn)  rejectBtn.disabled  = false;
  }
}

// ── Helpers ─────────────────────────────────────────────────────

function _setText(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v == null ? "" : String(v);
}
function _setHTML(id, v) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = v;
}
function _toggle(id, show) {
  const el = document.getElementById(id);
  if (el) el.hidden = !show;
}

function _statusChip(status) {
  const map = {
    running: ["running", "Running"],
    executing: ["running", "Executing"],
    planning: ["running", "Planning"],
    reviewing: ["waiting", "Reviewing"],
    waiting_approval: ["waiting", "Awaiting approval"],
    waiting: ["waiting", "Awaiting approval"],
    done: ["ok", "Done"],
    completed: ["ok", "Done"],
    success: ["ok", "Done"],
    failed: ["failed", "Failed"],
    error: ["failed", "Failed"],
    paused: ["paused", "Paused"],
    cancelled: ["paused", "Cancelled"],
  };
  const [variant, label] = map[status] || ["paused", status || "unknown"];
  return `<span class="lm-status lm-status--${variant}"><span class="lm-status__dot"></span>${escapeHtml(label)}</span>`;
}

function _renderToolChips(tools) {
  if (!tools) return `<span class="lm-mute">—</span>`;
  const list = Array.isArray(tools) ? tools : String(tools).split(",");
  if (!list.length) return `<span class="lm-mute">—</span>`;
  return list.slice(0, 8).map((t) => `
    <span class="lm-chip lm-chip--static">${escapeHtml(String(t).trim())}</span>
  `).join("");
}

function _extractSteps(job) {
  if (Array.isArray(job.plan)) return job.plan;
  if (Array.isArray(job.plan?.steps)) return job.plan.steps;
  if (Array.isArray(job.steps)) return job.steps;
  if (Array.isArray(job.pipeline)) return job.pipeline;
  if (Array.isArray(job.nodes)) return job.nodes;
  return [];
}

function _stepVariant(status) {
  if (status === "done" || status === "completed" || status === "success") return "done";
  if (status === "running" || status === "executing") return "running";
  if (status === "failed" || status === "error") return "failed";
  if (status === "skipped") return "skipped";
  return "pending";
}

function _isCancellable(status) {
  return ["running", "executing", "planning", "queued", "reviewing", "waiting", "waiting_approval"].includes(status);
}

function _fmtTime(iso) {
  if (!iso) return "—";
  const t = typeof iso === "number" ? iso : Date.parse(iso);
  if (!t || isNaN(t)) return "—";
  return new Date(t).toLocaleString();
}

function _duration(job) {
  const start = job.started_at || job.created_at;
  const end   = job.completed_at || job.ended_at || job.updated_at || Date.now();
  if (!start) return "—";
  const t0 = typeof start === "number" ? start : Date.parse(start);
  const t1 = typeof end   === "number" ? end   : Date.parse(end);
  if (!t0 || !t1) return "—";
  const ms = Math.max(0, t1 - t0);
  const s = ms / 1000;
  if (s < 60)   return `${s.toFixed(1)}s`;
  if (s < 3600) return `${(s / 60).toFixed(1)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

function _fmtSize(bytes) {
  if (!bytes) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 ? 0 : 1)} ${u[i]}`;
}

function _inferKind(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["md", "txt", "log"].includes(ext)) return "text";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "image";
  if (["py", "js", "ts", "tsx", "jsx", "go", "rs", "java"].includes(ext)) return "code";
  if (["pdf"].includes(ext)) return "pdf";
  if (["csv", "json", "yaml", "yml"].includes(ext)) return "data";
  return "file";
}

function _iconFor(kind) {
  return {
    text: "description",
    image: "image",
    code: "code",
    pdf: "picture_as_pdf",
    data: "dataset",
    file: "draft",
  }[kind] || "draft";
}

function _fmtMetric(v) {
  if (typeof v === "number") {
    return v < 1 && v > 0 ? v.toFixed(2) : String(Math.round(v * 100) / 100);
  }
  return String(v);
}

function _checkIcon(c) {
  const ok = c.passed === true || c.pass === true || c.ok === true || c.status === "ok";
  const icon = ok ? "check_circle" : "cancel";
  const cls  = ok ? "lm-status--ok" : "lm-status--failed";
  return `<span class="material-symbols-outlined ${cls}" style="font-size:14px;vertical-align:middle;">${icon}</span>`;
}
