"""
LocalMind — Tool Implementations
Defines all tools the agent can use: file operations, terminal, search.
Each tool function takes keyword arguments and returns a dict with the result.
"""

import os
import subprocess
import glob
import json
from pathlib import Path

# Maximum characters to return from file reads / command output
MAX_OUTPUT_CHARS = 50000
MAX_FILE_LINES = 500


def _sanitize_path(path: str, working_dir: str) -> Path:
    """
    Resolve a path relative to the working directory.
    Prevents path traversal attacks (e.g., ../../etc/passwd).
    """
    working = Path(working_dir).resolve()
    target = (working / path).resolve()

    # Ensure the resolved path is within the working directory
    if not str(target).startswith(str(working)):
        raise PermissionError(f"Access denied: path '{path}' is outside the working directory")

    return target


def read_file(path: str, working_dir: str) -> dict:
    """Read the contents of a file."""
    try:
        target = _sanitize_path(path, working_dir)
        if not target.exists():
            return {"success": False, "error": f"File not found: {path}"}
        if not target.is_file():
            return {"success": False, "error": f"Not a file: {path}"}

        content = target.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        truncated = len(lines) > MAX_FILE_LINES

        if truncated:
            content = "\n".join(lines[:MAX_FILE_LINES])
            content += f"\n\n... [truncated, showing first {MAX_FILE_LINES} of {len(lines)} lines]"

        return {
            "success": True,
            "path": str(target.relative_to(Path(working_dir).resolve())),
            "content": content[:MAX_OUTPUT_CHARS],
            "lines": min(len(lines), MAX_FILE_LINES),
            "total_lines": len(lines),
            "truncated": truncated,
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Failed to read file: {e}"}


def write_file(path: str, content: str, working_dir: str) -> dict:
    """Write content to a file. Creates parent directories if needed."""
    try:
        target = _sanitize_path(path, working_dir)

        # Create parent directories
        target.parent.mkdir(parents=True, exist_ok=True)

        existed = target.exists()
        target.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "path": str(target.relative_to(Path(working_dir).resolve())),
            "action": "updated" if existed else "created",
            "bytes": len(content.encode("utf-8")),
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Failed to write file: {e}"}


def list_directory(path: str = ".", working_dir: str = ".") -> dict:
    """List files and directories in a path."""
    try:
        target = _sanitize_path(path, working_dir)
        if not target.exists():
            return {"success": False, "error": f"Directory not found: {path}"}
        if not target.is_dir():
            return {"success": False, "error": f"Not a directory: {path}"}

        items = []
        for item in sorted(target.iterdir()):
            # Skip hidden files and common noisy dirs
            if item.name.startswith(".") or item.name in ("node_modules", "__pycache__", "venv", ".venv"):
                continue
            rel = str(item.relative_to(Path(working_dir).resolve()))
            if item.is_dir():
                child_count = sum(1 for _ in item.iterdir()) if item.is_dir() else 0
                items.append({"name": item.name, "type": "directory", "path": rel, "children": child_count})
            else:
                items.append({
                    "name": item.name,
                    "type": "file",
                    "path": rel,
                    "size": item.stat().st_size,
                })

        return {
            "success": True,
            "path": str(target.relative_to(Path(working_dir).resolve())) if target != Path(working_dir).resolve() else ".",
            "items": items[:100],  # Cap at 100 items
            "total": len(items),
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Failed to list directory: {e}"}


def run_command(command: str, working_dir: str) -> dict:
    """Execute a shell command in the working directory."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=working_dir,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        stdout = result.stdout[:MAX_OUTPUT_CHARS] if result.stdout else ""
        stderr = result.stderr[:MAX_OUTPUT_CHARS] if result.stderr else ""

        return {
            "success": result.returncode == 0,
            "command": command,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "command": command, "error": "Command timed out after 60 seconds"}
    except Exception as e:
        return {"success": False, "command": command, "error": f"Failed to run command: {e}"}


def search_files(pattern: str, path: str = ".", working_dir: str = ".") -> dict:
    """Search for a text pattern in files (like grep)."""
    try:
        target = _sanitize_path(path, working_dir)
        if not target.exists():
            return {"success": False, "error": f"Path not found: {path}"}

        matches = []
        search_path = target if target.is_dir() else target.parent

        # Binary extensions to skip
        skip_ext = {".pyc", ".pyo", ".exe", ".dll", ".so", ".bin", ".jpg", ".png",
                    ".gif", ".mp4", ".mp3", ".zip", ".tar", ".gz", ".pdf", ".woff",
                    ".woff2", ".ttf", ".eot", ".ico", ".svg"}

        for file_path in search_path.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() in skip_ext:
                continue
            # Skip hidden and noisy directories
            parts = file_path.relative_to(search_path).parts
            if any(p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".venv") for p in parts):
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for i, line in enumerate(content.split("\n"), 1):
                    if pattern.lower() in line.lower():
                        rel = str(file_path.relative_to(Path(working_dir).resolve()))
                        matches.append({
                            "file": rel,
                            "line": i,
                            "content": line.strip()[:200],
                        })
                        if len(matches) >= 50:
                            return {
                                "success": True,
                                "pattern": pattern,
                                "matches": matches,
                                "truncated": True,
                            }
            except (UnicodeDecodeError, PermissionError):
                continue

        return {
            "success": True,
            "pattern": pattern,
            "matches": matches,
            "truncated": False,
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Search failed: {e}"}


def web_search(query: str, **kwargs) -> dict:
    """Search the web using DuckDuckGo (no API key required)."""
    try:
        # Use DuckDuckGo HTML endpoint (no API key needed)
        import httpx

        url = "https://html.duckduckgo.com/html/"
        resp = httpx.post(url, data={"q": query}, timeout=10.0,
                          headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LocalMind/1.0"})

        # Basic extraction of results from HTML
        results = []
        text = resp.text

        # Simple regex-free parsing — find result snippets
        parts = text.split('class="result__a"')
        for part in parts[1:6]:  # Take top 5
            # Extract title
            title_end = part.find("</a>")
            title_start = part.find(">") + 1
            title = part[title_start:title_end].strip() if title_end > 0 else ""

            # Extract URL
            href_start = part.find('href="')
            href_end = part.find('"', href_start + 6) if href_start >= 0 else -1
            url = part[href_start + 6:href_end] if href_start >= 0 else ""

            # Extract snippet
            snippet_marker = 'class="result__snippet">'
            snip_start = part.find(snippet_marker)
            if snip_start >= 0:
                snip_start += len(snippet_marker)
                snip_end = part.find("</", snip_start)
                snippet = part[snip_start:snip_end].strip()
            else:
                snippet = ""

            if title:
                # Clean HTML tags from title and snippet
                import re
                title = re.sub(r"<[^>]+>", "", title)
                snippet = re.sub(r"<[^>]+>", "", snippet)
                results.append({"title": title, "url": url, "snippet": snippet})

        return {
            "success": True,
            "query": query,
            "results": results,
        }
    except Exception as e:
        return {"success": False, "query": query, "error": f"Web search failed: {e}"}


# ── Tool Definitions for Ollama ─────────────────────────────────────────────
# These are sent to Ollama so the model knows what tools are available.

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use this to understand code, check configurations, or read any text file in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path relative to the working directory (e.g., 'src/main.py' or 'README.md')",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates the file if it doesn't exist, or overwrites if it does. Use this to create scripts, edit code, or generate any text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path relative to the working directory",
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories. Use this to explore the project structure and find relevant files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to working directory. Use '.' for the current directory.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command. Use this to run scripts, install packages, perform git operations, compile code, run tests, or any other terminal command. The command runs in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute (e.g., 'python script.py', 'pip install requests', 'git status')",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a text pattern across files in the project (like grep). Use this to find function definitions, variable usage, imports, or any text pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The text pattern to search for (case-insensitive)",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in, relative to working directory. Defaults to '.' (entire project).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information. Use this to look up documentation, find code examples, research APIs, or answer questions that need up-to-date information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# ── Tool Executor ───────────────────────────────────────────────────────────
TOOL_FUNCTIONS = {
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
    "run_command": run_command,
    "search_files": search_files,
    "web_search": web_search,
}


def execute_tool(name: str, arguments: dict, working_dir: str) -> dict:
    """Execute a tool by name with the given arguments."""
    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return {"success": False, "error": f"Unknown tool: {name}"}

    # Inject working_dir for tools that need it
    if name in ("read_file", "write_file", "list_directory", "run_command", "search_files"):
        arguments["working_dir"] = working_dir

    try:
        return func(**arguments)
    except Exception as e:
        return {"success": False, "error": f"Tool execution failed: {e}"}
