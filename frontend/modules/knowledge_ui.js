/**
 * knowledge_ui.js — Knowledge page (LocalMind v2)
 *
 * Unified browser that surfaces everything LocalMind remembers across four
 * sources: memories, documents, conversations, and job templates. The page
 * composes into a two-pane layout: a filtered list on the left, and a
 * source-aware preview on the right.
 *
 * Ownership: this file + `frontend/design/knowledge.css` only.
 * Backend: reads from existing routes — no backend changes.
 *   - GET /api/memories              → { memories: [...] }
 *   - GET /api/memories/graph        → { nodes, edges, stats }
 *   - GET /api/documents/            → { documents: [...] }
 *   - GET /api/conversations         → { conversations: [...] }
 *   - GET /api/conversations/:id/messages
 *   - GET /api/jobs/templates        → { templates: [...] }
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

// ── Internal state ────────────────────────────────────────────────
let _inited = false;

const _state = {
  items: [],               // normalized unified items: { id, source, title, snippet, ts, raw }
  filter: "all",           // all | memory | document | conversation | template
  sort: "recent",          // recent | alpha | source
  search: "",
  loading: true,
  selectedKey: null,       // `${source}:${id}`
  graphOpen: false,
  graphSim: null,          // active force-simulation state (see _renderGraphSvg)
  // Per-source raw payload cache for detail rendering without re-fetch
  cache: {
    memories: null,
    documents: null,
    conversations: null,
    templates: null,
  },
  // Lazily-loaded transcripts keyed by conversation id
  transcripts: new Map(),
};

const SOURCES = [
  { key: "all",          label: "All",           icon: "apps" },
  { key: "memory",       label: "Memories",      icon: "psychology" },
  { key: "document",     label: "Documents",     icon: "description" },
  { key: "conversation", label: "Conversations", icon: "chat" },
  { key: "template",     label: "Templates",     icon: "workspaces" },
];

const SOURCE_ICON = {
  memory:       "psychology",
  document:     "description",
  conversation: "chat",
  template:     "workspaces",
};

const SOURCE_LABEL = {
  memory:       "Memory",
  document:     "Document",
  conversation: "Conversation",
  template:     "Template",
};

let _searchDebounce = null;

// ── Public entry point ────────────────────────────────────────────

export function initKnowledgeUI() {
  if (_inited) return;
  const target = document.getElementById("knowledgeContainer");
  if (!target) return;
  _inited = true;

  target.innerHTML = _renderShell();
  _bindEvents(target);
  _loadAll();

  // Pip's research panel — change candidates + auto-research scheduler.
  const researchHost = target.querySelector("#knowledgeResearchHost");
  if (researchHost) {
    import("./research_panel.js")
      .then((m) => m.mountResearchPanel?.(researchHost))
      .catch((err) => console.warn("[knowledge] research panel failed:", err));
  }
}

// ── Shell template ────────────────────────────────────────────────

function _renderShell() {
  return /* html */ `
    <header class="lm-page__header">
      <div>
        <div class="lm-page__eyebrow">What LocalMind remembers</div>
        <h1 class="lm-page__title">Knowledge</h1>
        <p class="lm-page__subtitle">Memories, documents, conversations, and saved templates — searchable in one place.</p>
      </div>
      <div class="lm-page__actions">
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="knowledgeRefreshBtn" title="Refresh sources">
          <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
          Refresh
        </button>
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="knowledgeImportBtn">
          <span class="material-symbols-outlined" aria-hidden="true">upload_file</span>
          Import doc
        </button>
        <button type="button" class="lm-btn lm-btn--primary lm-btn--sm" id="knowledgeNewMemoryBtn">
          <span class="material-symbols-outlined" aria-hidden="true">add</span>
          New memory
        </button>
      </div>
    </header>

    <div id="knowledgeResearchHost"></div>

    <section class="lm-knowledge__toolbar" role="toolbar" aria-label="Knowledge filters">
      <label class="lm-knowledge__search" for="knowledgeSearchInput">
        <span class="material-symbols-outlined" aria-hidden="true">search</span>
        <input
          id="knowledgeSearchInput"
          type="search"
          autocomplete="off"
          spellcheck="false"
          placeholder="Search memories, documents, conversations, templates…"
          aria-label="Search knowledge" />
      </label>

      <div class="lm-knowledge__sources" role="tablist" aria-label="Source filter">
        ${SOURCES.map((s) => `
          <button
            type="button"
            role="tab"
            class="lm-chip ${s.key === "all" ? "lm-chip--active" : ""}"
            data-source-filter="${s.key}"
            aria-selected="${s.key === "all" ? "true" : "false"}">
            <span class="material-symbols-outlined" aria-hidden="true">${s.icon}</span>
            ${escapeHtml(s.label)}
          </button>
        `).join("")}
      </div>

      <div class="lm-knowledge__sort">
        <span class="lm-knowledge__sort-label">Sort</span>
        <select id="knowledgeSortSelect" aria-label="Sort knowledge items">
          <option value="recent">Recent</option>
          <option value="alpha">A–Z</option>
          <option value="source">Source</option>
        </select>
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm lm-knowledge__graph-btn"
                id="knowledgeGraphBtn" title="Open memory graph">
          <span class="material-symbols-outlined" aria-hidden="true">hub</span>
          Graph
        </button>
      </div>
    </section>

    <section class="lm-knowledge__body">
      <div class="lm-knowledge__list-pane">
        <header class="lm-knowledge__list-header">
          <span class="lm-knowledge__list-title" id="knowledgeListTitle">All sources</span>
          <span class="lm-knowledge__list-count" id="knowledgeListCount">—</span>
        </header>
        <ul
          id="knowledgeList"
          class="lm-knowledge__list"
          role="listbox"
          aria-labelledby="knowledgeListTitle"
          tabindex="0">
          ${_skeletonMarkup(6)}
        </ul>
      </div>

      <div class="lm-knowledge__preview-pane" id="knowledgePreviewPane">
        ${_emptyPreview()}
      </div>
    </section>

    <div class="lm-knowledge__graph-modal" id="knowledgeGraphModal" data-open="false" role="dialog" aria-modal="true" aria-label="Memory graph" hidden>
      <div class="lm-knowledge__graph-card">
        <header class="lm-knowledge__graph-header">
          <div>
            <div class="lm-knowledge__graph-title">Memory graph</div>
            <div class="lm-knowledge__graph-subtitle" id="knowledgeGraphSubtitle">Loading…</div>
          </div>
          <button type="button" class="lm-icon-btn" id="knowledgeGraphClose" aria-label="Close graph">
            <span class="material-symbols-outlined" aria-hidden="true">close</span>
          </button>
        </header>
        <div class="lm-knowledge__graph-canvas" id="knowledgeGraphCanvas"></div>
      </div>
    </div>
  `;
}

// ── Event binding ─────────────────────────────────────────────────

