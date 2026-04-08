"""
SwarmTask & TaskQueue — Typed task system for the Hive Mind.
Inspired by Claw Code's task decomposition pattern.
"""

import time
import heapq
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskType(str, Enum):
    """Types of work the swarm can perform."""
    SCAN_FILES = "scan_files"           # CPU: AST analysis, complexity
    SCAN_SMELLS = "scan_smells"         # CPU: Code smell detection
    TEST_VALIDATE = "test_validate"     # CPU: Run pytest on specific files
    TEST_SYNTAX = "test_syntax"         # CPU: AST parse check
    RESEARCH_WEB = "research_web"       # I/O: Web search
    RESEARCH_ACADEMIC = "research_academic"  # I/O: Academic paper search
    INDEX_RAG = "index_rag"             # CPU: Build/update RAG index
    INDEX_HASH = "index_hash"           # CPU: File hash for dedup
    GIT_BRANCH = "git_branch"           # CPU: Branch operations
    GIT_DIFF = "git_diff"              # CPU: Diff analysis
    LLM_REFLECT = "llm_reflect"         # GPU: Generate proposals
    LLM_EDIT = "llm_edit"              # GPU: Code editing
    LLM_TARGET = "llm_target"          # GPU: File targeting
    DELEGATED_JOB = "delegated_job"    # Delegation: child job spawned by DelegationEngine


# Which task types require the GPU
GPU_TASKS = {TaskType.LLM_REFLECT, TaskType.LLM_EDIT, TaskType.LLM_TARGET}
IO_TASKS = {TaskType.RESEARCH_WEB, TaskType.RESEARCH_ACADEMIC}


@dataclass(order=False)
class SwarmTask:
    """A single unit of work for the swarm."""
    type: TaskType
    payload: dict
    priority: int = 3                    # 0=critical, 5=background
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timeout: float = 300.0
    created_at: float = field(default_factory=time.time)
    parent_id: Optional[str] = None      # For task chaining
    tree_root_id: Optional[str] = None   # Root job ID when part of a delegation tree
    retries: int = 0
    max_retries: int = 2

    @property
    def requires_gpu(self) -> bool:
        return self.type in GPU_TASKS

    @property
    def is_io_bound(self) -> bool:
        return self.type in IO_TASKS

    def __lt__(self, other):
        """Priority comparison for heapq (lower number = higher priority)."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.created_at < other.created_at


@dataclass
class SwarmResult:
    """Result from a completed swarm task."""
    task_id: str
    task_type: TaskType
    success: bool
    data: Any = None
    error: Optional[str] = None
    duration: float = 0.0
    agent_id: Optional[str] = None


class TaskQueue:
    """Thread-safe priority queue for swarm tasks.
    
    Uses a min-heap so lower priority numbers are processed first.
    Provides separate tracking for GPU vs CPU vs I/O tasks.
    """

    def __init__(self, max_size: int = 512):
        self._heap: list[SwarmTask] = []
        self._max_size = max_size
        self._total_submitted = 0
        self._total_completed = 0

    def submit(self, task: SwarmTask) -> bool:
        """Add a task to the queue. Returns False if queue is full."""
        if len(self._heap) >= self._max_size:
            return False
        heapq.heappush(self._heap, task)
        self._total_submitted += 1
        return True

    def submit_batch(self, tasks: list[SwarmTask]) -> int:
        """Submit multiple tasks. Returns count of successfully queued."""
        queued = 0
        for task in tasks:
            if self.submit(task):
                queued += 1
        return queued

    def pop_cpu(self) -> Optional[SwarmTask]:
        """Pop the highest-priority CPU-bound task."""
        return self._pop_matching(lambda t: not t.requires_gpu and not t.is_io_bound)

    def pop_gpu(self) -> Optional[SwarmTask]:
        """Pop the highest-priority GPU-bound task."""
        return self._pop_matching(lambda t: t.requires_gpu)

    def pop_io(self) -> Optional[SwarmTask]:
        """Pop the highest-priority I/O-bound task."""
        return self._pop_matching(lambda t: t.is_io_bound)

    def _pop_matching(self, predicate) -> Optional[SwarmTask]:
        """Pop the first task matching the predicate, maintaining heap order."""
        for i, task in enumerate(self._heap):
            if predicate(task):
                # Remove from heap and re-heapify
                self._heap[i] = self._heap[-1]
                self._heap.pop()
                if self._heap:
                    heapq.heapify(self._heap)
                self._total_completed += 1
                return task
        return None

    @property
    def depth(self) -> int:
        return len(self._heap)

    @property
    def gpu_depth(self) -> int:
        return sum(1 for t in self._heap if t.requires_gpu)

    @property
    def cpu_depth(self) -> int:
        return sum(1 for t in self._heap if not t.requires_gpu and not t.is_io_bound)

    @property
    def io_depth(self) -> int:
        return sum(1 for t in self._heap if t.is_io_bound)

    def get_stats(self) -> dict:
        return {
            "total_queued": self.depth,
            "gpu_queued": self.gpu_depth,
            "cpu_queued": self.cpu_depth,
            "io_queued": self.io_depth,
            "total_submitted": self._total_submitted,
            "total_completed": self._total_completed,
        }
