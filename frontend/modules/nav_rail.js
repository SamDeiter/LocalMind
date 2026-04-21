/**
 * nav_rail.js — Primary navigation (v2 IA)
 * Six destinations: home, jobs, artifacts, knowledge, ops, settings.
 * Plus the persistent "+ New Job" drawer trigger.
 */

// ── Constants ───────────────────────────────────────────────────
const NAV_ITEMS = ["home", "jobs", "artifacts", "knowledge", "ops", "settings"];
const LABELS = {
  home: "Home",
  jobs: "Jobs",
  artifacts: "Artifacts",
  knowledge: "Knowledge",
  ops: "Operations",
  settings: "Settings",
};

// Main panel IDs are PascalCase keyed (mainHome, mainJobs, ...)
const PANEL_ID = {
  home: "mainHome",
  jobs: "mainJobs",
  artifacts: "mainArtifacts",
  knowledge: "mainKnowledge",
  ops: "mainOps",
  settings: "mainSettings",
};

let _currentNav = "home";
const _lazyInited = new Set();

// ── Public API ──────────────────────────────────────────────────

/** Switch to a nav section by key. */
export function switchNav(key) {
  if (!NAV_ITEMS.includes(key)) return;
  _currentNav = key;

  // Nav rail items — shell.css uses aria-current="page" for active state
  document.querySelectorAll("#navRail [data-nav]").forEach((btn) => {
    const active = btn.dataset.nav === key;
    btn.setAttribute("aria-selected", String(active));
    if (active) {
      btn.setAttribute("aria-current", "page");
    } else {
      btn.removeAttribute("aria-current");
    }
  });

  // Main panels (show/hide via [hidden])
  NAV_ITEMS.forEach((id) => {
    const panel = document.getElementById(PANEL_ID[id]);
    if (!panel) return;
    if (id === key) {
      panel.hidden = false;
      panel.classList.remove("lm-page--hidden");
    } else {
      panel.hidden = true;
      panel.classList.add("lm-page--hidden");
    }
  });

  // URL hash for deep linking
  try {
    history.replaceState(null, "", `#/${key}`);
  } catch (_) { /* ignore */ }

  // Lazy init the section on first visit
  _lazyInit(key);
}

/** Get current nav key. */
export function currentNav() {
  return _currentNav;
}

// ── Init ────────────────────────────────────────────────────────

export function initNavRail() {
  // Nav rail item clicks
  document.querySelectorAll("#navRail [data-nav]").forEach((btn) => {
    btn.addEventListener("click", () => switchNav(btn.dataset.nav));
  });

  // "+ New Job" CTA — open drawer
  const newJobBtn = document.getElementById("newJobBtn");
  newJobBtn?.addEventListener("click", () => _openNewJobDrawer());

  // Any element marked [data-open-new-job] opens the drawer
  document.querySelectorAll("[data-open-new-job]").forEach((el) => {
    el.addEventListener("click", () => _openNewJobDrawer());
  });

  // Drawer close (scrim or X button)
  document.querySelectorAll("[data-close-drawer]").forEach((el) => {
    el.addEventListener("click", () => _closeNewJobDrawer());
  });

  // Collapse the nav rail
  document.getElementById("navCollapseBtn")?.addEventListener("click", () => {
    const app = document.getElementById("lmApp");
    if (!app) return;
    const collapsed = app.dataset.navCollapsed === "true";
    app.dataset.navCollapsed = collapsed ? "false" : "true";
  });

  // Keyboard: N = new job, Esc = close drawer, Cmd/Ctrl+K = focus search
  document.addEventListener("keydown", (e) => {
    const target = e.target;
    const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);

    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      document.getElementById("globalSearch")?.focus();
      return;
    }

    if (e.key === "Escape") {
      if (_isDrawerOpen()) _closeNewJobDrawer();
      return;
    }

    if (!typing && e.key.toLowerCase() === "n") {
      _openNewJobDrawer();
    }
  });

  // Initial nav from hash, else home
  const hashKey = (location.hash || "").replace(/^#\/?/, "").split("/")[0];
  const start = NAV_ITEMS.includes(hashKey) ? hashKey : "home";
  switchNav(start);
}

// ── New Job drawer ──────────────────────────────────────────────

function _openNewJobDrawer() {
  const drawer = document.getElementById("newJobDrawer");
  if (!drawer) return;
  drawer.dataset.open = "true";
  drawer.setAttribute("aria-hidden", "false");
  // Focus the first input inside the drawer if present
  setTimeout(() => {
    const firstField = drawer.querySelector("textarea, input, button");
    firstField?.focus();
  }, 50);
}

function _closeNewJobDrawer() {
  const drawer = document.getElementById("newJobDrawer");
  if (!drawer) return;
  drawer.dataset.open = "false";
  drawer.setAttribute("aria-hidden", "true");
}

function _isDrawerOpen() {
  const drawer = document.getElementById("newJobDrawer");
  return drawer?.dataset.open === "true";
}

// ── Lazy initialization per section ─────────────────────────────

function _lazyInit(key) {
  if (_lazyInited.has(key)) return;
  _lazyInited.add(key);

  switch (key) {
    case "jobs":
      import("./jobs_ui.js")
        .then((m) => m.showJobsView?.())
        .catch(() => {});
      break;
    case "artifacts":
      import("./artifacts_ui.js")
        .then((m) => m.initArtifactsUI?.())
        .catch(() => {});
      break;
    case "knowledge":
      import("./knowledge_ui.js")
        .then((m) => m.initKnowledgeUI?.())
        .catch(() => {});
      break;
    case "ops":
      import("./ops_ui.js")
        .then((m) => m.initOpsUI?.())
        .catch(() => {});
      break;
    case "settings":
      import("./settings_page.js")
        .then((m) => m.initSettingsPage?.())
        .catch(() => {});
      break;
    default:
      break;
  }
}