function _bindEvents(root) {
  // Header actions (stubs per spec — console log + toast)
  root.querySelector("#knowledgeRefreshBtn")?.addEventListener("click", () => {
    _loadAll(/* force */ true);
    showToast("Refreshing knowledge sources…", "info");
  });
  root.querySelector("#knowledgeImportBtn")?.addEventListener("click", () => {
    console.log("[knowledge] import doc clicked");
    showToast("Import is coming soon — drag files into the chat for now.", "info");
  });
  root.querySelector("#knowledgeNewMemoryBtn")?.addEventListener("click", () => {
    console.log("[knowledge] new memory clicked");
    showToast("Ad-hoc memory entry coming soon.", "info");
  });

  // Search (debounced)
  const searchInput = root.querySelector("#knowledgeSearchInput");
  searchInput?.addEventListener("input", (e) => {
    const value = e.target.value || "";
    if (_searchDebounce) clearTimeout(_searchDebounce);
    _searchDebounce = setTimeout(() => {
      _state.search = value.trim().toLowerCase();
      _renderList();
    }, 150);
  });

  // Source filter chips
  root.querySelectorAll("[data-source-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const key = chip.dataset.sourceFilter;
      _state.filter = key;
      root.querySelectorAll("[data-source-filter]").forEach((c) => {
        const active = c.dataset.sourceFilter === key;
        c.classList.toggle("lm-chip--active", active);
        c.setAttribute("aria-selected", String(active));
      });
      const titleEl = root.querySelector("#knowledgeListTitle");
      if (titleEl) {
        const match = SOURCES.find((s) => s.key === key);
        titleEl.textContent = match ? (key === "all" ? "All sources" : match.label) : "All sources";
      }
      _renderList();
    });
  });

  // Sort
  root.querySelector("#knowledgeSortSelect")?.addEventListener("change", (e) => {
    _state.sort = e.target.value;
    _renderList();
  });

  // List interactions (delegated)
  const listEl = root.querySelector("#knowledgeList");
  listEl?.addEventListener("click", (e) => {
    const row = e.target.closest("[data-key]");
    if (row) _select(row.dataset.key);
  });

  // Keyboard navigation (up / down / home / end / enter)
  listEl?.addEventListener("keydown", (e) => {
    const rows = Array.from(listEl.querySelectorAll("[data-key]"));
    if (!rows.length) return;
    const currentIdx = rows.findIndex((r) => r.dataset.key === _state.selectedKey);
    let nextIdx = currentIdx;

    if (e.key === "ArrowDown")       nextIdx = Math.min(rows.length - 1, currentIdx + 1);
    else if (e.key === "ArrowUp")    nextIdx = Math.max(0, currentIdx - 1);
    else if (e.key === "Home")       nextIdx = 0;
    else if (e.key === "End")        nextIdx = rows.length - 1;
    else if (e.key === "Enter" || e.key === " ") {
      if (currentIdx >= 0) { e.preventDefault(); _select(rows[currentIdx].dataset.key); }
      return;
    } else return;

    if (nextIdx !== currentIdx && rows[nextIdx]) {
      e.preventDefault();
      _select(rows[nextIdx].dataset.key);
      rows[nextIdx].scrollIntoView({ block: "nearest" });
    }
  });

  // Graph modal
  root.querySelector("#knowledgeGraphBtn")?.addEventListener("click", _openGraph);
  root.querySelector("#knowledgeGraphClose")?.addEventListener("click", _closeGraph);
  root.querySelector("#knowledgeGraphModal")?.addEventListener("click", (e) => {
    if (e.target.id === "knowledgeGraphModal") _closeGraph();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && _state.graphOpen) _closeGraph();
  });
}

// ── Data loading ──────────────────────────────────────────────────

async function _loadAll(force = false) {
  _state.loading = true;
  _renderList();

  const results = await Promise.allSettled([
    _fetchJson(`${API}/api/memories`),
    _fetchJson(`${API}/api/documents/`),
    _fetchJson(`${API}/api/conversations`),
    _fetchJson(`${API}/api/jobs/templates`),
  ]);

  const [memRes, docRes, convRes, tmplRes] = results;
  const items = [];

  // Memories
  if (memRes.status === "fulfilled" && memRes.value) {
    _state.cache.memories = memRes.value;
    const list = _extractArray(memRes.value, ["memories", "items", "results"]);
    list.forEach((m) => items.push(_normalizeMemory(m)));
  }

  // Documents
  if (docRes.status === "fulfilled" && docRes.value) {
    _state.cache.documents = docRes.value;
    const list = _extractArray(docRes.value, ["documents", "items", "results"]);
    list.forEach((d) => items.push(_normalizeDocument(d)));
  }

  // Conversations
  if (convRes.status === "fulfilled" && convRes.value) {
    _state.cache.conversations = convRes.value;
    const list = _extractArray(convRes.value, ["conversations", "items", "results"]);
    list.forEach((c) => items.push(_normalizeConversation(c)));
  }

  // Templates
  if (tmplRes.status === "fulfilled" && tmplRes.value) {
    _state.cache.templates = tmplRes.value;
    const list = _extractArray(tmplRes.value, ["templates", "items", "results"]);
    list.forEach((t) => items.push(_normalizeTemplate(t)));
  }

  _state.items = items;
  _state.loading = false;

  if (force) _state.transcripts.clear();
  _renderList();

  // Preserve selection if still valid, otherwise drop it (blank preview).
  if (_state.selectedKey && !_state.items.some((i) => _key(i) === _state.selectedKey)) {
    _state.selectedKey = null;
    _renderPreview(null);
  } else if (_state.selectedKey) {
    const item = _state.items.find((i) => _key(i) === _state.selectedKey);
    if (item) _renderPreview(item);
  }
}

async function _fetchJson(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    console.warn("[knowledge] fetch failed", url, e);
    return null;
  }
}

function _extractArray(payload, keys) {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload;
  for (const k of keys) {
    if (Array.isArray(payload[k])) return payload[k];
  }
  return [];
}

// ── Normalizers ───────────────────────────────────────────────────

function _normalizeMemory(m) {
  const content = m.content || m.text || m.memory || "";
  const snippet = _truncate(content.replace(/\s+/g, " "), 120);
  return {
    id: String(m.id ?? m.memory_id ?? ""),
    source: "memory",
    title: _truncate(content, 90) || "(empty memory)",
    snippet,
    ts: _parseTs(m.created_at || m.updated_at),
    raw: m,
  };
}

function _normalizeDocument(d) {
  const title = d.title || d.name || d.filename || d.source_path || "(untitled)";
  const snippet = d.summary || d.description || _fmtBytes(d.size) || `${d.chunk_count || 0} chunks`;
  return {
    id: String(d.id ?? d.filename ?? d.name ?? title),
    source: "document",
    title,
    snippet,
    ts: _parseTs(d.added_at || d.created_at || d.updated_at),
    raw: d,
  };
}

function _normalizeConversation(c) {
  const title = c.title || c.name || `Conversation ${String(c.id || "").slice(0, 6)}`;
  const turns = c.turn_count ?? c.message_count ?? "";
  const snippet = turns ? `${turns} turn${turns === 1 ? "" : "s"}` : (c.last_message || c.preview || "");
  return {
    id: String(c.id ?? c.conversation_id ?? ""),
    source: "conversation",
    title,
    snippet,
    ts: _parseTs(c.last_at || c.updated_at || c.created_at),
    raw: c,
  };
}

function _normalizeTemplate(t) {
  const title = t.name || t.title || "(untitled template)";
  const snippet = t.description || t.goal || _describeTemplate(t);
  return {
    id: String(t.id ?? t.template_id ?? title),
    source: "template",
    title,
    snippet: _truncate(snippet || "Saved job template", 120),
    ts: _parseTs(t.created_at || t.updated_at),
    raw: t,
  };
}

// ── List rendering ────────────────────────────────────────────────

function _renderList() {
  const listEl = document.getElementById("knowledgeList");
  const countEl = document.getElementById("knowledgeListCount");
  if (!listEl) return;

  if (_state.loading) {
    listEl.innerHTML = _skeletonMarkup(6);
    if (countEl) countEl.textContent = "…";
    return;
  }

  const visible = _filteredItems();
  if (countEl) countEl.textContent = String(visible.length);

  if (!visible.length) {
    listEl.innerHTML = `<li role="presentation">${_listEmptyMarkup()}</li>`;
    return;
  }

  listEl.innerHTML = visible.map((item) => {
    const key = _key(item);
    const selected = key === _state.selectedKey;
    return /* html */ `
      <li
        class="lm-knowledge__row lm-knowledge__row--${item.source}"
        data-key="${escapeHtml(key)}"
        role="option"
        tabindex="-1"
        aria-selected="${selected ? "true" : "false"}">
        <span class="lm-knowledge__row-icon" aria-hidden="true">
          <span class="material-symbols-outlined">${SOURCE_ICON[item.source]}</span>
        </span>
        <span class="lm-knowledge__row-title">${escapeHtml(item.title)}</span>
        <span class="lm-knowledge__row-ts" title="${escapeHtml(_fmtAbsolute(item.ts))}">${escapeHtml(_fmtRelative(item.ts))}</span>
        <span class="lm-knowledge__row-meta">
          <span class="lm-knowledge__source-tag lm-knowledge__source-tag--${item.source}">${escapeHtml(SOURCE_LABEL[item.source])}</span>
          <span class="lm-knowledge__row-snippet">${escapeHtml(item.snippet || "")}</span>
        </span>
      </li>
    `;
  }).join("");
}

