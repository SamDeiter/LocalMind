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
      resultsEl.innerHTML = `<div class="text-center text-[12px] text-[#787878] py-6">⚠️ ${escapeHtml(data.error)}</div>`;
      return;
    }

    const papers = data.papers || [];
    if (papers.length === 0) {
      resultsEl.innerHTML =
        '<div class="text-center text-[12px] text-[#787878] py-6">No papers found. Try a different query.</div>';
      return;
    }

    // Render paper cards
    let html = papers
      .map((p, idx) => {
        const title = escapeHtml(p.title || "Untitled");
        const authors = escapeHtml(
          (p.authors || "Unknown authors").substring(0, 100),
        );
        const pitch = p.pitch ? escapeHtml(p.pitch) : null;
        const abstract = escapeHtml(
          (p.abstract || "No abstract available").substring(0, 250),
        );
        const url = p.url || "#";
        const published = p.published
          ? new Date(p.published).toLocaleDateString()
          : "";

        // Unified paper card (Premium Style — matches reference image)
        return `
        <div class="insight-card p-4 rounded-xl border border-outline-variant/10 bg-[#2f2f2f] space-y-3 hover:border-primary/40 transition-all group/card" data-idx="${idx}">
          <div class="flex-col space-y-1">
             <div class="inline">
                <h4 class="text-[14px] font-bold text-[#fafafa] group-hover/card:text-primary transition-colors leading-snug inline">${title}</h4>
                <div class="inline-flex items-center ml-2 px-2 py-0.5 rounded border border-[#4d4872] bg-[#333140]/50 text-[9px] font-bold text-[#baafff] uppercase tracking-widest align-middle translate-y-[-1px]">
                   Source
                </div>
             </div>
             <div class="text-[10px] font-bold uppercase tracking-[0.1em] text-[#787878] pt-1">
                ${authors}
             </div>
          </div>
          <p class="text-[11.5px] text-[#999999] leading-relaxed">${abstract}</p>
          <div class="flex gap-2 pt-2">
            <button class="arxiv-apply-btn flex-[7] py-2 bg-[#383344] border border-[#4d4872] rounded shadow-sm text-[10px] font-bold uppercase tracking-[0.15em] text-[#baafff] hover:bg-[#464057] transition-all" data-idx="${idx}">
              Synthesize
            </button>
            <button class="arxiv-context-btn flex-[3] py-2 bg-[#363636] border border-[#444444] rounded shadow-sm text-[10px] font-bold uppercase tracking-[0.15em] text-[#999999] hover:text-white hover:bg-[#4a4a4a] transition-all" data-idx="${idx}">
              Save
            </button>
          </div>
        </div>`;
      })
      .join("");

    // Pagination controls
    html += `
      <div class="flex items-center justify-center gap-3 pt-4 pb-2">
        <button class="px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest text-[#baafff] bg-[#383344] border border-[#4d4872] rounded hover:bg-[#464057] disabled:opacity-30 disabled:cursor-not-allowed transition-all" id="arxivPrevPage" ${page === 0 ? "disabled" : ""}>◀ Prev</button>
        <span class="text-[11px] font-mono text-[#787878]">Page ${page + 1}</span>
        <button class="px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest text-[#baafff] bg-[#383344] border border-[#4d4872] rounded hover:bg-[#464057] disabled:opacity-30 disabled:cursor-not-allowed transition-all" id="arxivNextPage" ${papers.length < 8 ? "disabled" : ""}>Next ▶</button>
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
        '<div class="text-center text-[12px] text-[#787878] py-6">❌ Search failed — is the server running?</div>';
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

    if (!resp.ok) {
      const errText = await resp.text().catch(() => "Unknown server error");
      showToast(`⚠️ Server error ${resp.status}: ${errText.substring(0, 120)}`, "error");
      btnEl.textContent = "Synthesize";
      return;
    }

    const data = await resp.json();

    if (data.error) {
      showToast(`⚠️ ${data.error}`, "error");
      btnEl.textContent = "Synthesize";
    } else if (data.proposal) {
      const proposalTitle = data.proposal.title || "Untitled";
      showToast(`✅ Proposal created: "${proposalTitle}"`, "success");
      btnEl.textContent = "✅ Proposal Created";
      btnEl.classList.add("applied");
      return; // Don't re-enable
    } else {
      showToast("No proposal was generated — try a different paper", "error");
      btnEl.textContent = "Synthesize";
    }
  } catch (err) {
    console.error("Apply paper error:", err);
    const reason = err.message || "Network error";
    showToast(`❌ Synthesis failed: ${reason}`, "error");
    btnEl.textContent = "Synthesize";
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
    const q = e.target.value.trim();
    if (!q || q.length < 3) return;
    
    // Wire to arXiv search as requested ("paper feature" search)
    console.log("Obsidian Global Search Pulse:", q);
    searchArxiv(q);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        const q = input.value.trim();
        if (q) searchArxiv(q);
    }
  });
}


