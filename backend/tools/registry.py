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
            return {"success": False, "error": f"Unknown tool: {name}"}

        start = time.time()
        try:
            result = await tool.execute(**arguments)
            elapsed = time.time() - start
            logger.info(f"Tool '{name}' executed in {elapsed:.2f}s")
            return result
        except Exception as exc:
            elapsed = time.time() - start
            logger.error(f"Tool '{name}' failed after {elapsed:.2f}s: {exc}")
            return {"success": False, "error": str(exc)}

    def get_ollama_tools(self) -> list[dict]:
        """Get all tools in Ollama's JSON Schema format."""
        return [tool.to_ollama_tool() for tool in self.tools]
