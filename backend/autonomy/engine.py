import asyncio
import json
import logging
import json
import os
import re
import time
from pathlib import Path
import httpx

from backend.model_router import get_autonomy_models, get_startup_model
from backend.proposals import ProposalManager
from backend.config import OLLAMA_BASE_URL
from backend.research import (
    FailureAnalyzer, SuccessTracker, CodebaseScanner,
    PerformanceProfiler, ExternalResearcher, WebResearcher,
    AcademicResearcher
)
from backend.self_improver import SelfImprover
from backend.meta_critic import MetaCritic
from backend.priority_queue import PriorityQueue

from .config import *
from .utils import log_event
from .loops.health import run_health_loop
from .loops.reflection import run_reflection_loop
from .loops.execution import run_execution_loop
from .loops.research import run_auto_research_loop
from .loops.digest import run_digest_loop

from .reflection import run_reflection_cycle
from .execution import execute_proposal_cycle

logger = logging.getLogger("localmind.autonomy.engine")

class AutonomyEngine:
    """Background scheduler for autonomous LocalMind operations."""

    def __init__(self, ollama_url: str = OLLAMA_BASE_URL):
        self.ollama_url = ollama_url
        models = get_autonomy_models()
        self.reflection_model = models.get("reflection", "llama3.3:70b") # Default to 70b since user has it
        self.editing_model = models.get("editing", "llama3.3:70b")
        self.startup_model = get_startup_model()
        self.default_model = self.reflection_model
        
        self.mode = "autonomous"
        self.enabled = True
        self._last_chat_time = 0.0
        self._activity_subscribers: list[asyncio.Queue] = []
        self._recent_events: list[dict] = []
        self._start_time: float = time.time()
        
        self.status = {
            "enabled": True,
            "mode": "autonomous",
            "started_at": self._start_time,
            "current_activity": None,
            "health_check": {"last_run": None, "ollama_ok": False, "model_loaded": False},
            "reflection": {"last_run": None, "proposals_logged": 0},
            "execution": {"last_run": None, "proposals_executed": 0, "last_result": None},
            "auto_test": {"last_run": None, "passed": 0, "failed": 0},
            "research": {"last_run": 0}
        }
        
        self.proposals = ProposalManager()
        self.self_improver = SelfImprover(emit_activity=self._emit_activity)
        self.meta_critic = MetaCritic(
            ollama_url=self.ollama_url, 
            model=self.reflection_model, 
            emit_activity=self._emit_activity
        )
        self.priority_queue = PriorityQueue()

        # Research Pipeline
        self.failure_analyzer = FailureAnalyzer()
        self.success_tracker = SuccessTracker()
        self.codebase_scanner = CodebaseScanner()
        self.performance_profiler = PerformanceProfiler()
        self.external_researcher = ExternalResearcher()
        self.web_researcher = WebResearcher()
        self.academic_researcher = AcademicResearcher()

        self._manual_execution_event = asyncio.Event()
        self._manual_reflection_event = asyncio.Event()
        self._manual_research_event = asyncio.Event()
        
        # Circuit breaker + backoff state
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._current_backoff = BACKOFF_BASE
        self._reflection_rejections = 0
        self._reflection_backoff = 300
        
        self.AUTO_APPROVE_RISKS = AUTO_APPROVE_RISKS
        self.auto_research_enabled = True

    def notify_chat_activity(self):
        """Called by the chat route whenever the user sends a message."""
        self._last_chat_time = time.time()

    def is_user_active(self) -> bool:
        """Returns True if the user chatted within CHAT_COOLDOWN seconds."""
        return (time.time() - self._last_chat_time) < CHAT_COOLDOWN

    def subscribe_activity(self) -> asyncio.Queue:
        """Create a new subscriber queue for SSE activity events."""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._activity_subscribers.append(q)
        return q

    def unsubscribe_activity(self, q: asyncio.Queue):
        """Remove a subscriber queue."""
        if q in self._activity_subscribers:
            self._activity_subscribers.remove(q)

    def _emit_activity(self, action: str, detail: str = "", **extra):
        """Push a live activity event to all SSE subscribers and persist to file."""
        event = {
            "ts": time.time(),
            "time": time.strftime("%H:%M:%S"),
            "action": action,
            "detail": detail,
            "model": extra.get("model", self.default_model),
            "ideas": self.status["reflection"]["proposals_logged"],
            "applied": self.status["execution"]["proposals_executed"],
            **extra,
        }
        self.status["current_activity"] = event
        self._recent_events.append(event)
        if len(self._recent_events) > 30:
            self._recent_events = self._recent_events[-30:]

        # Persist to permanent log file
        log_event(action, {"detail": detail, **extra})

        for q in self._activity_subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def set_mode(self, mode: str) -> str:
        """Switch between 'supervised' and 'autonomous' mode."""
        if mode not in ("supervised", "autonomous"):
            raise ValueError(f"Invalid mode: {mode}")
        
        self.mode = mode
        self.status["mode"] = mode
        
        if mode == "autonomous":
            self.trigger_reflection()
            self.trigger_execution()
            
        log_event("mode_changed", {"mode": mode})
        self._emit_activity("mode_changed", f"Switched to {mode} mode")
        logger.info(f"🤖 Autonomy mode: {mode}")
        return mode

    def reset_engine(self) -> dict:
        """Full engine reset: archive stale proposals, reset state, retry failed."""
        self._emit_activity("engine_reset", "🔄 Full engine reset initiated")
        archive_result = self.proposals.archive_terminal()
        retried = self.proposals.retry_all_failed(emit_activity=self._emit_activity)
        cleared_titles = self.proposals.clear_failed_titles()

        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._current_backoff = BACKOFF_BASE
        self._reflection_rejections = 0
        self._reflection_backoff = 300
        self.set_mode("autonomous")

        summary = {
            "archived": archive_result.get("archived", 0),
            "retried": len(retried),
            "mode": "autonomous",
        }
        log_event("engine_reset", summary)
        return summary

    def toggle(self) -> bool:
        """Toggle the engine on/off."""
        self.enabled = not self.enabled
        self.status["enabled"] = self.enabled
        log_event("engine_toggled", {"enabled": self.enabled})
        logger.info(f"🤖 Autonomy Engine {'enabled' if self.enabled else 'paused'}")
        return self.enabled

    def get_status(self) -> dict:
        """Return the full autonomy status for the API."""
        return {
            **self.status,
            "uptime_seconds": round(time.time() - self._start_time)
            if self._start_time else 0,
        }

    def trigger_reflection(self):
        """Manually trigger the reflection cycle."""
        self._manual_reflection_event.set()
        logger.info("💡 Manual reflection event set")

    def trigger_execution(self):
        """Manually trigger the execution cycle."""
        self._manual_execution_event.set()
        logger.info("⚙️ Manual execution event set")

    def trigger_research(self):
        """Manually trigger the research cycle."""
        self._manual_research_event.set()
        logger.info("🔬 Manual research event set")

    def list_proposals(self, status_filter: str = "all") -> list[dict]:
        return self.proposals.list_proposals(status_filter)

    def approve_proposal(self, proposal_id: str):
        result = self.proposals.approve(proposal_id)
        if result:
            log_event("proposal_approved", {"id": proposal_id, "title": result.get("title", "?")})
        return result

    def deny_proposal(self, proposal_id: str):
        result = self.proposals.deny(proposal_id)
        if result:
            log_event("proposal_denied", {"id": proposal_id, "title": result.get("title", "?")})
        return result

    def retry_proposal(self, proposal_id: str):
        return self.proposals.retry(proposal_id, emit_activity=self._emit_activity)

    async def start(self):
        logger.info("🚀 Starting Autonomy Engine...")
        self._tasks = [
            asyncio.create_task(run_health_loop(self)),
            asyncio.create_task(run_reflection_loop(self)),
            asyncio.create_task(run_execution_loop(self)),
            asyncio.create_task(run_auto_research_loop(self)),
            asyncio.create_task(run_digest_loop(self)),
        ]

    async def stop(self):
        """Gracefully cancel all background tasks."""
        logger.info("🛑 Stopping Autonomy Engine...")
        for task in getattr(self, "_tasks", []):
            task.cancel()
        self._tasks = []

    async def _run_reflection(self):
        return await run_reflection_cycle(self)

    async def _execute_next_proposal(self):
        return await execute_proposal_cycle(self)

    async def _run_auto_research(self):
        """Run automated research cycle using codebase scanning + web research."""
        import httpx
        self._emit_activity("research_started", "🔬 Starting automated research cycle...")

        try:
            # 0. Load architecture context and user priorities
            arch_context = ""
            arch_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "ARCHITECTURE.md")
            if os.path.exists(arch_file):
                try:
                    with open(arch_file, "r", encoding="utf-8") as f:
                        arch_context = f.read()[:2000]  # Cap at 2k chars
                except Exception:
                    pass

            priority_context = ""
            prio_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "priorities.json")
            if os.path.exists(prio_file):
                try:
                    with open(prio_file, "r", encoding="utf-8") as f:
                        prios = json.loads(f.read())
                    active = [p for p in prios if p.get("status") == "active"]
                    if active:
                        priority_context = "USER PRIORITIES:\n" + "\n".join(
                            f"- [{p.get('priority','medium')}] {p.get('description','')}"
                            for p in active[:5]
                        )
                except Exception:
                    pass
            # 1. Scan codebase for complexity hot spots and code smells (non-blocking)
            complexity_task = asyncio.to_thread(self.codebase_scanner.scan_complexity)
            smells_task = asyncio.to_thread(self.codebase_scanner.scan_code_smells)
            complexity, smells = await asyncio.gather(complexity_task, smells_task)

            hot_categories = set()
            if any(f.get("severity") == "high" for f in complexity):
                hot_categories.add("code_quality")
            if any(s.get("type") == "large_file" for s in smells):
                hot_categories.add("code_quality")
            hot_categories.add("performance")  # always useful

            research_context = []
            for category in hot_categories:
                # Get web findings
                web_findings = await self.web_researcher.get_findings_for_prompt(category)
                if web_findings:
                    research_context.append(web_findings)
                
                # Get academic findings (ArXiv)
                academic_findings = await self.academic_researcher.get_findings_for_prompt(category)
                if academic_findings:
                    research_context.append(academic_findings)

            # 3. Get performance profile
            perf_report = self.performance_profiler.get_findings_for_prompt()
            if perf_report:
                research_context.append(perf_report)

            # 4. Get lessons from past failures
            lessons = self.failure_analyzer.get_lessons_for_prompt()
            if lessons:
                research_context.append(lessons)

            # 5. Build research-enriched prompt and generate proposals
            if research_context:
                research_blob = "\n".join(research_context)
                self._emit_activity(
                    "research_analyzing",
                    f"📊 Analyzed {len(complexity)} complex functions, "
                    f"{len(smells)} code smells across {len(hot_categories)} categories"
                )

                # Use the reflection model to generate research-driven proposals
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

                try:
                    async with httpx.AsyncClient(timeout=600.0) as client:
                        resp = await client.post(
                            f"{self.ollama_url}/api/chat",
                            json={
                                "model": self.reflection_model,
                                "messages": [{"role": "user", "content": prompt}],
                                "stream": False,
                                "options": {
                                    "num_ctx": 16384,
                                    "temperature": 0.1
                                }
                            },
                        )
                        if resp.status_code != 200:
                            logger.warning(f"Research LLM call failed with {resp.status_code}: {resp.text}")
                        else:
                            import json as _json
                            text = resp.json().get("message", {}).get("content", "")
                            # Try to extract JSON array from response
                            match = re.search(r'\[.*\]', text, re.DOTALL)
                            if match:
                                proposals = _json.loads(match.group())
                                logged = 0
                                for p in proposals[:2]:
                                    self.proposals.save(
                                        proposal={
                                            "title": p.get("title", "Research finding"),
                                            "description": p.get("description", ""),
                                            "category": p.get("category", "research"),
                                            "risk": p.get("risk", "low"),
                                            "files_affected": p.get("files_affected", []),
                                            "source": "auto_research",
                                            "context": research_blob if 'research_blob' in locals() else ""
                                        },
                                        mode=self.mode,
                                        auto_approve_risks=self.AUTO_APPROVE_RISKS,
                                        emit_activity=self._emit_activity
                                    )
                                    logged += 1
                                if logged:
                                    self.status["reflection"]["proposals_logged"] += logged
                                    self._emit_activity(
                                        "research_complete",
                                        f"🔬 Research generated {logged} new proposal(s)"
                                    )
                                    return
                except Exception as e:
                    logger.warning(f"Research LLM call failed: {e}")
                    # Fallback to Gemini if local model failed
                    gemini_result = await self._try_gemini_escalation(prompt)
                    if gemini_result:
                        import json as _json2
                        match2 = re.search(r'\[.*\]', gemini_result, re.DOTALL)
                        if match2:
                            try:
                                proposals = _json2.loads(match2.group())
                                for p in proposals[:2]:
                                    self.proposals.save(
                                        proposal={
                                            "title": p.get("title", "Research finding"),
                                            "description": p.get("description", ""),
                                            "category": p.get("category", "research"),
                                            "risk": p.get("risk", "low"),
                                            "files_affected": p.get("files_affected", []),
                                            "source": "gemini_escalation",
                                            "context": research_blob if 'research_blob' in locals() else ""
                                        },
                                        mode=self.mode,
                                        auto_approve_risks=self.AUTO_APPROVE_RISKS,
                                        emit_activity=self._emit_activity
                                    )
                                self.status["reflection"]["proposals_logged"] += len(proposals[:2])
                                self._emit_activity(
                                    "research_complete",
                                    f"☁️ Gemini fallback generated {len(proposals[:2])} proposal(s)"
                                )
                                return
                            except Exception:
                                pass

            self._emit_activity("research_complete", "🔬 Research cycle complete — no new findings")

        except Exception as e:
            logger.error(f"Auto-research error: {e}")
            self._emit_activity("research_error", f"❌ Research error: {str(e)}")

    async def _try_gemini_escalation(self, prompt: str) -> str:
        """Optionally escalate to Gemini when local model fails. Local-first, cheap."""
        try:
            from backend.gemini_client import is_available, generate
            if not is_available():
                return None
            logger.info("Escalating to Gemini (local model failed or unavailable)")
            self._emit_activity("gemini_escalation", "☁️ Escalating to Gemini for complex analysis...")
            result = await generate(prompt, scrub=True)
            return result
        except Exception as e:
            logger.warning(f"Gemini escalation failed: {e}")
            return None

    async def _generate_interactive_proposal(self, topic: str, question: str):
        """Create a proposal that asks the user a question before proceeding."""
        self.proposals.save(
            proposal={
                "title": f"❓ Input needed: {topic}",
                "description": question,
                "category": "interactive",
                "risk": "low",
                "files_affected": [],
                "source": "auto_research",
            },
            mode=self.mode,
            auto_approve_risks=self.AUTO_APPROVE_RISKS,
            emit_activity=self._emit_activity
        )
        self._emit_activity(
            "needs_input",
            f"❓ LocalMind needs your input: {topic}"
        )
        self.status["reflection"]["proposals_logged"] += 1


    def _check_health(self):
        # The health check itself is handled in run_health_loop, 
        # but the engine needs a method to actually hit Ollama or verify state.
        return "ok"
