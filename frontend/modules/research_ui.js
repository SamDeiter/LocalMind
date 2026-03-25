/**
 * research_ui.js — ArXiv Paper Search UI + Apply-to-Codebase + Save-to-Context
 * Provides the frontend panel for searching arXiv papers from the dashboard,
 * applying paper techniques as code improvement proposals, and injecting
 * paper context into the chat system prompt.
 */

import { API } from "./state.js";
import { systemPromptText } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

// ── State ───────────────────────────────────────────────────────────

let _searching = false;
let _currentPage = 0;
let _currentQuery = "";

// ── ArXiv Search ────────────────────────────────────────────────────

export async function searchArxiv(query, page = 0) {
  if (!query || query.trim().length < 2) {
    showToast("Please enter a search query", "error");
    return;
  }
  if (_searching) return;
  _searching = true;
  _currentQuery = query.trim();
  _currentPage = page;

  const resultsEl = document.getElementById("arxivResults");
  const btn = document.getElementById("arxivSearchBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Searching…";
  }
  if (resultsEl) {
    resultsEl.innerHTML =
      '<div class="arxiv-loading">🔄 Searching arXiv…</div>';
  }

  try {
    const resp = await fetch(
      `${API}/api/research/arxiv?q=${encodeURIComponent(_currentQuery)}&max=8&page=${page}`,
    );
    const data = await resp.json();

    if (!resultsEl) return;

    if (data.error) {
      resultsEl.innerHTML = `<div class="arxiv-empty">⚠️ ${escapeHtml(data.error)}</div>`;
      return;
    }

    const papers = data.papers || [];
    if (papers.length === 0) {
      resultsEl.innerHTML =
        '<div class="arxiv-empty">No papers found. Try a different query.</div>';
      return;
    }

    // Render paper cards
    let html = papers
      .map((p, idx) => {
        const title = escapeHtml(p.title || "Untitled");
        const authors = escapeHtml(
          (p.authors || "Unknown authors").substring(0, 80),
        );
        const abstract = escapeHtml(
          (p.abstract || "No abstract available").substring(0, 160),
        );
        const url = p.url || "#";
        const published = p.published
          ? new Date(p.published).toLocaleDateString()
          : "";

        return `
        <div class="bg-surface-container-high/40 border border-outline-variant/20 rounded-xl p-4 space-y-3 hover:border-primary/30 transition-all group/card" data-idx="${idx}">
          <a href="${url}" target="_blank" class="block space-y-2">
            <h4 class="text-[11px] font-bold text-on-background leading-tight group-hover/card:text-primary transition-colors">${title}</h4>
            <div class="flex items-center gap-2 text-[9px] font-bold uppercase tracking-widest text-outline/60">
              <span class="truncate max-w-[150px]">${authors}</span>
              ${published ? `<span class="w-1 h-1 rounded-full bg-outline-variant/40"></span><span>${published}</span>` : ""}
            </div>
            <p class="text-[10px] text-on-surface-variant leading-relaxed line-clamp-2 opacity-70">${abstract}…</p>
          </a>
          <div class="flex gap-2 pt-1 border-t border-outline-variant/10">
            <button class="arxiv-apply-btn flex-1 py-1.5 bg-primary/10 border border-primary/20 rounded text-[9px] font-bold uppercase tracking-widest text-primary hover:bg-primary/20 transition-all" data-idx="${idx}">
              Apply
            </button>
            <button class="arxiv-context-btn px-2 py-1.5 bg-surface-container-highest border border-outline-variant/30 rounded text-[9px] font-bold uppercase tracking-widest text-outline hover:text-on-surface transition-all" data-idx="${idx}">
              Save
            </button>
          </div>
        </div>`;
      })
      .join("");

    // Pagination controls
    html += `
      <div class="arxiv-pagination">
        <button class="arxiv-page-btn" id="arxivPrevPage" ${page === 0 ? "disabled" : ""}>◀ Prev</button>
        <span class="arxiv-page-info">Page ${page + 1}</span>
        <button class="arxiv-page-btn" id="arxivNextPage" ${papers.length < 8 ? "disabled" : ""}>Next ▶</button>
      </div>`;

    resultsEl.innerHTML = html;

    // Store paper data for buttons
    resultsEl._papers = papers;

    // Bind apply buttons
    resultsEl.querySelectorAll(".arxiv-apply-btn").forEach((applyBtn) => {
      applyBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        const idx = parseInt(applyBtn.dataset.idx, 10);
        const paper = resultsEl._papers[idx];
        if (paper) applyPaper(paper, applyBtn);
      });
    });

    // Bind save-to-context buttons
    resultsEl.querySelectorAll(".arxiv-context-btn").forEach((ctxBtn) => {
      ctxBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        const idx = parseInt(ctxBtn.dataset.idx, 10);
        const paper = resultsEl._papers[idx];
        if (paper) saveToContext(paper, ctxBtn);
      });
    });

    // Bind pagination
    const prevBtn = document.getElementById("arxivPrevPage");
    const nextBtn = document.getElementById("arxivNextPage");
    if (prevBtn) {
      prevBtn.addEventListener("click", () => searchArxiv(_currentQuery, _currentPage - 1));
    }
    if (nextBtn) {
      nextBtn.addEventListener("click", () => searchArxiv(_currentQuery, _currentPage + 1));
    }
  } catch (err) {
    console.error("ArXiv search error:", err);
    if (resultsEl) {
      resultsEl.innerHTML =
        '<div class="arxiv-empty">❌ Search failed — is the server running?</div>';
    }
  } finally {
    _searching = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = "🔍 Search";
    }
  }
}

