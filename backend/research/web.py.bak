import asyncio
import json
import logging
import re
import time
import httpx
from pathlib import Path
from backend.config import PROPOSALS_DIR

logger = logging.getLogger("localmind.research.web")
WORKSPACE = PROPOSALS_DIR.parent
ACADEMIC_CACHE_FILE = WORKSPACE / "academic_cache.json"
ACADEMIC_CACHE_TTL = 6 * 3600

_WEB_RESEARCH_QUERIES = {
    "performance":   ["python performance optimization best practices"],
    "security":      ["python web application security checklist"],
    "code_quality":  ["python code quality tools linting refactoring"],
}

class AcademicResearcher:
    def __init__(self):
        self.cache = {}
        self._load_cache()

    def _load_cache(self):
        if ACADEMIC_CACHE_FILE.exists():
            try: self.cache = json.loads(ACADEMIC_CACHE_FILE.read_text(encoding='utf-8'))
            except Exception: pass

    def _save_cache(self):
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        ACADEMIC_CACHE_FILE.write_text(json.dumps(self.cache, indent=2), encoding="utf-8")

    async def search_arxiv(self, query: str, max_results: int = 5) -> list[dict]:
        try:
            import feedparser
            url = "https://export.arxiv.org/api/query"
            params = {"search_query": f"all:{query}", "max_results": max_results}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200: return []
                feed = feedparser.parse(resp.text)
                return [{"title": e.title, "abstract": e.summary[:400], "url": e.link, "source": "arxiv"} for e in feed.entries]
        except Exception: return []

    async def get_findings_for_prompt(self, category: str) -> str:
        papers = await self.search_arxiv(category)
        if not papers: return ""
        lines = [f"ACADEMIC RESEARCH FOR '{category.upper()}':"]
        for p in papers[:3]:
            lines.append(f"  📄 {p['title']}\n     Summary: {p['abstract']}")
        return "\n".join(lines) + "\n"

class WebResearcher:
    async def get_findings_for_prompt(self, category: str) -> str:
        try:
            from backend.tools.mcp_browser import web_research
            query = _WEB_RESEARCH_QUERIES.get(category, [f"python {category} best practices"])[0]
            result = await web_research(query)
            if result.get("success"):
                return f"WEB RESEARCH FOR '{category.upper()}':\n{result['result'][:2000]}\n"
        except Exception: pass
        return ""

class ExternalResearcher:
    async def get_findings_for_prompt(self, category: str) -> str:
        return f"EXTERNAL BEST PRACTICES FOR '{category.upper()}':\n(Wikipedia insights placeholder)\n"
