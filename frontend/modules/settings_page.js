/**
 * settings_page.js — LocalMind v2 Settings
 *
 * Single scrollable page with a sticky 220px anchor sidebar on the left
 * and sections in sequence on the right. Industrial calm aesthetic.
 *
 * Sections:
 *   1. Profile
 *   2. Model routing
 *   3. Safety rails
 *   4. Integrations → Google
 *   5. API keys
 *   6. Appearance
 *   7. Advanced
 *   8. About
 *
 * Backend contracts (verified by grepping backend/routes/):
 *   - GET  /api/user-profile, POST /api/user-profile      (settings.py)
 *   - GET  /api/models                                     (system.py)
 *   - GET  /api/google/status                              (google_auth.py)
 *   - GET  /api/google/auth      (HTTP redirect → popup)   (google_auth.py)
 *   - POST /api/google/revoke                              (google_auth.py)
 *   - GET  /api/admin/keys                                 (admin.py, admin-only)
 *   - GET  /api/version                                    (system.py)
 *
 * Stubbed (no backend endpoint exists yet, persists to localStorage):
 *   - Model routing rules  (key: lm-routing)
 *   - Safety rails         (key: lm-safety)
 *   - Appearance           (key: lm-appearance)
 *   - Advanced toggles     (key: lm-advanced)
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

let _inited = false;
let _googlePollId = null;
let _googlePollTimeoutId = null;

// ── Public API ──────────────────────────────────────────────────

export function initSettingsPage() {
  const target = document.getElementById("settingsContainer");
  if (!target) return;

  // Always re-render so we pick up fresh state on tab revisit.
  target.innerHTML = _render();
  _bindEvents(target);

  if (_inited) {
    // Already loaded once — just refresh data.
    _loadAll();
    return;
  }
  _inited = true;

  _loadAll();
}

export function stopSettingsPage() {
  _stopGooglePolling();
}

// ── Top-level render ────────────────────────────────────────────

function _render() {
  return /* html */ `
    <header class="lm-page__header">
      <div>
        <div class="lm-page__eyebrow">Preferences</div>
        <h1 class="lm-page__title">Settings</h1>
        <p class="lm-page__subtitle">Profile, models, safety, integrations, and appearance.</p>
      </div>
    </header>

    <div class="lm-settings">
      <aside class="lm-settings__nav" aria-label="Settings sections">
        <nav class="lm-settings__nav-list" id="settingsNavList">
          ${_navItem("profile", "person", "Profile")}
          ${_navItem("routing", "deployed_code", "Model routing")}
          ${_navItem("safety", "shield", "Safety rails")}
          ${_navItem("integrations", "hub", "Integrations")}
          ${_navItem("keys", "key", "API keys")}
          ${_navItem("appearance", "palette", "Appearance")}
          ${_navItem("advanced", "science", "Advanced")}
          ${_navItem("about", "info", "About")}
        </nav>
      </aside>

      <div class="lm-settings__content" id="settingsContent">
        ${_sectionProfile()}
        ${_sectionRouting()}
        ${_sectionSafety()}
        ${_sectionIntegrations()}
        ${_sectionKeys()}
        ${_sectionAppearance()}
        ${_sectionAdvanced()}
        ${_sectionAbout()}
      </div>
    </div>
  `;
}

function _navItem(id, icon, label) {
  return /* html */ `
    <a href="#settings-${id}" class="lm-settings__nav-link" data-anchor="${id}">
      <span class="material-symbols-outlined" aria-hidden="true">${icon}</span>
      <span>${escapeHtml(label)}</span>
    </a>
  `;
}

// ── Section: Profile ────────────────────────────────────────────

function _sectionProfile() {
  return /* html */ `
    <section class="lm-settings__section" id="settings-profile" data-section="profile">
      ${_sectionHeader("Profile", "Who LocalMind is working for.")}
      <form class="lm-settings__form" id="profileForm" autocomplete="off">
        <fieldset class="lm-settings__fieldset">
          <legend class="lm-settings__sr-only">Profile details</legend>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <label for="profileName">Display name</label>
              <span class="lm-settings__hint" id="profileNameHint">Shown on the top bar and in generated reports.</span>
            </div>
            <div class="lm-settings__row-control">
              <input type="text" id="profileName" class="lm-input" aria-describedby="profileNameHint" placeholder="e.g. Sam Deiter" />
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <label for="profileRole">Role</label>
              <span class="lm-settings__hint">Helps tune tone and defaults.</span>
            </div>
            <div class="lm-settings__row-control">
              <input type="text" id="profileRole" class="lm-input" placeholder="e.g. Staff engineer" />
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <label for="profileCommStyle">Communication style</label>
              <span class="lm-settings__hint">Direct, verbose, bullets-first, etc.</span>
            </div>
            <div class="lm-settings__row-control">
              <input type="text" id="profileCommStyle" class="lm-input" placeholder="Direct, concise, action-oriented" />
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <label for="profileTimezone">Timezone</label>
              <span class="lm-settings__hint">Used for scheduled briefings.</span>
            </div>
            <div class="lm-settings__row-control">
              <input type="text" id="profileTimezone" class="lm-input" readonly />
            </div>
          </div>
        </fieldset>

        <div class="lm-settings__actions">
          <button type="button" class="lm-btn lm-btn--ghost" data-action="profile-reset">Revert</button>
          <button type="submit" class="lm-btn lm-btn--primary" data-dirty-target="profile" disabled>
            <span class="material-symbols-outlined" aria-hidden="true">check</span>
            Save profile
          </button>
        </div>
      </form>
    </section>
  `;
}

