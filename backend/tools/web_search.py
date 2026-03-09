"""
Web Search Tool — DuckDuckGo Lite scraping, no API key needed.
"""

import re
from html import unescape

import httpx

from .base import BaseTool

DDG_URL = "https://lite.duckduckgo.com/lite/"


class WebSearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the internet using DuckDuckGo. Returns top results with titles, snippets, and URLs."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look up on the web",
                }
            },
            "required": ["query"],
        }

    async def execute(self, query: str = "", **kwargs) -> dict:
        if not query.strip():
            return {"success": False, "error": "Query cannot be empty"}

        try:
            async with httpx.AsyncClient(
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                follow_redirects=True,
            ) as client:
                resp = await client.post(DDG_URL, data={"q": query})
                resp.raise_for_status()
                html = resp.text

            results = self._parse_results(html)
            if not results:
                return {"success": True, "result": "No results found.", "results": []}

            formatted = "\n\n".join(
                f"**{r['title']}**\n{r['snippet']}\n{r['url']}" for r in results
            )
            return {"success": True, "result": formatted, "results": results}

        except Exception as exc:
            return {"success": False, "error": f"Search failed: {exc}"}

    def _parse_results(self, html: str) -> list[dict]:
        """Parse DuckDuckGo Lite HTML for search results."""
        results = []
        # Match result links and their surrounding text
        link_pattern = re.compile(
            r'<a[^>]+href="([^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            re.DOTALL,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        # Fallback: try simpler patterns if class-based ones fail
        if not links:
            link_pattern = re.compile(
                r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                re.DOTALL,
            )
            links = link_pattern.findall(html)

        if not snippets:
            snippet_pattern = re.compile(
                r'<td\s+colspan="2"[^>]*>\s*<span[^>]*>(.*?)</span>',
                re.DOTALL,
            )
            snippets = snippet_pattern.findall(html)

        for i, (url, title) in enumerate(links[:5]):
            clean_title = self._clean_html(title)
            clean_snippet = self._clean_html(snippets[i]) if i < len(snippets) else ""
            if clean_title and url.startswith("http"):
                results.append({
                    "title": clean_title,
                    "snippet": clean_snippet,
                    "url": url,
                })

        return results

    @staticmethod
    def _clean_html(text: str) -> str:
        """Strip HTML tags and decode entities."""
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)
        return text.strip()
