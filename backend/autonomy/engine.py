import asyncio
import json
import logging
import time
from pathlib import Path
import httpx

from backend.model_router import get_autonomy_models, get_startup_model
from backend.proposals import ProposalManager
from backend.config import OLLAMA_BASE_URL, SERVER_PORT
from backend.research import (
    FailureAnalyzer, SuccessTracker, CodebaseScanner,
    PerformanceProfiler, ExternalResearcher, WebResearcher
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
        self.reflection_model = models.get("reflection", "qwen2.5-coder:14b")
        self.editing_model = models.get("editing", "qwen2.5-coder:14b")
        self.mode = "supervised"
        self.enabled = True
        self.status = {
            "health": "ok",
            "reflection": {"last_run": 0, "proposals_logged": 0},
            "execution": {"last_run": 0, "proposals_executed": 0, "last_result": ""},
            "research": {"last_run": 0},
            "auto_test": {"last_run": 0}
        }
        
        self.proposals = ProposalManager()
        self.self_improver = SelfImprover(emit_activity=self._emit_activity)
        self.meta_critic = MetaCritic(ollama_url=self.ollama_url, model=self.reflection_model, emit_activity=self._emit_activity)
        self.priority_queue = PriorityQueue()

        # Research Pipeline
        self.failure_analyzer = FailureAnalyzer()
        self.success_tracker = SuccessTracker()
        self.codebase_scanner = CodebaseScanner()
        self.performance_profiler = PerformanceProfiler()
        self.external_researcher = ExternalResearcher()
        self.web_researcher = WebResearcher()

        self._activity_subscribers = []
        self._manual_execution_event = asyncio.Event()
        self._manual_reflection_event = asyncio.Event()
        self._consecutive_failures = 0
        self._circuit_open_until = 0
        self._current_backoff = BACKOFF_BASE
        self.AUTO_APPROVE_RISKS = AUTO_APPROVE_RISKS

    def _emit_activity(self, type, message, **kwargs):
        event = {"type": type, "message": message, "ts": time.time(), **kwargs}
        for q in self._activity_subscribers:
            q.put_nowait(event)

    def is_user_active(self):
        # Implementation depends on server state, for now simple placeholder logic
        # or it can be passed from the server
        return False

    async def start(self):
        logger.info("🚀 Starting Autonomy Engine...")
        asyncio.create_task(run_health_loop(self))
        asyncio.create_task(run_reflection_loop(self))
        asyncio.create_task(run_execution_loop(self))
        asyncio.create_task(run_auto_research_loop(self))
        asyncio.create_task(run_digest_loop(self))

    async def _run_reflection(self):
        return await run_reflection_cycle(self)

    async def _execute_next_proposal(self):
        return await execute_proposal_cycle(self)

    def _check_health(self):
        # Ported from original health check
        return "ok"