// ── Section: Model routing ──────────────────────────────────────

function _sectionRouting() {
  return /* html */ `
    <section class="lm-settings__section" id="settings-routing" data-section="routing">
      ${_sectionHeader("Model routing", "Pick the default model and the routes for fast, thinking, and vision work.")}
      <form class="lm-settings__form" id="routingForm">
        <fieldset class="lm-settings__fieldset">
          <legend class="lm-settings__sr-only">Model routes</legend>

          ${_routeRow("default",  "Default",  "Everything that does not match a more specific route.")}
          ${_routeRow("fast",     "Fast",     "Short replies, autocomplete, quick reasoning.")}
          ${_routeRow("thinking", "Thinking", "Multi-step plans, longer reasoning chains.")}
          ${_routeRow("vision",   "Vision",   "Images, charts, screenshots, PDFs with figures.")}
        </fieldset>

        <div class="lm-settings__actions">
          <span class="lm-settings__status-note" id="routingSourceNote">Stored locally — routing rules are not yet synced to the backend.</span>
          <button type="button" class="lm-btn lm-btn--ghost" data-action="routing-reset">Revert</button>
          <button type="submit" class="lm-btn lm-btn--primary" data-dirty-target="routing" disabled>
            <span class="material-symbols-outlined" aria-hidden="true">check</span>
            Save routing
          </button>
        </div>
      </form>
    </section>
  `;
}

function _routeRow(key, label, hint) {
  const hintId = `routing-${key}-hint`;
  return /* html */ `
    <div class="lm-settings__row">
      <div class="lm-settings__row-label">
        <label for="routing-${key}">${escapeHtml(label)}</label>
        <span class="lm-settings__hint" id="${hintId}">${escapeHtml(hint)}</span>
      </div>
      <div class="lm-settings__row-control">
        <select id="routing-${key}" class="lm-input lm-settings__select" data-route="${key}" aria-describedby="${hintId}">
          <option value="">— not set —</option>
        </select>
      </div>
    </div>
  `;
}

// ── Section: Safety rails ───────────────────────────────────────

function _sectionSafety() {
  return /* html */ `
    <section class="lm-settings__section" id="settings-safety" data-section="safety">
      ${_sectionHeader("Safety rails", "Guardrails that apply to every autonomous job.")}
      <form class="lm-settings__form" id="safetyForm">
        <fieldset class="lm-settings__fieldset">
          <legend class="lm-settings__sr-only">Autonomy policy</legend>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <label for="safetyMaxParallel">Max parallel jobs</label>
              <span class="lm-settings__hint">Hard cap on concurrent autonomous jobs.</span>
            </div>
            <div class="lm-settings__row-control lm-settings__row-control--slider">
              <input type="range" id="safetyMaxParallel" class="lm-settings__slider" min="1" max="8" step="1" value="3" />
              <output for="safetyMaxParallel" id="safetyMaxParallelOut" class="lm-mono">3</output>
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <label for="safetyApproveThreshold">Auto-approve threshold</label>
              <span class="lm-settings__hint">Risk score below which jobs run without approval. Higher = more autonomy.</span>
            </div>
            <div class="lm-settings__row-control lm-settings__row-control--slider">
              <input type="range" id="safetyApproveThreshold" class="lm-settings__slider" min="0" max="100" step="5" value="25" />
              <output for="safetyApproveThreshold" id="safetyApproveThresholdOut" class="lm-mono">25</output>
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <span class="lm-settings__row-title">Allowed tool categories</span>
              <span class="lm-settings__hint">Toggle which tool classes any job can use.</span>
            </div>
            <div class="lm-settings__row-control">
              <div class="lm-settings__toggle-grid" id="safetyToolGrid">
                ${_toggleRow("tools_filesystem",  "Filesystem",  true)}
                ${_toggleRow("tools_network",     "Network",     true)}
                ${_toggleRow("tools_shell",       "Shell",       false)}
                ${_toggleRow("tools_email",       "Email",       true)}
                ${_toggleRow("tools_calendar",    "Calendar",    true)}
                ${_toggleRow("tools_code_write",  "Code write",  false)}
              </div>
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <label for="safetyDenylist">Denylist</label>
              <span class="lm-settings__hint">One entry per line — domains, commands, or tool names. Never used.</span>
            </div>
            <div class="lm-settings__row-control">
              <textarea id="safetyDenylist" class="lm-input lm-settings__textarea" rows="4" placeholder="rm -rf&#10;prod.example.com&#10;tool:shell.exec"></textarea>
            </div>
          </div>
        </fieldset>

        <div class="lm-settings__actions">
          <span class="lm-settings__status-note" id="safetySourceNote">Stored locally — autonomy policy endpoint is not wired yet.</span>
          <button type="button" class="lm-btn lm-btn--ghost" data-action="safety-reset">Revert</button>
          <button type="submit" class="lm-btn lm-btn--primary" data-dirty-target="safety" disabled>
            <span class="material-symbols-outlined" aria-hidden="true">check</span>
            Save rails
          </button>
        </div>
      </form>
    </section>
  `;
}

function _toggleRow(id, label, checked) {
  return /* html */ `
    <label class="lm-settings__toggle">
      <input type="checkbox" id="${id}" ${checked ? "checked" : ""} />
      <span class="lm-settings__toggle-track" aria-hidden="true"><span class="lm-settings__toggle-thumb"></span></span>
      <span class="lm-settings__toggle-label">${escapeHtml(label)}</span>
    </label>
  `;
}

// ── Section: Integrations (Google) ──────────────────────────────

