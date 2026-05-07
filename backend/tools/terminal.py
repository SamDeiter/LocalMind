"""
Sandboxed Terminal Tool for LocalMind.
Provides safe shell execution with stderr/stdout capturing.
"""

import asyncio
import re
from typing import Any
import logging
from backend.config import PROJECT_ROOT
from .base import BaseTool
from .propose_action import ProposeActionTool

logger = logging.getLogger("localmind.tools.terminal")

# Security: Block dangerous commands and shell operators.
# Use word boundaries (\b) to prevent bypasses (e.g. 'army' instead of 'rm').
# We also account for backslash escaping (e.g. 'r\m') by allowing optional backslashes between characters.
DANGEROUS_PATTERN = re.compile(
    r"\b("
    r"r\\*m|"
    r"d\\*e\\*l|"
    r"p\\*i\\*p(\s|\\)*i\\*n\\*s\\*t\\*a\\*l\\*l|"
    r"n\\*p\\*m(\s|\\)*i\\*n\\*s\\*t\\*a\\*l\\*l|"
    r"a\\*p\\*t|"
    r"c\\*a\\*r\\*g\\*o(\s|\\)*i\\*n\\*s\\*t\\*a\\*l\\*l|"
    r"f\\*o\\*r\\*m\\*a\\*t|"
    r"c\\*u\\*r\\*l|"
    r"w\\*g\\*e\\*t|"
    r"g\\*i\\*t(\s|\\)*p\\*u\\*s\\*h"
    r")\b",
    re.IGNORECASE
)
# Shell operator detection to prevent command chaining bypasses.
SHELL_OPERATORS = re.compile(r"[;&|\n]")

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
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout", 10)
        
        # Security: Multi-stage check for dangerous patterns and shell chaining.
        # We split by operators to check EACH command in a chain.
        commands_to_check = SHELL_OPERATORS.split(command)
        is_dangerous = any(DANGEROUS_PATTERN.search(cmd) for cmd in commands_to_check)

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
            # Use asyncio subprocess for non-blocking execution safely.
            # Security: Ensure cwd is always within the project root.
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
