"""
Self-Test Tool — Run pytest on the LocalMind project from within the agent.

Used after self_edit to validate changes didn't break anything.
- 60-second timeout
- Returns pass/fail summary + failure details
"""

import logging
import subprocess
from pathlib import Path

from .base import BaseTool

logger = logging.getLogger("localmind.tools.self_test")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class SelfTestTool(BaseTool):
    """Run the project's test suite to validate changes."""

    @property
    def name(self) -> str:
        return "self_test"

    @property
    def description(self) -> str:
        return (
            "Run pytest on the LocalMind project to validate that your changes "
            "didn't break anything. Call this after using self_edit. "
            "Returns a pass/fail summary with failure details."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "test_path": {
                    "type": "string",
                    "description": "Specific test file or directory to run (default: 'tests/')",
                    "default": "tests/",
                },
                "verbose": {
                    "type": "boolean",
                    "description": "Show verbose output with individual test results",
                    "default": True,
                },
            },
        }

    async def execute(self, test_path: str = "tests/", verbose: bool = True, **kwargs) -> dict:
        args = ["python", "-m", "pytest", test_path, "--tb=short"]
        if verbose:
            args.append("-v")

        try:
            result = subprocess.run(
                args,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )

            output = result.stdout
            if result.stderr:
                output += "\n\nSTDERR:\n" + result.stderr

            # Truncate very long output
            if len(output) > 5000:
                output = output[:2500] + "\n\n... [truncated] ...\n\n" + output[-2500:]

            passed = result.returncode == 0
            logger.info(f"Self-test {'PASSED' if passed else 'FAILED'} (exit code {result.returncode})")

            return {
                "success": True,
                "result": output,
                "passed": passed,
                "exit_code": result.returncode,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Tests timed out (60s limit). There may be a hanging test.",
                "passed": False,
            }
        except Exception as exc:
            return {
                "success": False,
                "error": f"Failed to run tests: {exc}",
                "passed": False,
            }