function _filteredItems() {
  const q = _state.search;
  const src = _state.filter;
  let items = _state.items;

  if (src !== "all") items = items.filter((i) => i.source === src);

  if (q) {
    items = items.filter((i) => {
      const hay = `${i.title} ${i.snippet}`.toLowerCase();
      return hay.includes(q);
    });
  }

  const sorted = items.slice();
  if (_state.sort === "alpha") {
    sorted.sort((a, b) => (a.title || "").localeCompare(b.title || "", undefined, { sensitivity: "base" }));
  } else if (_state.sort === "source") {
    const order = { memory: 0, document: 1, conversation: 2, template: 3 };
    sorted.sort((a, b) => {
      const d = (order[a.source] ?? 9) - (order[b.source] ?? 9);
      if (d !== 0) return d;
      return (b.ts || 0) - (a.ts || 0);
    });
  } else {
    // recent (default)
    sorted.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  }
  return sorted;
}

function _skeletonMarkup(rows) {
  let out = "";
  for (let i = 0; i < rows; i++) {
    out += `
      <li class="lm-knowledge__skeleton" role="presentation">
        <span class="lm-knowledge__skeleton-bar"></span>
        <span class="lm-knowledge__skeleton-bar"></span>
        <span class="lm-knowledge__skeleton-bar"></span>
      </li>
    `;
  }
  return out;
}

function _listEmptyMarkup() {
  const msgs = {
    all:          ["Nothing here yet.",        "LocalMind will populate this view as you run jobs, ingest docs, and build memories."],
    memory:       ["No memories yet.",         "LocalMind will start recording context as you run jobs."],
    document:     ["No documents ingested.",   "Drop a file into the chat or use Import to make it searchable."],
    conversation: ["No conversations yet.",    "Start a chat — transcripts will appear here for reference."],
    template:     ["No saved templates.",      "Finish a job you'll repeat, then save it as a template from Jobs."],
  };
  const [title, body] = msgs[_state.filter] || msgs.all;
  return `
    <div class="lm-knowledge__empty lm-knowledge__empty--inline">
      <span class="material-symbols-outlined" aria-hidden="true">inbox</span>
      <div class="lm-knowledge__empty-title">${escapeHtml(title)}</div>
      <div class="lm-knowledge__empty-body">${escapeHtml(body)}</div>
    </div>
  `;
}

// ── Selection + preview rendering ─────────────────────────────────

function _select(key) {
  _state.selectedKey = key;
  const listEl = document.getElementById("knowledgeList");
  if (listEl) {
    listEl.querySelectorAll("[data-key]").forEach((row) => {
      row.setAttribute("aria-selected", String(row.dataset.key === key));
    });
  }
  const item = _state.items.find((i) => _key(i) === key) || null;
  _renderPreview(item);
}

function _renderPreview(item) {
  const pane = document.getElementById("knowledgePreviewPane");
  if (!pane) return;

  if (!item) {
    pane.innerHTML = _emptyPreview();
    return;
  }

  switch (item.source) {
    case "memory":       pane.innerHTML = _renderMemoryPreview(item); break;
    case "document":     pane.innerHTML = _renderDocumentPreview(item); break;
    case "conversation": pane.innerHTML = _renderConversationShell(item); _loadTranscript(item); break;
    case "template":     pane.innerHTML = _renderTemplatePreview(item); _wireTemplateActions(item); break;
    default:             pane.innerHTML = _emptyPreview();
  }
}

function _emptyPreview() {
  return /* html */ `
    <div class="lm-knowledge__empty">
      <span class="material-symbols-outlined" aria-hidden="true">menu_book</span>
      <div class="lm-knowledge__empty-title">Select something to preview</div>
      <div class="lm-knowledge__empty-body">Pick a memory, document, conversation, or template from the list to read the full content here.</div>
    </div>
  `;
}

function _renderMemoryPreview(item) {
  const m = item.raw || {};
  const content = m.content || m.text || m.memory || "";
  const tags = _extractTags(m);
  const meta = [
    ["ID",         String(m.id ?? item.id ?? "—")],
    ["Category",   String(m.category || m.subcategory || "general")],
    ["Source",     String(m.source || "LocalMind")],
    ["Created",    _fmtAbsolute(item.ts) || (m.created_at || "—")],
  ];
  return /* html */ `
    <header class="lm-knowledge__preview-header">
      <div class="lm-knowledge__preview-heading">
        <div class="lm-knowledge__preview-kicker">Memory</div>
        <h2 class="lm-knowledge__preview-title">${escapeHtml(_truncate(content, 140) || "Memory")}</h2>
      </div>
      <div class="lm-knowledge__preview-actions">
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" data-action="copy" title="Copy">
          <span class="material-symbols-outlined" aria-hidden="true">content_copy</span>
          Copy
        </button>
      </div>
    </header>
    <div class="lm-knowledge__preview-body">
      <div class="lm-knowledge__memory-body">${escapeHtml(content) || "<em>(empty memory)</em>"}</div>
      ${tags.length ? `
        <div class="lm-knowledge__tags">
          ${tags.map((t) => `<span class="lm-knowledge__tag">${escapeHtml(t)}</span>`).join("")}
        </div>
      ` : ""}
      <div class="lm-knowledge__meta">
        ${meta.map(([k, v]) => `
          <span class="lm-knowledge__meta-key">${escapeHtml(k)}</span>
          <span class="lm-knowledge__meta-val">${escapeHtml(v)}</span>
        `).join("")}
      </div>
    </div>
  `;
}

function _renderDocumentPreview(item) {
  const d = item.raw || {};
  const title = d.title || d.name || d.filename || item.title;
  const kind = d.kind || d.type || _extOf(title);
  const size = _fmtBytes(d.size);
  const chunks = d.chunk_count != null ? String(d.chunk_count) : "";
  const body = d.content || d.preview || d.summary || "";
  const isMarkdown = /\.(md|markdown)$/i.test(title) || kind === "markdown";
  const rendered = body
    ? (isMarkdown && typeof window !== "undefined" && window.marked
        ? `<div class="lm-knowledge__markdown">${window.marked.parse(body, { breaks: true, gfm: true })}</div>`
        : `<pre class="lm-knowledge__markdown" style="white-space:pre-wrap;"><code>${escapeHtml(body)}</code></pre>`)
    : `<div class="lm-knowledge__empty lm-knowledge__empty--inline">
         <span class="material-symbols-outlined" aria-hidden="true">description</span>
         <div class="lm-knowledge__empty-title">Preview unavailable</div>
         <div class="lm-knowledge__empty-body">This document is indexed but the full body isn't included in the list payload.</div>
       </div>`;

  const meta = [
    ["Kind",     kind || "text"],
    size   ? ["Size",   size]   : null,
    chunks ? ["Chunks", chunks] : null,
    ["Added",    _fmtAbsolute(item.ts) || (d.added_at || "—")],
    d.source_path ? ["Path", d.source_path] : null,
  ].filter(Boolean);

  return /* html */ `
    <header class="lm-knowledge__preview-header">
      <div class="lm-knowledge__preview-heading">
        <div class="lm-knowledge__preview-kicker">Document</div>
        <h2 class="lm-knowledge__preview-title">${escapeHtml(title)}</h2>
      </div>
      <div class="lm-knowledge__preview-actions">
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" data-action="open-source" title="Open source path" ${d.source_path ? "" : "disabled"}>
          <span class="material-symbols-outlined" aria-hidden="true">open_in_new</span>
          Source
        </button>
      </div>
    </header>
    <div class="lm-knowledge__preview-body">
      ${rendered}
      <div class="lm-knowledge__meta">
        ${meta.map(([k, v]) => `
          <span class="lm-knowledge__meta-key">${escapeHtml(k)}</span>
          <span class="lm-knowledge__meta-val">${escapeHtml(String(v))}</span>
        `).join("")}
      </div>
    </div>
  `;
}

