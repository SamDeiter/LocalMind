import asyncio
import json
import logging
import re
import time
import httpx
from pathlib import Path

logger = logging.getLogger("localmind.autonomy.services.research")

class ResearchService:
    def __init__(self, engine):
        self.engine = engine

    async def run_auto_research(self):
        """Run automated research cycle using codebase scanning + web research."""
        AUTO_RESEARCH_TIMEOUT = 600
        async with httpx.AsyncClient(timeout=AUTO_RESEARCH_TIMEOUT) as client:
            logger.debug('Starting automated research cycle...')

            import uuid
            research_id = str(uuid.uuid4())
            self.engine._emit_activity("research_started", "🔬 Starting automated research cycle...", id=research_id)

            arch_context = await self._load_architecture_context()
            priority_context = await self._load_priority_context()
            try:
                # 1. Scan codebase for complexity hot spots and code smells (non-blocking)
                self.engine._emit_activity("research_scanning", "🔍 Deep codebase scan: analyzing complexity and hotspots...", id=research_id)
                complexity_task = asyncio.to_thread(self.engine.codebase_scanner.scan_complexity)
                smells_task = asyncio.to_thread(self.engine.codebase_scanner.scan_code_smells)
                complexity, smells = await asyncio.gather(complexity_task, smells_task)

                hot_categories = set()
                if any(f.get("severity") == "high" for f in complexity):
                    hot_categories.add("code_quality")
                if any(s.get("type") == "large_file" for s in smells):
                    hot_categories.add("code_quality")
                hot_categories.add("performance")  # always useful

                research_context = []
                for category in hot_categories:
                    self.engine._emit_activity("research_diving", f"🔬 Deep-diving into {category} optimization patterns...", id=research_id)
                    web_findings = await self.engine.web_researcher.get_findings_for_prompt(category)
                    if web_findings:
                        research_context.append(web_findings)
                
                    academic_findings = await self.engine.academic_researcher.get_findings_for_prompt(category)
                    if academic_findings:
                        research_context.append(academic_findings)

                perf_report = self.engine.performance_profiler.get_findings_for_prompt()
                if perf_report:
                    research_context.append(perf_report)

                lessons = self.engine.failure_analyzer.get_lessons_for_prompt()
                if lessons:
                    research_context.append(lessons)

                if research_context:
                    research_blob = "\n".join(research_context)
                    self.engine._emit_activity(
                        "research_analyzing",
                        f"📊 Analyzed {len(complexity)} complex functions, "
                        f"{len(smells)} code smells across {len(hot_categories)} categories"
                    )

                    prompt = (
                        "You are LocalMind's research engine. Based on the following automated "
                        "codebase analysis, web research findings, project architecture, and "
                        "user priorities, propose 1-2 specific, actionable improvements. "
                        "Each proposal should target real files and describe concrete changes "
                        "that align with the user's priorities.\n\n"
                        f"{research_blob}\n\n"
                        f"{('PROJECT ARCHITECTURE:\n' + arch_context + chr(10)*2) if arch_context else ''}"
                        f"{(priority_context + chr(10)*2) if priority_context else ''}"
                        f"Codebase scanner found {len(complexity)} complex functions and "
                        f"{len(smells)} code smells.\n\n"
                        "Respond in JSON format: "
                        '[{{"title": "...", "description": "...", "category": "...", '
                        '"risk": "low|medium", "files_affected": ["..."]}}]'
                    )

                    if await self._submit_research_proposals(prompt, research_blob, research_id):
                        return

                self.engine._emit_activity("research_complete", "🔬 Research cycle complete — no new findings", id=research_id)

            except Exception as e:
                logger.error(f"Auto-research error: {e}")
                self.engine._emit_activity("research_error", f"❌ Research error: {str(e)}", id=research_id)

    async def try_gemini_escalation(self, prompt: str) -> str:
        """Optionally escalate to Gemini when local model fails. Local-first, cheap."""
        try:
            from backend.gemini_client import is_available, generate
            if not is_available():
                return None
            logger.info("Escalating to Gemini (local model failed or unavailable)")
            self.engine._emit_activity("gemini_escalation", "☁️ Escalating to Gemini for complex analysis...")
            result = await generate(prompt, scrub=True)
            return result
        except Exception as e:
            logger.warning(f"Gemini escalation failed: {e}")
            return None

    async def _submit_research_proposals(self, prompt: str, research_blob: str, research_id: str) -> bool:
        """Call LLM with research prompt, parse proposals, save them. Returns True if proposals were saved."""
        text = None
        source = "auto_research"

        try:
            async with httpx.AsyncClient(timeout=600.0) as chat_client:
                resp = await chat_client.post(
                    f"{self.engine.ollama_url}/api/chat",
                    json={
                        "model": self.engine.reflection_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {
                            "num_ctx": 16384,
                            "temperature": 0.1
                        }
                    },
                )
                if resp.status_code == 200:
                    text = resp.json().get("message", {}).get("content", "")
                else:
                    logger.warning(f"Research LLM call failed with {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.warning(f"Research LLM call failed: {e}")

        # Fallback to Gemini if Ollama produced no usable text
        if not text or not re.search(r'\[.*\]', text, re.DOTALL):
            gemini_result = await self.try_gemini_escalation(prompt)
            if gemini_result:
                text = gemini_result
                source = "gemini_escalation"

        if not text:
            return False

        match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            return False

        try:
            proposals = json.loads(match.group())
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse research proposals JSON: {e}")
            return False

        return self._save_proposals(proposals, source, research_blob)

    def _save_proposals(self, proposals: list, source: str, research_blob: str) -> bool:
        """Save parsed proposals to the engine. Returns True if any were saved."""
        logged = 0
        for p in proposals[:2]:
            self.engine.proposals.save(
                proposal={
                    "title": p.get("title", "Research finding"),
                    "description": p.get("description", ""),
                    "category": p.get("category", "research"),
                    "risk": p.get("risk", "low"),
                    "files_affected": p.get("files_affected", []),
                    "source": source,
                    "context": research_blob
                },
                mode=self.engine.mode,
                auto_approve_risks=self.engine.AUTO_APPROVE_RISKS,
                emit_activity=self.engine._emit_activity
            )
            logged += 1
        if logged:
            self.engine.status["reflection"]["proposals_logged"] += logged
            label = "☁️ Gemini fallback" if source == "gemini_escalation" else "🔬 Research"
            self.engine._emit_activity(
                "research_complete",
                f"{label} generated {logged} new proposal(s)"
            )
            return True
        return False

    def generate_interactive_proposal(self, topic: str, question: str):
        """Create a proposal that asks the user a question before proceeding."""
        self.engine.proposals.save(
            proposal={
                "title": f"❓ Input needed: {topic}",
                "description": question,
                "category": "interactive",
                "risk": "low",
                "files_affected": [],
                "source": "auto_research",
            },
            mode=self.engine.mode,
            auto_approve_risks=self.engine.AUTO_APPROVE_RISKS,
            emit_activity=self.engine._emit_activity
        )
        self.engine._emit_activity(
            "needs_input",
            f"❓ LocalMind needs your input: {topic}"
        )
        self.engine.status["reflection"]["proposals_logged"] += 1

    async def _load_architecture_context(self):
        from backend.config import PROJECT_ROOT
        arch_file = PROJECT_ROOT / "ARCHITECTURE.md"
        if arch_file.exists():
            return arch_file.read_text(encoding="utf-8")[:3000]
        return ""

    async def _load_priority_context(self):
        from backend.config import PROJECT_ROOT
        prio_file = PROJECT_ROOT / "data" / "priorities.json"
        if prio_file.exists():
            try:
                import json as _json
                prios = _json.loads(prio_file.read_text(encoding="utf-8"))
                active = [p for p in prios if p.get("status") == "active"]
                return "\n".join([f"- {p.get('description')}" for p in active])
            except Exception as e:
                logger.warning(f"Failed to load priorities: {e}")
        return ""
