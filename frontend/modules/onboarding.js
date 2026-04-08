/**
 * Onboarding Tutorial — guided first-run walkthrough.
 *
 * State machine with spotlight overlays that highlights key UI areas
 * and optionally collects user preferences (name, mode).
 * Persists completion to localStorage so it only shows once.
 */

import { API } from "./state.js";

const LS_KEY = "localmind_onboarding_complete";
const LS_NAME_KEY = "localmind_user_name";

// ── Step definitions ────────────────────────────────────────────────
const STEPS = [
  {
    id: "welcome",
    title: "Welcome to LocalMind",
    body: "Your autonomous AI task worker — running 100% locally on your machine. Let's take a quick tour.",
    target: null, // no spotlight, centered modal
    position: "center",
  },
  {
    id: "name",
    title: "What should I call you?",
    body: null, // custom render (input field)
    target: null,
    position: "center",
    inputField: true,
  },
  {
    id: "task_input",
    title: "Task Input",
    body: "Describe any task here and hit Execute. LocalMind will plan, research, and complete it autonomously.",
    target: "#priorityInput",
    position: "right",
  },
  {
    id: "sidebar_nav",
    title: "Navigation",
    body: "Switch between the Dashboard, Chat, Jobs, and Templates from the sidebar.",
    target: "#overviewBtn",
    position: "right",
  },
  {
    id: "mode_toggle",
    title: "Operation Mode",
    body: "Supervised mode asks for approval before each step. Autonomous mode lets the AI run freely.",
    target: "#modeSupervisedBtn",
    position: "right",
  },
  {
    id: "status_bar",
    title: "System Status",
    body: "CPU, VRAM, and RAM usage are shown in real-time so you know what your machine is doing.",
    target: "#cpuVal",
    position: "bottom",
  },
  {
    id: "done",
    title: "You're all set!",
    body: "LocalMind is ready. Describe a task in the sidebar, or start a chat session. Happy building!",
    target: null,
    position: "center",
  },
];

// ── State ───────────────────────────────────────────────────────────
let _step = 0;
let _overlay = null;
let _tooltip = null;
let _userName = "";

// ── Public API ──────────────────────────────────────────────────────

export function initOnboarding() {
  if (localStorage.getItem(LS_KEY) === "done") return;
  // Small delay to let the DOM settle after app.js init
  setTimeout(_start, 600);
}

// ── Internal ────────────────────────────────────────────────────────

function _start() {
  _step = 0;
  _createOverlay();
  _renderStep();
}

function _createOverlay() {
  // Full-screen overlay with a transparent "hole" cut via CSS
  _overlay = document.createElement("div");
  _overlay.id = "onboardingOverlay";
  _overlay.className =
    "fixed inset-0 z-[9999] transition-opacity duration-300";
  _overlay.style.cssText =
    "background: rgba(2,6,23,0.82); pointer-events: auto;";

  _tooltip = document.createElement("div");
  _tooltip.id = "onboardingTooltip";
  _tooltip.className =
    "fixed z-[10000] bg-surface-container border border-primary/30 " +
    "rounded-2xl shadow-2xl shadow-primary/10 p-6 max-w-sm " +
    "transition-all duration-300 ease-out";

  document.body.appendChild(_overlay);
  document.body.appendChild(_tooltip);
}