function _renderConversationShell(item) {
  const c = item.raw || {};
  const turns = c.turn_count ?? c.message_count ?? "—";
  const meta = [
    ["ID",     String(c.id || item.id || "—")],
    ["Turns",  String(turns)],
    ["Model",  String(c.model || "—")],
    ["Last",   _fmtAbsolute(item.ts) || (c.last_at || c.updated_at || "—")],
  ];
  return /* html */ `
    <header class="lm-knowledge__preview-header">
      <div class="lm-knowledge__preview-heading">
        <div class="lm-knowledge__preview-kicker">Conversation</div>
        <h2 class="lm-knowledge__preview-title">${escapeHtml(item.title)}</h2>
      </div>
    </header>
    <div class="lm-knowledge__preview-body">
      <div class="lm-knowledge__meta" style="margin-top:0; margin-bottom:var(--lm-space-5);">
        ${meta.map(([k, v]) => `
          <span class="lm-knowledge__meta-key">${escapeHtml(k)}</span>
          <span class="lm-knowledge__meta-val">${escapeHtml(v)}</span>
        `).join("")}
      </div>
      <div class="lm-knowledge__transcript" id="knowledgeTranscript">
        <div class="lm-knowledge__skeleton" role="presentation">
          <span class="lm-knowledge__skeleton-bar"></span>
          <span class="lm-knowledge__skeleton-bar"></span>
          <span class="lm-knowledge__skeleton-bar"></span>
        </div>
      </div>
    </div>
  `;
}

async function _loadTranscript(item) {
  const target = document.getElementById("knowledgeTranscript");
  if (!target) return;

  const id = item.id;
  let messages = _state.transcripts.get(id);

  if (!messages) {
    const payload = await _fetchJson(`${API}/api/conversations/${encodeURIComponent(id)}/messages`);
    messages = payload ? _extractArray(payload, ["messages", "items"]) : [];
    _state.transcripts.set(id, messages);
  }

  // Guard: the user may have clicked another row before the fetch resolved.
  if (_state.selectedKey !== _key(item)) return;

  if (!messages || !messages.length) {
    target.outerHTML = `
      <div class="lm-knowledge__empty lm-knowledge__empty--inline">
        <span class="material-symbols-outlined" aria-hidden="true">chat</span>
        <div class="lm-knowledge__empty-title">Empty transcript</div>
        <div class="lm-knowledge__empty-body">This conversation has no messages yet.</div>
      </div>
    `;
    return;
  }

  target.innerHTML = messages.map((m) => {
    const role = String(m.role || "user").toLowerCase();
    const variant = role === "assistant" ? "assistant" : (role === "system" ? "system" : "user");
    return `
      <div class="lm-knowledge__turn lm-knowledge__turn--${variant}">
        <span class="lm-knowledge__turn-role">${escapeHtml(role)}</span>
        <div class="lm-knowledge__turn-content">${escapeHtml(m.content || "")}</div>
      </div>
    `;
  }).join("");
}

function _renderTemplatePreview(item) {
  const t = item.raw || {};
  const constraints = _collectConstraints(t);
  const nodes = Array.isArray(t.nodes) ? t.nodes : [];
  const meta = [
    ["ID",      String(t.id || item.id || "—")],
    ["Created", _fmtAbsolute(item.ts) || (t.created_at || "—")],
    nodes.length ? ["Steps", String(nodes.length)] : null,
    t.description ? ["Notes", t.description] : null,
  ].filter(Boolean);

  return /* html */ `
    <header class="lm-knowledge__preview-header">
      <div class="lm-knowledge__preview-heading">
        <div class="lm-knowledge__preview-kicker">Template</div>
        <h2 class="lm-knowledge__preview-title">${escapeHtml(item.title)}</h2>
      </div>
      <div class="lm-knowledge__preview-actions">
        <button type="button" class="lm-btn lm-btn--primary lm-btn--sm" data-action="use-template">
          <span class="material-symbols-outlined" aria-hidden="true">play_arrow</span>
          Use template
        </button>
      </div>
    </header>
    <div class="lm-knowledge__preview-body">
      <section class="lm-knowledge__template-section">
        <div class="lm-knowledge__template-label">Goal</div>
        <div class="lm-knowledge__template-goal">${escapeHtml(t.goal || t.prompt || t.description || "(no goal recorded)")}</div>
      </section>
      ${constraints.length ? `
        <section class="lm-knowledge__template-section">
          <div class="lm-knowledge__template-label">Constraints</div>
          <ul class="lm-knowledge__template-list">
            ${constraints.map((c) => `
              <li>
                <span class="material-symbols-outlined" aria-hidden="true">check_circle</span>
                <span>${escapeHtml(c)}</span>
              </li>
            `).join("")}
          </ul>
        </section>
      ` : ""}
      ${nodes.length ? `
        <section class="lm-knowledge__template-section">
          <div class="lm-knowledge__template-label">Steps</div>
          <ul class="lm-knowledge__template-list">
            ${nodes.map((n, i) => `
              <li>
                <span class="material-symbols-outlined" aria-hidden="true">linear_scale</span>
                <span>${escapeHtml(String(n.title || n.name || n.type || `Step ${i + 1}`))}</span>
              </li>
            `).join("")}
          </ul>
        </section>
      ` : ""}
      <div class="lm-knowledge__meta">
        ${meta.map(([k, v]) => `
          <span class="lm-knowledge__meta-key">${escapeHtml(k)}</span>
          <span class="lm-knowledge__meta-val">${escapeHtml(String(v))}</span>
        `).join("")}
      </div>
    </div>
  `;
}

function _wireTemplateActions(item) {
  const pane = document.getElementById("knowledgePreviewPane");
  if (!pane) return;
  const btn = pane.querySelector('[data-action="use-template"]');
  btn?.addEventListener("click", async () => {
    const t = item.raw || {};
    const goal = t.goal || t.prompt || t.description || t.name || "";

    // Prefer the New Job drawer if present; otherwise just navigate to Jobs.
    const drawer = document.getElementById("newJobDrawer");
    const taskField = drawer?.querySelector("#taskCreationArea textarea, textarea");
    if (drawer && taskField) {
      drawer.dataset.open = "true";
      drawer.setAttribute("aria-hidden", "false");
      taskField.value = goal;
      taskField.focus();
      showToast(`Loaded template "${item.title}" into new job`, "success");
      return;
    }

    try {
      const { switchNav } = await import("./nav_rail.js");
      switchNav("jobs");
      showToast(`Switched to Jobs to run "${item.title}"`, "info");
    } catch {
      showToast("Could not open Jobs — try the nav rail.", "error");
    }
  });
}

// ── Graph modal ───────────────────────────────────────────────────

