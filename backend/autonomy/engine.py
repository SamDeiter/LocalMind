import asyncio
import json
import logging
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

from .services.research_service import ResearchService
from .services.git_coordinator import GitCoordinator

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

AUTO_RESEARCH_TIMEOUT = 600

class AutonomyEngine:
    def __init__(self, config=None, ollama_url: str = OLLAMA_BASE_URL):
        """Background scheduler for autonomous LocalMind operations."""
        self.config = config if config is not None else {}
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
            "research": {"last_run": 0},
            "agent_loop": {"active": False, "current_agent": None}
        }

        # Initialize loop task lists to avoid AttributeError
        self._tasks = []        
        self.proposals = ProposalManager()
        assert hasattr(self, '_emit_activity'), 'self._emit_activity must be defined before initializing SelfImprover'
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

        self.research_service = ResearchService(self)
        self.git_coordinator = GitCoordinator(self)

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
        self._emit_activity("engine_reset_summary", json.dumps(summary))
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

        # Initialize the Hive Mind swarm coordinator
        from backend.swarm.coordinator import HiveCoordinator
        self.coordinator = HiveCoordinator(
            max_gpu_workers=3,
            max_cpu_workers=16,
            max_io_workers=8,
            ollama_url=self.ollama_url,
            emit_activity=self._emit_activity,
            proposals=self.proposals,
        )
        await self.coordinator.start()

        self._tasks = [
            asyncio.create_task(run_health_loop(self)),
            asyncio.create_task(run_reflection_loop(self)),
            asyncio.create_task(run_execution_loop(self)),
            asyncio.create_task(run_auto_research_loop(self)),
            asyncio.create_task(run_digest_loop(self)),
        ]
        logger.info("All tasks created and scheduled.")

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
        """Run automated research cycle delegated to ResearchService."""
        await self.research_service.run_auto_research()

    async def _try_gemini_escalation(self, prompt: str) -> str:
        return await self.research_service.try_gemini_escalation(prompt)

    async def _generate_interactive_proposal(self, topic: str, question: str):
        return self.research_service.generate_interactive_proposal(topic, question)

    def _check_health(self):
        # The health check itself is handled in run_health_loop, 
        # but the engine needs a method to actually hit Ollama or verify state.
        return "ok"