/**
 * knowledge_ui.js — Knowledge page (Phase 1 stub)
 * Landing for memory + brain graph + learning lab.
 */

import { API } from "./state.js";
import { escapeHtml } from "./utils.js";

let _inited = false;

export function initKnowledgeUI() {
  if (_inited) return;
  _inited = true;

  const target = document.getElementById("knowledgeContainer");
  if (!target) return;

  target.innerHTML = /* html */ `
    <header class="lm-page__header">
      <div>
        <div class="lm-page__eyebrow">Understanding</div>
        <h1 class="lm-page__title">Knowledge</h1>
        <p class="lm-page__subtitle">What LocalMind remembers, knows about you, and is learning.</p>
      </div>
    </header>

    <section class="lm-home__row lm-home__row--3">
      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">memory</span>
          <h3 class="lm-card__title">Memory</h3>
          <span class="lm-card__count" id="knowledgeMemoryCount" hidden></span>
        </header>
        <div class="lm-card__body" id="knowledgeMemoryList">—</div>
      </article>

      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">hub</span>
          <h3 class="lm-card__title">Concepts</h3>
        </header>
        <div class="lm-card__body">
          <div class="lm-stub" style="padding:var(--lm-space-5);">
            <div class="lm-stub__title">Brain graph</div>
            <div class="lm-stub__desc">Concept map view is coming in Phase 3.</div>
          </div>
        </div>
      </article>

      <article class="lm-card">
        <header class="lm-card__header">
          <span class="material-symbols-outlined lm-mute" aria-hidden="true">school</span>
          <h3 class="lm-card__title">Learning</h3>
        </header>
        <div class="lm-card__body">
          <div class="lm-stub" style="padding:var(--lm-space-5);">
            <div class="lm-stub__title">Feedback loop</div>
            <div class="lm-stub__desc">What LocalMind has learned from your corrections.</div>
          </div>
        </div>
      </article>
    </section>
  `;

  _loadMemory();
}

async function _loadMemory() {
  const el = document.getElementById("knowledgeMemoryList");
  const badge = document.getElementById("knowledgeMemoryCount");
  if (!el) return;

  try {
    const res = await fetch(`${API}/api/memories`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const items = Array.isArray(data) ? data : (data.memories || data.items || []);
    if (badge) {
      badge.textContent = String(items.length);
      badge.hidden = false;
    }
    if (!items.length) {
      el.innerHTML = `<div class="lm-home__empty">Nothing remembered yet.</div>`;
      return;
    }
    el.innerHTML = items.slice(0, 8).map((m) => `
      <div class="lm-home__list-row">
        <span class="lm-home__list-title">${escapeHtml(m.content || m.text || m.memory || "")}</span>
        <span class="lm-home__list-meta lm-mono">${escapeHtml(m.category || m.type || "")}</span>
      </div>
    `).join("");
  } catch (e) {
    el.innerHTML = `<div class="lm-home__empty">Memory service unavailable.</div>`;
  }
}