async function _openGraph() {
  const modal = document.getElementById("knowledgeGraphModal");
  if (!modal) return;
  modal.hidden = false;
  // Force a reflow so the opening transition triggers
  requestAnimationFrame(() => {
    modal.dataset.open = "true";
  });
  _state.graphOpen = true;

  const canvas = document.getElementById("knowledgeGraphCanvas");
  const subtitle = document.getElementById("knowledgeGraphSubtitle");
  if (!canvas) return;

  canvas.innerHTML = `
    <div class="lm-knowledge__empty">
      <span class="material-symbols-outlined" aria-hidden="true">graph_2</span>
      <div class="lm-knowledge__empty-title">Loading graph…</div>
    </div>
  `;
  if (subtitle) subtitle.textContent = "Loading…";

  const payload = await _fetchJson(`${API}/api/memories/graph`);
  if (!payload || !Array.isArray(payload.nodes) || !payload.nodes.length) {
    canvas.innerHTML = `
      <div class="lm-knowledge__empty">
        <span class="material-symbols-outlined" aria-hidden="true">graph_2</span>
        <div class="lm-knowledge__empty-title">No graph data yet</div>
        <div class="lm-knowledge__empty-body">Memories will cluster here once a few have been recorded.</div>
      </div>
    `;
    if (subtitle) subtitle.textContent = "0 nodes";
    return;
  }

  const total = payload.stats?.total ?? payload.nodes.length;
  if (subtitle) subtitle.textContent = `${total} node${total === 1 ? "" : "s"} • ${payload.edges?.length || 0} edges`;

  _renderGraphSvg(canvas, payload);
}

function _closeGraph() {
  const modal = document.getElementById("knowledgeGraphModal");
  if (!modal) return;
  _stopGraphSim();
  modal.dataset.open = "false";
  _state.graphOpen = false;
  // Hide after transition completes
  setTimeout(() => { if (!_state.graphOpen) modal.hidden = true; }, 260);
}

/**
 * Interactive force-directed graph — no dependencies.
 * Velocity-Verlet simulation with repulsion, spring edges, centering gravity.
 * Drag nodes, pan the canvas, wheel-zoom, hover to spotlight neighbourhoods,
 * click to select (shows full memory text in the info panel below).
 */
