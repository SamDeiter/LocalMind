"""
BaseAgent — Abstract base class for all swarm agents.
Each agent runs in its own asyncio task with scoped tools,
timeout management, and heartbeat reporting.
"""

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional

from backend.swarm.task_queue import SwarmTask, SwarmResult

logger = logging.getLogger("localmind.swarm.agent")


class BaseAgent(ABC):
    """Abstract base class for all Hive Mind agents."""

    agent_type: str = "base"

    def __init__(self, agent_id: Optional[str] = None):
        self.agent_id = agent_id or f"{self.agent_type}_{uuid.uuid4().hex[:6]}"
        self.is_running = False
        self.current_task: Optional[SwarmTask] = None
        self.tasks_completed = 0
        self.tasks_failed = 0
        self._start_time: Optional[float] = None
        self._last_heartbeat: float = 0.0

    @abstractmethod
    async def execute(self, task: SwarmTask) -> SwarmResult:
        """Execute a single task. Subclasses MUST implement this."""
        ...

    async def run(self, task: SwarmTask) -> SwarmResult:
        """Run a task with timeout, error handling, and metrics."""
        self.current_task = task
        self.is_running = True
        self._start_time = time.time()

        try:
            result = await asyncio.wait_for(
                self.execute(task),
                timeout=task.timeout,
            )
            self.tasks_completed += 1
            result.duration = time.time() - self._start_time
            result.agent_id = self.agent_id
            return result

        except asyncio.TimeoutError:
            self.tasks_failed += 1
            logger.warning(f"[{self.agent_id}] Task {task.id} timed out after {task.timeout}s")
            return SwarmResult(
                task_id=task.id,
                task_type=task.type,
                success=False,
                error=f"Timeout after {task.timeout}s",
                duration=time.time() - self._start_time,
                agent_id=self.agent_id,
            )

        except Exception as exc:
            self.tasks_failed += 1
            logger.error(f"[{self.agent_id}] Task {task.id} failed: {exc}")
            return SwarmResult(
                task_id=task.id,
                task_type=task.type,
                success=False,
                error=str(exc),
                duration=time.time() - self._start_time,
                agent_id=self.agent_id,
            )

        finally:
            self.current_task = None
            self.is_running = False

    def heartbeat(self):
        """Update heartbeat timestamp."""
        self._last_heartbeat = time.time()

    def get_status(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "is_running": self.is_running,
            "current_task": self.current_task.id if self.current_task else None,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "uptime": round(time.time() - self._start_time) if self._start_time else 0,
        }
