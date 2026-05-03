"""
backend/routes/research_routes.py — ArXiv Research Search + Apply API
======================================================================
Exposes the existing AcademicResearcher.search_arxiv() as a user-facing
API endpoint. Also provides an "apply paper" endpoint that generates
code-improvement proposals from papers and saves them to the pipeline.

The generate_paper_proposal() function is shared by both the endpoint
and the autonomy engine's auto-research loop.
"""

from backend.config import OLLAMA_BASE_URL
import json
import logging
import random
from pathlib import Path

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger("localmind.routes.research")

router = APIRouter(prefix="/api", tags=["research"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Lazy-initialized singletons (created on first request)
_researcher = None
_proposals = None


def _get_researcher():
    """Lazy-init the AcademicResearcher to avoid import-time side effects."""
    global _researcher
    if _researcher is None:
        from backend.research.web import AcademicResearcher
        _researcher = AcademicResearcher()
    return _researcher


def _get_proposals():
    """Lazy-init ProposalManager (returns None if proposals module removed)."""
    global _proposals
    if _proposals is None:
        try:
            from backend.proposals import ProposalManager
            _proposals = ProposalManager()
        except ImportError:
            logger.info("ProposalManager not available (autonomy engine removed)")
            return None
    return _proposals


async def _generate_layman_pitch(title: str, abstract: str) -> str:
    """Generate a 1-2 sentence layman's summary of a research paper."""
    from backend.model_router import get_autonomy_models
    import httpx
    models = get_autonomy_models()
    model = models.get("fast", "qwen2.5-coder:7b")

    prompt = (
        "You are an expert Chief Technology Officer. Explain the following research paper topic\n"
        "to a non-technical CEO in exactly one or two punchy, professional sentences.\n"
        "Focus on WHY it matters for a software platform.\n\n"
        f"TITLE: {title}\n"
        f"ABSTRACT: {abstract[:500]}\n\n"
        "Output ONLY the pitch, no preamble."
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 100, "num_ctx": 4096},
                },
            )
            if resp.status_code == 200:
                return resp.json().get("response", "").strip()
    except Exception:
        pass
    return "Intelligence synthesis pending... Review abstract for technical details."


# ── Search Endpoint ──────────────────────────────────────────────────


@router.get("/research/arxiv")
async def search_arxiv(
    q: str = Query(..., min_length=2, description="Search query"),
    max: int = Query(5, ge=1, le=20, description="Max results"),
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
):
    """Search arXiv for academic papers matching the query.

    Returns a list of papers with title, authors, abstract, URL, and
    publication date. Supports pagination via the `page` parameter.
    """
    researcher = _get_researcher()
    try:
        papers = await researcher.search_arxiv(
            q, max_results=max, start=page * max,
        )
        
        # Generate pitch for the top 3 papers if it's the first page
        if page == 0:
            import asyncio
            # Limit parallelism to 3
            pitches = await asyncio.gather(*[
                _generate_layman_pitch(p["title"], p["abstract"]) 
                for p in papers[:3]
            ])
            for i, pitch in enumerate(pitches):
                papers[i]["pitch"] = pitch
        
        return {
            "papers": papers,
            "count": len(papers),
            "query": q,
            "page": page,
        }
    except Exception as e:
        logger.warning(f"arXiv search failed for '{q}': {e}")
        return {"papers": [], "count": 0, "query": q, "error": str(e)}


# ── Shared Proposal Generation ──────────────────────────────────────


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


