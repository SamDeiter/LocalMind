"""
Self-Edit Tools — Read and modify LocalMind's own source code.

Safety:
- self_read: No restrictions, read-only access to project files
- self_edit: REQUIRES prior propose_action approval
- Blocked paths: .env, *.key, *.pem, *.secret, memory_db/
- Max edit size: 10KB
- Auto-creates .bak backup before editing
"""

import logging
import shutil
import time
from pathlib import Path

from .base import BaseTool

logger = logging.getLogger("localmind.tools.self_edit")

# LocalMind project root (two levels up from this file)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Patterns that are NEVER editable
BLOCKED_PATTERNS = {
    ".env", ".env.local", ".env.production",
}
BLOCKED_EXTENSIONS = {".key", ".pem", ".secret", ".p12", ".pfx"}
BLOCKED_DIRECTORIES = {"memory_db", ".git", "node_modules", "__pycache__", "venv"}

MAX_EDIT_SIZE = 10_240  # 10 KB


def _validate_self_path(filepath: str) -> Path:
    """Resolve a path relative to the project root with security checks.

    Raises ValueError if the path is blocked or escapes the project.
    """
    target = (PROJECT_ROOT / filepath).resolve()

    # Must stay inside project
    if not str(target).startswith(str(PROJECT_ROOT)):
        raise ValueError(f"Path escapes project: {filepath}")

    # Check blocked filenames
    if target.name in BLOCKED_PATTERNS:
        raise ValueError(f"Blocked file: {target.name} (security policy)")

    # Check blocked extensions
    if target.suffix.lower() in BLOCKED_EXTENSIONS:
        raise ValueError(f"Blocked extension: {target.suffix} (security policy)")

    # Check blocked directories
    for part in target.relative_to(PROJECT_ROOT).parts:
        if part in BLOCKED_DIRECTORIES:
            raise ValueError(f"Blocked directory: {part} (security policy)")

    return target


class SelfReadTool(BaseTool):
    """Read any file in the LocalMind project (read-only, no approval needed)."""

    @property
    def name(self) -> str:
        return "self_read"

    @property
    def description(self) -> str:
        return (
            "Read a file from LocalMind's own source code. "
            "Path is relative to the project root (e.g., 'backend/routes/chat.py'). "
            "Use this to understand your own code before making improvements."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file (e.g., 'backend/routes/chat.py' or 'frontend/app.js')",
                }
            },
            "required": ["path"],
        }

    async def execute(self, path: str = "", **kwargs) -> dict:
        try:
            target = _validate_self_path(path)
            if not target.exists():
                return {"success": False, "error": f"File not found: {path}"}
            if target.is_dir():
                # List directory contents
                entries = []
                for entry in sorted(target.iterdir()):
                    rel = entry.relative_to(PROJECT_ROOT)
                    prefix = "📁" if entry.is_dir() else "📄"
                    entries.append(f"{prefix} {rel}")
                return {
                    "success": True,
                    "result": f"Directory listing for {path}:\n" + "\n".join(entries[:50]),
                }

            content = target.read_text(encoding="utf-8", errors="replace")
            if len(content) > 50_000:
                content = content[:50_000] + "\n\n... [truncated — file too large]"

            return {
                "success": True,
                "result": content,
                "path": str(target.relative_to(PROJECT_ROOT)),
                "size": len(content),
            }
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"Read failed: {exc}"}


class SelfEditTool(BaseTool):
    """Write or patch a file in the LocalMind project.

    REQUIRES prior propose_action approval. Creates a .bak backup.
    """

    @property
    def name(self) -> str:
        return "self_edit"

    @property
    def description(self) -> str:
        return (
            "Edit a file in LocalMind's own source code. "
            "You MUST call propose_action FIRST and get approval before using this tool. "
            "Path is relative to the project root. Creates a backup before editing. "
            "Use self_test after editing to validate your changes."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file (e.g., 'backend/tools/memory.py')",
                },
                "content": {
                    "type": "string",
                    "description": "The new content to write to the file",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what you changed and why",
                },
            },
            "required": ["path", "content", "description"],
        }

    async def execute(self, path: str = "", content: str = "", description: str = "", **kwargs) -> dict:
        if not content.strip():
            return {"success": False, "error": "Content cannot be empty"}

        if len(content) > MAX_EDIT_SIZE:
            return {
                "success": False,
                "error": f"Edit too large: {len(content)} bytes (max {MAX_EDIT_SIZE}). Break into smaller edits.",
            }

        try:
            target = _validate_self_path(path)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        try:
            # Create backup if file exists
            if target.exists():
                backup = target.with_suffix(target.suffix + ".bak")
                shutil.copy2(target, backup)
                logger.info(f"Backed up {path} -> {backup.name}")

            # Write the new content
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

            logger.info(f"Self-edit applied: {path} ({len(content)} chars) — {description}")

            return {
                "success": True,
                "result": (
                    f"✅ File edited: {path} ({len(content)} chars)\n"
                    f"📝 Change: {description}\n"
                    f"💾 Backup created: {target.name}.bak\n"
                    f"⚠️ Run self_test to validate your changes!"
                ),
                "path": str(target.relative_to(PROJECT_ROOT)),
            }
        except Exception as exc:
            return {"success": False, "error": f"Edit failed: {exc}"}


class SelfListTool(BaseTool):
    """List files in the LocalMind project directory."""

    @property
    def name(self) -> str:
        return "self_list"

    @property
    def description(self) -> str:
        return (
            "List files and directories in LocalMind's own project. "
            "Path is relative to the project root. Use this to explore your own codebase."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to list (default: project root)",
                    "default": ".",
                }
            },
        }

    async def execute(self, path: str = ".", **kwargs) -> dict:
        try:
            target = _validate_self_path(path)
            if not target.exists():
                return {"success": False, "error": f"Path not found: {path}"}
            if not target.is_dir():
                return {"success": False, "error": f"Not a directory: {path}"}

            entries = []
            for entry in sorted(target.iterdir()):
                # Skip hidden/blocked dirs
                if entry.name.startswith(".") or entry.name in BLOCKED_DIRECTORIES:
                    continue
                rel = entry.relative_to(PROJECT_ROOT)
                if entry.is_dir():
                    entries.append(f"📁 {rel}/")
                else:
                    size = entry.stat().st_size
                    if size < 1024:
                        s = f"{size} B"
                    elif size < 1024 * 1024:
                        s = f"{size / 1024:.1f} KB"
                    else:
                        s = f"{size / (1024 * 1024):.1f} MB"
                    entries.append(f"📄 {rel} ({s})")

            return {
                "success": True,
                "result": "\n".join(entries) if entries else "Empty directory.",
            }
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"List failed: {exc}"}
