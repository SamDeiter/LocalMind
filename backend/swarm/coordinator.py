"""
HiveCoordinator — Central orchestrator for the LocalMind agent swarm.

Manages:
 - GPU semaphore (max 2-3 concurrent LLM requests)
 - CPU worker pool (up to 16 parallel scanners/testers)
 - I/O worker pool (up to 8 parallel researchers)
 - Task queue with priority dispatch
 - Result aggregation and metrics
 - Backpressure: pauses CPU workers if GPU queue is too deep
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from backend.config import OLLAMA_BASE_URL
from .task_queue import TaskQueue, SwarmTask, SwarmResult, TaskType
from .agents import BaseAgent
from .agents.scanner_agent import ScannerAgent
from .agents.test_agent import TestAgent
from .agents.research_agent import ResearchAgent
from .agents.llm_agent import LLMAgent

logger = logging.getLogger("localmind.swarm.coordinator")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKIP_DIRS = {"venv", "node_modules", "__pycache__", ".git", "memory_db",
             "rag_data", "browser_recordings", ".gemini", ".bak", "tmp"}


class HiveCoordinator:
    """Central coordinator for the multi-agent swarm."""

    def __init__(
        self,
        max_gpu_workers: int = 3,
        max_cpu_workers: int = 16,
        max_io_workers: int = 8,
        ollama_url: str = OLLAMA_BASE_URL,
        emit_activity=None,
        proposals=None,
        **kwargs
    ):
        self.max_gpu_workers = max_gpu_workers
        self.max_cpu_workers = max_cpu_workers
        self.max_io_workers = max_io_workers
        self.ollama_url = ollama_url
        self._emit_activity = emit_activity or (lambda *a, **k: None)
        self.proposals = proposals
        self._get_hw_status = kwargs.get("get_hw_status", lambda: {"adaptive_status": "safe"})

        # Semaphore gates GPU access
        self.gpu_semaphore = asyncio.Semaphore(max_gpu_workers)
        self._gpu_slots_in_use = 0

        # Task queue
        self.queue = TaskQueue(max_size=512)

        # Result storage
        self.results: list[SwarmResult] = []
        self._results_lock = asyncio.Lock()

        # Agent pools
        self._cpu_agents: list[BaseAgent] = []
        self._gpu_agents: list[LLMAgent] = []
        self._io_agents: list[BaseAgent] = []

        # Worker tasks
        self._worker_tasks: list[asyncio.Task] = []
        self._running = False

        # Metrics
        self._start_time: Optional[float] = None
        self._tasks_processed = 0
        self._tasks_failed = 0
        self._peak_queue_depth = 0
        self._last_peak_reset = time.time()

    async def start(self):
        """Start the swarm coordinator and all worker loops."""
        self._running = True
        self._start_time = time.time()

        logger.info(
            f"🐝 Hive starting: {self.max_cpu_workers} CPU + "
            f"{self.max_gpu_workers} GPU + {self.max_io_workers} I/O workers"
        )
        self._emit_activity(
            "swarm_started",
            f"🐝 Hive Mind active: {self.max_cpu_workers} CPU, "
            f"{self.max_gpu_workers} GPU, {self.max_io_workers} I/O agents"
        )

        # Spawn CPU worker loops
        for i in range(self.max_cpu_workers):
            agent = ScannerAgent() if i < self.max_cpu_workers // 2 else TestAgent()
            self._cpu_agents.append(agent)
            self._worker_tasks.append(
                asyncio.create_task(self._cpu_worker_loop(agent))
            )

        # Spawn GPU worker loops
        for i in range(self.max_gpu_workers):
            agent = LLMAgent(gpu_semaphore=self.gpu_semaphore, ollama_url=self.ollama_url)
            self._gpu_agents.append(agent)
            self._worker_tasks.append(
                asyncio.create_task(self._gpu_worker_loop(agent))
            )

        # Spawn I/O worker loops
        for i in range(self.max_io_workers):
            agent = ResearchAgent()
            self._io_agents.append(agent)
            self._worker_tasks.append(
                asyncio.create_task(self._io_worker_loop(agent))
            )

        # Start the auto-scheduler that feeds work into the queue
        self._worker_tasks.append(
            asyncio.create_task(self._auto_scheduler_loop())
        )

        logger.info("🐝 All swarm workers running (auto-scheduler active).")

    async def stop(self):
        """Gracefully stop all workers."""
        self._running = False
        for task in self._worker_tasks:
            task.cancel()
        self._worker_tasks = []
        logger.info("🛑 Hive stopped.")

    # ── Worker Loops ──────────────────────────────────────────────

    async def _cpu_worker_loop(self, agent: BaseAgent):
        """CPU worker: processes scan and test tasks."""
        while self._running:
            try:
                task = self.queue.pop_cpu()
                if task is None:
                    await asyncio.sleep(0.5)
                    continue

                # Backpressure: pause if GPU queue is overloaded
                if self.queue.gpu_depth > 10:
                    await asyncio.sleep(2.0)

                result = await agent.run(task)
                await self._collect_result(result)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"CPU worker error: {exc}")
                await asyncio.sleep(1.0)

    async def _gpu_worker_loop(self, agent: LLMAgent):
        """GPU worker: processes LLM inference tasks (semaphore-gated)."""
        while self._running:
            try:
                # Adaptive Scaling: if hardware is critical, wait longer or skip
                hw = self._get_hw_status()
                if hw.get("adaptive_status") == "critical":
                    await asyncio.sleep(5.0) # Aggressive backoff
                    continue
                elif hw.get("adaptive_status") == "warning":
                    await asyncio.sleep(2.0) # Mild backoff
                
                task = self.queue.pop_gpu()
                if task is None:
                    await asyncio.sleep(1.0)
                    continue

                self._gpu_slots_in_use += 1
                try:
                    result = await agent.run(task)
                finally:
                    self._gpu_slots_in_use -= 1
                await self._collect_result(result)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"GPU worker error: {exc}")
                await asyncio.sleep(2.0)

    async def _io_worker_loop(self, agent: BaseAgent):
        """I/O worker: processes research and web tasks."""
        while self._running:
            try:
                task = self.queue.pop_io()
                if task is None:
                    await asyncio.sleep(1.0)
                    continue

                result = await agent.run(task)
                await self._collect_result(result)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"I/O worker error: {exc}")
                await asyncio.sleep(1.0)

    # ── Auto Scheduler ────────────────────────────────────────────

    async def _auto_scheduler_loop(self):
        """Periodically submit scan and health-check tasks to keep agents busy."""
        # Short initial delay to let everything spin up
        await asyncio.sleep(5)
        logger.info("🗓️  Auto-scheduler started (60s interval)")

        while self._running:
            try:
                # Submit a syntax-check sweep of the whole project
                all_files = self._get_project_files()
                py_files = [f for f in all_files if f.endswith(".py")]

                if py_files:
                    # Chunk into groups of 25 for parallel scanning
                    for i in range(0, len(py_files), 25):
                        chunk = py_files[i:i + 25]
                        task = SwarmTask(
                            type=TaskType.TEST_SYNTAX,
                            payload={"files": chunk, "mode": "syntax"},
                            priority=5,  # Low priority — background work
                        )
                        self.queue.submit(task)

                    logger.info(f"🗓️  Scheduled syntax check for {len(py_files)} files")

                # Also submit a scan task for a random chunk
                if all_files:
                    import random
                    sample = random.sample(all_files, min(30, len(all_files)))
                    scan_task = SwarmTask(
                        type=TaskType.SCAN_FILES,
                        payload={"files": sample, "scan_type": "complexity"},
                        priority=5,
                    )
                    self.queue.submit(scan_task)

                # Occasionally submit a low-priority LLM reflection task to exercise GPU slots
                import random
                if random.random() < 0.3: # 30% chance every minute
                    self.submit_llm(
                        prompt="Audit current file structure for modularity improvements.",
                        priority=5,
                        task_type=TaskType.LLM_REFLECT
                    )
                    logger.info("🗓️  Scheduled background LLM reflection")

            except Exception as exc:
                logger.warning(f"Auto-scheduler error: {exc}")

            # Reset peak every 60 seconds
            if time.time() - self._last_peak_reset > 60:
                self._peak_queue_depth = self.queue.depth
                self._last_peak_reset = time.time()

            # Wait 60 seconds before next round
            await asyncio.sleep(60)

    # ── Result Collection ─────────────────────────────────────────

    async def _collect_result(self, result: SwarmResult):
        """Store a result and update metrics."""
        async with self._results_lock:
            self.results.append(result)
            # Keep only last 200 results
            if len(self.results) > 200:
                self.results = self.results[-200:]

        self._tasks_processed += 1
        if not result.success:
            self._tasks_failed += 1

        log_level = "info" if result.success else "warning"
        getattr(logger, log_level)(
            f"[{result.agent_id}] {result.task_type.value}: "
            f"{'✅' if result.success else '❌'} ({result.duration:.1f}s)"
        )

    # ── Task Submission Helpers ───────────────────────────────────

    def submit(self, task: SwarmTask) -> bool:
        """Submit a single task to the queue."""
        success = self.queue.submit(task)
        if success:
            self._peak_queue_depth = max(self._peak_queue_depth, self.queue.depth)
        return success

    def submit_scan(self, files: list[str], scan_type: str = "both", priority: int = 3) -> str:
        """Convenience: submit a scan task."""
        task = SwarmTask(
            type=TaskType.SCAN_FILES,
            payload={"files": files, "scan_type": scan_type},
            priority=priority,
        )
        self.queue.submit(task)
        return task.id

    def submit_parallel_scan(self, chunk_size: int = 20) -> list[str]:
        """Split the entire project into chunks and submit parallel scan tasks."""
        all_files = self._get_project_files()
        task_ids = []

        for i in range(0, len(all_files), chunk_size):
            chunk = all_files[i:i + chunk_size]
            task = SwarmTask(
                type=TaskType.SCAN_FILES,
                payload={"files": chunk, "scan_type": "both"},
                priority=4,  # Background priority
            )
            self.queue.submit(task)
            task_ids.append(task.id)

        logger.info(f"Submitted {len(task_ids)} parallel scan tasks for {len(all_files)} files")
        return task_ids

    def submit_llm(self, prompt: str, model: str = "qwen2.5-coder:14b",
                   parse_json: bool = False, priority: int = 2,
                   task_type: TaskType = TaskType.LLM_REFLECT) -> str:
        """Convenience: submit an LLM inference task."""
        task = SwarmTask(
            type=task_type,
            payload={
                "prompt": prompt,
                "model": model,
                "parse_json": parse_json,
            },
            priority=priority,
        )
        self.queue.submit(task)
        return task.id

    def submit_research(self, query: str, source: str = "web", priority: int = 3) -> str:
        """Convenience: submit a research task."""
        task = SwarmTask(
            type=TaskType.RESEARCH_WEB if source == "web" else TaskType.RESEARCH_ACADEMIC,
            payload={"query": query, "source": source},
            priority=priority,
        )
        self.queue.submit(task)
        return task.id

    def submit_test(self, files: list[str] = None, mode: str = "syntax", priority: int = 1) -> str:
        """Convenience: submit a test/validation task."""
        task = SwarmTask(
            type=TaskType.TEST_VALIDATE if mode == "pytest" else TaskType.TEST_SYNTAX,
            payload={"files": files or [], "mode": mode},
            priority=priority,
        )
        self.queue.submit(task)
        return task.id

    # ── Status & Metrics ──────────────────────────────────────────

    def get_status(self) -> dict:
        """Full swarm status for the API/dashboard."""
        all_agents = self._cpu_agents + self._gpu_agents + self._io_agents

        active_agents = [a for a in all_agents if a.is_running]
        idle_agents = [a for a in all_agents if not a.is_running]

        gpu_in_use = self._gpu_slots_in_use
        gpu_available = self.max_gpu_workers - gpu_in_use

        return {
            "running": self._running,
            "uptime": round(time.time() - self._start_time) if self._start_time else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "queue": {
                **self.queue.get_stats(),
                "peak_depth": self._peak_queue_depth
            },
            "recent_improvements": self.proposals.list_completed(limit=5) if self.proposals else [],
            "agents": {
                "total": len(all_agents),
                "active": len(active_agents),
                "idle": len(idle_agents),
                "by_type": {
                    "cpu": {"total": len(self._cpu_agents), "active": sum(1 for a in self._cpu_agents if a.is_running)},
                    "gpu": {"total": len(self._gpu_agents), "active": gpu_in_use, "available_slots": gpu_available},
                    "io": {"total": len(self._io_agents), "active": sum(1 for a in self._io_agents if a.is_running)},
                },
            },
            "hardware": self._get_hw_status(),
            "metrics": {
                "tasks_processed": self._tasks_processed,
                "tasks_failed": self._tasks_failed,
                "success_rate": round(
                    (self._tasks_processed - self._tasks_failed) / max(self._tasks_processed, 1) * 100, 1
                ),
            },
            "agent_details": self.get_agent_details(),
            "recent_results": [
                {
                    "task_id": r.task_id,
                    "type": r.task_type.value,
                    "success": r.success,
                    "duration": round(r.duration, 2),
                    "agent": r.agent_id,
                    "error": r.error,
                }
                for r in self.results[-10:]
            ],
        }

    def get_agent_details(self) -> list[dict]:
        """Detailed status for every agent."""
        all_agents = self._cpu_agents + self._gpu_agents + self._io_agents
        return [a.get_status() for a in all_agents]

    # ── Internal Helpers ──────────────────────────────────────────

    def _get_project_files(self) -> list[str]:
        """Get all scannable project files."""
        files = []
        for ext in ("*.py", "*.js", "*.html", "*.css"):
            for p in PROJECT_ROOT.rglob(ext):
                rel = p.relative_to(PROJECT_ROOT)
                if not any(part in SKIP_DIRS for part in rel.parts):
                    files.append(str(rel).replace("\\", "/"))
        return sorted(files)

    async def wait_for_results(self, task_ids: list[str], timeout: float = 60.0) -> list[SwarmResult]:
        """Wait for specific task IDs to complete."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            async with self._results_lock:
                found = [r for r in self.results if r.task_id in task_ids]
                if len(found) >= len(task_ids):
                    return found
            await asyncio.sleep(0.5)
        # Return whatever we have
        async with self._results_lock:
            return [r for r in self.results if r.task_id in task_ids]
