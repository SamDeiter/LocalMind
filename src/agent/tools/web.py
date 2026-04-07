"""
Web fetch and search tools.

Kept minimal — just HTTP GET and text extraction.
No browser automation or JavaScript rendering (that's in the existing
LocalMind browser tool). This is for the standalone agent.
"""

from __future__ import annotations

import re
from typing import Any

from .base import Tool


class WebFetchTool(Tool):
    """Fetch a URL and return its text content."""

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "Fetch a URL and return the text content. Strips HTML tags for readability."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                },
                "raw": {
                    "type": "boolean",
                    "description": "If true, return raw HTML instead of extracted text. Default false.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default: 8000)",
                },
            },
            "required": ["url"],
        }

    async def _execute(self, **kwargs) -> dict[str, Any]:
        url = kwargs["url"]
        raw = kwargs.get("raw", False)
        max_chars = kwargs.get("max_chars", 8000)

        try:
            import httpx
        except ImportError:
            return {"success": False, "error": "httpx is required: pip install httpx"}

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=15.0,
                headers={"User-Agent": "LocalMind-Agent/1.0"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {url}"}
        except Exception as e:
            return {"success": False, "error": f"Fetch failed: {e}"}

        content = resp.text
        if not raw:
            content = self._extract_text(content)

        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... (truncated, {len(resp.text)} total)"

        return {
            "success": True,
            "result": content,
            "url": str(resp.url),
            "status_code": resp.status_code,
        }

    @staticmethod
    def _extract_text(html: str) -> str:
        """Simple HTML-to-text extraction without external dependencies."""
        # Remove script and style blocks
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # Convert common block elements to newlines
        html = re.sub(r"<(br|hr|p|div|li|tr|h[1-6])[^>]*>", "\n", html, flags=re.IGNORECASE)
        # Strip all remaining tags
        text = re.sub(r"<[^>]+>", "", html)
        # Decode common entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
        # Collapse whitespace
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()


class WebSearchTool(Tool):
    """Search the web using DuckDuckGo (no API key required).

    Falls back to a simple scrape of DDG HTML results.
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web and return the top results. No API key needed."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 5)",
                },
            },
            "required": ["query"],
        }

    async def _execute(self, **kwargs) -> dict[str, Any]:
        query = kwargs["query"]
        max_results = kwargs.get("max_results", 5)

        # Try duckduckgo-search library first
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
                return {
                    "success": True,
                    "result": [
                        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                        for r in results
                    ],
                }
        except ImportError:
            pass
        except Exception as e:
            return {"success": False, "error": f"Search failed: {e}"}

        # Fallback: scrape DDG HTML lite
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "LocalMind-Agent/1.0"},
                )
                # Parse results from HTML
                results = []
                for match in re.finditer(
                    r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                    r'class="result__snippet"[^>]*>(.*?)</div>',
                    resp.text,
                    re.DOTALL,
                ):
                    url, title, snippet = match.groups()
                    title = re.sub(r"<[^>]+>", "", title).strip()
                    snippet = re.sub(r"<[^>]+>", "", snippet).strip()
                    results.append({"title": title, "url": url, "snippet": snippet})
                    if len(results) >= max_results:
                        break

                return {"success": True, "result": results}
        except Exception as e:
            return {"success": False, "error": f"Search fallback failed: {e}"}