function _renderGraphSvg(canvas, { nodes: rawNodes, edges: rawEdges }) {
  _stopGraphSim();

  const rect = canvas.getBoundingClientRect();
  const W = Math.max(rect.width, 320);
  const H = Math.max(rect.height, 320);

  // Normalize nodes/edges
  const nodes = rawNodes.map((n) => {
    const category = n.category || n.group || "general";
    const subcategory = n.subcategory || "";
    const rawLabel = (n.label || n.content || "").trim();
    // Fallback so every node has *something* visible instead of a mute red dot
    const label = rawLabel || `#${n.id} · ${category}${subcategory && subcategory !== category ? "/" + subcategory : ""}`;
    return {
      id: String(n.id),
      label,
      content: n.content || n.label || "",
      category,
      subcategory,
      access: Number(n.access_count || 0),
      x: W / 2 + (Math.random() - 0.5) * Math.min(W, H) * 0.6,
      y: H / 2 + (Math.random() - 0.5) * Math.min(W, H) * 0.6,
      vx: 0, vy: 0,
      degree: 0,
      compId: 0,
      pinned: false,
    };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = (rawEdges || [])
    .map((e) => ({ source: byId.get(String(e.source)), target: byId.get(String(e.target)) }))
    .filter((e) => e.source && e.target && e.source !== e.target);

  // Degree + connected components (union-find) for layout hints & coloring
  const parent = new Map(nodes.map((n) => [n.id, n.id]));
  const find = (x) => { while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x))); x = parent.get(x); } return x; };
  edges.forEach((e) => {
    e.source.degree++; e.target.degree++;
    const a = find(e.source.id), b = find(e.target.id);
    if (a !== b) parent.set(a, b);
  });
  const compMap = new Map();
  nodes.forEach((n) => {
    const root = find(n.id);
    if (!compMap.has(root)) compMap.set(root, compMap.size);
    n.compId = compMap.get(root);
  });
  const compCount = compMap.size;

  // ── Radial BFS layout ─────────────────────────────────────────
  // Pick the most important node as the root (high degree + access + has label),
  // then BFS to assign every reachable node a hop-distance "level". Nodes on
  // the same level share a concentric ring. Unreachable components start their
  // own BFS from their local root and share the ring system.
  const adj = new Map(nodes.map((n) => [n.id, []]));
  edges.forEach((e) => {
    adj.get(e.source.id).push(e.target.id);
    adj.get(e.target.id).push(e.source.id);
  });
  const importanceOf = (n) => (n.degree || 0) * 2 + (n.access || 0) + (n.label ? 1 : 0);
  const unvisited = new Set(nodes.map((n) => n.id));
  const levelById = new Map();
  let maxLevel = 0;

  while (unvisited.size) {
    // Pick the most-important remaining node as this component's root
    let rootId = null;
    let bestScore = -Infinity;
    unvisited.forEach((id) => {
      const s = importanceOf(byId.get(id));
      if (s > bestScore) { bestScore = s; rootId = id; }
    });
    // BFS
    const queue = [rootId];
    levelById.set(rootId, 0);
    unvisited.delete(rootId);
    while (queue.length) {
      const cur = queue.shift();
      const lvl = levelById.get(cur);
      if (lvl > maxLevel) maxLevel = lvl;
      (adj.get(cur) || []).forEach((nb) => {
        if (!unvisited.has(nb)) return;
        unvisited.delete(nb);
        levelById.set(nb, lvl + 1);
        queue.push(nb);
      });
    }
  }

  // Group nodes by level, then seat them evenly around each ring
  const byLevel = new Map();
  nodes.forEach((n) => {
    n.level = levelById.get(n.id) || 0;
    if (!byLevel.has(n.level)) byLevel.set(n.level, []);
    byLevel.get(n.level).push(n);
  });
  // Sort within-level by importance so high-value nodes get stable angular slots
  byLevel.forEach((arr) => arr.sort((a, b) => importanceOf(b) - importanceOf(a)));

  const cx = W / 2, cy = H / 2;
  const ringStep = Math.min(W, H) * 0.38 / Math.max(1, maxLevel || 1);
  byLevel.forEach((arr, level) => {
    if (level === 0) {
      arr.forEach((n, i) => {
        // Multiple "roots" (one per disconnected component) sit near center
        const theta = arr.length > 1 ? (i / arr.length) * Math.PI * 2 : 0;
        const r = arr.length > 1 ? ringStep * 0.25 : 0;
        n.x = cx + Math.cos(theta) * r;
        n.y = cy + Math.sin(theta) * r;
        n.ringRadius = r;
      });
      return;
    }
    const r = ringStep * level;
    arr.forEach((n, i) => {
      // Offset each ring's start angle a bit so spokes don't line up across rings
      const theta = (i / arr.length) * Math.PI * 2 + level * 0.3;
      n.x = cx + Math.cos(theta) * r;
      n.y = cy + Math.sin(theta) * r;
      n.ringRadius = r;
    });
  });

  // Build SVG scaffold
  canvas.innerHTML = `
    <svg class="lm-knowledge__graph-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"
         role="img" aria-label="Memory graph — drag nodes, wheel to zoom, drag background to pan">
      <rect class="lm-knowledge__graph-bg" x="0" y="0" width="${W}" height="${H}" fill="transparent"/>
      <g class="lm-knowledge__graph-viewport">
        <g class="lm-knowledge__graph-rings">
          ${Array.from({ length: maxLevel }, (_, i) => {
            const r = ringStep * (i + 1);
            return `<circle class="lm-knowledge__graph-ring" cx="${cx}" cy="${cy}" r="${r.toFixed(1)}"/>
                    <text class="lm-knowledge__graph-ring-label" x="${cx + r + 6}" y="${cy}">L${i + 1}</text>`;
          }).join("")}
        </g>
        <g class="lm-knowledge__graph-edges"></g>
        <g class="lm-knowledge__graph-nodes"></g>
        <g class="lm-knowledge__graph-labels"></g>
      </g>
    </svg>
    <div class="lm-knowledge__graph-hud">
      <div class="lm-knowledge__graph-legend" aria-hidden="true">
        <span class="lm-knowledge__graph-legend-dot" style="background:var(--lm-accent)"></span>
        <span>${nodes.length} nodes · ${edges.length} edges · ${compCount} cluster${compCount === 1 ? "" : "s"}</span>
      </div>
      <div class="lm-knowledge__graph-controls">
        <input type="search" class="lm-knowledge__graph-search" id="knowledgeGraphSearch" placeholder="Spotlight nodes…" aria-label="Spotlight matching nodes"/>
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="knowledgeGraphReset" aria-label="Reset view">
          <span class="material-symbols-outlined" aria-hidden="true">restart_alt</span> Reset
        </button>
        <button type="button" class="lm-btn lm-btn--ghost lm-btn--sm" id="knowledgeGraphPause" aria-pressed="false">
          <span class="material-symbols-outlined" aria-hidden="true">pause</span> <span id="knowledgeGraphPauseLabel">Pause</span>
        </button>
      </div>
    </div>
    <div class="lm-knowledge__graph-info" id="knowledgeGraphInfo" hidden></div>
    <div class="lm-knowledge__graph-tooltip" id="knowledgeGraphTooltip" hidden></div>
  `;

  const svg = canvas.querySelector("svg");
  const tooltipEl = canvas.querySelector("#knowledgeGraphTooltip");
  const viewport = canvas.querySelector(".lm-knowledge__graph-viewport");
  const edgesG = canvas.querySelector(".lm-knowledge__graph-edges");
  const nodesG = canvas.querySelector(".lm-knowledge__graph-nodes");
  const labelsG = canvas.querySelector(".lm-knowledge__graph-labels");
  const infoPanel = canvas.querySelector("#knowledgeGraphInfo");

  // Render static SVG children (once)
  const SVG_NS = "http://www.w3.org/2000/svg";
  const edgeEls = edges.map((e) => {
    const ln = document.createElementNS(SVG_NS, "line");
    ln.setAttribute("class", "lm-knowledge__graph-edge");
    edgesG.appendChild(ln);
    e.el = ln;
    return ln;
  });
  const nodeEls = nodes.map((n) => {
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("class", "lm-knowledge__graph-node");
    c.dataset.id = n.id;
    // Color by category (stable) so same category always reads the same; fall
    // back to subcategory variance for visual spread within a single category
    c.style.setProperty("--hue", String(_categoryHue(n.category, n.subcategory)));
    c.setAttribute("r", String(_nodeRadius(n)));
    const tt = document.createElementNS(SVG_NS, "title");
    tt.textContent = _shortLabel(n.label, 120);
    c.appendChild(tt);
    nodesG.appendChild(c);
    n.el = c;
    return c;
  });
  // Labels: only show for the top-N most important nodes at rest so the view
  // isn't a wall of overlapping text. Everyone else gets revealed on hover
  // (via the floating tooltip) or when selected / searched.
  const importance = (n) => (n.degree || 0) * 2 + (n.access || 0) + (n.label ? 1 : 0);
  const labelCap = Math.max(4, Math.min(10, Math.round(nodes.length * 0.2)));
  const labelIds = new Set(
    nodes
      .slice()
      .sort((a, b) => importance(b) - importance(a))
      .slice(0, labelCap)
      .map((n) => n.id)
  );
  nodes.forEach((n) => {
    if (!labelIds.has(n.id)) return;
    const t = document.createElementNS(SVG_NS, "text");
    t.setAttribute("class", "lm-knowledge__graph-label");
    t.textContent = _shortLabel(n.label, 22);
    labelsG.appendChild(t);
    n.labelEl = t;
  });

  // ── Simulation state ───────────────────────────────────────────
  const sim = {
    W, H,
    nodes, edges,
    running: true,
    alpha: 1.0,
    alphaDecay: 0.012,
    alphaMin: 0.02,
    transform: { k: 1, tx: 0, ty: 0 },
    selectedId: null,
    hoverId: null,
    filterQuery: "",
    rafId: 0,
    canvas,
  };
  _state.graphSim = sim;

  // ── Force tick ────────────────────────────────────────────────
  // Radial-BFS layout: each node has a target ring (n.ringRadius). Forces are
  // tuned to let nodes slide *around* their ring (tangential spacing) without
  // collapsing the level structure radially.
  const REPEL_K = 600;           // softer — the ring layout already spreads nodes
  const SPRING_K = 0.015;        // edges act as gentle ties, not primary layout driver
  const SPRING_LEN = 90;
  const RADIAL_K = 0.18;         // how aggressively nodes snap back to their ring
  const DAMPING = 0.82;
  const CX = W / 2, CY = H / 2;

  function tick() {
    if (!sim.running || sim.alpha < sim.alphaMin) {
      sim.running = false;
      _applyFrame(sim);
      return;
    }
    // Repulsion — plain O(n²); fine for ≤ ~500 nodes
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = dx * dx + dy * dy; }
        const f = (REPEL_K * sim.alpha) / d2;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }
    // Spring attraction along edges
    edges.forEach((e) => {
      const dx = e.target.x - e.source.x;
      const dy = e.target.y - e.source.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - SPRING_LEN) * SPRING_K * sim.alpha;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      e.source.vx += fx; e.source.vy += fy;
      e.target.vx -= fx; e.target.vy -= fy;
    });
    // Radial anchor: pull each node toward its assigned ring radius
    nodes.forEach((n) => {
      const dx = n.x - CX, dy = n.y - CY;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const target = n.ringRadius || 0;
      const err = target - d;                     // positive → need to move outward
      const fx = (dx / d) * err * RADIAL_K;
      const fy = (dy / d) * err * RADIAL_K;
      n.vx += fx; n.vy += fy;
    });
    // Integrate + dampen
    nodes.forEach((n) => {
      if (n.pinned) { n.vx = 0; n.vy = 0; return; }
      n.vx *= DAMPING; n.vy *= DAMPING;
      n.x += n.vx; n.y += n.vy;
      // Keep inside canvas with soft margin
      const m = 20;
      if (n.x < m) { n.x = m; n.vx = Math.abs(n.vx) * 0.5; }
      if (n.x > W - m) { n.x = W - m; n.vx = -Math.abs(n.vx) * 0.5; }
      if (n.y < m) { n.y = m; n.vy = Math.abs(n.vy) * 0.5; }
      if (n.y > H - m) { n.y = H - m; n.vy = -Math.abs(n.vy) * 0.5; }
    });
    sim.alpha -= sim.alphaDecay;

    _applyFrame(sim);
    sim.rafId = requestAnimationFrame(tick);
  }
  sim.rafId = requestAnimationFrame(tick);

  // ── Interaction ──────────────────────────────────────────────
  let dragNode = null;
  let dragStart = null;   // for panning
  let dragOrigin = null;

  const toLocal = (clientX, clientY) => {
    const r = svg.getBoundingClientRect();
    const xs = W / r.width, ys = H / r.height;
    const px = (clientX - r.left) * xs;
    const py = (clientY - r.top) * ys;
    // Undo viewport transform
    const { k, tx, ty } = sim.transform;
    return { x: (px - tx) / k, y: (py - ty) / k };
  };

  svg.addEventListener("pointerdown", (e) => {
    const targetNode = e.target.closest(".lm-knowledge__graph-node");
    svg.setPointerCapture(e.pointerId);
    if (targetNode) {
      dragNode = nodes.find((n) => n.id === targetNode.dataset.id) || null;
      if (dragNode) {
        dragNode.pinned = true;
        _reheat(sim, 0.35);
      }
    } else {
      dragStart = { x: e.clientX, y: e.clientY };
      dragOrigin = { tx: sim.transform.tx, ty: sim.transform.ty };
    }
  });
  svg.addEventListener("pointermove", (e) => {
    if (dragNode) {
      const p = toLocal(e.clientX, e.clientY);
      dragNode.x = p.x; dragNode.y = p.y;
      dragNode.vx = 0; dragNode.vy = 0;
      if (!sim.running) _applyFrame(sim);
    } else if (dragStart) {
      const r = svg.getBoundingClientRect();
      const xs = W / r.width, ys = H / r.height;
      sim.transform.tx = dragOrigin.tx + (e.clientX - dragStart.x) * xs;
      sim.transform.ty = dragOrigin.ty + (e.clientY - dragStart.y) * ys;
      _applyTransform(sim, viewport);
    } else {
      // Hover
      const t = e.target.closest(".lm-knowledge__graph-node");
      const id = t?.dataset.id || null;
      if (id !== sim.hoverId) {
        sim.hoverId = id;
        _updateHighlight(sim);
      }
      // Position floating tooltip beside the cursor when hovering a node
      if (tooltipEl) {
        if (id) {
          const n = nodes.find((nn) => nn.id === id);
          if (n) {
            const canvasRect = canvas.getBoundingClientRect();
            const x = e.clientX - canvasRect.left + 14;
            const y = e.clientY - canvasRect.top + 14;
            tooltipEl.hidden = false;
            tooltipEl.style.left = `${Math.min(x, canvasRect.width - 320)}px`;
            tooltipEl.style.top = `${Math.min(y, canvasRect.height - 120)}px`;
            tooltipEl.innerHTML = `
              <div class="lm-knowledge__graph-tooltip-head">
                <span class="lm-chip" style="--chip-hue:${_categoryHue(n.category, n.subcategory)}">
                  ${escapeHtml(n.category)}${n.subcategory && n.subcategory !== n.category ? " · " + escapeHtml(n.subcategory) : ""}
                </span>
                <span class="lm-mute">#${escapeHtml(n.id)}</span>
              </div>
              <div class="lm-knowledge__graph-tooltip-body">${escapeHtml(_shortLabel(n.content || n.label, 220))}</div>
              <div class="lm-knowledge__graph-tooltip-foot">${n.degree} connection${n.degree === 1 ? "" : "s"}${n.access ? ` · accessed ${n.access}×` : ""}</div>
            `;
          }
        } else {
          tooltipEl.hidden = true;
        }
      }
    }
  });
  const endDrag = () => {
    if (dragNode) { dragNode.pinned = false; dragNode = null; }
    dragStart = null; dragOrigin = null;
  };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("pointerleave", () => {
    if (sim.hoverId) { sim.hoverId = null; _updateHighlight(sim); }
    if (tooltipEl) tooltipEl.hidden = true;
  });

  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = svg.getBoundingClientRect();
    const xs = W / r.width, ys = H / r.height;
    const px = (e.clientX - r.left) * xs;
    const py = (e.clientY - r.top) * ys;
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const k2 = Math.max(0.3, Math.min(4, sim.transform.k * factor));
    // Zoom around cursor
    sim.transform.tx = px - (px - sim.transform.tx) * (k2 / sim.transform.k);
    sim.transform.ty = py - (py - sim.transform.ty) * (k2 / sim.transform.k);
    sim.transform.k = k2;
    _applyTransform(sim, viewport);
  }, { passive: false });

  // Click a node → select, show info
  svg.addEventListener("click", (e) => {
    const t = e.target.closest(".lm-knowledge__graph-node");
    const id = t?.dataset.id || null;
    sim.selectedId = sim.selectedId === id ? null : id;
    _updateHighlight(sim);
    _renderGraphInfo(sim, infoPanel);
  });

  // Controls
  canvas.querySelector("#knowledgeGraphReset")?.addEventListener("click", () => {
    sim.transform = { k: 1, tx: 0, ty: 0 };
    _applyTransform(sim, viewport);
    _reheat(sim, 0.8);
  });
  const pauseBtn = canvas.querySelector("#knowledgeGraphPause");
  const pauseLabel = canvas.querySelector("#knowledgeGraphPauseLabel");
  pauseBtn?.addEventListener("click", () => {
    if (sim.running) {
      sim.running = false;
      pauseBtn.setAttribute("aria-pressed", "true");
      if (pauseLabel) pauseLabel.textContent = "Resume";
      pauseBtn.querySelector(".material-symbols-outlined").textContent = "play_arrow";
    } else {
      _reheat(sim, 0.4);
      pauseBtn.setAttribute("aria-pressed", "false");
      if (pauseLabel) pauseLabel.textContent = "Pause";
      pauseBtn.querySelector(".material-symbols-outlined").textContent = "pause";
    }
  });
  const searchInput = canvas.querySelector("#knowledgeGraphSearch");
  searchInput?.addEventListener("input", (e) => {
    sim.filterQuery = String(e.target.value || "").trim().toLowerCase();
    _updateHighlight(sim);
  });
}

