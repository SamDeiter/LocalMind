"""
Tool base class with JSON Schema definitions.

Design informed by:
- arXiv:2512.15943 — Tool-calling SLMs perform best with strict JSON Schema
  definitions. Schema validity rate jumps from ~60% to ~90% when tools provide
  explicit type annotations, enums, and descriptions.
- arXiv:2502.11705 (ToolMaker) — Tools should be self-describing, with enough
  metadata that an LLM can select and invoke them without examples.
- arXiv:2512.08769 — Production agents need tool-level error handling and
  timeout enforcement per tool call.

Compatible with the existing LocalMind BaseTool interface (backend/tools/base.py)
so tools can be shared between the old and new agent systems.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from ..config import TOOL_TIMEOUT_SECONDS

logger = logging.getLogger("agent.tools")


class Tool(ABC):
    """Base class for all agent tools.

    Subclasses must define:
    - name: unique identifier
    - description: one-line summary for LLM tool selection
    - parameters: JSON Schema dict describing accepted arguments
    - _execute(**kwargs): the actual implementation

    The public execute() method adds timeout enforcement, logging, and
    error wrapping automatically.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier (e.g., 'file_read')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description for the LLM to understand what this tool does."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema for the tool's arguments.

        Example:
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"}
                },
                "required": ["path"]
            }
        """
        ...

    @abstractmethod
    async def _execute(self, **kwargs) -> dict[str, Any]:
        """Implement the tool logic. Called by execute() with timeout/error wrapping."""
        ...

    async def execute(self, **kwargs) -> dict[str, Any]:
        """Run the tool with timeout enforcement and error handling.

        Returns:
            {"success": True, "result": ...} on success
            {"success": False, "error": "..."} on failure
        """
        start = time.time()
        timeout = kwargs.pop("_timeout", TOOL_TIMEOUT_SECONDS)
        try:
            result = await asyncio.wait_for(self._execute(**kwargs), timeout=timeout)
            elapsed = time.time() - start
            logger.info(f"Tool '{self.name}' completed in {elapsed:.2f}s")
            return result
        except asyncio.TimeoutError:
            elapsed = time.time() - start
            logger.warning(f"Tool '{self.name}' timed out after {elapsed:.1f}s")
            return {"success": False, "error": f"Tool timed out after {timeout}s"}
        except Exception as exc:
            elapsed = time.time() - start
            logger.error(f"Tool '{self.name}' failed after {elapsed:.2f}s: {exc}")
            return {"success": False, "error": str(exc)}

    def to_schema(self) -> dict:
        """Export as JSON Schema tool definition (OpenAI/Ollama compatible).

        arXiv:2512.15943 shows that explicit JSON Schema definitions improve
        tool-call accuracy by 20-30% over free-form descriptions.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    # Alias for backward compatibility with existing LocalMind tools
    to_ollama_tool = to_schema
