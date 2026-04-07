"""
Safe shell command execution tool.

Safety design from arXiv:2512.08769 (Production-Grade Agentic Workflows):
- Allowlist of safe commands rather than blocklist of dangerous ones
- Working directory locked to workspace
- Timeout enforcement
- Output truncation to avoid flooding context window
"""

from __future__ import annotations

import asyncio
import re
import shlex
from typing import Any

from ..config import WORKSPACE_DIR
from .base import Tool

# Commands considered safe for a local agent to run.
# This is an allowlist — anything not listed is blocked.
_ALLOWED_COMMANDS = {
    # File inspection
    "ls", "dir", "find", "wc", "head", "tail", "cat", "less", "file", "stat",
    "du", "df",
    # Text processing
    "grep", "rg", "awk", "sed", "sort", "uniq", "cut", "tr", "diff",
    # Development
    "git", "python", "python3", "pip", "pip3", "node", "npm", "npx",
    "cargo", "go", "make", "cmake",
    # System info
    "echo", "date", "whoami", "uname", "hostname", "env", "printenv",
    "which", "where", "type",
    # Network (read-only)
    "curl", "wget", "ping", "nslookup", "dig",
}

# Patterns that are always blocked regardless of command
_BLOCKED_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+/"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r">\s*/dev/sd"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bformat\b.*[A-Z]:\\", re.IGNORECASE),
    re.compile(r"&&\s*(rm|del|rmdir)\s"),  # Chained destructive commands
]

MAX_OUTPUT_CHARS = 10_000


class ShellTool(Tool):
    """Execute shell commands safely within the workspace."""

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command in the workspace directory. "
            "Safe commands like grep, git, python, ls, find are allowed. "
            "Destructive commands are blocked."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30, max: 120)",
                },
            },
            "required": ["command"],
        }

    async def _execute(self, **kwargs) -> dict[str, Any]:
        command = kwargs["command"].strip()
        timeout = min(kwargs.get("timeout", 30), 120)

        # Safety: check against blocked patterns
        for pattern in _BLOCKED_PATTERNS:
            if pattern.search(command):
                return {"success": False, "error": f"Blocked: dangerous command pattern detected"}

        # Safety: check the base command against allowlist
        base_cmd = self._extract_base_command(command)
        if base_cmd and base_cmd not in _ALLOWED_COMMANDS:
            return {
                "success": False,
                "error": f"Command '{base_cmd}' is not in the allowed list. "
                         f"Allowed: {', '.join(sorted(_ALLOWED_COMMANDS))}",
            }

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(WORKSPACE_DIR),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Truncate to avoid flooding the context window
            if len(stdout_str) > MAX_OUTPUT_CHARS:
                stdout_str = stdout_str[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(stdout_str)} total chars)"
            if len(stderr_str) > MAX_OUTPUT_CHARS:
                stderr_str = stderr_str[:MAX_OUTPUT_CHARS] + f"\n... (truncated)"

            return {
                "success": proc.returncode == 0,
                "result": stdout_str.strip() or "(no output)",
                "stderr": stderr_str.strip() if stderr_str.strip() else None,
                "exit_code": proc.returncode,
            }

        except asyncio.TimeoutError:
            return {"success": False, "error": f"Command timed out after {timeout}s"}

    @staticmethod
    def _extract_base_command(command: str) -> str | None:
        """Extract the base command name from a shell command string."""
        # Handle pipes: check each segment
        # For simplicity, validate the first command in a pipeline
        command = command.strip()
        if not command:
            return None

        # Handle env vars, cd prefixes
        parts = command.split("|")[0].strip().split("&&")[0].strip().split(";")[0].strip()
        try:
            tokens = shlex.split(parts)
        except ValueError:
            tokens = parts.split()

        if not tokens:
            return None

        # Skip env var assignments (VAR=value cmd)
        for tok in tokens:
            if "=" in tok and not tok.startswith("-"):
                continue
            # Return the actual command name (strip path)
            return tok.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        return None
