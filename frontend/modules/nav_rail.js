/**
 * nav_rail.js — Nav rail + sidebar + main panel switching.
 * Replaces the old 3-tab shell (tabWork / tabChat / tabSystem).
 */

// ── Constants ───────────────────────────────────────────────────
const NAV_ITEMS = ["chat", "pipeline", "memory", "system", "tools", "autonomy", "config", "brain"];
const LABELS = {
  chat: "Chat",
  pipeline: "Pipeline",
  memory: "Memory",
  system: "System",
  tools: "Tools",
  autonomy: "Agent Mode",
  config: "Config",
  brain: "Intelligence Map",
};

let _currentNav = "chat";
const _lazyInited = new Set();

// ── Public API ──────────────────────────────────────────────────

/** Switch to a nav section by key (e.g. "chat", "pipeline"). */
export function switchNav(key) {
  if (!NAV_ITEMS.includes(key)) return;
  _currentNav = key;

  // 1. Nav rail buttons
  document.querySelectorAll("#navRail [data-nav]").forEach((btn) => {
    const active = btn.dataset.nav === key;
    btn.classList.toggle("nav-rail-active", active);
    btn.setAttribute("aria-selected", String(active));
  });

  // 2. Sidebar panels
  NAV_ITEMS.forEach((id) => {
    const panel = document.getElementById(`sidebar${_cap(id)}`);
    if (panel) panel.classList.toggle("hidden", id !== key);
  });

  // 3. Main panels
  NAV_ITEMS.forEach((id) => {
    const panel = document.getElementById(`main${_cap(id)}`);
    if (panel) panel.classList.toggle("hidden", id !== key);
  });

  // 4. Breadcrumb
  const crumb = document.getElementById("mainBreadcrumb");
  if (crumb) crumb.textContent = LABELS[key] || key;

  // 5. Lazy init
  _lazyInit(key);
}

/** Get current nav key. */
export function currentNav() {
  return _currentNav;
}

// ── Init ────────────────────────────────────────────────────────

export function initNavRail() {
  // Nav rail button clicks
  document.querySelectorAll("#navRail [data-nav]").forEach((btn) => {
    btn.addEventListener("click", () => switchNav(btn.dataset.nav));
  });

  // "Submit Job" header button → switch to pipeline
  document.getElementById("submitJobHeaderBtn")?.addEventListener("click", () => {
    switchNav("pipeline");
    // Focus the job title input if it exists
    setTimeout(() => {
      document.getElementById("jobTitleInput")?.focus();
    }, 100);
  });

  // Start on chat
  switchNav("chat");
}

// ── Lazy Initialization ─────────────────────────────────────────

function _lazyInit(key) {
  if (_lazyInited.has(key)) return;
  _lazyInited.add(key);

  switch (key) {
    case "pipeline":
      // Jobs UI is already initialized by app.js — just ensure it renders
      import("./jobs_ui.js")
        .then((m) => m.showJobsView?.())
        .catch(() => {});
      break;
    case "system":
      // Accordion lazy-init is handled by events.js _lazyInitAccordion
      // but start swarm polling eagerly when system tab first opens
      import("./swarm_ui.js")
        .then((m) => {
          m.initSwarmTabs?.();
          m.startPolling?.();
        })
        .catch(() => {});
      break;
    case "memory":
      // Memory sidebar is populated by sidebar.js loadMemories on init
      break;
    case "tools":
      // Generated tools list
      import("./tools_generated.js")
        .then((m) => m.loadGeneratedTools?.())
        .catch(() => {});
      break;
    case "autonomy":
      // Agent Mode UI
      import("./autonomy_ui.js")
        .then((m) => m.initAutonomyUI?.())
        .catch(() => {});
      break;
    case "brain":
      import("./intelligence_map_ui.js")
        .then((m) => m.loadIntelligenceMap?.())
        .catch(() => {});
      break;
    default:
      break;
  }
}

// ── Helpers ─────────────────────────────────────────────────────

function _cap(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
