"""
Tool Adapter — bridges backend.tools.BaseTool <-> src.agent.tools.Tool.

Allows tools from either system to be used in the other:
- BackendToolAdapter: wraps a BaseTool so it works in the new agent
- AgentToolAdapter: wraps a Tool so it works in the existing backend registry
"""

from __future__ import annotations

import logging
from typing import Any

from .tools.base import Tool

logger = logging.getLogger("agent.adapter")


class BackendToolAdapter(Tool):
    """Wraps a backend BaseTool so it can be used in the src.agent system.

    The new agent expects Tool subclasses with _execute(). This adapter
    delegates to the existing BaseTool.execute() method.
    """

    def __init__(self, backend_tool):
        self._backend_tool = backend_tool

    @property
    def name(self) -> str:
        return self._backend_tool.name

    @property
    def description(self) -> str:
        return self._backend_tool.description

    @property
    def parameters(self) -> dict:
        return self._backend_tool.parameters

    async def _execute(self, **kwargs) -> dict[str, Any]:
        return await self._backend_tool.execute(**kwargs)


class AgentToolAdapter:
    """Wraps a src.agent Tool so it can be used in the backend ToolRegistry.

    The backend expects BaseTool-compatible objects with:
    - name, description, parameters (properties)
    - execute(**kwargs) -> dict (async)
    - to_ollama_tool() -> dict

    This adapter delegates to the agent Tool's execute() method
    (which already includes timeout/error wrapping).
    """

    def __init__(self, agent_tool: Tool):
        self._agent_tool = agent_tool

    @property
    def name(self) -> str:
        return self._agent_tool.name

    @property
    def description(self) -> str:
        return self._agent_tool.description

    @property
    def parameters(self) -> dict:
        return self._agent_tool.parameters

    async def execute(self, **kwargs) -> dict[str, Any]:
        return await self._agent_tool.execute(**kwargs)

    def to_ollama_tool(self) -> dict:
        return self._agent_tool.to_ollama_tool()


def import_backend_tools(registry) -> list[BackendToolAdapter]:
    """Import all tools from a backend ToolRegistry into agent-compatible adapters.

    Args:
        registry: A backend.tools.registry.ToolRegistry instance

    Returns:
        List of BackendToolAdapter instances ready for agent.register_tool()
    """
    adapters = []
    for tool in registry.tools:
        adapter = BackendToolAdapter(tool)
        adapters.append(adapter)
        logger.info(f"Adapted backend tool: {tool.name}")
    return adapters


def export_agent_tools(agent) -> list[AgentToolAdapter]:
    """Export all tools from an Agent into backend-compatible adapters.

    Args:
        agent: A src.agent.core.Agent instance

    Returns:
        List of AgentToolAdapter instances ready for registry injection
    """
    adapters = []
    for tool in agent.tools.values():
        adapter = AgentToolAdapter(tool)
        adapters.append(adapter)
        logger.info(f"Adapted agent tool: {tool.name}")
    return adapters
