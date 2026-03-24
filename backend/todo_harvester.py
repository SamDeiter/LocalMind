"""
todo_harvester.py — Scan codebase for TODO/FIXME/HACK comments
================================================================
Pre-validated improvement ideas from the developer, injected into
the reflection prompt so the AI addresses real known issues.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger("localmind.autonomy.todos")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"venv", "__pycache__", ".git", "node_modules", "memory_db", ".bak", "browser_recordings"}
TODO_PATTERN = re.compile(r"#\s*(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE)


def harvest_todos(max_results: int = 15) -> list[dict]:
    """Scan project files for TODO/FIXME/HACK comments.

    Returns list of {file, line, tag, comment} dicts.
    """
    results = []

    for ext in ("*.py", "*.js", "*.html", "*.css"):
        for filepath in PROJECT_ROOT.rglob(ext):
            rel = filepath.relative_to(PROJECT_ROOT)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue

            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(content.split("\n"), 1):
                    match = TODO_PATTERN.search(line)
                    if match:
                        tag = match.group(1).upper()
                        comment = match.group(2).strip()
                        if len(comment) > 10:  # Skip trivially short ones
                            results.append({
                                "file": str(rel).replace("\\", "/"),
                                "line": i,
                                "tag": tag,
                                "comment": comment[:120],
                            })
            except (OSError, UnicodeDecodeError):
                continue

    # Sort: FIXME > HACK > TODO, then by file
    rank = {"FIXME": 0, "HACK": 1, "XXX": 2, "TODO": 3}
    results.sort(key=lambda r: (rank.get(r["tag"], 9), r["file"]))
    return results[:max_results]


def get_todos_for_prompt(max_items: int = 10) -> str:
    """Format TODOs for injection into the reflection prompt."""
    todos = harvest_todos(max_items)
    if not todos:
        return ""

    lines = ["\nTODO/FIXME ITEMS FOUND IN CODEBASE (prefer to address one of these):"]
    for t in todos:
        lines.append(f"  📌 [{t['tag']}] {t['file']}:{t['line']} — {t['comment']}")
    lines.append("")
    return "\n".join(lines)