function _sectionIntegrations() {
  return /* html */ `
    <section class="lm-settings__section" id="settings-integrations" data-section="integrations">
      ${_sectionHeader("Integrations", "Connect external accounts so LocalMind can pull context and take action.")}
      <div class="lm-settings__integration" id="googleIntegration" data-state="loading">
        <div class="lm-settings__integration-head">
          <div class="lm-settings__integration-identity">
            <span class="lm-settings__integration-logo" aria-hidden="true">G</span>
            <div>
              <div class="lm-settings__integration-title">Google Workspace</div>
              <div class="lm-settings__integration-sub" id="googleIntegrationSub">Checking connection…</div>
            </div>
          </div>
          <div class="lm-settings__integration-status">
            <span class="lm-settings__dot lm-settings__dot--paused" id="googleDot" aria-hidden="true"></span>
            <span id="googleStateLabel">Loading</span>
          </div>
        </div>

        <div class="lm-settings__integration-scopes" id="googleScopes" hidden>
          ${_scopeRow("drive",    "Drive",    "Read/write files and folders.")}
          ${_scopeRow("calendar", "Calendar", "Read and schedule events.")}
          ${_scopeRow("gmail",    "Gmail",    "Read, search, and draft messages.")}
        </div>

        <div class="lm-settings__integration-actions">
          <button type="button" class="lm-btn lm-btn--primary" id="googleConnectBtn">
            <span class="material-symbols-outlined" aria-hidden="true">link</span>
            Connect Google Account
          </button>
          <button type="button" class="lm-btn lm-btn--danger" id="googleDisconnectBtn" hidden>
            <span class="material-symbols-outlined" aria-hidden="true">link_off</span>
            Disconnect
          </button>
          <button type="button" class="lm-btn lm-btn--ghost" id="googleRefreshBtn">
            <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
            Refresh
          </button>
        </div>
      </div>
    </section>
  `;
}

function _scopeRow(id, label, hint) {
  return /* html */ `
    <div class="lm-settings__scope" data-scope="${id}">
      <div class="lm-settings__scope-body">
        <div class="lm-settings__scope-title">${escapeHtml(label)}</div>
        <div class="lm-settings__scope-hint">${escapeHtml(hint)}</div>
      </div>
      <span class="lm-chip lm-settings__scope-chip" data-scope-chip="${id}">Not granted</span>
    </div>
  `;
}

// ── Section: API keys ───────────────────────────────────────────

function _sectionKeys() {
  return /* html */ `
    <section class="lm-settings__section" id="settings-keys" data-section="keys">
      ${_sectionHeader("API keys", "Keys LocalMind uses to reach external providers.")}
      <div class="lm-settings__table-wrap">
        <table class="lm-settings__table" aria-describedby="apiKeysNote">
          <thead>
            <tr>
              <th scope="col">Provider</th>
              <th scope="col">Description</th>
              <th scope="col">Key</th>
              <th scope="col">Created</th>
              <th scope="col" class="lm-settings__table-actions-col">Actions</th>
            </tr>
          </thead>
          <tbody id="apiKeysBody">
            <tr><td colspan="5" class="lm-settings__table-empty">Loading keys…</td></tr>
          </tbody>
        </table>
      </div>
      <div class="lm-settings__actions">
        <span class="lm-settings__status-note" id="apiKeysNote">Requires admin privileges. Contact your workspace admin to rotate.</span>
        <button type="button" class="lm-btn lm-btn--ghost" id="apiKeysRefreshBtn">
          <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
          Refresh
        </button>
      </div>
    </section>
  `;
}

// ── Section: Appearance ─────────────────────────────────────────

function _sectionAppearance() {
  return /* html */ `
    <section class="lm-settings__section" id="settings-appearance" data-section="appearance">
      ${_sectionHeader("Appearance", "How LocalMind looks on this device.")}
      <form class="lm-settings__form" id="appearanceForm">
        <fieldset class="lm-settings__fieldset">
          <legend class="lm-settings__sr-only">Appearance</legend>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <span class="lm-settings__row-title">Theme</span>
              <span class="lm-settings__hint">Light mode is reserved; dark is the shipping theme today.</span>
            </div>
            <div class="lm-settings__row-control">
              <div class="lm-settings__segmented" role="radiogroup" aria-label="Theme">
                ${_segment("theme", "dark",   "Dark",   true)}
                ${_segment("theme", "light",  "Light",  false)}
                ${_segment("theme", "system", "System", false)}
              </div>
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <label for="accentColor">Accent color</label>
              <span class="lm-settings__hint">Default indigo. Subtle changes only.</span>
            </div>
            <div class="lm-settings__row-control lm-settings__row-control--accent">
              <input type="color" id="accentColor" class="lm-settings__color" value="#6366F1" />
              <code class="lm-mono" id="accentColorOut">#6366F1</code>
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <label for="fontSize">Font size</label>
              <span class="lm-settings__hint">Scales body text across the app.</span>
            </div>
            <div class="lm-settings__row-control lm-settings__row-control--slider">
              <input type="range" id="fontSize" class="lm-settings__slider" min="12" max="18" step="1" value="14" />
              <output for="fontSize" id="fontSizeOut" class="lm-mono">14px</output>
            </div>
          </div>
        </fieldset>

        <div class="lm-settings__actions">
          <span class="lm-settings__status-note">Saved to this browser.</span>
          <button type="button" class="lm-btn lm-btn--ghost" data-action="appearance-reset">Revert</button>
          <button type="submit" class="lm-btn lm-btn--primary" data-dirty-target="appearance" disabled>
            <span class="material-symbols-outlined" aria-hidden="true">check</span>
            Save appearance
          </button>
        </div>
      </form>
    </section>
  `;
}