async def generate_paper_proposal(
    title: str,
    abstract: str,
    url: str = "",
    model: str | None = None,
) -> dict:
    """Shared logic: Generate a code improvement proposal from a paper.

    Used by both the POST endpoint and the auto-research loop.

    Args:
        title:    Paper title.
        abstract: Abstract or distilled change description.
        url:      Source URL.
        model:    Optional explicit model override. If None, tries the
                  configured "reflection" model first, then falls back to
                  whichever model Ollama currently has loaded so this
                  doesn't 404 when the configured model isn't installed.

    Returns:
        {"proposal": <dict>, "error": None} on success
        {"proposal": None, "error": "<message>"} on failure
    """
    from backend.model_router import get_autonomy_models

    if model:
        chosen_model = model
    else:
        models = get_autonomy_models()
        chosen_model = models.get("reflection") or "qwen2.5-coder:7b"
    # Build a sensible fallback chain: the chosen model, then any installed
    # alternative we can ask Ollama for. Picked from likely-present families.
    fallback_chain = [chosen_model] + [
        m for m in (
            "qwen2.5:7b", "qwen2.5-coder:7b", "deepseek-r1:14b",
            "gemma3:4b", "gemma4:e2b", "aya-expanse:8b",
        ) if m != chosen_model
    ]

    file_list = _get_file_list()
    code_samples = _sample_codebase(count=3)

    prompt = (
        "You are LocalMind, an AI assistant. A user found this academic paper and wants you to\n"
        "propose a CONCRETE code improvement inspired by the paper's technique.\n\n"
        f"PAPER TITLE: {title}\n"
        f"PAPER ABSTRACT: {abstract[:600]}\n"
        f"PAPER URL: {url}\n\n"
        f"PROJECT FILES:\n{file_list}\n\n"
        f"{code_samples}\n"
        "YOUR TASK:\n"
        "1. Read the paper's technique.\n"
        "2. Find a SPECIFIC place in THIS codebase where you could apply the idea.\n"
        "3. Propose a concrete, implementable change.\n\n"
        "RULES:\n"
        "- ONLY reference files from the list above.\n"
        "- Be SPECIFIC — name exact functions, classes, or logic to change.\n"
        f"- Reference the paper: '{title}'\n"
        "- The title must describe the SPECIFIC change, not just the paper topic.\n\n"
        "Output a JSON object with keys: title, category "
        "(performance/feature/bugfix/ux/security/code_quality), "
        "description, files_affected (list of real filenames from above), "
        "effort (small/medium/large), priority (low/medium/high/critical).\n"
        "Only output the JSON, nothing else."
    )

    try:
        last_error = None
        data = None
        async with httpx.AsyncClient(timeout=120.0) as client:
            for candidate_model in fallback_chain:
                resp = await client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": candidate_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 500, "num_ctx": 8192},
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    chosen_model = candidate_model
                    break
                last_error = f"Ollama returned {resp.status_code} for model={candidate_model}"
                # 404 = model not installed locally; try the next one. Anything
                # else (5xx/timeouts) is a real failure — bail out.
                if resp.status_code != 404:
                    break

            if data is None:
                return {"error": last_error or "Ollama call failed", "proposal": None}

            response_text = data.get("response", "")

            # Parse JSON from response
            json_text = response_text.strip()
            if "```" in json_text:
                json_text = json_text.split("```")[1]
                if json_text.startswith("json"):
                    json_text = json_text[4:]
                json_text = json_text.strip()

            proposal = json.loads(json_text)

            # Tag as research-sourced
            proposal["source"] = "arxiv"
            proposal["source_paper"] = title
            proposal["source_url"] = url

            # Save via ProposalManager (if available)
            pm = _get_proposals()
            if pm is not None:
                saved = pm.save(
                    proposal,
                    mode="supervised",
                    auto_approve_risks=set(),
                    log_fn=lambda *a, **k: None,
                    emit_activity=lambda *a, **k: None,
                )

                if saved:
                    logger.info(f"Paper proposal saved: {saved.get('title', '?')}")
                    return {"proposal": saved, "error": None}
                else:
                    return {"error": "Proposal was rejected (duplicate or invalid)", "proposal": None}
            else:
                # ProposalManager removed; return the generated proposal directly
                logger.info(f"Paper proposal generated (no ProposalManager): {proposal.get('title', '?')}")
                return {"proposal": proposal, "error": None}

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM response as JSON: {e}")
        return {"error": "AI response was not valid JSON — try again", "proposal": None}
    except Exception as e:
        logger.error(f"apply-paper failed: {e}")
        return {"error": str(e), "proposal": None}


