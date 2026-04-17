"""
Run Code Tool — execute Python in a sandboxed subprocess.
Pre-execution blocklist prevents dangerous operations.
"""

import asyncio
import re
import ast
import tempfile
from pathlib import Path

from .base import BaseTool

WORKSPACE = Path.home() / "LocalMind_Workspace"
TIMEOUT_SECONDS = 30

# Patterns that are BLOCKED before execution (file deletion, system damage)
BLOCKLIST_PATTERNS = [
    r"\bos\.remove\b",
    r"\bos\.unlink\b",
    r"\bos\.rmdir\b",
    r"\bos\.removedirs\b",
    r"\bshutil\.rmtree\b",
    r"\bshutil\.move\b",
    r"\bpathlib\.Path\([^)]*\)\.unlink\b",
    r"\.unlink\s*\(",
    r"\.rmdir\s*\(",
    r"\bsubprocess\b.*\brm\b",
    r"\bsubprocess\b.*\bdel\b",
    r"\bsubprocess\b.*\brmdir\b",
    r"\bsubprocess\b.*\bformat\b",
    r"\bsend2trash\b",
    r"\b__import__\s*\(\s*['\"]os['\"]\s*\)\s*\.remove\b",
    r"\bexec\s*\(",
    r"\beval\s*\(",
    # Obfuscation prevention
    r"\bbase64\b",
    r"\bbinascii\b",
]


def _safety_check(code: str) -> str | None:
    """Scan code for dangerous patterns using Regex and AST analysis.
    Returns error message or None if safe.
    """
    # Layer 1: Regex first-pass
    for pattern in BLOCKLIST_PATTERNS:
        match = re.search(pattern, code, re.IGNORECASE)
        if match:
            return f"BLOCKED: Code contains dangerous operation (regex): '{match.group()}'. LocalMind cannot delete files."

    # Layer 2: AST analysis for deeper detection (aliasing, getattr obfuscation)
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Syntax error in code: {e}"

    BLOCKED_MODULES = {"os", "subprocess", "shutil", "pty", "commands", "runpy", "pickle", "marshal", "shelve"}
    BLOCKED_FUNCS = {"eval", "exec", "__import__", "getattr", "setattr", "delattr", "compile"}

    issues = []
    for node in ast.walk(tree):
        # 1. Direct Name Access (blocking 'os', 'eval', etc. even as variables)
        if isinstance(node, ast.Name):
            if node.id in BLOCKED_MODULES or node.id in BLOCKED_FUNCS:
                issues.append(f"use of blocked name '{node.id}'")

        # 2. Imports
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split('.')[0] for alias in node.names]
            else:
                if node.module:
                    modules = [node.module.split('.')[0]]

            for mod in modules:
                if mod in BLOCKED_MODULES:
                    issues.append(f"importing blocked module '{mod}'")

        # 3. Attribute Access (e.g. builtins.eval)
        elif isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_FUNCS:
                issues.append(f"accessing blocked attribute '{node.attr}'")

        # 4. Function Calls with dynamic string building (getattr(os, 'sys' + 'tem'))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "getattr":
                # Already caught by Name check, but here we can check for string concatenation
                if len(node.args) >= 2 and isinstance(node.args[1], ast.BinOp):
                    issues.append("dynamic attribute access via string concatenation")

    if issues:
        return f"BLOCKED: Code failed safety check: {', '.join(issues)}. LocalMind restricts dangerous operations."

    return None


class RunCodeTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_code"

    @property
    def description(self) -> str:
        return (
            "Execute Python code in a sandboxed subprocess. "
            "Code runs inside ~/LocalMind_Workspace with a 30-second timeout. "
            "Cannot delete files or run system commands."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python code to execute",
                }
            },
            "required": ["code"],
        }

    async def execute(self, code: str = "", **kwargs) -> dict:
        if not code.strip():
            return {"success": False, "error": "No code provided"}

        # Layer 3: Pre-execution safety scan
        violation = _safety_check(code)
        if violation:
            return {"success": False, "error": violation}

        WORKSPACE.mkdir(parents=True, exist_ok=True)

        # Write code to temp file inside workspace
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                dir=str(WORKSPACE),
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(code)
                tmp_path = Path(tmp.name)

            # Execute in subprocess
            proc = await asyncio.create_subprocess_exec(
                "python",
                str(tmp_path),
                cwd=str(WORKSPACE),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {
                    "success": False,
                    "error": f"Code execution timed out after {TIMEOUT_SECONDS} seconds",
                }

            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            stderr_str = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode == 0:
                output = stdout_str or "(no output)"
                if stderr_str:
                    output += f"\n\n[stderr]: {stderr_str}"
                return {"success": True, "result": output, "return_code": 0}
            else:
                return {
                    "success": False,
                    "error": stderr_str or stdout_str or "Unknown error",
                    "return_code": proc.returncode,
                }

        except Exception as exc:
            return {"success": False, "error": f"Execution failed: {exc}"}
        finally:
            # Clean up temp file
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
