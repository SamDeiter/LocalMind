/**
 * settings_page.js — Settings page (Phase 1 stub)
 *
 * Dedicated settings destination. Opens the legacy settings modal
 * for the full surface in Phase 1, with a condensed quick-edit view inline.
 */

import { API } from "./state.js";

let _inited = false;

export function initSettingsPage() {
  if (_inited) return;
  _inited = true;

  const target = document.getElementById("settingsContainer");
  if (!target) return;

  target.innerHTML = /* html */ `
    <header class="lm-page__header">
      <div>
        <div class="lm-page__eyebrow">Preferences</div>
        <h1 class="lm-page__title">Settings</h1>
        <p class="lm-page__subtitle">Models, tools, safety, and personalization.</p>
      </div>
    </header>

    <section class="lm-home__row lm-home__row--3">
      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">deployed_code</span>
          <h3 class="lm-card__title">Model routing</h3>
        </header>
        <div class="lm-card__body">
          <div class="lm-home__list-row"><span class="lm-mute">Active model</span><span class="lm-mono" id="settingsActiveModel">—</span></div>
          <div class="lm-home__list-row"><span class="lm-mute">Fallback chain</span><span class="lm-mono" id="settingsFallback">—</span></div>
        </div>
      </article>

      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">shield</span>
          <h3 class="lm-card__title">Safety</h3>
        </header>
        <div class="lm-card__body">
          <div class="lm-home__list-row"><span class="lm-mute">Auto-approve low-risk</span><span class="lm-mono">off</span></div>
          <div class="lm-home__list-row"><span class="lm-mute">Network allow-list</span><span class="lm-mono">localhost</span></div>
        </div>
      </article>

      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">person</span>
          <h3 class="lm-card__title">Profile</h3>
        </header>
        <div class="lm-card__body">
          <div class="lm-home__list-row"><span class="lm-mute">Name</span><span class="lm-mono" id="settingsName">—</span></div>
          <div class="lm-home__list-row"><span class="lm-mute">Timezone</span><span class="lm-mono" id="settingsTz">—</span></div>
        </div>
      </article>
    </section>

    <section>
      <div class="lm-page__actions" style="justify-content:flex-start;">
        <button type="button" class="lm-btn lm-btn--primary" id="settingsOpenFull">
          <span class="material-symbols-outlined" aria-hidden="true">tune</span>
          Open full settings
        </button>
        <button type="button" class="lm-btn lm-btn--ghost" id="settingsViewVersion">
          <span class="material-symbols-outlined" aria-hidden="true">info</span>
          About LocalMind
        </button>
      </div>
    </section>
  `;

  document.getElementById("settingsOpenFull")?.addEventListener("click", () => {
    // Legacy settings modal is opened via settings_ui.js
    import("./settings_ui.js").then((m) => m.openSettingsModal?.()).catch(() => {});
    // Fallback: dispatch a custom event in case the module uses event-based wiring
    document.dispatchEvent(new CustomEvent("lm:open-settings"));
  });

  document.getElementById("settingsViewVersion")?.addEventListener("click", _showVersion);

  _loadProfile();
  _loadRouting();
}

async function _loadRouting() {
  try {
    const r = await fetch(`${API}/api/models`);
    if (!r.ok) return;
    const data = await r.json();
    const list = Array.isArray(data) ? data : (data.models || []);
    const active = list.find((m) => m.loaded || m.active) || list[0];
    document.getElementById("settingsActiveModel").textContent = active?.name || active?.id || "—";
    document.getElementById("settingsFallback").textContent =
      list.filter((m) => m !== active).slice(0, 2).map((m) => m.name || m.id).join(" → ") || "—";
  } catch (_) { /* silent */ }
}

function _loadProfile() {
  try {
    document.getElementById("settingsTz").textContent = Intl.DateTimeFormat().resolvedOptions().timeZone || "—";
  } catch (_) { /* silent */ }
  document.getElementById("settingsName").textContent = localStorage.getItem("lm_user_name") || "—";
}

async function _showVersion() {
  try {
    const r = await fetch(`${API}/api/version`);
    if (!r.ok) return;
    const v = await r.json();
    alert(`LocalMind ${v.version || ""}\nBuild: ${v.build || "—"}`);
  } catch (_) { /* silent */ }
}
