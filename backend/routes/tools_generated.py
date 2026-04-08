"""
routes/tools_generated.py — Self-Extending Tools API
=====================================================
Create, validate, reload, and remove dynamically generated tools.
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("localmind.routes.tools_generated")

router = APIRouter(prefix="/api/tools/generated", tags=["tools-generated"])


# ── Pydantic Models ────────────────────────────────────────────────────

class ToolCreate(BaseModel):
    tool_name: str
    python_code: str
    description: str = ""


class CodeValidate(BaseModel):
    python_code: str


class TemplateRequest(BaseModel):
    tool_name: str
    description: str = ""
    param_schema: dict = Field(default_factory=dict)


# ── Endpoints ──────────────────────────────────────────────────────────


@router.get("")
async def list_generated_tools():
    """List all generated tools."""
    try:
        from backend.tools.tool_generator import get_generator
        gen = get_generator()
        tools = gen.list_generated_tools()
        return {"tools": tools, "count": len(tools)}
    except Exception as e:
        logger.exception("list_generated_tools failed")
        return {"tools": [], "count": 0, "error": str(e)}


@router.post("")
async def create_tool(req: ToolCreate):
    """Create (generate) a new tool from user-supplied Python code."""
    try:
        from backend.tools.tool_generator import get_generator
        gen = get_generator()

        # Validate first
        validation = gen.validate_code(req.python_code)
        if not validation.get("valid"):
            return {
                "ok": False,
                "error": "Code validation failed",
                "details": validation.get("errors", []),
            }

        result = gen.generate_tool(
            tool_name=req.tool_name,
            code=req.python_code,
            description=req.description,
            requested_by="user",
        )

        if result.get("ok"):
            return {"ok": True, "tool": result}
        return {"ok": False, "error": result.get("error", "Unknown error")}
    except Exception as e:
        logger.exception("create_tool failed")
        return {"ok": False, "error": str(e)}


@router.post("/validate")
async def validate_code(req: CodeValidate):
    """Validate tool code without saving."""
    try:
        from backend.tools.tool_generator import get_generator
        gen = get_generator()
        result = gen.validate_code(req.python_code)
        return result
    except Exception as e:
        logger.exception("validate_code failed")
        return {"valid": False, "errors": [str(e)]}


@router.post("/template")
async def generate_template(req: TemplateRequest):
    """Generate boilerplate code for a new tool."""
    try:
        from backend.tools.tool_generator import get_generator
        gen = get_generator()
        code = gen.get_template(
            tool_name=req.tool_name,
            tool_description=req.description,
            param_schema=req.param_schema,
        )
        return {"ok": True, "code": code}
    except Exception as e:
        logger.exception("generate_template failed")
        return {"ok": False, "error": str(e)}


@router.delete("/{tool_name}")
async def remove_tool(tool_name: str):
    """Remove a generated tool by name."""
    try:
        from backend.tools.tool_generator import get_generator
        gen = get_generator()
        removed = gen.remove_tool(tool_name)
        if removed:
            return {"ok": True}
        return {"ok": False, "error": f"Tool '{tool_name}' not found"}
    except Exception as e:
        logger.exception("remove_tool(%s) failed", tool_name)
        return {"ok": False, "error": str(e)}


@router.post("/{tool_name}/reload")
async def reload_tool(tool_name: str):
    """Reload a generated tool (hot-swap into memory)."""
    try:
        from backend.tools.tool_generator import get_generator
        gen = get_generator()
        loaded = gen.load_tool(tool_name)
        if loaded is not None:
            return {"ok": True}
        return {"ok": False, "error": f"Failed to load tool '{tool_name}'"}
    except Exception as e:
        logger.exception("reload_tool(%s) failed", tool_name)
        return {"ok": False, "error": str(e)}
