"""
Dynamic tool loader — persistence layer for custom tools created at runtime.

Saves and loads tool definitions as JSON files in WORKSPACE_DIR/custom_tools/,
allowing tools created by ToolMaker to survive across agent sessions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..config import WORKSPACE_DIR

logger = logging.getLogger("agent.tools.toolmaker")

CUSTOM_TOOLS_DIR = WORKSPACE_DIR / "custom_tools"


def _ensure_dir() -> Path:
    """Create the custom_tools directory if it doesn't exist."""
    CUSTOM_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    return CUSTOM_TOOLS_DIR


def save_tool_definition(
    name: str,
    description: str,
    parameters: dict[str, Any],
    code: str,
) -> Path:
    """Save a tool definition to disk as JSON.

    Returns the path to the saved file.
    """
    _ensure_dir()
    definition = {
        "name": name,
        "description": description,
        "parameters": parameters,
        "code": code,
    }
    path = CUSTOM_TOOLS_DIR / f"{name}.json"
    path.write_text(json.dumps(definition, indent=2), encoding="utf-8")
    logger.info(f"Saved custom tool definition: {path}")
    return path


def delete_tool_definition(name: str) -> bool:
    """Remove a custom tool definition from disk.

    Returns True if the file existed and was deleted.
    """
    path = CUSTOM_TOOLS_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
        logger.info(f"Deleted custom tool definition: {path}")
        return True
    logger.warning(f"Custom tool definition not found: {path}")
    return False


def load_custom_tools() -> list:
    """Load all saved tool definitions and return DynamicTool instances.

    Returns a list of DynamicTool instances.  Broken definitions are
    logged and skipped rather than raising.
    """
    # Import here to avoid circular imports
    from .toolmaker import DynamicTool, _build_executor, _validate_code_safety

    _ensure_dir()
    tools = []

    for path in sorted(CUSTOM_TOOLS_DIR.glob("*.json")):
        try:
            definition = json.loads(path.read_text(encoding="utf-8"))
            name = definition["name"]
            description = definition["description"]
            parameters = definition["parameters"]
            code = definition["code"]

            # Re-validate safety before loading
            _validate_code_safety(code)

            executor = _build_executor(code)
            tool = DynamicTool(
                tool_name=name,
                tool_description=description,
                tool_parameters=parameters,
                executor=executor,
            )
            tools.append(tool)
            logger.info(f"Loaded custom tool from disk: {name}")
        except Exception as exc:
            logger.error(f"Failed to load custom tool from {path.name}: {exc}")

    return tools