// ── Apply Paper to Codebase ─────────────────────────────────────────

async function applyPaper(paper, btnEl) {
  btnEl.disabled = true;
  btnEl.textContent = "🔄 Analyzing…";
  btnEl.classList.add("applying");

  try {
    const resp = await fetch(`${API}/api/research/apply-paper`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: paper.title || "",
        abstract: paper.abstract || "",
        url: paper.url || "",
      }),
    });

    const data = await resp.json();

    if (data.error) {
      showToast(`⚠️ ${data.error}`, "error");
      btnEl.textContent = "🧠 Apply to Codebase";
    } else if (data.proposal) {
      const proposalTitle = data.proposal.title || "Untitled";
      showToast(`✅ Proposal created: "${proposalTitle}"`, "success");
      btnEl.textContent = "✅ Proposal Created";
      btnEl.classList.add("applied");
      return; // Don't re-enable
    } else {
      showToast("No proposal was generated — try a different paper", "error");
      btnEl.textContent = "🧠 Apply to Codebase";
    }
  } catch (err) {
    console.error("Apply paper error:", err);
    showToast("❌ Failed to apply paper — is the server running?", "error");
    btnEl.textContent = "🧠 Apply to Codebase";
  } finally {
    btnEl.disabled = false;
    btnEl.classList.remove("applying");
  }
}

// ── Save to Context ─────────────────────────────────────────────────

function saveToContext(paper, btnEl) {
  const title = paper.title || "Untitled";
  const abstract = (paper.abstract || "").substring(0, 500);

  if (!systemPromptText) {
    showToast("System prompt textarea not found", "error");
    return;
  }

  const contextBlock = `\n\n[Research Context: ${title}]\n${abstract}\n`;
  const current = systemPromptText.value || "";

  // Don't add duplicates
  if (current.includes(title)) {
    showToast("This paper is already in context", "error");
    return;
  }

  systemPromptText.value = current + contextBlock;
  showToast(`💬 Paper added to context: "${title.substring(0, 50)}…"`, "success");

  btnEl.textContent = "✅ In Context";
  btnEl.classList.add("applied");
  btnEl.disabled = true;
}

// ── Init ────────────────────────────────────────────────────────────

export function initResearchPanel() {
  const btn = document.getElementById("arxivSearchBtn");
  const input = document.getElementById("arxivSearchInput");

  if (btn && input) {
    btn.addEventListener("click", () => {
      _currentPage = 0;
      searchArxiv(input.value);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        _currentPage = 0;
        searchArxiv(input.value);
      }
    });
  }
}

/** Global Search Implementation */
export function initGlobalSearch() {
  const input = document.getElementById("globalSearchInput");
  if (!input) return;

  input.addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase().trim();
    if (!q) {
      // Clear or reset view if empty
      return;
    }
    
    // Performance: debounced search would be better, but simple is fine for now
    console.log("Obsidian Global Search Pulse:", q);
  });
}


