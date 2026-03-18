"""
Project Context Tool — Load a directory tree to understand codebase structure.

Returns a formatted tree view with file sizes, suitable for feeding
to the AI as context about a project's layout.

Safety:
- All paths validated against ~/LocalMind_Workspace sandbox
- Skips common noise directories (.git, node_modules, etc.)
- Max depth and entry limits prevent runaway traversal
"""

from pathlib import Path

from .base import BaseTool

# Sandbox: same workspace as file tools
WORKSPACE = Path.home() / "LocalMind_Workspace"

# Directories and files to always skip
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv",
    "env", ".env", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".next", ".nuxt", "coverage",
}

SKIP_FILES = {".DS_Store", "Thumbs.db"}


def _validate_context_path(dir_path: str) -> Path:
    """Validate a directory path stays within the workspace sandbox.

    Raises ValueError if the path escapes the sandbox.
    """
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    target = (WORKSPACE / dir_path).resolve()

    if not str(target).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path escapes sandbox: {dir_path}")

    if not target.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    return target


def _format_size(size_bytes: int) -> str:
    """Format file size to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _build_tree(
    directory: Path,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 4,
    entries: list[str] | None = None,
    max_entries: int = 200,
) -> list[str]:
    """Recursively build a formatted directory tree.

    Args:
        directory: The directory to traverse
        prefix: Current line prefix for alignment (e.g., "│   ")
        depth: Current recursion depth
        max_depth: Maximum depth to recurse
        entries: Accumulator list for tree lines
        max_entries: Maximum entries before truncation

    Returns:
        List of formatted tree lines
    """
    if entries is None:
        entries = []

    if depth >= max_depth:
        entries.append(f"{prefix}... (max depth reached)")
        return entries

    try:
        children = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        entries.append(f"{prefix}⚠ Permission denied")
        return entries

    # Filter out skipped items
    children = [
        c for c in children
        if not (c.is_dir() and c.name in SKIP_DIRS)
        and not (c.is_file() and c.name in SKIP_FILES)
        and not c.name.startswith(".env")  # Skip .env, .env.local, etc.
    ]

    for i, child in enumerate(children):
        if len(entries) >= max_entries:
            remaining = len(children) - i
            entries.append(f"{prefix}... and {remaining} more items (truncated)")
            break

        is_last = i == len(children) - 1
        connector = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "

        if child.is_dir():
            # Count files recursively for summary
            try:
                file_count = sum(1 for _ in child.rglob("*") if _.is_file())
            except PermissionError:
                file_count = 0
            entries.append(f"{prefix}{connector}{child.name}/ ({file_count} files)")

            # Recurse into subdirectory
            _build_tree(
                child,
                prefix=prefix + extension,
                depth=depth + 1,
                max_depth=max_depth,
                entries=entries,
                max_entries=max_entries,
            )
        else:
            try:
                size = _format_size(child.stat().st_size)
            except OSError:
                size = "? B"
            entries.append(f"{prefix}{connector}{child.name} ({size})")

    return entries


class ProjectContextTool(BaseTool):
    """Load the directory structure of a project for AI context."""

    @property
    def name(self) -> str:
        return "project_context"

    @property
    def description(self) -> str:
        return (
            "Load the directory tree structure of a project in the workspace. "
            "Returns a formatted tree showing files, directories, and sizes. "
            "Useful for understanding codebase layout before making changes. "
            "Path is relative to ~/LocalMind_Workspace."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the project directory (e.g., 'my-project')",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum directory depth to traverse (default: 4, max: 6)",
                    "default": 4,
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str = "", max_depth: int = 4, **kwargs) -> dict:
        try:
            target = _validate_context_path(path)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        # Clamp max_depth
        max_depth = max(1, min(6, max_depth))

        # Build tree
        tree_lines = [f"{target.name}/"]
        _build_tree(target, prefix="", depth=0, max_depth=max_depth, entries=tree_lines)

        tree_text = "\n".join(tree_lines)

        # Count totals for summary
        total_files = sum(1 for _ in target.rglob("*") if _.is_file())
        total_dirs = sum(1 for _ in target.rglob("*") if _.is_dir())

        summary = f"\n\n📊 {total_files} files, {total_dirs} directories"

        return {
            "success": True,
            "result": tree_text + summary,
        }