function _segment(group, value, label, checked) {
  return /* html */ `
    <label class="lm-settings__segment">
      <input type="radio" name="${group}" value="${value}" ${checked ? "checked" : ""} />
      <span>${escapeHtml(label)}</span>
    </label>
  `;
}

// ── Section: Advanced ───────────────────────────────────────────

function _sectionAdvanced() {
  return /* html */ `
    <section class="lm-settings__section" id="settings-advanced" data-section="advanced">
      ${_sectionHeader("Advanced", "Developer toggles. Flip carefully.")}
      <form class="lm-settings__form" id="advancedForm">
        <fieldset class="lm-settings__fieldset">
          <legend class="lm-settings__sr-only">Advanced</legend>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <span class="lm-settings__row-title">Verbose logging</span>
              <span class="lm-settings__hint">Writes extra diagnostic lines to the console.</span>
            </div>
            <div class="lm-settings__row-control">
              ${_toggleRow("advVerbose", "Enabled", false)}
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <span class="lm-settings__row-title">Experimental features</span>
              <span class="lm-settings__hint">Opt into in-flight features before they ship.</span>
            </div>
            <div class="lm-settings__row-control">
              ${_toggleRow("advExperimental", "Enabled", false)}
            </div>
          </div>

          <div class="lm-settings__row">
            <div class="lm-settings__row-label">
              <span class="lm-settings__row-title">Streaming preview</span>
              <span class="lm-settings__hint">Render partial tool outputs as they arrive.</span>
            </div>
            <div class="lm-settings__row-control">
              ${_toggleRow("advStream", "Enabled", true)}
            </div>
          </div>
        </fieldset>

        <div class="lm-settings__actions">
          <span class="lm-settings__status-note">Saved to this browser.</span>
          <button type="button" class="lm-btn lm-btn--ghost" data-action="advanced-reset">Revert</button>
          <button type="submit" class="lm-btn lm-btn--primary" data-dirty-target="advanced" disabled>
            <span class="material-symbols-outlined" aria-hidden="true">check</span>
            Save
          </button>
        </div>
      </form>
    </section>
  `;
}

// ── Section: About ──────────────────────────────────────────────

function _sectionAbout() {
  return /* html */ `
    <section class="lm-settings__section" id="settings-about" data-section="about">
      ${_sectionHeader("About", "Build and release info.")}
      <div class="lm-settings__about">
        <div class="lm-settings__about-row">
          <span class="lm-settings__hint">Version</span>
          <span class="lm-mono" id="aboutVersion">—</span>
        </div>
        <div class="lm-settings__about-row">
          <span class="lm-settings__hint">Build</span>
          <span class="lm-mono" id="aboutBuild">—</span>
        </div>
        <div class="lm-settings__about-row">
          <span class="lm-settings__hint">Links</span>
          <span class="lm-settings__about-links">
            <a class="lm-settings__link" href="CHANGELOG.md" target="_blank" rel="noreferrer">Changelog</a>
            <a class="lm-settings__link" href="https://github.com/" target="_blank" rel="noreferrer">GitHub</a>
          </span>
        </div>
      </div>
    </section>
  `;
}

// ── Shared UI helpers ──────────────────────────────────────────

function _sectionHeader(title, subtitle) {
  return /* html */ `
    <header class="lm-settings__section-header">
      <h2 class="lm-settings__section-title">${escapeHtml(title)}</h2>
      <p class="lm-settings__section-sub">${escapeHtml(subtitle)}</p>
    </header>
  `;
}

// ── Event wiring ────────────────────────────────────────────────

function _bindEvents(root) {
  // Anchor nav: active state on scroll, smooth scroll on click.
  _bindAnchors(root);

  // Profile
  _bindForm("profileForm", async () => _saveProfile(), () => _loadProfile(), [
    "#profileName", "#profileRole", "#profileCommStyle",
  ]);
  root.querySelector("[data-action='profile-reset']")?.addEventListener("click", () => _loadProfile());

  // Routing
  _bindForm("routingForm", async () => _saveRouting(), () => _loadRouting(), [
    "#routing-default", "#routing-fast", "#routing-thinking", "#routing-vision",
  ]);
  root.querySelector("[data-action='routing-reset']")?.addEventListener("click", () => _loadRouting());

  // Safety
  _bindForm("safetyForm", async () => _saveSafety(), () => _loadSafety(), [
    "#safetyMaxParallel", "#safetyApproveThreshold", "#safetyDenylist",
    "#tools_filesystem", "#tools_network", "#tools_shell",
    "#tools_email", "#tools_calendar", "#tools_code_write",
  ]);
  root.querySelector("[data-action='safety-reset']")?.addEventListener("click", () => _loadSafety());
  _bindSliderOutput(root, "#safetyMaxParallel", "#safetyMaxParallelOut", (v) => v);
  _bindSliderOutput(root, "#safetyApproveThreshold", "#safetyApproveThresholdOut", (v) => v);

  // Integrations
  root.querySelector("#googleConnectBtn")?.addEventListener("click", _googleConnect);
  root.querySelector("#googleDisconnectBtn")?.addEventListener("click", _googleDisconnect);
  root.querySelector("#googleRefreshBtn")?.addEventListener("click", () => _refreshGoogleStatus());

  // API keys
  root.querySelector("#apiKeysRefreshBtn")?.addEventListener("click", () => _loadApiKeys());

  // Appearance
  _bindForm("appearanceForm", async () => _saveAppearance(), () => _loadAppearance(), [
    "#accentColor", "#fontSize",
    "input[name='theme']",
  ]);
  root.querySelector("[data-action='appearance-reset']")?.addEventListener("click", () => _loadAppearance());
  _bindSliderOutput(root, "#fontSize", "#fontSizeOut", (v) => `${v}px`);
  root.querySelector("#accentColor")?.addEventListener("input", (e) => {
    const out = root.querySelector("#accentColorOut");
    if (out) out.textContent = String(e.target.value).toUpperCase();
  });

  // Advanced
  _bindForm("advancedForm", async () => _saveAdvanced(), () => _loadAdvanced(), [
    "#advVerbose", "#advExperimental", "#advStream",
  ]);
  root.querySelector("[data-action='advanced-reset']")?.addEventListener("click", () => _loadAdvanced());

  // Global Esc → revert active form
  root.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const form = e.target.closest("form");
    if (!form) return;
    _revertForm(form.id);
  });
}

