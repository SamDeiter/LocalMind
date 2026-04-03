"""
ResearchAgent — I/O-bound concurrent web/academic researcher.
Spawns parallel httpx requests for web search and academic lookups.
"""

import asyncio
import logging
import httpx

from backend.swarm.task_queue import SwarmTask, SwarmResult, TaskType
from backend.swarm.agents import BaseAgent

logger = logging.getLogger("localmind.swarm.research")


class ResearchAgent(BaseAgent):
    """Concurrent research agent for web and academic searches.
    
    Payload keys:
        query: str          — Search query
        source: str         — "web" | "academic" | "package"
        max_results: int    — Limit results (default 5)
    """

    agent_type = "researcher"

    async def execute(self, task: SwarmTask) -> SwarmResult:
        query = task.payload.get("query", "")
        source = task.payload.get("source", "web")
        max_results = task.payload.get("max_results", 5)

        if not query:
            return SwarmResult(
                task_id=task.id,
                task_type=task.type,
                success=False,
                error="No query provided",
            )

        try:
            if source == "web":
                results = await self._search_web(query, max_results)
            elif source == "academic":
                results = await self._search_academic(query, max_results)
            elif source == "package":
                results = await self._search_packages(query)
            else:
                results = []

            return SwarmResult(
                task_id=task.id,
                task_type=task.type,
                success=True,
                data={"query": query, "source": source, "results": results},
            )

        except Exception as exc:
            return SwarmResult(
                task_id=task.id,
                task_type=task.type,
                success=False,
                error=str(exc),
            )

    async def _search_web(self, query: str, max_results: int) -> list[dict]:
        """Search DuckDuckGo for general web results."""
        results = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "LocalMind/0.8 Research Agent"},
                )
                if resp.status_code == 200:
                    # Extract result snippets from the HTML
                    text = resp.text
                    # Simple extraction — find result links and snippets
                    import re
                    links = re.findall(r'class="result__a" href="([^"]+)"', text)
                    snippets = re.findall(r'class="result__snippet">(.*?)</a>', text, re.DOTALL)

                    for i, (link, snippet) in enumerate(zip(links, snippets)):
                        if i >= max_results:
                            break
                        # Clean HTML from snippet
                        clean = re.sub(r'<[^>]+>', '', snippet).strip()
                        results.append({
                            "url": link,
                            "snippet": clean[:300],
                        })
        except Exception as exc:
            logger.debug(f"Web search failed: {exc}")

        return results

    async def _search_academic(self, query: str, max_results: int) -> list[dict]:
        """Search Semantic Scholar for academic papers."""
        results = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params={"query": query, "limit": max_results, "fields": "title,abstract,year,citationCount"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for paper in data.get("data", [])[:max_results]:
                        results.append({
                            "title": paper.get("title", ""),
                            "abstract": (paper.get("abstract") or "")[:300],
                            "year": paper.get("year"),
                            "citations": paper.get("citationCount", 0),
                        })
        except Exception as exc:
            logger.debug(f"Academic search failed: {exc}")

        return results

    async def _search_packages(self, query: str) -> list[dict]:
        """Search PyPI for relevant packages."""
        results = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://pypi.org/pypi/{query}/json",
                )
                if resp.status_code == 200:
                    data = resp.json()
                    info = data.get("info", {})
                    results.append({
                        "name": info.get("name"),
                        "version": info.get("version"),
                        "summary": info.get("summary", "")[:200],
                        "license": info.get("license", "Unknown"),
                    })
        except Exception:
            pass
        return results