function _nodeRadius(n) {
  return 4 + Math.min(8, Math.log2((n.access || 0) + (n.degree || 0) * 2 + 2));
}

// Stable hue per category (with subtle subcategory variance) so nodes read as
// grouped-by-category regardless of how the force sim settles them
function _categoryHue(category, subcategory) {
  const str = String(category || "general");
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  const base = h % 360;
  if (!subcategory || subcategory === category) return base;
  let s = 0;
  for (let i = 0; i < String(subcategory).length; i++) s = (s * 17 + String(subcategory).charCodeAt(i)) >>> 0;
  // ±18° around the category hue so same-category nodes stay recognisably together
  return (base + (s % 37) - 18 + 360) % 360;
}

function _shortLabel(label, n = 60) {
  const v = String(label || "").replace(/\s+/g, " ").trim();
  if (!v) return "";
  return v.length > n ? v.slice(0, n - 1) + "…" : v;
}

function _stopGraphSim() {
  const sim = _state.graphSim;
  if (sim) {
    sim.running = false;
    if (sim.rafId) cancelAnimationFrame(sim.rafId);
  }
  _state.graphSim = null;
}

function _reheat(sim, alpha) {
  sim.alpha = alpha;
  if (!sim.running) {
    sim.running = true;
    const tick = () => {
      // Re-enter the simulation by flagging and letting the already-running
      // RAF loop resume via the same mechanism — simplest: just call through
      // the stored node update until it settles.
    };
    // The tick loop inside _renderGraphSvg captures `sim` by closure and
    // re-checks `sim.running`; spin up a fresh ticker here.
    const loop = () => {
      if (!sim.running || sim.alpha < sim.alphaMin) { sim.running = false; return; }
      // Copy of the simulation step, lightweight — just uses the same sim
      _stepSim(sim);
      _applyFrame(sim);
      sim.alpha -= sim.alphaDecay;
      sim.rafId = requestAnimationFrame(loop);
    };
    if (sim.rafId) cancelAnimationFrame(sim.rafId);
    sim.rafId = requestAnimationFrame(loop);
  }
}

function _stepSim(sim) {
  const { nodes, edges, W, H } = sim;
  const REPEL_K = 600, SPRING_K = 0.015, SPRING_LEN = 90, RADIAL_K = 0.18, DAMPING = 0.82;
  const CX = W / 2, CY = H / 2;
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = dx * dx + dy * dy; }
      const d = Math.sqrt(d2);
      const f = (REPEL_K * sim.alpha) / d2;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
  }
  edges.forEach((e) => {
    const dx = e.target.x - e.source.x, dy = e.target.y - e.source.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const f = (d - SPRING_LEN) * SPRING_K * sim.alpha;
    const fx = (dx / d) * f, fy = (dy / d) * f;
    e.source.vx += fx; e.source.vy += fy; e.target.vx -= fx; e.target.vy -= fy;
  });
  nodes.forEach((n) => {
    const dx = n.x - CX, dy = n.y - CY;
    const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const err = (n.ringRadius || 0) - d;
    n.vx += (dx / d) * err * RADIAL_K;
    n.vy += (dy / d) * err * RADIAL_K;
  });
  nodes.forEach((n) => {
    if (n.pinned) { n.vx = 0; n.vy = 0; return; }
    n.vx *= DAMPING; n.vy *= DAMPING;
    n.x += n.vx; n.y += n.vy;
    const m = 20;
    if (n.x < m) { n.x = m; n.vx = Math.abs(n.vx) * 0.5; }
    if (n.x > W - m) { n.x = W - m; n.vx = -Math.abs(n.vx) * 0.5; }
    if (n.y < m) { n.y = m; n.vy = Math.abs(n.vy) * 0.5; }
    if (n.y > H - m) { n.y = H - m; n.vy = -Math.abs(n.vy) * 0.5; }
  });
}

