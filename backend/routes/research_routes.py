"""
backend/routes/research_routes.py — ArXiv Research Search + Apply API
======================================================================
Exposes the existing AcademicResearcher.search_arxiv() as a user-facing
API endpoint so the frontend dashboard can search for academic papers.

Also provides an "apply paper" endpoint that takes a paper's details,
asks the LLM to generate a concrete code-improvement proposal, and
saves it into the ProposalManager pipeline.
"""

import json
import logging
import random
import subprocess
from pathlib import Path

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger("localmind.routes.research")

router = APIRouter(prefix="/api", tags=["research"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OLLAMA_URL = "http://localhost:11434"

# Lazy-initialized singletons (created on first request)
_researcher = None
_proposals = None


def _get_researcher():
    """Lazy-init the AcademicResearcher to avoid import-time side effects."""
    global _researcher
    if _researcher is None:
        from backend.research_engine import AcademicResearcher
        _researcher = AcademicResearcher()
    return _researcher


def _get_proposals():
    """Lazy-init ProposalManager."""
    global _proposals
    if _proposals is None:
        from backend.proposals import ProposalManager
        _proposals = ProposalManager()
    return _proposals


# ── Search Endpoint ──────────────────────────────────────────────────


@router.get("/research/arxiv")
async def search_arxiv(
    q: str = Query(..., min_length=2, description="Search query"),
    max: int = Query(5, ge=1, le=20, description="Max results"),
):
    """Search arXiv for academic papers matching the query.

    Returns a list of papers with title, authors, abstract, URL, and
    publication date. Uses the arXiv Atom API under the hood.
    """
    researcher = _get_researcher()
    try:
        papers = await researcher.search_arxiv(q, max_results=max)
        return {"papers": papers, "count": len(papers), "query": q}
    except Exception as e:
        logger.warning(f"arXiv search failed for '{q}': {e}")
        return {"papers": [], "count": 0, "query": q, "error": str(e)}


# ── Apply-Paper Endpoint ────────────────────────────────────────────


class ApplyPaperRequest(BaseModel):
    title: str
    abstract: str
    url: str = ""


def _sample_codebase(count: int = 3) -> str:
    """Sample a few project files for the LLM to reference."""
    code_exts = {".py", ".js", ".html", ".css"}
    candidates = []
    for ext_glob in ("**/*.py", "**/*.js", "**/*.html", "**/*.css"):
        for f in PROJECT_ROOT.glob(ext_glob):
            rel = f.relative_to(PROJECT_ROOT)
            skip = any(
                part.startswith(".") or part in ("node_modules", "__pycache__", "memory_db", "browser_recordings")
                for part in rel.parts
            )
            if not skip and f.suffix in code_exts:
                candidates.append(rel)

    if not candidates:
        return ""

    sampled = random.sample(candidates, min(count, len(candidates)))
    snippets = []
    for rel_path in sampled:
        try:
            full = PROJECT_ROOT / rel_path
            content = full.read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")
            if len(lines) > 150:
                continue
            numbered = [f"{i:>4}| {line}" for i, line in enumerate(lines[:80], 1)]
            preview = "\n".join(numbered)
            if len(lines) > 80:
                preview += f"\n     ... ({len(lines) - 80} more lines)"
            snippets.append(f"### {rel_path} ({len(lines)} lines)\n```\n{preview}\n```")
        except (OSError, UnicodeDecodeError):
            continue

    if not snippets:
        return ""
    return "\nCODEBASE FILES (propose changes to THESE files):\n" + "\n\n".join(snippets) + "\n"


def _get_file_list() -> str:
    """Get a flat listing of project files for the LLM."""
    files = []
    for ext in ("*.py", "*.js", "*.html", "*.css", "*.json", "*.md"):
        for f in PROJECT_ROOT.rglob(ext):
            rel = f.relative_to(PROJECT_ROOT)
            skip = any(
                part.startswith(".") or part in ("node_modules", "__pycache__", "memory_db", "browser_recordings")
                for part in rel.parts
            )
            if not skip:
                files.append(str(rel).replace("\\", "/"))
    return "\n".join(f"  - {f}" for f in sorted(files)[:50])


@router.post("/research/apply-paper")
async def apply_paper(req: ApplyPaperRequest):
    """Generate a code-improvement proposal from an arXiv paper.

    Takes the paper's title and abstract, samples codebase files,
    and asks the LLM to generate a concrete, actionable proposal.
    The proposal is saved into the ProposalManager pipeline.
    """
    from backend.model_router import get_autonomy_models

    models = get_autonomy_models()
    model = models.get("reflection", "qwen2.5-coder:7b")

    file_list = _get_file_list()
    code_samples = _sample_codebase(count=3)

    prompt = (
        "You are LocalMind, an AI assistant. A user found this academic paper and wants you to\n"
        "propose a CONCRETE code improvement inspired by the paper's technique.\n\n"
        f"PAPER TITLE: {req.title}\n"
        f"PAPER ABSTRACT: {req.abstract[:600]}\n"
        f"PAPER URL: {req.url}\n\n"
        f"PROJECT FILES:\n{file_list}\n\n"
        f"{code_samples}\n"
        "YOUR TASK:\n"
        "1. Read the paper's technique.\n"
        "2. Find a SPECIFIC place in THIS codebase where you could apply the idea.\n"
        "3. Propose a concrete, implementable change.\n\n"
        "RULES:\n"
        "- ONLY reference files from the list above.\n"
        "- Be SPECIFIC — name exact functions, classes, or logic to change.\n"
        f"- Reference the paper: '{req.title}'\n"
        "- The title must describe the SPECIFIC change, not just the paper topic.\n\n"
        "Output a JSON object with keys: title, category "
        "(performance/feature/bugfix/ux/security/code_quality), "
        "description, files_affected (list of real filenames from above), "
        "effort (small/medium/large), priority (low/medium/high/critical).\n"
        "Only output the JSON, nothing else."
    )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 500, "num_ctx": 8192},
                },
            )

            if resp.status_code != 200:
                return {"error": f"Ollama returned {resp.status_code}", "proposal": None}

            data = resp.json()
            response_text = data.get("response", "")

            # Parse JSON from response
            json_text = response_text.strip()
            if "```" in json_text:
                json_text = json_text.split("```")[1]
                if json_text.startswith("json"):
                    json_text = json_text[4:]
                json_text = json_text.strip()

            proposal = json.loads(json_text)

            # Tag it as research-sourced
            proposal["source"] = "arxiv"
            proposal["source_paper"] = req.title
            proposal["source_url"] = req.url

            # Save via ProposalManager
            pm = _get_proposals()
            saved = pm.save(
                proposal,
                mode="supervised",
                auto_approve_risks=set(),
                log_fn=lambda *a, **k: None,
                emit_activity=lambda *a, **k: None,
            )

            if saved:
                logger.info(f"📄 Paper proposal saved: {saved.get('title', '?')}")
                return {"proposal": saved, "error": None}
            else:
                return {"error": "Proposal was rejected (duplicate or invalid)", "proposal": None}

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM response as JSON: {e}")
        return {"error": "AI response was not valid JSON — try again", "proposal": None}
    except Exception as e:
        logger.error(f"apply-paper failed: {e}")
        return {"error": str(e), "proposal": None}
