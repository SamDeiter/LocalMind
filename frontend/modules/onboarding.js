/**
 * Onboarding Tutorial — guided first-run walkthrough with AI interview.
 *
 * State machine with spotlight overlays that highlights key UI areas
 * and collects a multi-step user profile (name, role, interests,
 * communication style, tools). The profile is encrypted server-side
 * via AES-256-GCM and stored as individual MemoryManager preferences.
 *
 * Persists completion to localStorage so it only shows once.
 */

import { API, state } from "./state.js";

const LS_KEY = "localmind_onboarding_complete";
const LS_WELCOME_KEY = "localmind_welcome_dismissed";
const LS_NAME_KEY = "localmind_user_name";
const LS_PROFILE_KEY = "localmind_user_profile";

// ── Interview option data ───────────────────────────────────────────

const ROLE_OPTIONS = [
  "Developer",
  "Data Scientist",
  "Designer",
  "Manager",
  "Student",
  "Other",
];

const INTEREST_OPTIONS = [
  "AI/ML",
  "Web Dev",
  "Data Analysis",
  "DevOps",
  "Security",
  "Research",
  "Creative",
  "Other",
];

const STYLE_OPTIONS = [
  { value: "concise", label: "Concise & Technical" },
  { value: "detailed", label: "Detailed & Explanatory" },
  { value: "casual", label: "Casual & Friendly" },
];

