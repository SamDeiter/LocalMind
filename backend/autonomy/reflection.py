
import asyncio
import json
import logging
import time
import httpx
from pathlib import Path
from backend.todo_harvester import get_todos_for_prompt
from backend.tools.manage_model import ManageModelTool
from .utils import log_event, sample_code_snippets

logger = logging.getLogger("localmind.autonomy.reflection")

async def run_reflection_cycle(engine) -> bool:
    """Logic for a single reflection cycle."""
    try:
        # Self-improvement
        try:
            changes = engine.self_improver.optimize()
            if changes:
                logger.info(f"🧬 Self-improvement: {len(changes)} config change(s)")
        except Exception as si_exc:
            logger.warning(f"Self-improvement failed: {si_exc}")

        # Gather file listing
        project_root = Path(__file__).parent.parent.parent
        real_files = []
        for ext in ("*.py", "*.js", "*.html", "*.css", "*.json", "*.md"):
            for f in project_root.rglob(ext):
                rel = f.relative_to(project_root)
                skip = any(part.startswith(".") or part in ("node_modules", "__pycache__", "memory_db", "browser_recordings") for part in rel.parts)
                if not skip:
                    real_files.append(str(rel).replace("\\", "/"))

        file_list = "\n".join(f"  - {f}" for f in sorted(real_files)[:60])
        code_snippets = sample_code_snippets(real_files)
        todo_context = get_todos_for_prompt()

        engine._emit_activity("reflecting", f"Step 2/2: Generating proposals with {engine.reflection_model}...")

        if "70b" in engine.reflection_model.lower():
            await ManageModelTool().execute(model_name=engine.reflection_model, action="load", keep_alive="30m")

        existing_proposals = engine.proposals.list_proposals()
        category_counts = {}
        for p in existing_proposals:
            cat = p.get("category", "unknown")
            category_counts[cat] = category_counts.get(cat, 0) + 1

        focus_categories = ["performance", "feature", "ux", "security", "code_quality", "bugfix"]
        blocked_cats = engine.self_improver.get_blocked_categories()
        if blocked_cats:
            available = [c for c in focus_categories if c not in blocked_cats]
            if available:
                focus_categories = available

        category_weights = [(cat, category_counts.get(cat, 0)) for cat in focus_categories]
        category_weights.sort(key=lambda x: x[1])
        focus_category = category_weights[0][0]

        all_blocked = engine.proposals.get_anti_repeat_titles()
        anti_repeat = ""
        if all_blocked:
            anti_repeat = "\nALREADY PROPOSED:\n" + "\n".join(f"  ❌ {t}" for t in list(all_blocked)[:20])

        async with httpx.AsyncClient(timeout=120.0) as client:
            lessons_block = engine.failure_analyzer.get_lessons_for_prompt()
            stats_block = engine.success_tracker.get_stats_for_prompt()
            scan_block = engine.codebase_scanner.get_findings_for_prompt()
            perf_block = engine.performance_profiler.get_findings_for_prompt()
            ext_block = await engine.external_researcher.get_findings_for_prompt(focus_category)
            web_block = await engine.web_researcher.get_findings_for_prompt(focus_category)

            research_context = "\n".join(filter(None, [lessons_block, stats_block, scan_block, perf_block, ext_block, web_block]))
            
            brain_context = engine.self_improver.get_config_for_prompt()
            priority_context = engine.priority_queue.get_prompt_injection()
            banned_list = engine.self_improver.config.get("banned_patterns", [])
            banned_str = ", ".join(f"'{b}'" for b in banned_list[:10])

            prompt = (
                f"You are LocalMind, an AI assistant reviewing your OWN codebase.\n\n"
                f"HERE ARE THE ACTUAL FILES:\n{file_list}\n\n"
                f"{code_snippets}{todo_context}{brain_context}{research_context}{priority_context}"
                f"REQUIRED CATEGORY: {focus_category}\n{anti_repeat}"
                f"BANNED TOPICS: {banned_str}\n"
                "Output JSON: title, category, description, files_affected, effort, priority."
            )

            resp = await client.post(
                f"{engine.ollama_url}/api/generate",
                json={"model": engine.reflection_model, "prompt": prompt, "stream": False},
            )

            if resp.status_code != 200:
                logger.warning(f"Ollama returned HTTP {resp.status_code} for model {engine.reflection_model}")
                return False

            response_text = resp.json().get("response", "")
            try:
                proposal = json.loads(response_text)
            except json.JSONDecodeError as e:
                logger.warning(f"Model returned non-JSON response: {e} — first 200 chars: {response_text[:200]}")
                return False

            try:
                critique = await engine.meta_critic.review(proposal, file_list=real_files)
                if critique.approved:
                    if critique.refinement: proposal = critique.refinement
                    saved = engine.proposals.save(proposal, mode=engine.mode, auto_approve_risks=engine.AUTO_APPROVE_RISKS)
                    if saved:
                        engine.status["reflection"]["proposals_logged"] += 1
                        return True
                else:
                    logger.info(f"MetaCritic rejected: {critique.reason}")
            except Exception as e:
                logger.warning(f"Meta-critic/save failed: {e}")
        return False
    except Exception as exc:
        logger.warning(f"Reflection failed: {exc}")
        return False
