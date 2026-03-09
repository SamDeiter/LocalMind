"""
LocalMind Tool Plugin Base Class.
All tools subclass BaseTool and get auto-discovered by the registry.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Abstract base class for all LocalMind tool plugins.

    To create a new tool:
    1. Create a .py file in backend/tools/
    2. Subclass BaseTool
    3. Define name, description, parameters, and execute()
    4. Restart server — auto-discovered
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier (e.g., 'web_search')."""
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
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    }
                },
                "required": ["query"]
            }
        """
        ...

    @abstractmethod
    async def execute(self, **kwargs) -> dict[str, Any]:
        """Run the tool and return results.

        Returns a dict with at minimum:
            {"result": "...", "success": True/False}
        """
        ...

    def to_ollama_tool(self) -> dict:
        """Convert this tool to Ollama's tool calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