function _bindAnchors(root) {
  const links = Array.from(root.querySelectorAll("[data-anchor]"));
  const sections = Array.from(root.querySelectorAll("[data-section]"));
  if (links.length === 0 || sections.length === 0) return;

  links.forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const id = a.dataset.anchor;
      const section = root.querySelector(`#settings-${id}`);
      if (!section) return;
      const scroller = section.closest(".lm-page__inner") || section.parentElement;
      const top = section.offsetTop - 16;
      scroller?.scrollTo({ top, behavior: "smooth" });
      history.replaceState(null, "", `#settings-${id}`);
    });
  });

  // Observe which section is in view.
  const setActive = (id) => {
    links.forEach((l) => l.classList.toggle("lm-settings__nav-link--active", l.dataset.anchor === id));
  };

  const io = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
      if (visible[0]) {
        const id = visible[0].target.dataset.section;
        setActive(id);
      }
    },
    { rootMargin: "-20% 0px -60% 0px", threshold: [0, 0.25, 0.5, 1] }
  );
  sections.forEach((s) => io.observe(s));

  // Default to whatever is in the URL hash.
  const hash = (location.hash || "").replace(/^#settings-/, "");
  if (hash) setActive(hash);
  else setActive("profile");
}

function _bindSliderOutput(root, inputSel, outSel, fmt) {
  const input = root.querySelector(inputSel);
  const out = root.querySelector(outSel);
  if (!input || !out) return;
  const update = () => { out.textContent = fmt(input.value); };
  input.addEventListener("input", update);
  update();
}

// ── Form dirty + save machinery ─────────────────────────────────

const _snapshots = new Map(); // formId → JSON string baseline

function _bindForm(formId, saver, reverter, watchSels) {
  const form = document.getElementById(formId);
  if (!form) return;

  const markDirty = () => _updateDirty(formId);
  watchSels.forEach((sel) => {
    form.querySelectorAll(sel).forEach((node) => {
      const type = (node.type || "").toLowerCase();
      const evt = (type === "checkbox" || type === "radio" || node.tagName === "SELECT") ? "change" : "input";
      node.addEventListener(evt, markDirty);
    });
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const saveBtn = form.querySelector("[type='submit']");
    const actionBtns = form.querySelectorAll("button, input, select, textarea");
    actionBtns.forEach((b) => { b.disabled = true; });
    try {
      await saver();
      _captureSnapshot(formId);
      _updateDirty(formId);
      showToast(`${_formLabel(formId)} saved.`, "success");
    } catch (err) {
      console.warn(`[settings] ${formId} save failed`, err);
      showToast(`Could not save ${_formLabel(formId).toLowerCase()}: ${err?.message || err}`, "error");
    } finally {
      actionBtns.forEach((b) => { b.disabled = false; });
      _updateDirty(formId);
      saveBtn && (saveBtn.disabled = !_isDirty(formId));
    }
  });

  // Save reverter for Esc.
  form.dataset.reverter = "1";
  form._revert = reverter;
}

function _formLabel(id) {
  const map = {
    profileForm: "Profile",
    routingForm: "Routing",
    safetyForm: "Safety rails",
    appearanceForm: "Appearance",
    advancedForm: "Advanced",
  };
  return map[id] || "Settings";
}

function _revertForm(formId) {
  const form = document.getElementById(formId);
  if (form && typeof form._revert === "function") {
    form._revert();
  }
}

function _captureSnapshot(formId) {
  const form = document.getElementById(formId);
  if (!form) return;
  _snapshots.set(formId, _formFingerprint(form));
  _updateDirty(formId);
}

function _formFingerprint(form) {
  const parts = [];
  form.querySelectorAll("input, select, textarea").forEach((el) => {
    const type = (el.type || "").toLowerCase();
    if (type === "checkbox" || type === "radio") {
      parts.push(`${el.name || el.id}=${el.checked ? "1" : "0"}`);
    } else {
      parts.push(`${el.name || el.id}=${el.value ?? ""}`);
    }
  });
  return parts.join("|");
}

function _isDirty(formId) {
  const form = document.getElementById(formId);
  if (!form) return false;
  const now = _formFingerprint(form);
  const baseline = _snapshots.get(formId);
  return baseline !== undefined && now !== baseline;
}

function _updateDirty(formId) {
  const form = document.getElementById(formId);
  if (!form) return;
  const dirty = _isDirty(formId);
  const saveBtn = form.querySelector("[type='submit']");
  if (saveBtn) saveBtn.disabled = !dirty;
  form.classList.toggle("lm-settings__form--dirty", dirty);
}

// ── Load-all entry point ────────────────────────────────────────

function _loadAll() {
  _loadProfile();
  _loadRoutingModelsThenValues();
  _loadSafety();
  _refreshGoogleStatus();
  _loadApiKeys();
  _loadAppearance();
  _loadAdvanced();
  _loadAbout();
}