# ── Research Lanes (LocalMind self-improvement) ─────────────────────

# Curated arxiv-search lanes. Each lane is one axis along which we want
# LocalMind to get smarter. The lane's `focus_text` is injected into the
# job prompt so the agent biases its takeaway toward something LocalMind
# can actually act on, rather than generic paper trivia.
LANES: dict = {
    "agentic": {
        "label": "Agentic patterns",
        "description": "Tool use, planning, replanning, error recovery.",
        "queries": [
            "LLM agent tool use",
            "language model planning multi-step",
            "agentic AI replanning error recovery",
            "LLM tool calling reliability benchmark",
        ],
        "focus_text": (
            "agentic tool-use chains, planning vs replanning, "
            "error recovery, and reliability of multi-step agent loops"
        ),
    },
    "local_llm": {
        "label": "Local LLM ops",
        "description": "Inference, quantization, routing, KV cache, latency.",
        "queries": [
            "LLM inference quantization",
            "language model routing efficiency",
            "KV cache attention optimization",
            "small language model on-device inference",
        ],
        "focus_text": (
            "local-LLM inference: quantization, routing between fast/deep "
            "models, KV cache reuse, and latency"
        ),
    },
    "memory_rag": {
        "label": "Memory & RAG",
        "description": "What to store, how to retrieve, when to forget.",
        "queries": [
            "LLM long-term memory retrieval",
            "retrieval augmented generation embeddings",
            "memory consolidation language model",
            "RAG deduplication compression",
        ],
        "focus_text": (
            "agent memory + RAG: what to persist, retrieval strategies, "
            "deduplication, embedding choice, decay, and re-ranking"
        ),
    },
    "evaluation": {
        "label": "Evaluation",
        "description": "Eval harnesses, judges, regression detection.",
        "queries": [
            "LLM evaluation benchmark agent",
            "LLM as judge evaluation reliability",
            "language model agent task benchmark",
            "regression detection LLM agent",
        ],
        "focus_text": (
            "evaluation of LLM agents: harness design, judge prompts, "
            "task benchmarks, and regression detection"
        ),
    },
}

LOCALMIND_CONTEXT = (
    "LocalMind is a local-first jobs-first AI mission control. The user "
    "delegates work as 'jobs' (quick or pipeline mode); a node-based executor "
    "(NodeExecutor in backend/jobs/executor.py) runs tools — web_search, "
    "browse_web, learn_from_web, save_memory/recall_memories, file ops, Google "
    "Workspace — against local Ollama models (gemma3:4b for fast, deepseek-r1:14b "
    "for deep). Memory + Knowledge graph + an evidence_items audit trail are "
    "first-class. Pip is a topbar mascot with mood + a planned bandit-driven brain. "
    "The goal is autonomous, auditable task execution — not a chat assistant."
)


def _build_lane_job_prompt(lane_key: str, paper: dict) -> tuple[str, str]:
    """Build a (title, description) pair for a research-lane job."""
    lane = LANES[lane_key]
    safe_title = (paper.get("title") or "Untitled").strip()[:90]
    title = f"Research lane [{lane['label']}]: {safe_title}"
    description = (
        f"You are reading a paper to make LocalMind smarter at "
        f"{lane['focus_text']}.\n\n"
        f"CONTEXT — what LocalMind is:\n{LOCALMIND_CONTEXT}\n\n"
        f"PAPER:\n"
        f"  Title:    {paper.get('title', '?')}\n"
        f"  Abstract: {paper.get('abstract', '')}\n"
        f"  URL:      {paper.get('url', '')}\n\n"
        f"DO THIS, IN ORDER:\n"
        f"1. Call browse_web on the URL to read the abstract page in full.\n"
        f"2. Decide if the paper has anything actionable for LocalMind's "
        f"{lane['focus_text']}. If NOT, say 'No actionable insight for "
        f"LocalMind in this paper.' and STOP — do not save trivia.\n"
        f"3. If yes, call learn_from_web with category=\"instruction\" and "
        f"a `note` formatted EXACTLY like:\n"
        f"   \"LocalMind change candidate ({lane['label']}): "
        f"<one-sentence concrete change>. Hook: <which file/module/concept "
        f"this would touch>. Why: <one-sentence justification>.\"\n"
        f"4. Output the saved note plus a 'NEXT STEP:' line: one specific "
        f"small experiment that would test the change in LocalMind today.\n\n"
        f"Hard rules:\n"
        f"  - Be concrete. No 'consider', 'investigate', 'explore'.\n"
        f"  - Name real LocalMind surfaces when relevant: NodeExecutor, "
        f"ToolRegistry, ToolInvocationLog, EvidenceCollector, the bandit, "
        f"Knowledge graph, /api/jobs, mascot_brain, etc.\n"
        f"  - If the paper is irrelevant, skipping is the correct move."
    )
    return title, description


