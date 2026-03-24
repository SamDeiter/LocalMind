"""
Generate UI Tool — LocalMind tool plugin for AI-powered UI generation.

Allows the LLM to generate HTML/CSS interfaces using Gemini,
similar to Google Stitch but callable as a tool within the agent loop.
"""

from typing import Any

from backend.tools.base import BaseTool
from backend.metacognition.ui_generator import UIGenerator


class GenerateUITool(BaseTool):
    """Tool that generates HTML/CSS UIs from natural language descriptions."""

    _generator = None

    @property
    def name(self) -> str:
        return "generate_ui"

    @property
    def description(self) -> str:
        return (
            "Generate a production-quality HTML/CSS user interface from a "
            "natural language description. Can create dashboards, panels, "
            "forms, data visualizations, and custom components. "
            "Uses AI (Gemini) to produce polished, dark-themed, responsive HTML. "
            "Preset types: calibration_dashboard, intent_panel, memory_panel, "
            "thinking_stream, uncertainty_gauge."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "Natural language description of the UI to generate. "
                        "Be specific about layout, data to display, and style."
                    ),
                },
                "component_type": {
                    "type": "string",
                    "description": (
                        "Optional preset component type. One of: "
                        "calibration_dashboard, intent_panel, memory_panel, "
                        "thinking_stream, uncertainty_gauge. "
                        "If provided, uses an optimized prompt for that component."
                    ),
                    "enum": [
                        "calibration_dashboard",
                        "intent_panel",
                        "memory_panel",
                        "thinking_stream",
                        "uncertainty_gauge",
                        "",
                    ],
                },
                "save": {
                    "type": "boolean",
                    "description": "If true, save the generated HTML to a file and return the path.",
                },
                "name": {
                    "type": "string",
                    "description": "Filename for the saved UI (used only if save=true).",
                },
            },
            "required": ["description"],
        }

    def _get_generator(self) -> UIGenerator:
        if self._generator is None:
            self._generator = UIGenerator()
        return self._generator

    async def execute(self, **kwargs) -> dict[str, Any]:
        description = kwargs.get("description", "")
        component_type = kwargs.get("component_type", "")
        save = kwargs.get("save", False)
        name = kwargs.get("name", "generated_ui")

        if not description and not component_type:
            return {
                "success": False,
                "result": "Please provide a description or component_type.",
            }

        gen = self._get_generator()

        try:
            if save:
                path = await gen.generate_and_save(
                    name=name,
                    description=description,
                    component_type=component_type,
                )
                return {
                    "success": True,
                    "result": f"UI generated and saved to: {path}",
                    "path": str(path),
                }
            else:
                html = await gen.generate(
                    description=description,
                    component_type=component_type,
                )
                # Return first 3000 chars to avoid flooding context
                preview = html[:3000] + ("..." if len(html) > 3000 else "")
                return {
                    "success": True,
                    "result": preview,
                    "length": len(html),
                }

        except Exception as e:
            return {
                "success": False,
                "result": f"UI generation failed: {str(e)}",
            }
