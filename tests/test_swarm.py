"""
Tests for the Swarm subsystem — TaskQueue operations and HiveCoordinator
task submission, status reporting, and project file discovery.
"""
import asyncio
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.swarm.task_queue import TaskQueue, SwarmTask, SwarmResult, TaskType, GPU_TASKS, IO_TASKS
from backend.swarm.coordinator import HiveCoordinator, SKIP_DIRS


# ══════════════════════════════════════════════════════════════════
#  TaskQueue Tests
# ══════════════════════════════════════════════════════════════════

class TestTaskQueueSubmit:
    """Basic submit and depth tracking."""

    def test_submit_returns_true(self):
        q = TaskQueue(max_size=10)
        task = SwarmTask(type=TaskType.SCAN_FILES, payload={"files": []})
        assert q.submit(task) is True

    def test_submit_increments_depth(self):
        q = TaskQueue(max_size=10)
        assert q.depth == 0
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        assert q.depth == 1
        q.submit(SwarmTask(type=TaskType.LLM_REFLECT, payload={}))
        assert q.depth == 2

    def test_submit_increments_total_submitted(self):
        q = TaskQueue(max_size=10)
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        assert q._total_submitted == 2

    def test_submit_batch(self):
        q = TaskQueue(max_size=10)
        tasks = [SwarmTask(type=TaskType.SCAN_FILES, payload={}) for _ in range(5)]
        count = q.submit_batch(tasks)
        assert count == 5
        assert q.depth == 5


class TestTaskQueueMaxSize:
    """Queue rejects tasks when full."""

    def test_rejects_when_full(self):
        q = TaskQueue(max_size=2)
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        result = q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        assert result is False
        assert q.depth == 2

    def test_batch_partial_when_near_full(self):
        q = TaskQueue(max_size=3)
        tasks = [SwarmTask(type=TaskType.SCAN_FILES, payload={}) for _ in range(5)]
        count = q.submit_batch(tasks)
        assert count == 3
        assert q.depth == 3


class TestTaskQueuePopCpu:
    """pop_cpu returns only CPU-bound tasks."""

    def test_pop_cpu_returns_cpu_task(self):
        q = TaskQueue()
        cpu_task = SwarmTask(type=TaskType.SCAN_FILES, payload={})
        q.submit(cpu_task)
        result = q.pop_cpu()
        assert result is not None
        assert result.id == cpu_task.id
        assert result.type == TaskType.SCAN_FILES

    def test_pop_cpu_skips_gpu_tasks(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.LLM_REFLECT, payload={}))
        result = q.pop_cpu()
        assert result is None

    def test_pop_cpu_skips_io_tasks(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.RESEARCH_WEB, payload={}))
        result = q.pop_cpu()
        assert result is None

    def test_pop_cpu_returns_none_when_empty(self):
        q = TaskQueue()
        assert q.pop_cpu() is None


class TestTaskQueuePopGpu:
    """pop_gpu returns only GPU-bound tasks."""

    def test_pop_gpu_returns_gpu_task(self):
        q = TaskQueue()
        gpu_task = SwarmTask(type=TaskType.LLM_REFLECT, payload={})
        q.submit(gpu_task)
        result = q.pop_gpu()
        assert result is not None
        assert result.type == TaskType.LLM_REFLECT

    def test_pop_gpu_skips_cpu_tasks(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        result = q.pop_gpu()
        assert result is None

    def test_pop_gpu_returns_none_when_empty(self):
        q = TaskQueue()
        assert q.pop_gpu() is None

    def test_all_llm_types_are_gpu(self):
        """LLM_REFLECT, LLM_EDIT, and LLM_TARGET are all GPU tasks."""
        for task_type in [TaskType.LLM_REFLECT, TaskType.LLM_EDIT, TaskType.LLM_TARGET]:
            q = TaskQueue()
            q.submit(SwarmTask(type=task_type, payload={}))
            result = q.pop_gpu()
            assert result is not None, f"{task_type} should be a GPU task"


class TestTaskQueuePopIo:
    """pop_io returns only I/O-bound tasks."""

    def test_pop_io_returns_io_task(self):
        q = TaskQueue()
        io_task = SwarmTask(type=TaskType.RESEARCH_WEB, payload={})
        q.submit(io_task)
        result = q.pop_io()
        assert result is not None
        assert result.type == TaskType.RESEARCH_WEB

    def test_pop_io_skips_cpu_tasks(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.TEST_SYNTAX, payload={}))
        result = q.pop_io()
        assert result is None

    def test_academic_research_is_io(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.RESEARCH_ACADEMIC, payload={}))
        result = q.pop_io()
        assert result is not None
        assert result.type == TaskType.RESEARCH_ACADEMIC


