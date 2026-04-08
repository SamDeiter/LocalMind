"""
Self-Extending Tools Engine.

Safely generates, validates, and loads tool plugins at runtime.
Generated tools live in backend/tools/generated/ and are recorded in the
``generated_tools`` SQLite table for persistence and management.
"""

import ast
import hashlib
import importlib
import importlib.util
import inspect
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from backend.config import DB_PATH, PROJECT_ROOT, WORKSPACE_ROOT

logger = logging.getLogger("localmind.tools.generator")

GENERATED_DIR = Path(__file__).parent / "generated"

# ---------------------------------------------------------------------------
# Patterns that are flat-out rejected in generated code.
# We block truly dangerous operations — not all stdlib usage.
# ---------------------------------------------------------------------------
BLOCKED_CALLS: list[str] = [
    "os.system",
    "os.popen",
    "os.exec",
    "os.execl",
    "os.execle",
    "os.execlp",
    "os.execlpe",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.spawn",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "shutil.rmtree",
    "shutil.move",
    "eval(",
    "exec(",
    "__import__(",
    "compile(",
]

BLOCKED_IMPORTS: set[str] = {
    "subprocess",
    "shutil",
    "ctypes",
    "socket",
    "multiprocessing",
    "signal",
    "pty",
    "resource",
    "fcntl",
    "termios",
    "mmap",
    "webbrowser",
}


