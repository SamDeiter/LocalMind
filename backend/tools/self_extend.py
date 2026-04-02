"""
Dynamic Tool Generator.
"""
from typing import Any
from pathlib import Path
from .base import BaseTool

class SelfExtendTool(BaseTool):
    @property
    def name(self) -> str:
        return "self_extend"

    @property
    def description(self) -> str:
        return "Write a new Python tool plugin to give yourself new capabilities at runtime."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the new tool (e.g. format_xml)"},
                "python_code": {"type": "string", "description": "Complete BaseTool subclass python code"}
            },
            "required": ["tool_name", "python_code"]
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        name = kwargs.get("tool_name")
        code = kwargs.get("python_code")
        
        if not name or not code:
            return {"success": False, "error": "Missing name or code"}
            
        tool_path = Path(__file__).parent / f"{name}.py"
        try:
            tool_path.write_text(code, encoding="utf-8")
            return {"success": True, "result": f"Tool {name} created successfully. Will load on restart or dynamic reload."}
        except Exception as e:
            return {"success": False, "error": str(e)}
