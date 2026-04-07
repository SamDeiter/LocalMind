"""
ToolMaker — Lets the ReAct agent create new tools at runtime.

Inspired by arXiv:2502.11705 (ToolMaker): agents that can define their own
tools are more capable on novel tasks.  This module provides:

- DynamicTool: a Tool subclass backed by a runtime-generated async function
- ToolMakerTool: a meta-tool the agent invokes to create new DynamicTools
- Code validation via ast to block dangerous operations before execution

Security approach:
  The code provided by the LLM is parsed with ast and checked for disallowed
  constructs (imports of dangerous modules, calls to exec/eval/os.system/etc).
  It then runs inside a restricted namespace that only exposes safe stdlib
  modules plus httpx.  This is a practical prototype guard, not a full sandbox.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import types
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from .base import Tool

if TYPE_CHECKING:
    from ..core import Agent

logger = logging.getLogger("agent.tools.toolmaker")

# ---------------------------------------------------------------------------
# Allowed / blocked lists
# ---------------------------------------------------------------------------

# Modules available inside dynamic tool code
ALLOWED_MODULES = {
    "json": __import__("json"),
    "re": __import__("re"),
    "math": __import__("math"),
    "datetime": __import__("datetime"),
    "collections": __import__("collections"),
    "itertools": __import__("itertools"),
    "functools": __import__("functools"),
    "pathlib": __import__("pathlib"),
}

# We lazily add httpx at execution time so the import is deferred
_HTTPX_SENTINEL = object()

# Names that must never appear as function calls or attribute accesses
BLOCKED_NAMES = frozenset({
    "os",
    "subprocess",
    "shutil",
    "sys",
    "importlib",
    "ctypes",
    "socket",
    "signal",
    "multiprocessing",
    "threading",
    "pickle",
    "shelve",
    "marshal",
    "code",
    "codeop",
    "compile",
    "compileall",
})

BLOCKED_BUILTINS = frozenset({
    "eval",
    "exec",
    "compile",
    "__import__",
    "globals",
    "locals",
    "breakpoint",
    "exit",
    "quit",
    "input",
})


# ---------------------------------------------------------------------------
# AST-based code validation
# ---------------------------------------------------------------------------

class _SafetyVisitor(ast.NodeVisitor):
    """Walk the AST and raise ValueError on disallowed constructs."""

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in ALLOWED_MODULES:
                raise ValueError(f"Import of '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top = node.module.split(".")[0]
            if top not in ALLOWED_MODULES:
                raise ValueError(f"Import from '{node.module}' is not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Block direct calls to dangerous builtins: eval(...), exec(...)
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_BUILTINS:
            raise ValueError(f"Call to '{node.func.id}()' is not allowed")
        # Block os.system(...), subprocess.run(...), etc.
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                if node.func.value.id in BLOCKED_NAMES:
                    raise ValueError(
                        f"Access to '{node.func.value.id}.{node.func.attr}' is not allowed"
                    )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Block attribute access on blocked modules even without a call
        if isinstance(node.value, ast.Name) and node.value.id in BLOCKED_NAMES:
            raise ValueError(
                f"Access to '{node.value.id}.{node.attr}' is not allowed"
            )
        # Block dunder attribute access (__class__, __subclasses__, etc.)
        if node.attr.startswith("__") and node.attr.endswith("__"):
            # Allow __init__ and __name__ which are benign
            if node.attr not in ("__init__", "__name__", "__doc__"):
                raise ValueError(f"Access to dunder '{node.attr}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BLOCKED_BUILTINS:
            # Just referencing these names (not calling) is suspicious
            # but we only hard-block when they appear as a call target
            pass
        self.generic_visit(node)


def _validate_code_safety(code: str) -> ast.Module:
    """Parse and validate code, returning the AST on success.

    Raises ValueError with a descriptive message if the code is unsafe.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"Syntax error in tool code: {exc}") from exc

    _SafetyVisitor().visit(tree)
    return tree


# ---------------------------------------------------------------------------
# Build the async executor function from code string
# ---------------------------------------------------------------------------

