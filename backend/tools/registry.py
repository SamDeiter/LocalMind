"""
Tool Registry — auto-discovers all tool plugins and routes calls.
Drop a new BaseTool subclass in tools/ and it appears automatically.
"""

import importlib
import inspect
import logging
import pkgutil
import time
from pathlib import Path
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools")


class ToolRegistry:
    """Auto-discovers and manages all tool plugins."""

    def __init__(self):
        self.tools: list[BaseTool] = []
        self._tool_map: dict[str, BaseTool] = {}
        self._discover_tools()

    def _discover_tools(self):
        """Scan the tools/ directory for BaseTool subclasses."""
        tools_dir = Path(__file__).parent
        package_name = __package__ or "backend.tools"

        for module_info in pkgutil.iter_modules([str(tools_dir)]):
            if module_info.name in ("base", "registry", "__init__"):
                continue
            try:
                module = importlib.import_module(f".{module_info.name}", package=package_name)
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseTool) and obj is not BaseTool:
                        instance = obj()
                        self.tools.append(instance)
                        self._tool_map[instance.name] = instance
                        logger.info(f"Discovered tool: {instance.name}")
            except Exception as exc:
                logger.warning(f"Failed to load tool module '{module_info.name}': {exc}")

        logger.info(f"Registry loaded {len(self.tools)} tools: {[t.name for t in self.tools]}")

    def get_tool(self, name: str) -> BaseTool | None:
        """Look up a tool by name."""
        return self._tool_map.get(name)

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by name with the given arguments. Returns result dict."""
        tool = self.get_tool(name)
        if not tool:
            self._record_activity(name, arguments, None, 0.0, success=False, error="Unknown tool")
            return {"success": False, "error": f"Unknown tool: {name}"}

        start = time.time()
        try:
            result = await tool.execute(**arguments)
            elapsed = time.time() - start
            logger.info(f"Tool '{name}' executed in {elapsed:.2f}s")
            self._record_activity(name, arguments, result, elapsed * 1000)
            return result
        except Exception as exc:
            elapsed = time.time() - start
            logger.error(f"Tool '{name}' failed after {elapsed:.2f}s: {exc}")
            self._record_activity(name, arguments, None, elapsed * 1000, success=False, error=str(exc))
            return {"success": False, "error": str(exc)}

    @staticmethod
    def _record_activity(name: str, arguments: dict, result: Any, duration_ms: float,
                         success: bool | None = None, error: str | None = None) -> None:
        """Append a tool-call entry to the global activity ring buffer."""
        try:
            from backend.observability.activity_log import ActivityKind, record
            ok = success
            if ok is None:
                ok = bool(isinstance(result, dict) and result.get("success", True)) and error is None
            args_preview = ", ".join(f"{k}={v}" for k, v in list((arguments or {}).items())[:3])
            summary = f"{name}({args_preview})" if args_preview else name
            detail = {"args": arguments or {}}
            if isinstance(result, dict):
                if "result" in result:
                    detail["result"] = result.get("result")
                if "error" in result:
                    detail["error"] = result.get("error")
            if error:
                detail["error"] = error
            record(
                ActivityKind.TOOL_CALL,
                summary,
                actor="bot",
                detail=detail,
                duration_ms=duration_ms,
                success=ok,
            )
        except Exception:
            pass  # observability must never break tool execution

    def get_ollama_tools(self) -> list[dict]:
        """Get all tools in Ollama's JSON Schema format."""
        return [tool.to_ollama_tool() for tool in self.tools]
