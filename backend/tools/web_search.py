"""
Web Search Tool — Multi-provider with automatic fallback.

Primary:  ddgs library (DuckDuckGo backend API — reliable, no scraping).
Fallback: HTTP scrapers for DuckDuckGo HTML, Google, Brave.
No API keys needed.
"""

import re
import logging
from html import unescape

import httpx

from .base import BaseTool

logger = logging.getLogger(__name__)

# ── User-Agent pool ──────────────────────────────────────────────
UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
UA_FIREFOX = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0"
)

TIMEOUT = 10  # seconds per provider


class WebSearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the internet. Returns top results with titles, snippets, and URLs."

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

        providers = [
            ("DuckDuckGo-API", self._search_ddgs_lib),
            ("DuckDuckGo-HTML", self._search_ddg),
            ("Google", self._search_google),
            ("Brave", self._search_brave),
        ]

        last_error = ""
        for name, fn in providers:
            try:
                logger.info(f"Trying {name} for: {query}")
                results = await fn(query)
                if results:
                    formatted = "\n\n".join(
                        f"**{r['title']}**\n{r['snippet']}\n{r['url']}"
                        for r in results
                    )
                    return {
                        "success": True,
                        "result": formatted,
                        "results": results,
                        "provider": name,
                    }
                logger.info(f"{name}: no results, trying next")
            except Exception as exc:
                last_error = f"{name}: {exc}"
                logger.warning(f"{name} failed: {exc}")
                continue

        logger.error("All search providers failed for query: %s", query)
        return {
            "success": False,
            "error": f"All search providers failed. Last error: {last_error}",
        }

    # ── Provider 0: ddgs library (primary) ──────────────────────────
    async def _search_ddgs_lib(self, query: str) -> list[dict]:
        """Use the ddgs library for reliable DuckDuckGo access."""
        import asyncio

        def _sync_search():
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=5))
            return [
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url": r.get("href", ""),
                }
                for r in raw
                if r.get("href")
            ]

        return await asyncio.get_event_loop().run_in_executor(None, _sync_search)

    # ── Provider 1: DuckDuckGo HTML (fallback) ──────────────────────
    async def _search_ddg(self, query: str) -> list[dict]:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={
                "User-Agent": UA_CHROME,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://html.duckduckgo.com/",
            },
            follow_redirects=True,
        ) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
            )
            if resp.status_code != 200:
                logger.warning("DuckDuckGo returned HTTP %d", resp.status_code)
                return []
            return self._parse_ddg(resp.text)

    def _parse_ddg(self, html: str) -> list[dict]:
        results = []
        link_pat = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pat = re.compile(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        links = link_pat.findall(html)
        snippets = snippet_pat.findall(html)

        if not links:
            link_pat = re.compile(
                r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                re.DOTALL,
            )
            links = link_pat.findall(html)

        for i, (url, title) in enumerate(links[:5]):
            real_url = url
            uddg_match = re.search(r'uddg=([^&]+)', url)
            if uddg_match:
                from urllib.parse import unquote
                real_url = unquote(uddg_match.group(1))

            clean_title = _clean(title)
            clean_snippet = _clean(snippets[i]) if i < len(snippets) else ""
            if clean_title and real_url.startswith("http"):
                results.append({
                    "title": clean_title,
                    "snippet": clean_snippet,
                    "url": real_url,
                })
        return results

    # ── Provider 2: Google scraper ───────────────────────────────
    async def _search_google(self, query: str) -> list[dict]:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={
                "User-Agent": UA_FIREFOX,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
            },
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                "https://www.google.com/search",
                params={"q": query, "hl": "en", "num": "5"},
            )
            if resp.status_code != 200:
                logger.warning("Google returned HTTP %d", resp.status_code)
                return []
            return self._parse_google(resp.text)

    def _parse_google(self, html: str) -> list[dict]:
        results = []
        block_pat = re.compile(r'<div class="g">(.*?)</div>\s*</div>\s*</div>', re.DOTALL)
        blocks = block_pat.findall(html)

        if not blocks:
            link_pat = re.compile(
                r'<a[^>]+href="/url\?q=(https?://[^&"]+)[^"]*"[^>]*>(.*?)</a>',
                re.DOTALL,
            )
            matches = link_pat.findall(html)
            for url, title in matches[:5]:
                clean_title = _clean(title)
                if clean_title and not url.startswith("https://accounts.google"):
                    results.append({
                        "title": clean_title,
                        "snippet": "",
                        "url": url,
                    })
            return results

        for block in blocks[:5]:
            url_match = re.search(r'href="(https?://[^"]+)"', block)
            title_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
            snippet_match = re.search(
                r'<span[^>]*class="[^"]*st[^"]*"[^>]*>(.*?)</span>',
                block, re.DOTALL,
            )
            if not snippet_match:
                snippet_match = re.search(
                    r'<div[^>]*data-sncf[^>]*>(.*?)</div>',
                    block, re.DOTALL,
                )

            if url_match and title_match:
                url = url_match.group(1)
                if not url.startswith("https://accounts.google"):
                    results.append({
                        "title": _clean(title_match.group(1)),
                        "snippet": _clean(snippet_match.group(1)) if snippet_match else "",
                        "url": url,
                    })
        return results

    # ── Provider 3: Brave Search scraper ─────────────────────────
    async def _search_brave(self, query: str) -> list[dict]:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={
                "User-Agent": UA_CHROME,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                "https://search.brave.com/search",
                params={"q": query},
            )
            if resp.status_code != 200:
                logger.warning("Brave returned HTTP %d", resp.status_code)
                return []
            return self._parse_brave(resp.text)

    def _parse_brave(self, html: str) -> list[dict]:
        results = []
        link_pat = re.compile(
            r'<a[^>]+class="[^"]*result-header[^"]*"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pat = re.compile(
            r'<p[^>]+class="[^"]*snippet-description[^"]*"[^>]*>(.*?)</p>',
            re.DOTALL,
        )
        links = link_pat.findall(html)
        snippets = snippet_pat.findall(html)

        for i, (url, title) in enumerate(links[:5]):
            results.append({
                "title": _clean(title),
                "snippet": _clean(snippets[i]) if i < len(snippets) else "",
                "url": url,
            })
        return results


def _clean(text: str) -> str:
    """Strip HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return text.strip()
