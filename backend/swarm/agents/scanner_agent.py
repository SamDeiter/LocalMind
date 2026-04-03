"""
ScannerAgent — CPU-bound parallel codebase analyzer.
Splits the file tree across multiple workers for complexity and smell detection.
Runs entirely on CPU — no GPU needed.
"""

import ast
import logging
from pathlib import Path

from backend.swarm.task_queue import SwarmTask, SwarmResult, TaskType
from backend.swarm.agents import BaseAgent

logger = logging.getLogger("localmind.swarm.scanner")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKIP_DIRS = {"venv", "node_modules", "__pycache__", ".git", "memory_db",
             "rag_data", "browser_recordings", ".gemini", ".bak", "tmp"}


class ScannerAgent(BaseAgent):
    """Scans a subset of project files for complexity and code smells.
    
    Payload keys:
        files: list[str]  — Relative file paths to scan
        scan_type: str    — "complexity" | "smells" | "both"
    """

    agent_type = "scanner"

    async def execute(self, task: SwarmTask) -> SwarmResult:
        files = task.payload.get("files", [])
        scan_type = task.payload.get("scan_type", "both")

        results = {
            "complexity": [],
            "smells": [],
            "files_scanned": 0,
        }

        for rel_path in files:
            full_path = PROJECT_ROOT / rel_path
            if not full_path.exists():
                continue

            results["files_scanned"] += 1
            self.heartbeat()

            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()

                if scan_type in ("complexity", "both") and rel_path.endswith(".py"):
                    results["complexity"].extend(
                        self._scan_complexity(rel_path, content)
                    )

                if scan_type in ("smells", "both"):
                    results["smells"].extend(
                        self._scan_smells(rel_path, lines)
                    )

            except Exception as exc:
                logger.debug(f"Scanner skipped {rel_path}: {exc}")
                continue

        return SwarmResult(
            task_id=task.id,
            task_type=task.type,
            success=True,
            data=results,
        )

    def _scan_complexity(self, rel_path: str, source: str) -> list[dict]:
        """AST-based complexity analysis for Python files."""
        findings = []
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    length = (node.end_lineno - node.lineno + 1) if hasattr(node, "end_lineno") and node.end_lineno else 10
                    branches = sum(
                        1 for child in ast.walk(node)
                        if isinstance(child, (ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler))
                    )
                    if length > 40 or branches > 8:
                        findings.append({
                            "file": rel_path,
                            "function": node.name,
                            "lines": length,
                            "branches": branches,
                            "severity": "high" if length > 80 or branches > 12 else "medium",
                        })
        except SyntaxError:
            pass
        return findings

    def _scan_smells(self, rel_path: str, lines: list[str]) -> list[dict]:
        """Line-level code smell detection."""
        smells = []

        if len(lines) > 500:
            smells.append({
                "type": "large_file",
                "file": rel_path,
                "detail": f"{len(lines)} lines",
                "severity": "high" if len(lines) > 800 else "medium",
            })

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            for marker in ("TODO", "FIXME", "HACK"):
                if marker in stripped and not stripped.startswith("#!"):
                    smells.append({
                        "type": "todo_marker",
                        "file": rel_path,
                        "line": i,
                        "detail": stripped[:100],
                        "severity": "low",
                    })
                    break  # One marker per line is enough

        return smells
