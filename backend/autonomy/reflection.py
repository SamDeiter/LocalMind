
import asyncio
import json
import logging
import traceback
from backend import gemini_client
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

        file_list = "\n".join(f"  - {f}" for f in sorted(real_files)[:30])
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

        async with httpx.AsyncClient(timeout=600.0) as client:
            # Gather research blocks concurrently
            lessons_task = asyncio.to_thread(engine.failure_analyzer.get_lessons_for_prompt)
            stats_task = asyncio.to_thread(engine.success_tracker.get_stats_for_prompt)
            scan_task = asyncio.to_thread(engine.codebase_scanner.get_findings_for_prompt)
            perf_task = asyncio.to_thread(engine.performance_profiler.get_findings_for_prompt)
            
            research_results = await asyncio.gather(
                lessons_task, stats_task, scan_task, perf_task,
                engine.external_researcher.get_findings_for_prompt(focus_category),
                engine.web_researcher.get_findings_for_prompt(focus_category)
            )
            research_context = "\n".join(filter(None, research_results))
            
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

            # Safety cap for extremely large context
            if len(prompt) > 40000:
                logger.warning(f"Prompt too large ({len(prompt)} chars), truncating research context.")
                research_context = research_context[:10000] + "\n...[truncated]...\n"
                prompt = (
                    f"You are LocalMind, an AI assistant reviewing your OWN codebase.\n\n"
                    f"HERE ARE THE ACTUAL FILES:\n{file_list}\n\n"
                    f"{code_snippets}{todo_context}{brain_context}{research_context}{priority_context}"
                    f"REQUIRED CATEGORY: {focus_category}\n{anti_repeat}"
                    f"BANNED TOPICS: {banned_str}\n"
                    "Output JSON: title, category, description, files_affected, effort, priority."
                )

            response_text = ""
            used_gemini = False

            # Try Gemini first for high-quality reasoning if available
            if gemini_client.is_available():
                try:
                    engine._emit_activity("reasoning", "Asking Gemini (Hybrid Model)...")
                    response_text = await gemini_client.generate(prompt, model="gemini-2.0-flash")
                    used_gemini = True
                    logger.info("Hybrid reflection: Gemini generated a proposal")
                except Exception as e:
                    logger.warning(f"Hybrid reflection: Gemini failed ({e}). Falling back to Ollama.")

            # Fallback to local Ollama if Gemini failed or is not available
            if not used_gemini:
                resp = await client.post(
                    f"{engine.ollama_url}/api/chat",
                    json={
                        "model": engine.reflection_model, 
                        "messages": [{"role": "user", "content": prompt}], 
                        "stream": False,
                        "options": {
                            "num_ctx": 8192,
                            "temperature": 0.1,
                            "top_p": 0.9
                        }
                    },
                )

                if resp.status_code != 200:
                    err_msg = f"Ollama returned HTTP {resp.status_code} for model {engine.reflection_model}"
                    logger.warning(f"{err_msg}: {resp.text}")
                    engine._emit_activity("reflection_error", f"❌ {err_msg}")
                    return False

                response_dict = resp.json()
                response_text = response_dict.get("message", {}).get("content", "")

            text = response_text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            try:
                proposal = json.loads(text)
            except json.JSONDecodeError as e:
                err_snippet = text[:100] + "..." if len(text) > 100 else text
                logger.warning(f"Model returned non-JSON response: {e} — snippet: {err_snippet}")
                engine._emit_activity("reflection_error", f"❌ Model returned invalid JSON: {str(e)}")
                return False

            try:
                critique = await engine.meta_critic.review(proposal, file_list=real_files)
                if critique.approved:
                    if critique.refinement: proposal = critique.refinement
                    saved = engine.proposals.save(proposal, mode=engine.mode, auto_approve_risks=engine.AUTO_APPROVE_RISKS)
                    if saved:
                        engine.status["reflection"]["proposals_logged"] += 1
                        return True
            except Exception as critique_exc:
                logger.warning(f"Meta-critic/save failed: {critique_exc}\n{traceback.format_exc()}")
                engine._emit_activity("reflection_error", f"Meta-critic/save failed: {str(critique_exc)}")
                return False

        return False
    except Exception as exc:
        logger.warning(f"Reflection failed: {exc}\n{traceback.format_exc()}")
        engine._emit_activity("reflection_error", f"Reflection error: {type(exc).__name__}: {exc}")
        return False
    finally:
        engine._emit_activity("idle", "Waiting for next reflection cycle...")