class TestTaskQueuePriority:
    """Lower priority number = processed first."""

    def test_higher_priority_popped_first(self):
        q = TaskQueue()
        low = SwarmTask(type=TaskType.SCAN_FILES, payload={}, priority=5)
        high = SwarmTask(type=TaskType.SCAN_FILES, payload={}, priority=1)
        q.submit(low)
        q.submit(high)

        result = q.pop_cpu()
        assert result.id == high.id

    def test_same_priority_fifo(self):
        """Tasks with the same priority are returned in creation order."""
        q = TaskQueue()
        # Ensure distinct created_at by setting them explicitly
        first = SwarmTask(type=TaskType.SCAN_FILES, payload={}, priority=3)
        first.created_at = 1000.0
        second = SwarmTask(type=TaskType.SCAN_FILES, payload={}, priority=3)
        second.created_at = 2000.0

        q.submit(first)
        q.submit(second)

        result = q.pop_cpu()
        assert result.id == first.id

    def test_mixed_type_priority(self):
        """Priority ordering works across task types for a specific pop."""
        q = TaskQueue()
        gpu_low = SwarmTask(type=TaskType.LLM_REFLECT, payload={}, priority=5)
        gpu_high = SwarmTask(type=TaskType.LLM_EDIT, payload={}, priority=1)
        q.submit(gpu_low)
        q.submit(gpu_high)

        result = q.pop_gpu()
        assert result.id == gpu_high.id


