"""
Self-Extending Tool — runtime tool generation via the ToolGenerator engine.

Replaces the naive stub that wrote raw Python to the main tools directory.
All generated code is validated, sandboxed to backend/tools/generated/,
and recorded in the ``generated_tools`` SQLite table.
"""

from typing import Any

from .base import BaseTool


class SelfExtendTool(BaseTool):
    @property
    def name(self) -> str:
        return "self_extend"

    @property
    def description(self) -> str:
        return (
            "Generate a new tool plugin. Provide tool_name, description, and either "
            "python_code (complete BaseTool subclass) or use generate_template mode."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Unique snake_case identifier for the tool (e.g. 'format_xml').",
                },
                "action": {
                    "type": "string",
                    "enum": ["create", "template", "list", "remove"],
                    "description": (
                        "Action to perform. 'create' validates and saves code, "
                        "'template' generates boilerplate, 'list' shows generated tools, "
                        "'remove' deletes a generated tool."
                    ),
                    "default": "create",
                },
                "python_code": {
                    "type": "string",
                    "description": "Complete BaseTool subclass Python source code (for 'create' action).",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description of what the tool does.",
                },
                "param_schema": {
                    "type": "object",
                    "description": "JSON Schema for the tool's parameters (for 'template' action).",
                },
            },
            "required": ["tool_name"],
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        action = kwargs.get("action", "create")
        tool_name = kwargs.get("tool_name", "")

        # Lazy import to avoid circular dependency at module load time
        from backend.tools.tool_generator import get_generator

        generator = get_generator()

        # ── CREATE ─────────────────────────────────────────────────
        if action == "create":
            python_code = kwargs.get("python_code", "")
            description = kwargs.get("description", "")

            if not tool_name:
                return {"success": False, "error": "tool_name is required."}
            if not python_code:
                return {"success": False, "error": "python_code is required for 'create' action."}

            result = generator.generate_tool(
                tool_name=tool_name,
                code=python_code,
                description=description,
                requested_by=kwargs.get("requested_by", "ai"),
            )

            if not result["ok"]:
                return {"success": False, "error": result["error"]}

            # Try to load it immediately to verify it actually works
            instance = generator.load_tool(result["tool_name"])
            loaded = instance is not None

            return {
                "success": True,
                "result": (
                    f"Tool '{result['tool_name']}' created and "
                    f"{'loaded successfully' if loaded else 'saved (load deferred)'}."
                ),
                "tool_name": result["tool_name"],
                "file_path": result["file_path"],
                "class_name": result.get("class_name"),
                "loaded": loaded,
            }

        # ── TEMPLATE ───────────────────────────────────────────────
        elif action == "template":
            description = kwargs.get("description", "A new tool.")
            param_schema = kwargs.get("param_schema", {
                "type": "object",
                "properties": {},
                "required": [],
            })

            if not tool_name:
                return {"success": False, "error": "tool_name is required."}

            template = generator.get_template(tool_name, description, param_schema)
            return {
                "success": True,
                "result": template,
                "tool_name": tool_name,
            }

        # ── LIST ───────────────────────────────────────────────────
        elif action == "list":
            tools = generator.list_generated_tools()
            return {
                "success": True,
                "result": f"Found {len(tools)} generated tool(s).",
                "tools": tools,
            }

        # ── REMOVE ─────────────────────────────────────────────────
        elif action == "remove":
            if not tool_name:
                return {"success": False, "error": "tool_name is required."}

            removed = generator.remove_tool(tool_name)
            if removed:
                return {
                    "success": True,
                    "result": f"Tool '{tool_name}' removed.",
                }
            return {
                "success": False,
                "error": f"Tool '{tool_name}' not found or could not be removed.",
            }

        else:
            return {
                "success": False,
                "error": f"Unknown action: {action!r}. Use 'create', 'template', 'list', or 'remove'.",
            }