function _renderStep() {
  const step = STEPS[_step];
  if (!step) return _finish();

  // Spotlight target element
  const targetEl = step.target ? document.querySelector(step.target) : null;
  if (targetEl) {
    _spotlightElement(targetEl);
  } else {
    // No spotlight — just darken
    _overlay.style.background = "rgba(2,6,23,0.82)";
    _overlay.style.clipPath = "";
  }

  // Build tooltip content
  let html = `<div class="text-xs font-bold uppercase tracking-widest text-primary mb-2">
    Step ${_step + 1} of ${STEPS.length}
  </div>
  <h3 class="text-lg font-headline font-bold text-white mb-2">${step.title}</h3>`;

  if (step.inputField) {
    html += `<div class="mb-4">
      <input id="onboardingNameInput" type="text" maxlength="40"
        placeholder="Your name (optional)"
        class="w-full bg-surface-container-high border border-outline-variant/40
          rounded-lg px-3 py-2 text-sm text-white placeholder:text-outline
          focus:outline-none focus:ring-1 focus:ring-primary" />
    </div>`;
  } else if (step.body) {
    html += `<p class="text-sm text-slate-400 leading-relaxed mb-4">${step.body}</p>`;
  }

  // Navigation buttons
  const isFirst = _step === 0;
  const isLast = _step === STEPS.length - 1;
  html += `<div class="flex items-center justify-between gap-3">`;
  if (!isFirst) {
    html += `<button id="onbBack" class="text-xs text-slate-500 hover:text-white transition-colors">Back</button>`;
  } else {
    html += `<button id="onbSkip" class="text-xs text-slate-500 hover:text-white transition-colors">Skip tour</button>`;
  }
  html += `<button id="onbNext"
    class="primary-gradient text-on-primary text-xs font-bold px-5 py-2 rounded-lg
      hover:brightness-110 active:scale-95 transition-all">
    ${isLast ? "Get Started" : "Next"}
  </button>`;
  html += `</div>`;

  _tooltip.innerHTML = html;
  _positionTooltip(targetEl, step.position);

  // Bind buttons
  const nextBtn = document.getElementById("onbNext");
  const backBtn = document.getElementById("onbBack");
  const skipBtn = document.getElementById("onbSkip");
  if (nextBtn) nextBtn.addEventListener("click", _next);
  if (backBtn) backBtn.addEventListener("click", _prev);
  if (skipBtn) skipBtn.addEventListener("click", _finish);

  // Auto-focus name input
  const nameInput = document.getElementById("onboardingNameInput");
  if (nameInput) {
    nameInput.value = _userName;
    nameInput.focus();
    nameInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") _next();
    });
  }
}

function _spotlightElement(el) {
  const r = el.getBoundingClientRect();
  const pad = 8;
  // CSS clip-path: polygon that carves a rectangular hole
  const x1 = r.left - pad,
    y1 = r.top - pad;
  const x2 = r.right + pad,
    y2 = r.bottom + pad;
  _overlay.style.clipPath = `polygon(
    0% 0%, 0% 100%, ${x1}px 100%, ${x1}px ${y1}px,
    ${x2}px ${y1}px, ${x2}px ${y2}px, ${x1}px ${y2}px,
    ${x1}px 100%, 100% 100%, 100% 0%)`;
}

function _positionTooltip(targetEl, position) {
  if (!targetEl || position === "center") {
    // Center on screen
    _tooltip.style.left = "50%";
    _tooltip.style.top = "50%";
    _tooltip.style.transform = "translate(-50%, -50%)";
    return;
  }

  const r = targetEl.getBoundingClientRect();
  _tooltip.style.transform = "";

  if (position === "right") {
    _tooltip.style.left = `${r.right + 16}px`;
    _tooltip.style.top = `${r.top}px`;
  } else if (position === "bottom") {
    _tooltip.style.left = `${r.left}px`;
    _tooltip.style.top = `${r.bottom + 12}px`;
  } else if (position === "left") {
    _tooltip.style.left = `${r.left - 400}px`;
    _tooltip.style.top = `${r.top}px`;
  }

  // Clamp inside viewport
  requestAnimationFrame(() => {
    const tr = _tooltip.getBoundingClientRect();
    if (tr.right > window.innerWidth - 16)
      _tooltip.style.left = `${window.innerWidth - tr.width - 16}px`;
    if (tr.bottom > window.innerHeight - 16)
      _tooltip.style.top = `${window.innerHeight - tr.height - 16}px`;
    if (tr.left < 16) _tooltip.style.left = "16px";
    if (tr.top < 16) _tooltip.style.top = "16px";
  });
}

function _next() {
  // Capture name if on the name step
  const nameInput = document.getElementById("onboardingNameInput");
  if (nameInput) {
    _userName = nameInput.value.trim();
  }
  _step++;
  _renderStep();
}

function _prev() {
  if (_step > 0) {
    _step--;
    _renderStep();
  }
}

function _finish() {
  localStorage.setItem(LS_KEY, "done");

  // Save user name if provided
  if (_userName) {
    localStorage.setItem(LS_NAME_KEY, _userName);
    _saveNameToMemory(_userName);
  }

  // Animate out
  if (_overlay) {
    _overlay.style.opacity = "0";
    _tooltip.style.opacity = "0";
    setTimeout(() => {
      _overlay?.remove();
      _tooltip?.remove();
      _overlay = null;
      _tooltip = null;
    }, 300);
  }
}

async function _saveNameToMemory(name) {
  try {
    await fetch(`${API}/api/memories`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: `The user's name is ${name}.`,
        category: "user_preference",
        subcategory: "identity",
      }),
    });
  } catch {
    // Non-critical — silently ignore
  }
}
