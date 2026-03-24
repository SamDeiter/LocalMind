/**
 * research_ui.js — ArXiv Paper Search UI + Apply-to-Codebase
 * Provides the frontend panel for searching arXiv papers from the dashboard
 * and applying paper techniques as code improvement proposals.
 */

import { API } from "./state.js";
import { escapeHtml, showToast } from "./utils.js";

// ── ArXiv Search ────────────────────────────────────────────────────

let _searching = false;

export async function searchArxiv(query) {
  if (!query || query.trim().length < 2) {
    showToast("Please enter a search query", "error");
    return;
  }
  if (_searching) return;
  _searching = true;

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
      `${API}/api/research/arxiv?q=${encodeURIComponent(query.trim())}&max=8`,
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

    resultsEl.innerHTML = papers
      .map((p, idx) => {
        const title = escapeHtml(p.title || "Untitled");
        const authors = escapeHtml(
          (p.authors || "Unknown authors").substring(0, 80),
        );
        const abstract = escapeHtml(
          (p.abstract || "No abstract available").substring(0, 200),
        );
        const url = p.url || "#";
        const published = p.published
          ? new Date(p.published).toLocaleDateString()
          : "";

        return `
        <div class="arxiv-card" data-idx="${idx}">
          <a class="arxiv-card-link-area" href="${url}" target="_blank" rel="noopener noreferrer" title="Open on arXiv">
            <div class="arxiv-card-title">${title}</div>
            <div class="arxiv-card-meta">
              <span class="arxiv-card-authors">${authors}</span>
              ${published ? `<span class="arxiv-card-date">${published}</span>` : ""}
            </div>
            <div class="arxiv-card-abstract">${abstract}…</div>
            <span class="arxiv-card-link">📄 View on arXiv →</span>
          </a>
          <button class="arxiv-apply-btn" data-idx="${idx}" title="Apply this paper's technique to the codebase">
            🧠 Apply to Codebase
          </button>
        </div>`;
      })
      .join("");

    // Store paper data for apply buttons
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
      // Don't re-enable — the paper was already applied
      return;
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

// ── Init ────────────────────────────────────────────────────────────

export function initResearchPanel() {
  const btn = document.getElementById("arxivSearchBtn");
  const input = document.getElementById("arxivSearchInput");

  if (btn && input) {
    btn.addEventListener("click", () => searchArxiv(input.value));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") searchArxiv(input.value);
    });
  }
}