function _applyFrame(sim) {
  sim.edges.forEach((e) => {
    if (!e.el) return;
    e.el.setAttribute("x1", e.source.x.toFixed(2));
    e.el.setAttribute("y1", e.source.y.toFixed(2));
    e.el.setAttribute("x2", e.target.x.toFixed(2));
    e.el.setAttribute("y2", e.target.y.toFixed(2));
  });
  sim.nodes.forEach((n) => {
    if (!n.el) return;
    n.el.setAttribute("cx", n.x.toFixed(2));
    n.el.setAttribute("cy", n.y.toFixed(2));
    if (n.labelEl) {
      n.labelEl.setAttribute("x", (n.x + _nodeRadius(n) + 4).toFixed(2));
      n.labelEl.setAttribute("y", (n.y + 3).toFixed(2));
    }
  });
}

function _applyTransform(sim, viewport) {
  const { k, tx, ty } = sim.transform;
  viewport.setAttribute("transform", `translate(${tx} ${ty}) scale(${k})`);
}

function _updateHighlight(sim) {
  const { nodes, edges, hoverId, selectedId, filterQuery } = sim;
  const focus = hoverId || selectedId;
  const neighbourIds = new Set();
  if (focus) {
    neighbourIds.add(focus);
    edges.forEach((e) => {
      if (e.source.id === focus) neighbourIds.add(e.target.id);
      if (e.target.id === focus) neighbourIds.add(e.source.id);
    });
  }
  const q = filterQuery;
  nodes.forEach((n) => {
    if (!n.el) return;
    const matches = !q || (n.label || "").toLowerCase().includes(q) || n.id.includes(q);
    const inFocus = !focus || neighbourIds.has(n.id);
    const dim = !matches || !inFocus;
    n.el.classList.toggle("lm-knowledge__graph-node--dim", dim);
    n.el.classList.toggle("lm-knowledge__graph-node--selected", selectedId === n.id);
    n.el.classList.toggle("lm-knowledge__graph-node--hot", focus === n.id);
    if (n.labelEl) n.labelEl.classList.toggle("lm-knowledge__graph-label--dim", dim);
  });
  edges.forEach((e) => {
    if (!e.el) return;
    const connectsFocus = focus && (e.source.id === focus || e.target.id === focus);
    const sMatch = !q || (e.source.label || "").toLowerCase().includes(q);
    const tMatch = !q || (e.target.label || "").toLowerCase().includes(q);
    const dim = (focus && !connectsFocus) || (q && !sMatch && !tMatch);
    e.el.classList.toggle("lm-knowledge__graph-edge--dim", !!dim);
    e.el.classList.toggle("lm-knowledge__graph-edge--hot", !!connectsFocus);
  });
}

function _renderGraphInfo(sim, panel) {
  const sel = sim.selectedId ? sim.nodes.find((n) => n.id === sim.selectedId) : null;
  if (!sel) { panel.hidden = true; panel.innerHTML = ""; return; }
  const neighbours = sim.edges
    .map((e) => (e.source.id === sel.id ? e.target : (e.target.id === sel.id ? e.source : null)))
    .filter(Boolean);
  panel.hidden = false;
  panel.innerHTML = `
    <div class="lm-knowledge__graph-info-head">
      <div class="lm-knowledge__graph-info-tags">
        <span class="lm-chip">${escapeHtml(sel.category || "memory")}</span>
        ${sel.subcategory ? `<span class="lm-chip">${escapeHtml(sel.subcategory)}</span>` : ""}
        <span class="lm-mute">#${escapeHtml(sel.id)} · ${sel.degree} connection${sel.degree === 1 ? "" : "s"}</span>
      </div>
      <button type="button" class="lm-icon-btn" id="knowledgeGraphInfoClose" aria-label="Close selection">
        <span class="material-symbols-outlined" aria-hidden="true">close</span>
      </button>
    </div>
    <div class="lm-knowledge__graph-info-body">${escapeHtml(sel.label || "(no content)")}</div>
    ${neighbours.length ? `<div class="lm-knowledge__graph-info-neighbours">
      <span class="lm-label">Connected to</span>
      ${neighbours.slice(0, 12).map((n) => `<button type="button" class="lm-chip lm-knowledge__graph-info-neighbour" data-id="${escapeHtml(n.id)}">${escapeHtml(_shortLabel(n.label, 36) || `#${n.id}`)}</button>`).join("")}
      ${neighbours.length > 12 ? `<span class="lm-mute">+${neighbours.length - 12} more</span>` : ""}
    </div>` : ""}
  `;
  panel.querySelector("#knowledgeGraphInfoClose")?.addEventListener("click", () => {
    sim.selectedId = null;
    _updateHighlight(sim);
    _renderGraphInfo(sim, panel);
  });
  panel.querySelectorAll(".lm-knowledge__graph-info-neighbour").forEach((btn) => {
    btn.addEventListener("click", () => {
      sim.selectedId = btn.dataset.id;
      _updateHighlight(sim);
      _renderGraphInfo(sim, panel);
    });
  });
}

// ── Helpers ───────────────────────────────────────────────────────

function _key(item) {
  return `${item.source}:${item.id}`;
}

function _extractTags(m) {
  if (Array.isArray(m.tags)) return m.tags.map(String);
  if (typeof m.tags === "string") return m.tags.split(",").map((t) => t.trim()).filter(Boolean);
  const cat = m.category || m.subcategory;
  return cat ? [String(cat)] : [];
}

function _collectConstraints(t) {
  if (Array.isArray(t.constraints)) return t.constraints.map(String);
  if (typeof t.constraints === "string") {
    return t.constraints.split(/\r?\n|;/).map((s) => s.trim()).filter(Boolean);
  }
  return [];
}

function _describeTemplate(t) {
  if (Array.isArray(t.nodes) && t.nodes.length) {
    return `${t.nodes.length} step${t.nodes.length === 1 ? "" : "s"}`;
  }
  return "";
}

function _truncate(str, n) {
  const v = String(str || "");
  if (v.length <= n) return v;
  return v.slice(0, n - 1) + "…";
}

function _parseTs(value) {
  if (!value) return 0;
  if (typeof value === "number") return value < 1e12 ? value * 1000 : value;
  const n = Date.parse(value);
  return Number.isFinite(n) ? n : 0;
}

function _fmtRelative(ts) {
  if (!ts) return "—";
  const delta = Math.floor((Date.now() - ts) / 1000);
  if (delta < 60)    return `${delta}s`;
  if (delta < 3600)  return `${Math.floor(delta / 60)}m`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h`;
  if (delta < 604800) return `${Math.floor(delta / 86400)}d`;
  return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function _fmtAbsolute(ts) {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function _fmtBytes(n) {
  if (!n || !Number.isFinite(+n)) return "";
  const v = +n;
  if (v < 1024)            return `${v} B`;
  if (v < 1024 * 1024)     return `${(v / 1024).toFixed(1)} KB`;
  if (v < 1024 ** 3)       return `${(v / 1024 / 1024).toFixed(1)} MB`;
  return `${(v / 1024 ** 3).toFixed(2)} GB`;
}

function _extOf(filename) {
  const m = /\.([A-Za-z0-9]+)$/.exec(String(filename || ""));
  return m ? m[1].toLowerCase() : "text";
}
