"""
learn_from_web — Composite tool: fetch a URL and save a memory linking back to it.

This is the bridge between LocalMind's existing `browse_web` and `save_memory`
tools. The agent uses it like: read an arXiv abstract → distill the key insight
→ call learn_from_web(url=..., note="Paper X showed that...") → the memory now
has the insight + a clickable source the user (or future agent) can verify.

Two call shapes:
  1. learn_from_web(url, note)              — agent has already distilled.
  2. learn_from_web(url)                    — auto-summary from page title+lead.

The memory body always ends with " (source: URL)" so the link is preserved
no matter which retrieval path surfaces it later.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import BaseTool
from .mcp_browser import MCPBrowserTool

logger = logging.getLogger("localmind.tools.learn_from_web")

VALID_CATEGORIES = ("fact", "preference", "instruction", "context")
DEFAULT_CATEGORY = "fact"
MAX_NOTE_CHARS = 800
MAX_AUTO_LEAD_CHARS = 320


def _truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


class LearnFromWebTool(BaseTool):
    """Fetch a URL and save a memory anchored to its source URL."""

    def __init__(self):
        # Reuse the read-only browser tool — same safety, rate limit, extract.
        self._browser = MCPBrowserTool()

    @property
    def name(self) -> str:
        return "learn_from_web"

    @property
    def description(self) -> str:
        return (
            "Fetch a public web page and persist what's worth remembering, "
            "with a link back to the source. Use this whenever you find a "
            "fact, finding, or technique on the web that's worth keeping "
            "across conversations (papers, docs, blog posts, references). "
            "Provide a concise `note` summarizing the takeaway; if omitted, "
            "the page title and lead are saved as a placeholder. The saved "
            "memory always includes the URL so it can be re-verified."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Public URL to fetch (https://...). No localhost.",
                },
                "note": {
                    "type": "string",
                    "description": (
                        "Optional 1–3 sentence takeaway that should be remembered. "
                        "Leave empty to save the page's title + lead automatically."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": "Memory category. Default: 'fact'.",
                    "enum": list(VALID_CATEGORIES),
                },
            },
            "required": ["url"],
        }

    async def execute(
        self,
        url: str = "",
        note: str = "",
        category: str = DEFAULT_CATEGORY,
        **kwargs,
    ) -> dict:
        if not url or not url.strip():
            return {"success": False, "error": "url is required"}

        cat = category if category in VALID_CATEGORIES else DEFAULT_CATEGORY

        # 1) Fetch via the existing browser tool — same safety + extract.
        fetched = await self._browser._browse_url(url)
        if not fetched.get("success"):
            return {
                "success": False,
                "error": f"Could not fetch {url}: {fetched.get('error', 'unknown')}",
                "url": url,
            }

        title = fetched.get("title") or "Untitled"
        body  = fetched.get("result") or ""
        # `body` is already "**title**\n\ntext" — strip the title block for the lead.
        lead = body.split("\n\n", 1)[1] if "\n\n" in body else body
        lead = _truncate(lead, MAX_AUTO_LEAD_CHARS)

        # 2) Compose the memory content. Agent-supplied note wins; otherwise
        #    fall back to "Read X: <lead>".
        if note and note.strip():
            memory_body = _truncate(note.strip(), MAX_NOTE_CHARS)
        else:
            memory_body = f"Read “{title}”: {lead}" if lead else f"Read “{title}”."

        memory_content = f"{memory_body} (source: {url})"

        # 3) Persist via the existing memory retriever — same code path as
        #    save_memory, so this learning shows up in the Knowledge graph
        #    and recall_memories results.
        memory_id: Optional[str] = None
        try:
            from backend.tools.memory import _get_retriever, _learning_enabled
            if not _learning_enabled:
                return {
                    "success": True,
                    "saved": False,
                    "reason": "Learning is currently paused — memory not saved.",
                    "url": url,
                    "title": title,
                    "would_save": memory_content,
                }
            retriever = _get_retriever()
            if not retriever:
                return {
                    "success": False,
                    "error": "Memory store unavailable",
                    "url": url,
                    "title": title,
                }
            memory_id = retriever.save_from_conversation(
                content=memory_content,
                category="semantic",
                subcategory=cat,
                source="learn_from_web",
            )
        except Exception as exc:
            logger.warning("learn_from_web save failed: %s", exc)
            return {
                "success": False,
                "error": f"Save failed: {exc}",
                "url": url,
                "title": title,
            }

        return {
            "success": True,
            "saved": True,
            "result": (
                f"Learned from “{title}” and remembered it ({cat}). "
                f"Source: {url}"
            ),
            "memory_id": memory_id,
            "url": url,
            "title": title,
            "category": cat,
            "chars_fetched": int(fetched.get("chars") or 0),
            "note_used": bool(note and note.strip()),
        }