class ToolGenerator:
    """Core engine for safe runtime generation of BaseTool plugins."""

    def __init__(self) -> None:
        self.db_path = str(DB_PATH)
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # DB helpers (short-lived connections, WAL mode, FK ON)
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # 1. validate_code
    # ------------------------------------------------------------------
    def validate_code(self, code: str) -> dict[str, Any]:
        """Parse *code* and apply structural + safety checks.

        Returns ``{"valid": True, "class_name": ..., "tool_name": ...}``
        or ``{"valid": False, "errors": [...]}``.
        """
        errors: list[str] = []

        # --- Syntax check ---
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return {"valid": False, "errors": [f"SyntaxError: {exc}"]}

        # --- Structural: exactly one BaseTool subclass ---
        classes: list[ast.ClassDef] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_name = ""
                    if isinstance(base, ast.Name):
                        base_name = base.id
                    elif isinstance(base, ast.Attribute):
                        base_name = base.attr
                    if base_name == "BaseTool":
                        classes.append(node)

        if len(classes) == 0:
            errors.append("No class subclassing BaseTool found.")
        elif len(classes) > 1:
            errors.append(
                f"Expected exactly 1 BaseTool subclass, found {len(classes)}: "
                f"{[c.name for c in classes]}"
            )

        if errors:
            return {"valid": False, "errors": errors}

        cls_node = classes[0]

        # --- Check required members ---
        required_properties = {"name", "description", "parameters"}
        required_methods = {"execute"}

        found_properties: set[str] = set()
        found_methods: set[str] = set()

        for item in cls_node.body:
            # Properties are FunctionDefs decorated with @property
            if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
                is_property = any(
                    (isinstance(d, ast.Name) and d.id == "property")
                    or (isinstance(d, ast.Attribute) and d.attr == "property")
                    for d in item.decorator_list
                )
                if is_property and item.name in required_properties:
                    found_properties.add(item.name)
                elif item.name in required_methods:
                    found_methods.add(item.name)

        missing_props = required_properties - found_properties
        missing_methods = required_methods - found_methods

        if missing_props:
            errors.append(f"Missing required properties: {sorted(missing_props)}")
        if missing_methods:
            errors.append(f"Missing required methods: {sorted(missing_methods)}")

        # --- Safety: blocked patterns in raw source ---
        for pattern in BLOCKED_CALLS:
            if pattern in code:
                errors.append(f"Blocked pattern detected: {pattern!r}")

        # --- Safety: blocked imports ---
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in BLOCKED_IMPORTS:
                        errors.append(f"Blocked import: {alias.name!r}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    if top in BLOCKED_IMPORTS:
                        errors.append(f"Blocked import: {node.module!r}")

        # --- Safety: reject open() unless it references WORKSPACE_ROOT ---
        if "open(" in code:
            # Allow only if WORKSPACE_ROOT guard is present nearby
            if "WORKSPACE_ROOT" not in code:
                errors.append(
                    "open() detected without WORKSPACE_ROOT context. "
                    "File I/O must be scoped to WORKSPACE_ROOT."
                )

        if errors:
            return {"valid": False, "errors": errors}

        # Derive tool_name from the class's name property (fall back to class name)
        tool_name = cls_node.name
        for item in cls_node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "name":
                # Try to extract the return value string
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
                        tool_name = str(stmt.value.value)
                        break

        return {"valid": True, "class_name": cls_node.name, "tool_name": tool_name}

    # ------------------------------------------------------------------
    # 2. generate_tool
    # ------------------------------------------------------------------
    def generate_tool(
        self,
        tool_name: str,
        code: str,
        description: str = "",
        requested_by: str = "system",
    ) -> dict[str, Any]:
        """Validate *code*, persist it to disk, and register in SQLite.

        Returns ``{"ok": True, ...}`` or ``{"ok": False, "error": ...}``.
        """
        # Sanitise tool_name to a valid filename
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name)
        if not safe_name or safe_name[0].isdigit():
            return {"ok": False, "error": f"Invalid tool name: {tool_name!r}"}

        validation = self.validate_code(code)
        if not validation["valid"]:
            return {"ok": False, "error": "; ".join(validation["errors"])}

        file_path = GENERATED_DIR / f"{safe_name}.py"
        code_hash = hashlib.sha256(code.encode()).hexdigest()

        try:
            file_path.write_text(code, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": f"Failed to write file: {exc}"}

        # Persist metadata
        tool_id = str(uuid.uuid4())
        now = time.time()
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO generated_tools
                    (id, tool_name, description, file_path, code_hash, status,
                     requested_by, created_at, last_loaded_at, error_log)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?, NULL, NULL)
                ON CONFLICT(tool_name) DO UPDATE SET
                    description = excluded.description,
                    file_path = excluded.file_path,
                    code_hash = excluded.code_hash,
                    status = 'active',
                    requested_by = excluded.requested_by,
                    error_log = NULL
                """,
                (tool_id, safe_name, description, str(file_path), code_hash,
                 requested_by, now),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.error("DB error recording generated tool %s: %s", safe_name, exc)
            return {"ok": False, "error": f"Database error: {exc}"}

        logger.info("Generated tool '%s' -> %s", safe_name, file_path)
        return {
            "ok": True,
            "tool_name": safe_name,
            "file_path": str(file_path),
            "class_name": validation["class_name"],
            "code_hash": code_hash,
        }

    # ------------------------------------------------------------------
    # 3. load_tool
    # ------------------------------------------------------------------
    def load_tool(self, tool_name: str) -> Any | None:
        """Dynamically import a generated module and return an instance of
        its BaseTool subclass, or *None* on failure."""
        from backend.tools.base import BaseTool  # local to avoid circular

        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name)
        module_path = GENERATED_DIR / f"{safe_name}.py"

        if not module_path.exists():
            logger.warning("Generated tool file not found: %s", module_path)
            return None

        module_name = f"backend.tools.generated.{safe_name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(module_path))
            if spec is None or spec.loader is None:
                logger.error("Cannot create module spec for %s", module_path)
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find the BaseTool subclass
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BaseTool) and obj is not BaseTool:
                    instance = obj()
                    # Update last_loaded_at
                    try:
                        conn = self._connect()
                        conn.execute(
                            "UPDATE generated_tools SET last_loaded_at = ?, status = 'active' "
                            "WHERE tool_name = ?",
                            (time.time(), safe_name),
                        )
                        conn.commit()
                        conn.close()
                    except Exception as exc:
                        logger.warning("Failed to update last_loaded_at for %s: %s", safe_name, exc)
                    return instance

            logger.error("No BaseTool subclass found in %s", module_path)
            return None

        except Exception as exc:
            logger.error("Failed to load generated tool '%s': %s", safe_name, exc)
            # Record the failure
            try:
                conn = self._connect()
                conn.execute(
                    "UPDATE generated_tools SET status = 'failed', error_log = ? "
                    "WHERE tool_name = ?",
                    (str(exc), safe_name),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
            return None

    # ------------------------------------------------------------------
    # 4. list_generated_tools
    # ------------------------------------------------------------------
    def list_generated_tools(self) -> list[dict[str, Any]]:
        """Return all rows from the generated_tools table."""
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, tool_name, description, file_path, code_hash, status, "
                "requested_by, created_at, last_loaded_at, error_log "
                "FROM generated_tools ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.error("Failed to list generated tools: %s", exc)
            return []

    # ------------------------------------------------------------------
    # 5. remove_tool
    # ------------------------------------------------------------------
    def remove_tool(self, tool_name: str) -> bool:
        """Delete the .py file and remove the DB record. Returns True on success."""
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name)
        file_path = GENERATED_DIR / f"{safe_name}.py"

        deleted_file = False
        if file_path.exists():
            try:
                file_path.unlink()
                deleted_file = True
            except OSError as exc:
                logger.error("Could not delete %s: %s", file_path, exc)
                return False

        try:
            conn = self._connect()
            cursor = conn.execute(
                "DELETE FROM generated_tools WHERE tool_name = ?", (safe_name,)
            )
            conn.commit()
            deleted_row = cursor.rowcount > 0
            conn.close()
        except Exception as exc:
            logger.error("DB error removing generated tool %s: %s", safe_name, exc)
            return False

        success = deleted_file or deleted_row
        if success:
            logger.info("Removed generated tool '%s'", safe_name)
        return success

    # ------------------------------------------------------------------
    # 6. get_template
    # ------------------------------------------------------------------
    def get_template(
        self, tool_name: str, tool_description: str, param_schema: dict[str, Any]
    ) -> str:
        """Return a ready-to-fill BaseTool subclass template as a Python string."""
        class_name = "".join(
            word.capitalize() for word in re.sub(r"[^a-zA-Z0-9]", " ", tool_name).split()
        )
        if not class_name:
            class_name = "GeneratedTool"
        class_name += "Tool"

        # Pretty-print the param schema
        schema_str = json.dumps(param_schema, indent=12)

        return f'''"""
Auto-generated tool: {tool_name}
{tool_description}
"""

from typing import Any
from backend.tools.base import BaseTool
from backend.config import WORKSPACE_ROOT


class {class_name}(BaseTool):
    """Generated tool plugin — {tool_description}"""

    @property
    def name(self) -> str:
        return {tool_name!r}

    @property
    def description(self) -> str:
        return {tool_description!r}

    @property
    def parameters(self) -> dict:
        return {schema_str}

    async def execute(self, **kwargs) -> dict[str, Any]:
        # TODO: Implement tool logic here.
        # Available kwargs are defined by the parameters schema above.
        # Must return {{"result": "...", "success": True/False}}
        try:
            # --- Your implementation goes here ---
            result = "Not yet implemented"
            return {{"success": True, "result": result}}
        except Exception as exc:
            return {{"success": False, "error": str(exc)}}
'''


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_generator: ToolGenerator | None = None


def get_generator() -> ToolGenerator:
    """Return (or create) the module-level ToolGenerator singleton."""
    global _generator
    if _generator is None:
        _generator = ToolGenerator()
    return _generator
