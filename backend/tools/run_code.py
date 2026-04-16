"""
Run Code Tool — execute Python in a sandboxed subprocess.
Pre-execution blocklist prevents dangerous operations.
"""

import ast
import asyncio
import re
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
    r"import\s+base64",
    r"import\s+binascii",
]

DANGEROUS_FUNCS = {"exec", "eval", "compile", "__import__", "breakpoint"}
DANGEROUS_ATTRS = {
    "os": {
        "system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe", "remove", "unlink",
        "rmdir", "removedirs"
    },
    "subprocess": {"run", "call", "check_call", "check_output", "Popen"},
    "shutil": {"rmtree", "move"},
}


def _safety_check(code: str) -> str | None:
    """Scan code for dangerous patterns using regex and AST. Returns error message or None if safe."""
    # Stage 1: Fast Regex check
    for pattern in BLOCKLIST_PATTERNS:
        match = re.search(pattern, code, re.IGNORECASE)
        if match:
            return f"BLOCKED: Code contains dangerous operation: '{match.group()}'. LocalMind cannot delete files."

    # Stage 2: AST Analysis for obfuscation bypasses
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Syntax Error: {e}"

    for node in ast.walk(tree):
        # 1. Detect calls to dangerous functions
        if isinstance(node, ast.Call):
            func = node.func
            # Direct name calls: exec(), eval(), etc.
            if isinstance(func, ast.Name) and func.id in DANGEROUS_FUNCS:
                return f"BLOCKED: Code contains dangerous call: '{func.id}'"

            # Attribute calls: os.system(), subprocess.run(), etc.
            if isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name) and func.value.id in DANGEROUS_ATTRS:
                    if func.attr in DANGEROUS_ATTRS[func.value.id]:
                        return f"BLOCKED: Code contains dangerous call: '{func.value.id}.{func.attr}'"

            # Detect getattr(os, 'system') type obfuscation
            if isinstance(func, ast.Name) and func.id == "getattr":
                if len(node.args) >= 2:
                    obj_arg = node.args[0]
                    attr_arg = node.args[1]
                    target_attr = None
                    if isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str):
                        target_attr = attr_arg.value

                    if target_attr:
                        # Only block if target object is a dangerous module Name
                        if isinstance(obj_arg, ast.Name):
                            if obj_arg.id in DANGEROUS_ATTRS and target_attr in DANGEROUS_ATTRS[obj_arg.id]:
                                return f"BLOCKED: Code contains dangerous getattr: '{obj_arg.id}.{target_attr}'"
                            # Also check for __builtins__
                            if obj_arg.id == "__builtins__" and target_attr in DANGEROUS_FUNCS:
                                return f"BLOCKED: Code contains dangerous getattr: '{obj_arg.id}.{target_attr}'"

                        # Or if it's a direct dangerous function name being accessed as an attribute (rare but possible)
                        # We only block this if we can't determine the object is safe
                        # To minimize false positives, we check if target_attr is in DANGEROUS_FUNCS
                        # but we already covered that with Direct name calls if they call it.
                        # getattr(x, 'exec') is still dangerous if x is builtins or similar.

        # 2. Detect ImportFrom: from os import system
        if isinstance(node, ast.ImportFrom):
            if node.module in DANGEROUS_ATTRS:
                for alias in node.names:
                    if alias.name in DANGEROUS_ATTRS[node.module]:
                        return f"BLOCKED: Dangerous import from '{node.module}': '{alias.name}'"

        # 3. Detect aliasing: e = exec; s = os.system
        if isinstance(node, ast.Assign):
            # Direct name aliasing: e = exec
            if isinstance(node.value, ast.Name) and node.value.id in DANGEROUS_FUNCS:
                return f"BLOCKED: Code attempts to alias dangerous function: '{node.value.id}'"
            # Attribute aliasing: s = os.system
            if isinstance(node.value, ast.Attribute):
                if isinstance(node.value.value, ast.Name) and node.value.value.id in DANGEROUS_ATTRS:
                    if node.value.attr in DANGEROUS_ATTRS[node.value.value.id]:
                        return f"BLOCKED: Code attempts to alias dangerous attribute: '{node.value.value.id}.{node.value.attr}'"

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