@router.get("/research/lanes")
async def list_research_lanes():
    """List all available research lanes (for the mascot menu / Settings)."""
    return {
        "lanes": [
            {"key": k, "label": v["label"], "description": v["description"]}
            for k, v in LANES.items()
        ]
    }


@router.post("/research/lane")
async def run_research_lane(body: dict):
    """Pick a fresh arxiv paper from a lane and queue a learning job.

    Body: {"lane": "agentic"|"local_llm"|"memory_rag"|"evaluation"|"random"}.
    Returns the queued job_id and the paper that will be read.
    """
    from fastapi import HTTPException

    requested = (body.get("lane") or "random").strip().lower()
    if requested == "random":
        lane_key = random.choice(list(LANES.keys()))
    elif requested in LANES:
        lane_key = requested
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown lane '{requested}'. Valid: {list(LANES.keys())} or 'random'.",
        )

    lane = LANES[lane_key]
    query = random.choice(lane["queries"])

    researcher = _get_researcher()
    papers = await researcher.search_arxiv(query, max_results=8)
    if not papers:
        raise HTTPException(
            status_code=503,
            detail=f"arXiv returned no papers for '{query}'. Try again later.",
        )

    paper = random.choice(papers[: min(5, len(papers))])

    job_title, job_description = _build_lane_job_prompt(lane_key, paper)

    try:
        # Reuse the same default-workspace lookup the rest of the API uses.
        from backend.core.identity import IdentityService
        from backend.jobs.queue import JobQueue
        ws = IdentityService().get_default_workspace()
        queue = JobQueue()
        job = queue.create_job(
            workspace_id=ws.id,
            title=job_title,
            description=job_description,
            source="api",
            requester=None,
            mode="quick",
        )
    except Exception as exc:
        logger.exception("Failed to queue research-lane job")
        raise HTTPException(status_code=500, detail=f"Failed to queue job: {exc}")

    return {
        "ok": True,
        "lane": lane_key,
        "lane_label": lane["label"],
        "query": query,
        "paper": {
            "title": paper.get("title"),
            "url": paper.get("url"),
        },
        "job_id": job.id,
    }


# ── Apply-Paper Endpoint ────────────────────────────────────────────


class ApplyPaperRequest(BaseModel):
    title: str
    abstract: str
    url: str = ""


@router.post("/research/apply-paper")
async def apply_paper(req: ApplyPaperRequest):
    """Generate a code-improvement proposal from an arXiv paper.

    Takes the paper's title and abstract, samples codebase files,
    and asks the LLM to generate a concrete, actionable proposal.
    The proposal is saved into the ProposalManager pipeline.
    """
    return await generate_paper_proposal(req.title, req.abstract, req.url)


# ── Change-candidate triage endpoints ───────────────────────────────


def _list_memories_sync() -> list[dict]:
    """Inline copy of the /memories logic so we can call it without HTTP."""
    try:
        from backend.tools.memory import _get_fts_store
        import datetime as _dt
        store = _get_fts_store()
        if not store:
            return []
        recent = store.get_recent(limit=400)
        out = []
        for m in recent:
            try:
                ts = _dt.datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                ts = "unknown"
            out.append({
                "id": m.id,
                "content": m.content,
                "category": m.subcategory or m.category,
                "created_at": ts,
            })
        return out
    except Exception as exc:
        logger.warning("Failed to list memories for candidates: %s", exc)
        return []