class TestTaskQueueDepthTracking:
    """Depth properties track queue composition correctly."""

    def test_gpu_depth(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.LLM_REFLECT, payload={}))
        q.submit(SwarmTask(type=TaskType.LLM_EDIT, payload={}))
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        assert q.gpu_depth == 2

    def test_cpu_depth(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        q.submit(SwarmTask(type=TaskType.TEST_SYNTAX, payload={}))
        q.submit(SwarmTask(type=TaskType.LLM_REFLECT, payload={}))
        assert q.cpu_depth == 2

    def test_io_depth(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.RESEARCH_WEB, payload={}))
        q.submit(SwarmTask(type=TaskType.RESEARCH_ACADEMIC, payload={}))
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        assert q.io_depth == 2

    def test_depth_decrements_on_pop(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        assert q.depth == 1
        q.pop_cpu()
        assert q.depth == 0


class TestTaskQueueGetStats:
    """get_stats returns a complete summary dict."""

    def test_stats_structure(self):
        q = TaskQueue()
        stats = q.get_stats()
        expected_keys = {
            "total_queued", "gpu_queued", "cpu_queued",
            "io_queued", "total_submitted", "total_completed"
        }
        assert set(stats.keys()) == expected_keys

    def test_stats_after_activity(self):
        q = TaskQueue()
        q.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        q.submit(SwarmTask(type=TaskType.LLM_REFLECT, payload={}))
        q.submit(SwarmTask(type=TaskType.RESEARCH_WEB, payload={}))
        q.pop_cpu()

        stats = q.get_stats()
        assert stats["total_submitted"] == 3
        assert stats["total_completed"] == 1
        assert stats["total_queued"] == 2
        assert stats["gpu_queued"] == 1
        assert stats["cpu_queued"] == 0
        assert stats["io_queued"] == 1


class TestSwarmTaskProperties:
    """SwarmTask property helpers."""

    def test_requires_gpu_true_for_llm_tasks(self):
        for tt in GPU_TASKS:
            t = SwarmTask(type=tt, payload={})
            assert t.requires_gpu is True

    def test_requires_gpu_false_for_cpu_tasks(self):
        t = SwarmTask(type=TaskType.SCAN_FILES, payload={})
        assert t.requires_gpu is False

    def test_is_io_bound_true_for_research(self):
        for tt in IO_TASKS:
            t = SwarmTask(type=tt, payload={})
            assert t.is_io_bound is True

    def test_is_io_bound_false_for_scan(self):
        t = SwarmTask(type=TaskType.SCAN_FILES, payload={})
        assert t.is_io_bound is False

    def test_task_has_unique_id(self):
        t1 = SwarmTask(type=TaskType.SCAN_FILES, payload={})
        t2 = SwarmTask(type=TaskType.SCAN_FILES, payload={})
        assert t1.id != t2.id

    def test_task_ordering(self):
        """Lower priority sorts first (for heapq)."""
        high = SwarmTask(type=TaskType.SCAN_FILES, payload={}, priority=1)
        low = SwarmTask(type=TaskType.SCAN_FILES, payload={}, priority=5)
        assert high < low


# ══════════════════════════════════════════════════════════════════
#  HiveCoordinator Tests
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def coordinator():
    """Create a HiveCoordinator without starting workers."""
    coord = HiveCoordinator(
        max_gpu_workers=2,
        max_cpu_workers=4,
        max_io_workers=2,
        ollama_url="http://localhost:11434",
        emit_activity=MagicMock(),
        proposals=None,
    )
    return coord


class TestHiveCoordinatorGetProjectFiles:
    """_get_project_files returns the correct filtered file list."""

    def test_returns_py_and_js_files(self, tmp_path):
        """Finds .py, .js, .html, .css files."""
        (tmp_path / "main.py").write_text("pass")
        (tmp_path / "app.js").write_text("//")
        (tmp_path / "index.html").write_text("<html>")
        (tmp_path / "style.css").write_text("body {}")
        (tmp_path / "readme.md").write_text("# hi")  # Not scanned

        with patch("backend.swarm.coordinator.PROJECT_ROOT", tmp_path):
            coord = HiveCoordinator()
            files = coord._get_project_files()

        assert "main.py" in files
        assert "app.js" in files
        assert "index.html" in files
        assert "style.css" in files
        assert "readme.md" not in files

    def test_excludes_venv_and_pycache(self, tmp_path):
        """Directories in SKIP_DIRS are excluded."""
        (tmp_path / "good.py").write_text("pass")
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "lib.py").write_text("pass")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mod.py").write_text("pass")
        node = tmp_path / "node_modules"
        node.mkdir()
        (node / "pkg.js").write_text("//")

        with patch("backend.swarm.coordinator.PROJECT_ROOT", tmp_path):
            coord = HiveCoordinator()
            files = coord._get_project_files()

        assert "good.py" in files
        assert not any("venv" in f for f in files)
        assert not any("__pycache__" in f for f in files)
        assert not any("node_modules" in f for f in files)

    def test_excludes_git_directory(self, tmp_path):
        """The .git directory is excluded."""
        (tmp_path / "app.py").write_text("pass")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config.py").write_text("pass")

        with patch("backend.swarm.coordinator.PROJECT_ROOT", tmp_path):
            coord = HiveCoordinator()
            files = coord._get_project_files()

        assert not any(".git" in f for f in files)

    def test_nested_files_use_forward_slashes(self, tmp_path):
        """Nested paths use forward slashes regardless of OS."""
        sub = tmp_path / "backend"
        sub.mkdir()
        (sub / "server.py").write_text("pass")

        with patch("backend.swarm.coordinator.PROJECT_ROOT", tmp_path):
            coord = HiveCoordinator()
            files = coord._get_project_files()

        assert "backend/server.py" in files

    def test_returns_sorted_list(self, tmp_path):
        """Output is alphabetically sorted."""
        (tmp_path / "z_module.py").write_text("pass")
        (tmp_path / "a_module.py").write_text("pass")
        (tmp_path / "m_module.py").write_text("pass")

        with patch("backend.swarm.coordinator.PROJECT_ROOT", tmp_path):
            coord = HiveCoordinator()
            files = coord._get_project_files()

        assert files == sorted(files)


