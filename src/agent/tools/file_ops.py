"""
File operation tools — read, write, search, list.

All paths are sandboxed to a workspace directory to prevent arbitrary
filesystem access (arXiv:2512.08769 — safety/governance patterns).
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from ..config import WORKSPACE_DIR
from .base import Tool


def _validate_path(path: str) -> Path:
    """Resolve a path within the workspace sandbox.

    Raises ValueError if the resolved path escapes the workspace.
    """
    workspace = WORKSPACE_DIR.resolve()
    # Handle both absolute and relative paths
    target = Path(path)
    if not target.is_absolute():
        target = workspace / target
    resolved = target.resolve()

    if not str(resolved).startswith(str(workspace)):
        raise ValueError(f"Path escapes sandbox: {path}")
    return resolved


class FileReadTool(Tool):
    """Read the contents of a file."""

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read the full contents of a file. Returns the text content."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file (relative to workspace or absolute)",
                },
                "line_start": {
                    "type": "integer",
                    "description": "Optional: start reading from this line number (1-based)",
                },
                "line_end": {
                    "type": "integer",
                    "description": "Optional: stop reading at this line number (inclusive)",
                },
            },
            "required": ["path"],
        }

    async def _execute(self, **kwargs) -> dict[str, Any]:
        path = _validate_path(kwargs["path"])
        if not path.exists():
            return {"success": False, "error": f"File not found: {kwargs['path']}"}
        if not path.is_file():
            return {"success": False, "error": f"Not a file: {kwargs['path']}"}

        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)

        line_start = kwargs.get("line_start")
        line_end = kwargs.get("line_end")
        if line_start or line_end:
            start = max(0, (line_start or 1) - 1)
            end = line_end or len(lines)
            lines = lines[start:end]
            text = "".join(lines)

        return {
            "success": True,
            "result": text,
            "line_count": len(lines),
            "path": str(path),
        }


class FileWriteTool(Tool):
    """Write content to a file (creates parent directories if needed)."""

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return "Write text content to a file. Creates the file and parent directories if they don't exist."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file (relative to workspace or absolute)",
                },
                "content": {
                    "type": "string",
                    "description": "The text content to write",
                },
                "append": {
                    "type": "boolean",
                    "description": "If true, append to the file instead of overwriting. Default false.",
                },
            },
            "required": ["path", "content"],
        }

    async def _execute(self, **kwargs) -> dict[str, Any]:
        path = _validate_path(kwargs["path"])
        path.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if kwargs.get("append") else "w"
        path.write_text(kwargs["content"], encoding="utf-8") if mode == "w" else \
            path.open("a", encoding="utf-8").write(kwargs["content"])

        return {
            "success": True,
            "result": f"{'Appended to' if mode == 'a' else 'Wrote'} {path} ({len(kwargs['content'])} chars)",
            "path": str(path),
        }


class FileSearchTool(Tool):
    """Search for files by name pattern and optionally grep content."""

    @property
    def name(self) -> str:
        return "file_search"

    @property
    def description(self) -> str:
        return (
            "Search for files matching a glob pattern, optionally filtering by content. "
            "Returns matching file paths and line matches."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match filenames (e.g., '*.py', '**/*.js')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: workspace root)",
                },
                "content_pattern": {
                    "type": "string",
                    "description": "Optional regex to filter files by content",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 50)",
                },
            },
            "required": ["pattern"],
        }

    async def _execute(self, **kwargs) -> dict[str, Any]:
        search_dir = _validate_path(kwargs.get("path", "."))
        if not search_dir.is_dir():
            return {"success": False, "error": f"Not a directory: {kwargs.get('path', '.')}"}

        pattern = kwargs["pattern"]
        content_pat = kwargs.get("content_pattern")
        max_results = kwargs.get("max_results", 50)

        matches = []
        try:
            content_re = re.compile(content_pat) if content_pat else None
        except re.error as e:
            return {"success": False, "error": f"Invalid regex: {e}"}

        for root, _dirs, files in os.walk(search_dir):
            for fname in files:
                if not fnmatch.fnmatch(fname, pattern):
                    continue
                fpath = Path(root) / fname
                rel = fpath.relative_to(WORKSPACE_DIR.resolve())

                if content_re:
                    try:
                        text = fpath.read_text(encoding="utf-8", errors="replace")
                        line_matches = []
                        for i, line in enumerate(text.splitlines(), 1):
                            if content_re.search(line):
                                line_matches.append({"line": i, "text": line.strip()})
                        if line_matches:
                            matches.append({
                                "path": str(rel),
                                "matches": line_matches[:10],
                            })
                    except Exception:
                        continue
                else:
                    matches.append({"path": str(rel)})

                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break

        return {
            "success": True,
            "result": matches,
            "total_matches": len(matches),
        }


class FileListTool(Tool):
    """List directory contents."""

    @property
    def name(self) -> str:
        return "file_list"

    @property
    def description(self) -> str:
        return "List files and directories at a given path."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (default: workspace root)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "If true, list recursively. Default false.",
                },
            },
        }

    async def _execute(self, **kwargs) -> dict[str, Any]:
        target = _validate_path(kwargs.get("path", "."))
        if not target.is_dir():
            return {"success": False, "error": f"Not a directory: {kwargs.get('path', '.')}"}

        entries = []
        if kwargs.get("recursive"):
            for root, dirs, files in os.walk(target):
                rel_root = Path(root).relative_to(WORKSPACE_DIR.resolve())
                for d in dirs:
                    entries.append({"path": str(rel_root / d), "type": "dir"})
                for f in files:
                    fpath = Path(root) / f
                    entries.append({
                        "path": str(rel_root / f),
                        "type": "file",
                        "size": fpath.stat().st_size,
                    })
        else:
            for item in sorted(target.iterdir()):
                rel = item.relative_to(WORKSPACE_DIR.resolve())
                entry = {"path": str(rel), "type": "dir" if item.is_dir() else "file"}
                if item.is_file():
                    entry["size"] = item.stat().st_size
                entries.append(entry)

        return {
            "success": True,
            "result": entries,
            "count": len(entries),
        }
