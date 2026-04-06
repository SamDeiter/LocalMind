"""
Create a new migration file from the template.

Usage:
    python -m backend.migrations.create "add user_preferences collection"

This generates a timestamped file like:
    backend/migrations/versions/002_add_user_preferences_collection.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_VERSIONS_DIR = Path(__file__).resolve().parent / "versions"

def _render_template(version: str, title: str, description: str) -> str:
    """Build the migration file content."""
    header = f"Migration {version} — {title}"
    separator = "=" * len(header)
    return f'''\
"""
{header}
{separator}
TODO: Describe what this migration does and why.

The `clients` dict contains:
    clients["memory"] — chromadb.PersistentClient for backend/memory_db/
    clients["rag"]    — chromadb.PersistentClient for backend/rag_data/
"""

version = "{version}"
description = "{description}"


def up(clients: dict) -> None:
    """Apply the migration."""
    # TODO: implement
    pass


def down(clients: dict) -> None:
    """Revert the migration (best-effort). Set to `pass` if not reversible."""
    # TODO: implement
    pass
'''


def _next_version_number() -> int:
    """Scan existing files and return the next available version number."""
    existing = sorted(_VERSIONS_DIR.glob("*.py"))
    max_num = 0
    for p in existing:
        if p.name.startswith("_"):
            continue
        parts = p.stem.split("_", 1)
        if parts[0].isdigit():
            max_num = max(max_num, int(parts[0]))
    return max_num + 1


def _slugify(text: str) -> str:
    """Convert a description into a filename-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug[:60]  # keep filenames reasonable


def create_migration(description: str) -> Path:
    """Generate a new migration file and return its path."""
    _VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    num = _next_version_number()
    version = str(num)
    padded = f"{num:03d}"
    slug = _slugify(description)
    filename = f"{padded}_{slug}.py"
    filepath = _VERSIONS_DIR / filename

    title = description.strip().capitalize()
    content = _render_template(
        version=version,
        title=title,
        description=description.strip(),
    )

    filepath.write_text(content, encoding="utf-8")
    return filepath


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m backend.migrations.create <description>")
        print('  Example: python -m backend.migrations.create "add user_preferences collection"')
        sys.exit(1)

    desc = " ".join(sys.argv[1:])
    path = create_migration(desc)
    print(f"Created migration: {path}")
    print(f"Edit the file, then restart the server to apply it.")


if __name__ == "__main__":
    main()
