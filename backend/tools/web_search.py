"""
Web Search Tool — Multi-provider with automatic fallback.
Providers: DuckDuckGo HTML → Google scraper → Brave scraper.
No API keys needed — all free scraping.
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
            ("DuckDuckGo", self._search_ddg),
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

        return {
            "success": False,
            "error": f"All search providers failed. Last error: {last_error}",
        }

    # ── Provider 1: DuckDuckGo HTML ──────────────────────────────
    async def _search_ddg(self, query: str) -> list[dict]:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={"User-Agent": UA_CHROME},
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            resp.raise_for_status()
            return self._parse_ddg(resp.text)

    def _parse_ddg(self, html: str) -> list[dict]:
        results = []
        # DuckDuckGo HTML version uses class="result__a" for links
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

        # Fallback patterns
        if not links:
            link_pat = re.compile(
                r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                re.DOTALL,
            )
            links = link_pat.findall(html)

        for i, (url, title) in enumerate(links[:5]):
            # DDG wraps URLs in a redirect — extract real URL
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
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                "https://www.google.com/search",
                params={"q": query, "hl": "en", "num": "5"},
            )
            resp.raise_for_status()
            return self._parse_google(resp.text)

    def _parse_google(self, html: str) -> list[dict]:
        results = []
        # Google wraps results in <div class="g"> blocks
        block_pat = re.compile(r'<div class="g">(.*?)</div>\s*</div>\s*</div>', re.DOTALL)
        blocks = block_pat.findall(html)

        if not blocks:
            # Fallback: find all href links that look like results
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
            # Extract URL
            url_match = re.search(r'href="(https?://[^"]+)"', block)
            # Extract title (usually in <h3>)
            title_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
            # Extract snippet
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
                "Accept": "text/html",
            },
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                "https://search.brave.com/search",
                params={"q": query},
            )
            resp.raise_for_status()
            return self._parse_brave(resp.text)

    def _parse_brave(self, html: str) -> list[dict]:
        results = []
        # Brave uses <a class="result-header" href="...">
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