// ── Profile ─────────────────────────────────────────────────────

async function _loadProfile() {
  const nameEl = document.getElementById("profileName");
  const roleEl = document.getElementById("profileRole");
  const commEl = document.getElementById("profileCommStyle");
  const tzEl   = document.getElementById("profileTimezone");
  if (!nameEl || !tzEl) return;

  try {
    tzEl.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch (_) {
    tzEl.value = "";
  }

  try {
    const res = await fetch(`${API}/api/user-profile`);
    if (res.ok) {
      const data = await res.json();
      const p = data?.profile || {};
      nameEl.value = p.name || localStorage.getItem("lm_user_name") || "";
      roleEl.value = p.role || "";
      commEl.value = p.communication_style || "";
    } else {
      nameEl.value = localStorage.getItem("lm_user_name") || "";
    }
  } catch (_) {
    nameEl.value = localStorage.getItem("lm_user_name") || "";
  }

  _captureSnapshot("profileForm");
}

async function _saveProfile() {
  const payload = {
    user_id: "default",
    name: document.getElementById("profileName")?.value?.trim() || "",
    role: document.getElementById("profileRole")?.value?.trim() || "",
    communication_style: document.getElementById("profileCommStyle")?.value?.trim() || "",
  };
  const res = await fetch(`${API}/api/user-profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  if (payload.name) localStorage.setItem("lm_user_name", payload.name);
}

// ── Routing ─────────────────────────────────────────────────────

let _modelCache = [];

async function _loadRoutingModelsThenValues() {
  try {
    const res = await fetch(`${API}/api/models`);
    if (res.ok) {
      const data = await res.json();
      _modelCache = Array.isArray(data) ? data : (data.models || []);
    }
  } catch (_) {
    _modelCache = [];
  }
  _populateRoutingSelects();
  _loadRouting();
}

function _populateRoutingSelects() {
  const selects = document.querySelectorAll("[data-route]");
  selects.forEach((sel) => {
    const current = sel.value;
    const opts = [`<option value="">— not set —</option>`];
    _modelCache.forEach((m) => {
      const id = m.id || m.name || "";
      const label = m.name || m.id || id;
      if (!id) return;
      opts.push(`<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`);
    });
    sel.innerHTML = opts.join("");
    if (current) sel.value = current;
  });
}

function _loadRouting() {
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem("lm-routing") || "{}");
  } catch (_) { stored = {}; }
  ["default", "fast", "thinking", "vision"].forEach((key) => {
    const sel = document.getElementById(`routing-${key}`);
    if (!sel) return;
    sel.value = stored[key] || "";
  });
  _captureSnapshot("routingForm");
}

async function _saveRouting() {
  const payload = {};
  ["default", "fast", "thinking", "vision"].forEach((key) => {
    const v = document.getElementById(`routing-${key}`)?.value || "";
    payload[key] = v;
  });
  // Stub: no backend endpoint for model-router/config yet. Persist locally.
  localStorage.setItem("lm-routing", JSON.stringify(payload));
}

// ── Safety rails ────────────────────────────────────────────────

const _SAFETY_DEFAULTS = {
  max_parallel: 3,
  approve_threshold: 25,
  tools_filesystem: true,
  tools_network: true,
  tools_shell: false,
  tools_email: true,
  tools_calendar: true,
  tools_code_write: false,
  denylist: "",
};

function _loadSafety() {
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem("lm-safety") || "{}"); } catch (_) {}
  const s = { ..._SAFETY_DEFAULTS, ...stored };

  _setValue("safetyMaxParallel", s.max_parallel);
  _setValue("safetyApproveThreshold", s.approve_threshold);
  _setValue("safetyDenylist", s.denylist);
  ["tools_filesystem", "tools_network", "tools_shell", "tools_email", "tools_calendar", "tools_code_write"].forEach((k) => {
    _setCheckbox(k, !!s[k]);
  });

  // refresh slider outputs
  document.getElementById("safetyMaxParallelOut") && (document.getElementById("safetyMaxParallelOut").textContent = String(s.max_parallel));
  document.getElementById("safetyApproveThresholdOut") && (document.getElementById("safetyApproveThresholdOut").textContent = String(s.approve_threshold));

  _captureSnapshot("safetyForm");
}

async function _saveSafety() {
  const payload = {
    max_parallel: Number(document.getElementById("safetyMaxParallel")?.value) || 3,
    approve_threshold: Number(document.getElementById("safetyApproveThreshold")?.value) || 25,
    tools_filesystem: !!document.getElementById("tools_filesystem")?.checked,
    tools_network:    !!document.getElementById("tools_network")?.checked,
    tools_shell:      !!document.getElementById("tools_shell")?.checked,
    tools_email:      !!document.getElementById("tools_email")?.checked,
    tools_calendar:   !!document.getElementById("tools_calendar")?.checked,
    tools_code_write: !!document.getElementById("tools_code_write")?.checked,
    denylist: document.getElementById("safetyDenylist")?.value || "",
  };
  localStorage.setItem("lm-safety", JSON.stringify(payload));
}

// ── Integrations → Google ───────────────────────────────────────

async function _refreshGoogleStatus() {
  const card = document.getElementById("googleIntegration");
  if (!card) return;
  _setGoogleState(card, "loading");

  try {
    const res = await fetch(`${API}/api/google/status`);
    if (!res.ok) {
      _setGoogleState(card, "unavailable", {
        sub: `Status endpoint returned ${res.status}.`,
      });
      return;
    }
    const s = await res.json();
    const authed = !!(s.authenticated || s.connected);

    if (authed) {
      _setGoogleState(card, "connected", {
        sub: _googleScopesSummary(s.scopes),
        scopes: s.scopes || [],
      });
    } else if (!s.has_client_credentials) {
      _setGoogleState(card, "unconfigured", {
        sub: "No Google client credentials configured — see docs/google_setup.md.",
      });
    } else {
      _setGoogleState(card, "disconnected", { sub: "Not connected." });
    }
  } catch (err) {
    console.warn("[settings] google status failed", err);
    _setGoogleState(card, "unavailable", { sub: "Could not reach status endpoint." });
  }
}

function _googleScopesSummary(scopes) {
  if (!Array.isArray(scopes) || scopes.length === 0) return "Connected.";
  const bits = [];
  if (scopes.some((s) => /drive/.test(s)))    bits.push("Drive");
  if (scopes.some((s) => /calendar/.test(s))) bits.push("Calendar");
  if (scopes.some((s) => /gmail|mail/.test(s))) bits.push("Gmail");
  return bits.length ? `Connected — ${bits.join(", ")}.` : "Connected.";
}

function _setGoogleState(card, state, opts = {}) {
  card.dataset.state = state;
  const dot = card.querySelector("#googleDot");
  const label = card.querySelector("#googleStateLabel");
  const sub = card.querySelector("#googleIntegrationSub");
  const connectBtn = card.querySelector("#googleConnectBtn");
  const disconnectBtn = card.querySelector("#googleDisconnectBtn");
  const scopesEl = card.querySelector("#googleScopes");

  const presets = {
    loading:      { cls: "paused",  text: "Checking",    connect: true,  disconnect: false },
    connected:    { cls: "ok",      text: "Connected",   connect: false, disconnect: true  },
    disconnected: { cls: "waiting", text: "Disconnected", connect: true,  disconnect: false },
    unconfigured: { cls: "failed",  text: "Unavailable", connect: true,  disconnect: false },
    unavailable:  { cls: "failed",  text: "Unavailable", connect: true,  disconnect: false },
    connecting:   { cls: "running", text: "Connecting…", connect: false, disconnect: false },
  };
  const p = presets[state] || presets.loading;

  if (dot) {
    dot.className = `lm-settings__dot lm-settings__dot--${p.cls}`;
  }
  if (label) label.textContent = p.text;
  if (sub && opts.sub) sub.textContent = opts.sub;

  if (connectBtn) connectBtn.hidden = !p.connect;
  if (disconnectBtn) disconnectBtn.hidden = !p.disconnect;

  if (scopesEl) {
    if (state === "connected") {
      scopesEl.hidden = false;
      const granted = new Set((opts.scopes || []).join(" ").toLowerCase());
      card.querySelectorAll("[data-scope]").forEach((row) => {
        const key = row.dataset.scope;
        const chip = row.querySelector(`[data-scope-chip='${key}']`);
        if (!chip) return;
        const rx = new RegExp(key === "gmail" ? "gmail|mail" : key);
        const ok = rx.test(Array.from(granted).join(" "));
        chip.textContent = ok ? "Granted" : "Not granted";
        chip.classList.toggle("lm-settings__scope-chip--granted", ok);
      });
    } else {
      scopesEl.hidden = true;
    }
  }
}

async function _googleConnect() {
  const card = document.getElementById("googleIntegration");
  if (!card) return;
  _setGoogleState(card, "connecting", { sub: "Opening Google consent window…" });

  // /api/google/auth redirects; open directly in a popup.
  const authUrl = `${API}/api/google/auth`;
  const popup = window.open(authUrl, "google-oauth", "width=520,height=640,noopener=no");
  if (!popup) {
    showToast("Popup blocked — allow popups for LocalMind and try again.", "error");
    _setGoogleState(card, "disconnected", { sub: "Popup blocked." });
    return;
  }

  // Poll status every 3s until the popup reports success, max 2 minutes.
  _stopGooglePolling();
  _googlePollId = setInterval(async () => {
    try {
      const res = await fetch(`${API}/api/google/status`);
      if (!res.ok) return;
      const s = await res.json();
      if (s.authenticated || s.connected) {
        _stopGooglePolling();
        try { popup.close(); } catch (_) {}
        _refreshGoogleStatus();
        showToast("Google connected.", "success");
      }
    } catch (_) { /* keep polling */ }
  }, 3000);

  _googlePollTimeoutId = setTimeout(() => {
    _stopGooglePolling();
    _refreshGoogleStatus();
  }, 120_000);
}

function _stopGooglePolling() {
  if (_googlePollId) { clearInterval(_googlePollId); _googlePollId = null; }
  if (_googlePollTimeoutId) { clearTimeout(_googlePollTimeoutId); _googlePollTimeoutId = null; }
}

async function _googleDisconnect() {
  const card = document.getElementById("googleIntegration");
  if (!card) return;
  const proceed = window.confirm("Disconnect LocalMind from your Google account? Jobs that rely on Google tools will fail until you reconnect.");
  if (!proceed) return;

  _setGoogleState(card, "connecting", { sub: "Revoking access…" });
  try {
    const res = await fetch(`${API}/api/google/revoke`, { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast("Google disconnected.", "success");
  } catch (err) {
    console.warn("[settings] google revoke failed", err);
    showToast(`Could not disconnect: ${err?.message || err}`, "error");
  } finally {
    _refreshGoogleStatus();
  }
}

// ── API keys ────────────────────────────────────────────────────

async function _loadApiKeys() {
  const tbody = document.getElementById("apiKeysBody");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="5" class="lm-settings__table-empty">Loading keys…</td></tr>`;

  try {
    const res = await fetch(`${API}/api/admin/keys`);
    if (res.status === 401 || res.status === 403) {
      tbody.innerHTML = `<tr><td colspan="5" class="lm-settings__table-empty">Admin privileges required to list keys.</td></tr>`;
      return;
    }
    if (!res.ok) {
      tbody.innerHTML = `<tr><td colspan="5" class="lm-settings__table-empty">Key management unavailable (${res.status}).</td></tr>`;
      return;
    }
    const data = await res.json();
    const keys = data?.keys || [];
    if (keys.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" class="lm-settings__table-empty">No keys configured.</td></tr>`;
      return;
    }
    tbody.innerHTML = keys.map(_apiKeyRow).join("");
    tbody.querySelectorAll("[data-revoke]").forEach((btn) => {
      btn.addEventListener("click", () => _revokeKey(btn.dataset.revoke));
    });
  } catch (err) {
    console.warn("[settings] api keys failed", err);
    tbody.innerHTML = `<tr><td colspan="5" class="lm-settings__table-empty">Could not load keys.</td></tr>`;
  }
}

function _apiKeyRow(k) {
  const masked = k.masked || k.prefix || (k.id ? `${String(k.id).slice(0, 6)}••••` : "••••");
  const created = k.created_at ? _relativeTime(k.created_at) : "—";
  return `
    <tr>
      <td>${escapeHtml(k.provider || k.role || "—")}</td>
      <td>${escapeHtml(k.description || k.user_id || "—")}</td>
      <td class="lm-mono">${escapeHtml(masked)}</td>
      <td class="lm-mono">${escapeHtml(created)}</td>
      <td class="lm-settings__table-actions">
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" data-revoke="${escapeHtml(k.id)}">
          <span class="material-symbols-outlined" aria-hidden="true">delete</span>
          Revoke
        </button>
      </td>
    </tr>
  `;
}

async function _revokeKey(id) {
  if (!id) return;
  if (!window.confirm("Revoke this key? Clients using it will fail on next request.")) return;
  try {
    const res = await fetch(`${API}/api/admin/keys/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast("Key revoked.", "success");
    _loadApiKeys();
  } catch (err) {
    showToast(`Could not revoke: ${err?.message || err}`, "error");
  }
}

// ── Appearance ──────────────────────────────────────────────────

const _APPEARANCE_DEFAULTS = { theme: "dark", accent: "#6366F1", font_size: 14 };

function _loadAppearance() {
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem("lm-appearance") || "{}"); } catch (_) {}
  const a = { ..._APPEARANCE_DEFAULTS, ...stored };

  document.querySelectorAll("input[name='theme']").forEach((r) => {
    r.checked = r.value === a.theme;
  });
  _setValue("accentColor", a.accent);
  _setValue("fontSize", a.font_size);
  const accentOut = document.getElementById("accentColorOut");
  if (accentOut) accentOut.textContent = String(a.accent).toUpperCase();
  const fontOut = document.getElementById("fontSizeOut");
  if (fontOut) fontOut.textContent = `${a.font_size}px`;

  _captureSnapshot("appearanceForm");
}

async function _saveAppearance() {
  const payload = {
    theme: document.querySelector("input[name='theme']:checked")?.value || "dark",
    accent: document.getElementById("accentColor")?.value || "#6366F1",
    font_size: Number(document.getElementById("fontSize")?.value) || 14,
  };
  localStorage.setItem("lm-appearance", JSON.stringify(payload));
  // Apply live on this page only — app-wide theming is out of scope for this agent.
  document.documentElement.style.setProperty("--lm-accent", payload.accent);
}

// ── Advanced ────────────────────────────────────────────────────

const _ADVANCED_DEFAULTS = { verbose: false, experimental: false, stream: true };

function _loadAdvanced() {
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem("lm-advanced") || "{}"); } catch (_) {}
  const a = { ..._ADVANCED_DEFAULTS, ...stored };

  _setCheckbox("advVerbose", !!a.verbose);
  _setCheckbox("advExperimental", !!a.experimental);
  _setCheckbox("advStream", !!a.stream);

  _captureSnapshot("advancedForm");
}

async function _saveAdvanced() {
  const payload = {
    verbose: !!document.getElementById("advVerbose")?.checked,
    experimental: !!document.getElementById("advExperimental")?.checked,
    stream: !!document.getElementById("advStream")?.checked,
  };
  localStorage.setItem("lm-advanced", JSON.stringify(payload));
}

// ── About ───────────────────────────────────────────────────────

async function _loadAbout() {
  const vEl = document.getElementById("aboutVersion");
  const bEl = document.getElementById("aboutBuild");
  if (!vEl || !bEl) return;
  try {
    const res = await fetch(`${API}/api/version`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const v = await res.json();
    vEl.textContent = v.version || "unknown";
    bEl.textContent = v.build !== undefined ? String(v.build) : (v.build_hash || "—");
  } catch (_) {
    vEl.textContent = "—";
    bEl.textContent = "—";
  }
}

// ── Small utilities ─────────────────────────────────────────────

function _setValue(id, v) {
  const el = document.getElementById(id);
  if (el) el.value = v ?? "";
}

function _setCheckbox(id, v) {
  const el = document.getElementById(id);
  if (el) el.checked = !!v;
}

function _relativeTime(iso) {
  if (!iso) return "—";
  const t = typeof iso === "number" ? iso : Date.parse(iso);
  if (!t || isNaN(t)) return String(iso);
  const delta = Math.floor((Date.now() - t) / 1000);
  if (delta < 60)    return `${delta}s ago`;
  if (delta < 3600)  return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}
