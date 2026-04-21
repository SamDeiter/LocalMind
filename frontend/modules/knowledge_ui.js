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
  modal.dataset.open = "false";
  _state.graphOpen = false;
  // Hide after transition completes
  setTimeout(() => { if (!_state.graphOpen) modal.hidden = true; }, 260);
}

/**
 * Lightweight radial graph renderer — no d3 dependency.
 * Groups nodes by category and lays them out on concentric rings so the
 * clusters read as distinct segments without a real force simulation.
 */
function _renderGraphSvg(canvas, { nodes, edges }) {
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(rect.width, 320);
  const h = Math.max(rect.height, 320);
  const cx = w / 2;
  const cy = h / 2;

  // Group nodes by category
  const groups = new Map();
  nodes.forEach((n) => {
    const key = n.category || n.group || "general";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(n);
  });

  const groupKeys = Array.from(groups.keys());
  const groupAngleStep = (Math.PI * 2) / Math.max(groupKeys.length, 1);
  const positions = new Map();

  groupKeys.forEach((k, gi) => {
    const members = groups.get(k);
    const ringRadius = Math.min(w, h) * 0.38;
    const innerRadius = Math.min(w, h) * 0.18;
    members.forEach((n, i) => {
      const angle = gi * groupAngleStep + (i / Math.max(members.length, 1)) * (groupAngleStep * 0.9);
      const r = innerRadius + (ringRadius - innerRadius) * ((i % 4) / 4);
      positions.set(n.id, { x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r });
    });
  });

  const svgEdges = (edges || []).map((e) => {
    const a = positions.get(String(e.source));
    const b = positions.get(String(e.target));
    if (!a || !b) return "";
    return `<line class="lm-knowledge__graph-edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" />`;
  }).join("");

  const svgNodes = nodes.map((n) => {
    const p = positions.get(n.id);
    if (!p) return "";
    const r = 4 + Math.min(6, Math.log2((n.access_count || 0) + 2));
    const title = escapeHtml(n.label || n.id);
    return `<circle class="lm-knowledge__graph-node" cx="${p.x}" cy="${p.y}" r="${r}"><title>${title}</title></circle>`;
  }).join("");

  canvas.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Memory graph">
      ${svgEdges}
      ${svgNodes}
    </svg>
  `;
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
