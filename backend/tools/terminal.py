"""
Sandboxed Terminal Tool for LocalMind.
Provides safe shell execution with stderr/stdout capturing.
"""

import asyncio
import subprocess
import re
from typing import Any
import logging
from .base import BaseTool
from .propose_action import ProposeActionTool
from backend.config import PROJECT_ROOT

logger = logging.getLogger("localmind.tools.terminal")

DANGEROUS_COMMANDS = ("rm", "del", "pip install", "npm install", "apt", "cargo install", "format", "curl", "wget", "git push")

class TerminalTool(BaseTool):
    @property
    def name(self) -> str:
        return "terminal"

    @property
    def description(self) -> str:
        return (
            "Run shell commands in the workspace. Use this to run tests, build projects, "
            "or explore files. Captures stdout and stderr."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The exact shell command to run"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 10)", "default": 10}
            },
            "required": ["command"]
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        command = kwargs.get("command", "").strip()
        timeout = kwargs.get("timeout", 10)
        
        # Security check: detect dangerous commands even if chained or preceded by whitespace
        # Use regex to find dangerous commands at the start of the string or after shell separators
        pattern = r"(?:^|[;&|]|\n)\s*\b(" + "|".join(re.escape(cmd) for cmd in DANGEROUS_COMMANDS) + r")\b"
        is_dangerous = bool(re.search(pattern, command))

        if is_dangerous:
            proposer = ProposeActionTool()
            app_req = await proposer.execute(
                action_type="system_command",
                description=f"Run dangerous command: {command}",
                reason="Agent requested command execution",
                risk_level="HIGH"
            )
            if not app_req.get("approved"):
                return {"success": False, "error": "User denied execution of dangerous command."}
        
        try:
            # Use asyncio subprocess for non-blocking execution safely
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(PROJECT_ROOT)
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out = stdout.decode().strip()
            err = stderr.decode().strip()
            
            return {
                "success": proc.returncode == 0,
                "stdout": out[:2000] if out else "",
                "stderr": err[:2000] if err else "",
                "return_code": proc.returncode
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": f"Command timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}
