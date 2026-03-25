"""
MCP Web Browser Tool — Read-only web browsing for the autonomy engine.

Provides two capabilities:
  1. browse_url(url) — Fetch any URL, convert HTML to clean text
  2. web_research(query) — Search + auto-fetch top results

Rate-limited (2s gap), domain-sandboxed (no localhost/internal IPs).
Auto-discovered by ToolRegistry via BaseTool subclassing.
"""

import re
import time
import logging
from html import unescape
from urllib.parse import urlparse

import httpx

from .base import BaseTool

logger = logging.getLogger("localmind.tools.mcp_browser")

# ── Safety ───────────────────────────────────────────────────────
BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
}
BLOCKED_PREFIXES = ("10.", "192.168.", "172.16.", "172.17.", "172.18.",
                     "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                     "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                     "172.29.", "172.30.", "172.31.")
MAX_CONTENT_CHARS = 8000
FETCH_TIMEOUT = 15
MIN_FETCH_GAP = 2.0  # seconds between requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _is_blocked_url(url: str) -> bool:
    """Check if a URL points to a blocked/internal host."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host in BLOCKED_HOSTS:
            return True
        if any(host.startswith(p) for p in BLOCKED_PREFIXES):
            return True
        if not parsed.scheme or parsed.scheme not in ("http", "https"):
            return True
    except Exception:
        return True
    return False


def _html_to_text(html: str) -> str:
    """Strip HTML tags, scripts, styles → clean text."""
    # Remove script and style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<nav[^>]*>.*?</nav>", "", text, flags=re.S | re.I)
    text = re.sub(r"<footer[^>]*>.*?</footer>", "", text, flags=re.S | re.I)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities
    text = unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_title(html: str) -> str:
    """Extract <title> from HTML."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return unescape(match.group(1).strip()) if match else "Untitled"


class MCPBrowserTool(BaseTool):
    """Read-only web browser: fetch URLs and extract text content."""

    def __init__(self):
        self._last_fetch_time: float = 0.0

    @property
    def name(self) -> str:
        return "browse_web"

    @property
    def description(self) -> str:
        return (
            "Browse a web page by URL and extract its text content. "
            "Use for researching best practices, reading documentation, "
            "or fetching technical articles. Read-only, no forms or JS."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch (https://...)",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Optional: if provided instead of url, performs a web search "
                        "and auto-fetches the top 3 results"
                    ),
                },
            },
        }

    async def execute(self, url: str = "", query: str = "", **kwargs) -> dict:
        """Fetch a URL or search+fetch top results."""
        if query and not url:
            return await self._web_research(query)
        if url:
            return await self._browse_url(url)
        return {"success": False, "error": "Provide either 'url' or 'query'"}

    async def _browse_url(self, url: str) -> dict:
        """Fetch a single URL and return extracted text."""
        if _is_blocked_url(url):
            return {"success": False, "error": f"Blocked URL: {url}"}

        # Rate limiting
        elapsed = time.time() - self._last_fetch_time
        if elapsed < MIN_FETCH_GAP:
            await _async_sleep(MIN_FETCH_GAP - elapsed)

        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": UA},
            ) as client:
                resp = await client.get(url)
                self._last_fetch_time = time.time()

                if resp.status_code != 200:
                    return {
                        "success": False,
                        "error": f"HTTP {resp.status_code} for {url}",
                    }

                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return {
                        "success": False,
                        "error": f"Unsupported content type: {content_type}",
                    }

                html = resp.text
                title = _extract_title(html)
                text = _html_to_text(html)

                # Truncate to max chars
                if len(text) > MAX_CONTENT_CHARS:
                    text = text[:MAX_CONTENT_CHARS] + "\n\n[... truncated]"

                return {
                    "success": True,
                    "result": f"**{title}**\n\n{text}",
                    "title": title,
                    "url": url,
                    "chars": len(text),
                }

        except httpx.TimeoutException:
            return {"success": False, "error": f"Timeout fetching {url}"}
        except Exception as exc:
            logger.warning(f"Browse failed for {url}: {exc}")
            return {"success": False, "error": str(exc)}

    async def _web_research(self, query: str) -> dict:
        """Search DuckDuckGo, then auto-fetch top 3 result pages."""
        # First, get search results using DDG HTML
        search_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": UA},
            ) as client:
                resp = await client.get(search_url)
                self._last_fetch_time = time.time()

                if resp.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Search failed: HTTP {resp.status_code}",
                    }

                # Extract result URLs from DDG HTML
                urls = re.findall(
                    r'class="result__a"[^>]*href="([^"]+)"',
                    resp.text,
                )
                # DDG wraps URLs in redirects — extract the actual URL
                clean_urls = []
                for u in urls[:5]:
                    if "uddg=" in u:
                        match = re.search(r"uddg=([^&]+)", u)
                        if match:
                            from urllib.parse import unquote
                            clean_urls.append(unquote(match.group(1)))
                    elif u.startswith("http"):
                        clean_urls.append(u)

                if not clean_urls:
                    return {
                        "success": False,
                        "error": f"No search results for: {query}",
                    }

        except Exception as exc:
            return {"success": False, "error": f"Search failed: {exc}"}

        # Fetch top 3 results
        results = []
        for url in clean_urls[:3]:
            if _is_blocked_url(url):
                continue
            page = await self._browse_url(url)
            if page.get("success"):
                # Limit each page to 2500 chars for research summaries
                text = page.get("result", "")
                if len(text) > 2500:
                    text = text[:2500] + "\n[... truncated]"
                results.append({
                    "url": url,
                    "title": page.get("title", ""),
                    "content": text,
                })

        if not results:
            return {
                "success": False,
                "error": f"Could not fetch any results for: {query}",
            }

        # Format combined output
        combined = f"## Web Research: {query}\n\n"
        for i, r in enumerate(results, 1):
            combined += f"### Result {i}: {r['title']}\n"
            combined += f"URL: {r['url']}\n\n"
            combined += f"{r['content']}\n\n---\n\n"

        return {
            "success": True,
            "result": combined,
            "results_fetched": len(results),
            "query": query,
        }


async def _async_sleep(seconds: float):
    """Async sleep helper for rate limiting."""
    import asyncio
    await asyncio.sleep(seconds)
"""Standalone helper for use by the research engine (not via tool registry)."""


async def browse_url(url: str) -> dict:
    """Convenience function: fetch a URL and return extracted text."""
    tool = MCPBrowserTool()
    return await tool._browse_url(url)


async def web_research(query: str) -> dict:
    """Convenience function: search + fetch top results."""
    tool = MCPBrowserTool()
    return await tool._web_research(query)
