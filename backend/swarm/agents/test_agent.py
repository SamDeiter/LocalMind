"""
TestAgent — CPU-bound parallel test runner.
Validates file edits via AST parsing and pytest execution.
"""

import ast
import asyncio
import logging
import subprocess
from pathlib import Path

from backend.swarm.task_queue import SwarmTask, SwarmResult, TaskType
from backend.swarm.agents import BaseAgent

logger = logging.getLogger("localmind.swarm.test")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class TestAgent(BaseAgent):
    """Runs validation tasks: syntax checks, pytest, import verification.
    
    Payload keys:
        mode: str           — "syntax" | "pytest" | "import_check"
        files: list[str]    — Files to validate (for syntax/import)
        test_args: list[str] — Extra pytest arguments
    """

    agent_type = "tester"

    async def execute(self, task: SwarmTask) -> SwarmResult:
        mode = task.payload.get("mode", "syntax")

        if mode == "syntax":
            return await self._check_syntax(task)
        elif mode == "pytest":
            return await self._run_pytest(task)
        elif mode == "import_check":
            return await self._check_imports(task)
        else:
            return SwarmResult(
                task_id=task.id,
                task_type=task.type,
                success=False,
                error=f"Unknown test mode: {mode}",
            )

    async def _check_syntax(self, task: SwarmTask) -> SwarmResult:
        """AST-parse Python files to catch syntax errors."""
        files = task.payload.get("files", [])
        errors = []
        checked = 0

        for rel_path in files:
            full_path = PROJECT_ROOT / rel_path
            if not full_path.exists() or not rel_path.endswith(".py"):
                continue
            checked += 1
            try:
                source = full_path.read_text(encoding="utf-8")
                ast.parse(source)
            except SyntaxError as e:
                errors.append({
                    "file": rel_path,
                    "line": e.lineno,
                    "message": str(e.msg),
                })

        return SwarmResult(
            task_id=task.id,
            task_type=task.type,
            success=len(errors) == 0,
            data={"checked": checked, "errors": errors},
        )

    async def _run_pytest(self, task: SwarmTask) -> SwarmResult:
        """Run pytest in a subprocess (non-blocking)."""
        test_args = task.payload.get("test_args", [])
        cmd = ["python", "-m", "pytest", "-x", "--tb=short", "-q"] + test_args

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(PROJECT_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=task.timeout
            )

            output = stdout.decode("utf-8", errors="replace")
            err_output = stderr.decode("utf-8", errors="replace")
            passed = proc.returncode == 0

            return SwarmResult(
                task_id=task.id,
                task_type=task.type,
                success=passed,
                data={
                    "output": output[-2000:],  # Cap output size
                    "stderr": err_output[-500:],
                    "exit_code": proc.returncode,
                },
            )

        except asyncio.TimeoutError:
            return SwarmResult(
                task_id=task.id,
                task_type=task.type,
                success=False,
                error="pytest timed out",
            )

    async def _check_imports(self, task: SwarmTask) -> SwarmResult:
        """Verify that a Python file's imports resolve."""
        files = task.payload.get("files", [])
        broken = []

        for rel_path in files:
            full_path = PROJECT_ROOT / rel_path
            if not full_path.exists() or not rel_path.endswith(".py"):
                continue
            try:
                source = full_path.read_text(encoding="utf-8")
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        module = node.module if isinstance(node, ast.ImportFrom) else None
                        if module and module.startswith("backend."):
                            # Verify the backend module path exists
                            parts = module.replace(".", "/")
                            mod_path = PROJECT_ROOT / (parts + ".py")
                            pkg_path = PROJECT_ROOT / parts / "__init__.py"
                            if not mod_path.exists() and not pkg_path.exists():
                                broken.append({
                                    "file": rel_path,
                                    "import": module,
                                    "line": node.lineno,
                                })
            except Exception:
                continue

        return SwarmResult(
            task_id=task.id,
            task_type=task.type,
            success=len(broken) == 0,
            data={"broken_imports": broken},
        )