class TestHiveCoordinatorGetStatus:
    """get_status returns the expected structure."""

    def test_status_structure(self, coordinator):
        """Status dict has all required top-level keys."""
        status = coordinator.get_status()
        expected_keys = {
            "running", "uptime", "timestamp", "queue", "agents",
            "hardware", "metrics", "agent_details", "recent_results",
            "recent_improvements",
        }
        assert set(status.keys()) == expected_keys

    def test_status_running_flag(self, coordinator):
        """Running flag reflects coordinator state."""
        assert coordinator.get_status()["running"] is False

    def test_status_queue_stats(self, coordinator):
        """Queue stats include peak_depth."""
        status = coordinator.get_status()
        assert "peak_depth" in status["queue"]
        assert "total_queued" in status["queue"]

    def test_status_agent_counts(self, coordinator):
        """Agent counts reflect configured pools (before start)."""
        status = coordinator.get_status()
        agents = status["agents"]
        assert agents["total"] == 0  # No agents until start()
        assert agents["by_type"]["gpu"]["total"] == 0
        assert agents["by_type"]["gpu"]["available_slots"] == 2

    def test_status_metrics(self, coordinator):
        """Metrics include tasks_processed, tasks_failed, success_rate."""
        status = coordinator.get_status()
        metrics = status["metrics"]
        assert metrics["tasks_processed"] == 0
        assert metrics["tasks_failed"] == 0
        # With 0 tasks processed: (0 - 0) / max(0, 1) * 100 = 0.0
        assert isinstance(metrics["success_rate"], float)

    def test_status_uptime_zero_before_start(self, coordinator):
        """Uptime is 0 before start() is called."""
        assert coordinator.get_status()["uptime"] == 0

    def test_status_recent_results_empty(self, coordinator):
        """Recent results are empty initially."""
        assert coordinator.get_status()["recent_results"] == []


class TestHiveCoordinatorSubmitScan:
    """submit_scan creates SCAN_FILES tasks."""

    def test_returns_task_id(self, coordinator):
        task_id = coordinator.submit_scan(["file1.py", "file2.py"])
        assert isinstance(task_id, str)
        assert len(task_id) > 0

    def test_task_is_queued(self, coordinator):
        coordinator.submit_scan(["file1.py"])
        assert coordinator.queue.depth == 1

    def test_task_has_correct_type(self, coordinator):
        coordinator.submit_scan(["file1.py"])
        task = coordinator.queue.pop_cpu()
        assert task.type == TaskType.SCAN_FILES

    def test_task_payload_contains_files(self, coordinator):
        coordinator.submit_scan(["a.py", "b.py"], scan_type="complexity")
        task = coordinator.queue.pop_cpu()
        assert task.payload["files"] == ["a.py", "b.py"]
        assert task.payload["scan_type"] == "complexity"

    def test_task_respects_priority(self, coordinator):
        coordinator.submit_scan(["a.py"], priority=1)
        task = coordinator.queue.pop_cpu()
        assert task.priority == 1