// ── Step definitions ────────────────────────────────────────────────
const STEPS = [
  {
    id: "welcome",
    title: "Welcome to LocalMind",
    body: "Your autonomous AI task worker — running 100% locally on your machine. Let's get to know you, then take a quick tour.",
    target: null,
    position: "center",
  },
  // ── AI Interview Steps ──
  {
    id: "name",
    title: "What should I call you?",
    body: null,
    target: null,
    position: "center",
    customRender: "name",
  },
  {
    id: "role",
    title: "What kind of work do you primarily do?",
    body: null,
    target: null,
    position: "center",
    customRender: "role",
  },
  {
    id: "interests",
    title: "What topics interest you most?",
    body: "Pick as many as you like — this helps me tailor suggestions.",
    target: null,
    position: "center",
    customRender: "interests",
  },
  {
    id: "style",
    title: "How should I communicate with you?",
    body: null,
    target: null,
    position: "center",
    customRender: "style",
  },
  {
    id: "tools",
    title: "Any tools or languages you use daily?",
    body: null,
    target: null,
    position: "center",
    customRender: "tools",
  },
  {
    id: "encrypting",
    title: "Encrypting your profile...",
    body: null,
    target: null,
    position: "center",
    customRender: "encrypting",
  },
  // ── Tour Steps ──
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

// User profile collected during the interview
let _userProfile = {
  name: "",
  role: "",
  role_other: "",
  interests: [],
  communication_style: "",
  tools: "",
};

// ── Public API ──────────────────────────────────────────────────────

export function initOnboarding() {
  // Show full onboarding wizard if never completed
  if (localStorage.getItem(LS_KEY) !== "done") {
    setTimeout(_start, 600);
    return;
  }

  // Otherwise, show the lightweight welcome overlay if not yet dismissed
  // and the user has no conversations or messages
  if (localStorage.getItem(LS_WELCOME_KEY) !== "done") {
    setTimeout(_showWelcomeOverlay, 800);
  }
}

// ── Lightweight Welcome Overlay ────────────────────────────────────

function _showWelcomeOverlay() {
  // Only show if user has no active conversations
  if (state.conversations && state.conversations.length > 0) return;
  if (state.messages && state.messages.length > 0) return;

  const overlay = document.createElement("div");
  overlay.className = "welcome-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Welcome to LocalMind");

  overlay.innerHTML = `
    <div class="welcome-overlay-card bg-slate-900 border border-slate-700/50 shadow-2xl">
      <span class="material-symbols-outlined text-indigo-400 mb-3" style="font-size:48px" aria-hidden="true">neurology</span>
      <h2 class="text-xl font-bold text-white mb-2" style="font-family:'Space Grotesk',sans-serif">Welcome to LocalMind</h2>
      <p class="text-sm text-slate-400 mb-5 leading-relaxed">Your autonomous AI task worker, running 100% locally. Here are some things you can do:</p>

      <div class="flex flex-col gap-1 mb-6">
        <div class="welcome-feature" aria-label="Drop a file to analyze it">
          <span class="material-symbols-outlined text-indigo-400 text-lg mt-0.5" aria-hidden="true">upload_file</span>
          <div>
            <p class="text-sm font-semibold text-slate-200">Drop a file to get started</p>
            <p class="text-xs text-slate-500">Analyze documents, images, code, and more</p>
          </div>
        </div>
        <div class="welcome-feature" aria-label="Ask LocalMind to research something">
          <span class="material-symbols-outlined text-emerald-400 text-lg mt-0.5" aria-hidden="true">travel_explore</span>
          <div>
            <p class="text-sm font-semibold text-slate-200">Ask me to research something</p>
            <p class="text-xs text-slate-500">Web search, summarize findings, generate reports</p>
          </div>
        </div>
        <div class="welcome-feature" aria-label="Create a PowerPoint deck or document">
          <span class="material-symbols-outlined text-amber-400 text-lg mt-0.5" aria-hidden="true">slideshow</span>
          <div>
            <p class="text-sm font-semibold text-slate-200">Create a PowerPoint deck</p>
            <p class="text-xs text-slate-500">Generate presentations, spreadsheets, and documents</p>
          </div>
        </div>
        <div class="welcome-feature" aria-label="Write and run code">
          <span class="material-symbols-outlined text-cyan-400 text-lg mt-0.5" aria-hidden="true">code</span>
          <div>
            <p class="text-sm font-semibold text-slate-200">Write and run code</p>
            <p class="text-xs text-slate-500">Python, JavaScript, shell scripts, and more</p>
          </div>
        </div>
      </div>

      <button
        id="welcomeDismissBtn"
        class="w-full py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-bold rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500/50"
        aria-label="Dismiss welcome message"
      >Got it</button>
    </div>
  `;

  document.body.appendChild(overlay);

  const dismissBtn = overlay.querySelector("#welcomeDismissBtn");
  const dismiss = () => {
    localStorage.setItem(LS_WELCOME_KEY, "done");
    overlay.classList.add("welcome-fade-out");
    setTimeout(() => overlay.remove(), 250);
  };

  dismissBtn.addEventListener("click", dismiss);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) dismiss();
  });
  // Focus the dismiss button for keyboard accessibility
  dismissBtn.focus();
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
    "rounded-2xl shadow-2xl shadow-primary/10 p-6 max-w-md " +
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

  // Custom interview step renderers
  if (step.customRender) {
    html += _renderCustomStep(step);
  } else if (step.body) {
    html += `<p class="text-sm text-slate-400 leading-relaxed mb-4">${step.body}</p>`;
  }

  // Navigation buttons (skip the encrypting step — it auto-advances)
  if (step.customRender !== "encrypting") {
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
  }

  _tooltip.innerHTML = html;
  _positionTooltip(targetEl, step.position);

  // Bind buttons
  const nextBtn = document.getElementById("onbNext");
  const backBtn = document.getElementById("onbBack");
  const skipBtn = document.getElementById("onbSkip");
  if (nextBtn) nextBtn.addEventListener("click", _next);
  if (backBtn) backBtn.addEventListener("click", _prev);
  if (skipBtn) skipBtn.addEventListener("click", _finish);

  // Post-render hooks for custom steps
  _bindCustomStep(step);
}

// ── Custom step renderers ───────────────────────────────────────────