def _build_executor(code: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Wrap user-provided code body into an async function.

    The code is the BODY of an async function that receives **kwargs
    matching the declared parameters.  It must return a dict with
    'success' and 'result' keys.
    """
    # Indent the user code to sit inside the async def
    indented = "\n".join("    " + line for line in code.splitlines())

    wrapper = f"""\
async def _dynamic_tool_fn(**kwargs):
{indented}
"""

    # Build a restricted namespace
    namespace: dict[str, Any] = {}

    # Safe builtins: standard builtins minus the dangerous ones
    import builtins as _builtins

    safe_builtins = {
        k: v
        for k, v in vars(_builtins).items()
        if k not in BLOCKED_BUILTINS and not k.startswith("_")
    }
    # Re-add some useful dunders
    safe_builtins["__name__"] = "__dynamic_tool__"
    safe_builtins["__build_class__"] = _builtins.__build_class__

    # Provide a safe open that only allows reading within workspace
    from ..config import WORKSPACE_DIR as _ws

    def _safe_open(path: str, mode: str = "r", **kw: Any) -> Any:
        from pathlib import Path as _P

        resolved = _P(path)
        if not resolved.is_absolute():
            resolved = _ws / resolved
        resolved = resolved.resolve()
        if not str(resolved).startswith(str(_ws.resolve())):
            raise PermissionError(f"Cannot access path outside workspace: {path}")
        if mode not in ("r", "rb"):
            raise PermissionError(f"Only read mode is allowed, got '{mode}'")
        return open(resolved, mode, **kw)  # noqa: SIM115

    safe_builtins["open"] = _safe_open

    namespace["__builtins__"] = safe_builtins

    # Inject allowed modules
    for mod_name, mod in ALLOWED_MODULES.items():
        namespace[mod_name] = mod

    # Inject httpx (lazy)
    try:
        import httpx
        namespace["httpx"] = httpx
    except ImportError:
        pass  # httpx not installed — tool code that needs it will fail at runtime

    # Inject pathlib.Path shortcut
    namespace["Path"] = __import__("pathlib").Path

    # Compile and exec the wrapper to define the function in namespace
    compiled = compile(wrapper, "<dynamic_tool>", "exec")
    exec(compiled, namespace)  # noqa: S102 — intentional; code was AST-validated

    fn = namespace["_dynamic_tool_fn"]
    return fn


# ---------------------------------------------------------------------------
# DynamicTool
# ---------------------------------------------------------------------------

class DynamicTool(Tool):
    """A Tool backed by a runtime-generated async function."""

    def __init__(
        self,
        tool_name: str,
        tool_description: str,
        tool_parameters: dict[str, Any],
        executor: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None:
        self._name = tool_name
        self._description = tool_description
        self._parameters = tool_parameters
        self._executor = executor

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return self._parameters

    async def _execute(self, **kwargs: Any) -> dict[str, Any]:
        result = await self._executor(**kwargs)
        # Ensure the result matches the expected shape
        if not isinstance(result, dict):
            return {"success": True, "result": result}
        if "success" not in result:
            result["success"] = True
        return result


# ---------------------------------------------------------------------------
# ToolMakerTool — the meta-tool
# ---------------------------------------------------------------------------

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


class ToolMakerTool(Tool):
    """Meta-tool that lets the agent create new tools at runtime.

    The agent calls this tool with a name, description, parameter schema,
    and Python code body.  ToolMaker validates the code for safety,
    wraps it in a DynamicTool, registers it with the agent, and persists
    the definition to disk for future sessions.
    """

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    @property
    def name(self) -> str:
        return "create_tool"

    @property
    def description(self) -> str:
        return (
            "Create a new tool at runtime. Provide a snake_case name, description, "
            "JSON Schema parameters, and an async Python function body. The function "
            "receives **kwargs matching the parameters and must return a dict with "
            "'success' and 'result' keys."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Snake_case name for the new tool (e.g., 'parse_csv')",
                },
                "tool_description": {
                    "type": "string",
                    "description": "One-line description of what the tool does",
                },
                "tool_parameters": {
                    "type": "object",
                    "description": (
                        "JSON Schema for the tool's parameters. Must be an object "
                        "with 'type', 'properties', and optionally 'required'."
                    ),
                },
                "tool_code": {
                    "type": "string",
                    "description": (
                        "Python async function body. Gets **kwargs matching parameters. "
                        "Must return {'success': True, 'result': ...}. "
                        "Available modules: json, re, math, datetime, collections, "
                        "itertools, functools, pathlib.Path, httpx."
                    ),
                },
            },
            "required": ["tool_name", "tool_description", "tool_parameters", "tool_code"],
        }

    async def _execute(self, **kwargs: Any) -> dict[str, Any]:
        tool_name: str = kwargs["tool_name"]
        tool_description: str = kwargs["tool_description"]
        tool_parameters: dict = kwargs["tool_parameters"]
        tool_code: str = kwargs["tool_code"]

        # --- 1. Validate tool name ---
        if not _SNAKE_CASE_RE.match(tool_name):
            return {
                "success": False,
                "error": (
                    f"Invalid tool name '{tool_name}'. "
                    "Must be snake_case (e.g., 'parse_csv')."
                ),
            }

        if tool_name in self._agent.tools:
            return {
                "success": False,
                "error": (
                    f"Tool '{tool_name}' already exists. "
                    "Choose a different name."
                ),
            }

        # --- 2. Validate parameter schema ---
        if not isinstance(tool_parameters, dict):
            return {
                "success": False,
                "error": "tool_parameters must be a JSON Schema object.",
            }
        if tool_parameters.get("type") != "object":
            # Auto-wrap if they forgot the outer type
            tool_parameters = {
                "type": "object",
                "properties": tool_parameters.get("properties", {}),
                "required": tool_parameters.get("required", []),
            }

        # --- 3. Validate code safety ---
        try:
            _validate_code_safety(tool_code)
        except ValueError as exc:
            return {
                "success": False,
                "error": f"Code safety check failed: {exc}",
            }

        # --- 4. Build executor ---
        try:
            executor = _build_executor(tool_code)
        except Exception as exc:
            return {
                "success": False,
                "error": f"Failed to compile tool code: {exc}",
            }

        # --- 5. Create DynamicTool and register ---
        dynamic_tool = DynamicTool(
            tool_name=tool_name,
            tool_description=tool_description,
            tool_parameters=tool_parameters,
            executor=executor,
        )
        self._agent.register_tool(dynamic_tool)

        # --- 6. Persist to disk ---
        try:
            from .dynamic_loader import save_tool_definition

            save_tool_definition(
                name=tool_name,
                description=tool_description,
                parameters=tool_parameters,
                code=tool_code,
            )
        except Exception as exc:
            logger.error(f"Failed to persist tool definition: {exc}")
            # Tool is still registered in memory, just won't survive restart

        logger.info(f"Created dynamic tool: {tool_name}")
        return {
            "success": True,
            "result": (
                f"Tool '{tool_name}' created and registered successfully. "
                f"It is now available for use."
            ),
        }
