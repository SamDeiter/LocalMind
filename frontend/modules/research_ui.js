/**
 * research_ui.js — ArXiv Paper Search UI
 * Provides the frontend panel for searching arXiv papers from the dashboard.
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
      .map((p) => {
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
        <a class="arxiv-card" href="${url}" target="_blank" rel="noopener noreferrer" title="Open on arXiv">
          <div class="arxiv-card-title">${title}</div>
          <div class="arxiv-card-meta">
            <span class="arxiv-card-authors">${authors}</span>
            ${published ? `<span class="arxiv-card-date">${published}</span>` : ""}
          </div>
          <div class="arxiv-card-abstract">${abstract}…</div>
          <span class="arxiv-card-link">📄 View on arXiv →</span>
        </a>`;
      })
      .join("");
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