class TestHiveCoordinatorSubmitLlm:
    """submit_llm creates GPU-bound LLM tasks."""

    def test_returns_task_id(self, coordinator):
        task_id = coordinator.submit_llm("Analyze code quality")
        assert isinstance(task_id, str)

    def test_task_is_gpu_bound(self, coordinator):
        coordinator.submit_llm("Reflect on architecture")
        task = coordinator.queue.pop_gpu()
        assert task is not None
        assert task.requires_gpu is True

    def test_default_task_type_is_reflect(self, coordinator):
        coordinator.submit_llm("Think about design")
        task = coordinator.queue.pop_gpu()
        assert task.type == TaskType.LLM_REFLECT

    def test_custom_task_type(self, coordinator):
        coordinator.submit_llm("Edit file", task_type=TaskType.LLM_EDIT)
        task = coordinator.queue.pop_gpu()
        assert task.type == TaskType.LLM_EDIT

    def test_payload_contains_prompt_and_model(self, coordinator):
        coordinator.submit_llm("My prompt", model="gemma3:4b", parse_json=True)
        task = coordinator.queue.pop_gpu()
        assert task.payload["prompt"] == "My prompt"
        assert task.payload["model"] == "gemma3:4b"
        assert task.payload["parse_json"] is True


class TestHiveCoordinatorSubmitResearch:
    """submit_research creates I/O-bound research tasks."""

    def test_returns_task_id(self, coordinator):
        task_id = coordinator.submit_research("Python best practices")
        assert isinstance(task_id, str)

    def test_web_research_task_type(self, coordinator):
        coordinator.submit_research("AI trends", source="web")
        task = coordinator.queue.pop_io()
        assert task is not None
        assert task.type == TaskType.RESEARCH_WEB

    def test_academic_research_task_type(self, coordinator):
        coordinator.submit_research("Neural networks", source="academic")
        task = coordinator.queue.pop_io()
        assert task.type == TaskType.RESEARCH_ACADEMIC

    def test_payload_contains_query(self, coordinator):
        coordinator.submit_research("Test query", source="web")
        task = coordinator.queue.pop_io()
        assert task.payload["query"] == "Test query"
        assert task.payload["source"] == "web"

    def test_research_is_io_bound(self, coordinator):
        coordinator.submit_research("Something")
        task = coordinator.queue.pop_io()
        assert task.is_io_bound is True


class TestHiveCoordinatorSubmitTest:
    """submit_test creates test/validation tasks."""

    def test_syntax_mode(self, coordinator):
        task_id = coordinator.submit_test(files=["app.py"], mode="syntax")
        assert isinstance(task_id, str)
        task = coordinator.queue.pop_cpu()
        assert task.type == TaskType.TEST_SYNTAX

    def test_pytest_mode(self, coordinator):
        coordinator.submit_test(files=["test_app.py"], mode="pytest")
        task = coordinator.queue.pop_cpu()
        assert task.type == TaskType.TEST_VALIDATE

    def test_default_mode_is_syntax(self, coordinator):
        coordinator.submit_test()
        task = coordinator.queue.pop_cpu()
        assert task.type == TaskType.TEST_SYNTAX


class TestHiveCoordinatorSubmit:
    """The generic submit() method."""

    def test_submit_returns_true(self, coordinator):
        task = SwarmTask(type=TaskType.SCAN_FILES, payload={})
        assert coordinator.submit(task) is True

    def test_submit_tracks_peak_depth(self, coordinator):
        for _ in range(5):
            coordinator.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        assert coordinator._peak_queue_depth == 5

    def test_submit_returns_false_when_full(self, coordinator):
        coordinator.queue = TaskQueue(max_size=1)
        coordinator.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        result = coordinator.submit(SwarmTask(type=TaskType.SCAN_FILES, payload={}))
        assert result is False


class TestHiveCoordinatorParallelScan:
    """submit_parallel_scan chunks project files."""

    def test_creates_chunked_tasks(self, coordinator, tmp_path):
        # Create 50 .py files
        for i in range(50):
            (tmp_path / f"mod_{i}.py").write_text("pass")

        with patch("backend.swarm.coordinator.PROJECT_ROOT", tmp_path):
            task_ids = coordinator.submit_parallel_scan(chunk_size=20)

        assert len(task_ids) == 3  # ceil(50/20) = 3
        assert coordinator.queue.depth == 3