@router.get("/research/candidates")
async def list_change_candidates(
    status: str | None = Query(None),
    lane: str | None = Query(None),
):
    """List parsed LocalMind change candidates with status."""
    from backend.research.candidates import list_candidates_from_memory_list, VALID_STATUSES

    if status and status not in VALID_STATUSES:
        from fastapi import HTTPException
        raise HTTPException(400, f"status must be one of {list(VALID_STATUSES)}")

    memories = _list_memories_sync()
    items = list_candidates_from_memory_list(memories)
    if status:
        items = [c for c in items if c["status"] == status]
    if lane:
        items = [c for c in items if (c["lane"] or "").lower() == lane.lower()]

    counts = {s: 0 for s in VALID_STATUSES}
    for c in items:
        counts[c["status"]] = counts.get(c["status"], 0) + 1

    return {
        "candidates": items,
        "count": len(items),
        "counts_by_status": counts,
    }


class CandidateStatusBody(BaseModel):
    status: str


@router.post("/research/candidates/{memory_id}/status")
async def set_candidate_status(memory_id: int, body: CandidateStatusBody):
    """Update a candidate's triage status."""
    from backend.research.candidates import set_status, VALID_STATUSES
    from fastapi import HTTPException
    try:
        st = set_status(memory_id, body.status)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "memory_id": memory_id, "status": st, "valid": list(VALID_STATUSES)}


@router.post("/research/candidates/{memory_id}/propose")
async def propose_from_candidate(memory_id: int):
    """Generate a code-improvement proposal from an accepted change candidate.

    Reuses generate_paper_proposal() with the candidate's lane + change/hook/
    why text packed into the 'abstract' slot. On success, auto-advances
    the candidate's status to 'implemented'.
    """
    from backend.research.candidates import (
        list_candidates_from_memory_list,
        set_status,
    )
    from fastapi import HTTPException

    memories = _list_memories_sync()
    candidates = list_candidates_from_memory_list(memories)
    target = next((c for c in candidates if c["memory_id"] == memory_id), None)
    if not target:
        raise HTTPException(404, f"No change candidate found for memory_id={memory_id}")

    lane = target.get("lane") or "Unknown"
    title = f"LocalMind change candidate ({lane})"
    abstract = (
        f"Change: {target.get('change') or '?'}\n"
        f"Hook: {target.get('hook') or '?'}\n"
        f"Why: {target.get('why') or '?'}"
    )
    url = target.get("source_url") or ""

    result = await generate_paper_proposal(title=title, abstract=abstract, url=url)

    new_status = target.get("status") or "accepted"
    if result.get("proposal") and not result.get("error"):
        try:
            new_status = set_status(memory_id, "implemented")
        except Exception as exc:
            logger.warning("Could not flip candidate status: %s", exc)

    return {
        "ok": bool(result.get("proposal")) and not result.get("error"),
        "memory_id": memory_id,
        "lane": lane,
        "status": new_status,
        "proposal": result.get("proposal"),
        "error": result.get("error"),
    }


# ── Scheduler endpoints ─────────────────────────────────────────────


@router.get("/research/scheduler")
async def get_scheduler_status():
    """Read the overnight research scheduler config + state."""
    from backend.research.scheduler import status_payload
    return status_payload()


@router.post("/research/scheduler")
async def update_scheduler(body: dict):
    """Update scheduler config. Body: {enabled, interval_hours, lane}."""
    from backend.research.scheduler import save_config, status_payload, start_scheduler
    save_config(body or {})
    # Make sure the loop is running so config changes take effect now.
    start_scheduler()
    return status_payload()


@router.post("/research/scheduler/run-now")
async def run_scheduler_now():
    """Force-fire one scheduled run regardless of cadence."""
    from backend.research.scheduler import run_now
    return await run_now()