"""
File Tools — Read, write, and list files in the sandboxed workspace.
NO delete operations. All paths validated against sandbox.
"""

import os
from pathlib import Path

from .base import BaseTool

# Sandbox: all file operations are restricted to this directory
WORKSPACE = Path.home() / "LocalMind_Workspace"


def _validate_path(filepath: str) -> Path:
    """Validate and resolve a path, ensuring it stays within the sandbox.

    Raises ValueError if the path escapes the sandbox.
    """
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    # Resolve to absolute path within workspace
    target = (WORKSPACE / filepath).resolve()

    # Security: ensure the resolved path is still inside the workspace
    if not str(target).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path escapes sandbox: {filepath}")

    # Reject symlinks that point outside workspace
    if target.is_symlink():
        real = target.resolve()
        if not str(real).startswith(str(WORKSPACE.resolve())):
            raise ValueError(f"Symlink escapes sandbox: {filepath}")

    return target


class ReadFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file from the workspace. Path is relative to ~/LocalMind_Workspace."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file within the workspace (e.g., 'hello.py' or 'projects/app.js')",
                }
            },
            "required": ["path"],
        }

    async def execute(self, path: str = "", **kwargs) -> dict:
        try:
            target = _validate_path(path)
            if not target.exists():
                return {"success": False, "error": f"File not found: {path}"}
            if not target.is_file():
                return {"success": False, "error": f"Not a file: {path}"}

            content = target.read_text(encoding="utf-8", errors="replace")
            # Truncate very large files
            if len(content) > 50_000:
                content = content[:50_000] + "\n\n... [truncated — file is very large]"

            return {"success": True, "result": content, "path": str(target)}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"Read failed: {exc}"}


class WriteFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write or create a file in the workspace. Path is relative to ~/LocalMind_Workspace. Cannot delete files."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path for the file (e.g., 'hello.py' or 'projects/app.js')",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str = "", content: str = "", **kwargs) -> dict:
        try:
            target = _validate_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {
                "success": True,
                "result": f"File written: {path} ({len(content)} chars)",
                "path": str(target),
            }
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"Write failed: {exc}"}


class ListFilesTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        return "List files and directories in the workspace. Path is relative to ~/LocalMind_Workspace."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to list (default: root of workspace)",
                    "default": ".",
                }
            },
        }

    async def execute(self, path: str = ".", **kwargs) -> dict:
        try:
            target = _validate_path(path)
            if not target.exists():
                return {"success": False, "error": f"Directory not found: {path}"}
            if not target.is_dir():
                return {"success": False, "error": f"Not a directory: {path}"}

            entries = []
            for entry in sorted(target.iterdir()):
                rel = entry.relative_to(WORKSPACE)
                if entry.is_dir():
                    count = sum(1 for _ in entry.rglob("*") if _.is_file())
                    entries.append(f"📁 {rel}/ ({count} files)")
                else:
                    size = entry.stat().st_size
                    if size < 1024:
                        size_str = f"{size} B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f} KB"
                    else:
                        size_str = f"{size / (1024 * 1024):.1f} MB"
                    entries.append(f"📄 {rel} ({size_str})")

            if not entries:
                return {"success": True, "result": "Workspace is empty.", "entries": []}

            return {
                "success": True,
                "result": "\n".join(entries),
                "entries": entries,
            }
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "error": f"List failed: {exc}"}