function _renderCustomStep(step) {
  switch (step.customRender) {
    case "name":
      return `<p class="text-sm text-slate-400 mb-3">I'd love to know what to call you.</p>
        <div class="mb-4">
          <input id="onboardingNameInput" type="text" maxlength="40"
            placeholder="Your name (optional)"
            class="w-full bg-surface-container-high border border-outline-variant/40
              rounded-lg px-3 py-2 text-sm text-white placeholder:text-outline
              focus:outline-none focus:ring-1 focus:ring-primary" />
        </div>`;

    case "role":
      return `<p class="text-sm text-slate-400 mb-3">This helps me understand the kind of tasks you'll throw my way.</p>
        <div class="flex flex-wrap gap-2 mb-3" id="onbRoleChips">
          ${ROLE_OPTIONS.map(
            (r) =>
              `<button data-role="${r}"
                class="onb-role-chip px-3 py-1.5 text-xs rounded-full border transition-all duration-200
                  ${_userProfile.role === r
                    ? "bg-primary/20 border-primary text-primary"
                    : "bg-surface-container-high border-outline-variant/40 text-slate-400 hover:border-primary/50 hover:text-white"
                  }">${r}</button>`
          ).join("")}
        </div>
        <div id="onbRoleOtherWrap" class="${_userProfile.role === "Other" ? "" : "hidden"} mb-4">
          <input id="onbRoleOther" type="text" maxlength="60"
            placeholder="Tell me more..."
            value="${_userProfile.role_other}"
            class="w-full bg-surface-container-high border border-outline-variant/40
              rounded-lg px-3 py-2 text-sm text-white placeholder:text-outline
              focus:outline-none focus:ring-1 focus:ring-primary" />
        </div>`;

    case "interests":
      return `<div class="flex flex-wrap gap-2 mb-4" id="onbInterestChips">
          ${INTEREST_OPTIONS.map(
            (t) =>
              `<button data-interest="${t}"
                class="onb-interest-chip px-3 py-1.5 text-xs rounded-full border transition-all duration-200 cursor-pointer
                  ${_userProfile.interests.includes(t)
                    ? "bg-primary/20 border-primary text-primary"
                    : "bg-surface-container-high border-outline-variant/40 text-slate-400 hover:border-primary/50 hover:text-white"
                  }">${t}</button>`
          ).join("")}
        </div>`;

    case "style":
      return `<p class="text-sm text-slate-400 mb-3">Everyone has a preference — pick yours.</p>
        <div class="flex flex-col gap-2 mb-4" id="onbStyleOptions">
          ${STYLE_OPTIONS.map(
            (s) =>
              `<label data-style="${s.value}"
                class="onb-style-option flex items-center gap-3 px-4 py-2.5 rounded-xl border cursor-pointer transition-all duration-200
                  ${_userProfile.communication_style === s.value
                    ? "bg-primary/20 border-primary"
                    : "bg-surface-container-high border-outline-variant/40 hover:border-primary/50"
                  }">
                <span class="w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 transition-all
                  ${_userProfile.communication_style === s.value
                    ? "border-primary"
                    : "border-outline-variant"
                  }">
                  ${_userProfile.communication_style === s.value
                    ? '<span class="w-2 h-2 rounded-full bg-primary"></span>'
                    : ""
                  }
                </span>
                <span class="text-sm ${_userProfile.communication_style === s.value ? "text-white" : "text-slate-400"}">${s.label}</span>
              </label>`
          ).join("")}
        </div>`;

    case "tools":
      return `<p class="text-sm text-slate-400 mb-3">e.g. Python, VS Code, Docker, React, PostgreSQL...</p>
        <div class="mb-4">
          <input id="onbToolsInput" type="text" maxlength="200"
            placeholder="Comma-separated or free text"
            value="${_userProfile.tools}"
            class="w-full bg-surface-container-high border border-outline-variant/40
              rounded-lg px-3 py-2 text-sm text-white placeholder:text-outline
              focus:outline-none focus:ring-1 focus:ring-primary" />
        </div>`;

    case "encrypting":
      return `<div class="flex flex-col items-center py-6">
          <div class="relative mb-4">
            <div class="w-12 h-12 rounded-full border-2 border-primary/30 border-t-primary animate-spin"></div>
            <svg class="absolute inset-0 m-auto w-5 h-5 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <p class="text-sm text-slate-400">Securely encrypting your profile with AES-256...</p>
        </div>`;

    default:
      return "";
  }
}

function _bindCustomStep(step) {
  if (!step.customRender) return;

  switch (step.customRender) {
    case "name": {
      const nameInput = document.getElementById("onboardingNameInput");
      if (nameInput) {
        nameInput.value = _userProfile.name;
        nameInput.focus();
        nameInput.addEventListener("keydown", (e) => {
          if (e.key === "Enter") _next();
        });
      }
      break;
    }

    case "role": {
      document.querySelectorAll(".onb-role-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const role = chip.dataset.role;
          _userProfile.role = role;
          // Re-render to update visual state
          _renderStep();
        });
      });
      const otherInput = document.getElementById("onbRoleOther");
      if (otherInput) {
        otherInput.addEventListener("keydown", (e) => {
          if (e.key === "Enter") _next();
        });
      }
      break;
    }

    case "interests": {
      document.querySelectorAll(".onb-interest-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const interest = chip.dataset.interest;
          const idx = _userProfile.interests.indexOf(interest);
          if (idx >= 0) {
            _userProfile.interests.splice(idx, 1);
          } else {
            _userProfile.interests.push(interest);
          }
          // Re-render to update visual state
          _renderStep();
        });
      });
      break;
    }

    case "style": {
      document.querySelectorAll(".onb-style-option").forEach((opt) => {
        opt.addEventListener("click", () => {
          _userProfile.communication_style = opt.dataset.style;
          // Re-render to update visual state
          _renderStep();
        });
      });
      break;
    }

    case "tools": {
      const toolsInput = document.getElementById("onbToolsInput");
      if (toolsInput) {
        toolsInput.focus();
        toolsInput.addEventListener("keydown", (e) => {
          if (e.key === "Enter") _next();
        });
      }
      break;
    }

    case "encrypting": {
      // Auto-advance after saving profile
      _saveProfileToBackend().then(() => {
        setTimeout(() => {
          _step++;
          _renderStep();
        }, 1200);
      });
      break;
    }
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

function _captureCurrentStep() {
  const step = STEPS[_step];
  if (!step || !step.customRender) return;

  switch (step.customRender) {
    case "name": {
      const nameInput = document.getElementById("onboardingNameInput");
      if (nameInput) {
        _userProfile.name = nameInput.value.trim();
        _userName = _userProfile.name;
      }
      break;
    }
    case "role": {
      const otherInput = document.getElementById("onbRoleOther");
      if (otherInput) {
        _userProfile.role_other = otherInput.value.trim();
      }
      break;
    }
    case "tools": {
      const toolsInput = document.getElementById("onbToolsInput");
      if (toolsInput) {
        _userProfile.tools = toolsInput.value.trim();
      }
      break;
    }
  }
}

function _next() {
  _captureCurrentStep();
  _step++;
  _renderStep();
}

function _prev() {
  _captureCurrentStep();
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
  }

  // Persist the whole profile to localStorage for offline access
  localStorage.setItem(LS_PROFILE_KEY, JSON.stringify(_userProfile));

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

async function _saveProfileToBackend() {
  const payload = {
    name: _userProfile.name,
    role:
      _userProfile.role === "Other" && _userProfile.role_other
        ? _userProfile.role_other
        : _userProfile.role,
    interests: _userProfile.interests,
    communication_style: _userProfile.communication_style,
    tools: _userProfile.tools,
    user_id: "default",
  };

  try {
    await fetch(`${API}/api/user-profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // Non-critical — profile saves to localStorage as fallback
  }

  // Also save name to memories for backwards compatibility
  if (_userProfile.name) {
    try {
      await fetch(`${API}/api/memories`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: `The user's name is ${_userProfile.name}.`,
          category: "user_preference",
          subcategory: "identity",
        }),
      });
    } catch {
      // Non-critical
    }
  }
}
