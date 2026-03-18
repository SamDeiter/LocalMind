"""
Generate API documentation from Python source code.

Uses pdoc to auto-generate HTML documentation from docstrings.
Run with: python scripts/generate_docs.py

Output goes to docs/api/ — these can be served locally or
committed for GitHub Pages.

Why pdoc?
- Zero-config: works immediately with existing docstrings
- Lightweight: single dependency, no build system needed
- Markdown-friendly: renders docstrings as Markdown
- FastAPI-aware: properly documents route decorators
"""

import os
import subprocess
import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).parent.parent
DOCS_OUTPUT = ROOT / "docs" / "api"


def generate():
    """Generate HTML API docs from all backend Python modules.
    
    Scans backend/ and backend/routes/ for Python files with docstrings
    and produces navigable HTML documentation in docs/api/.
    """
    # Ensure pdoc is installed
    try:
        import pdoc  # noqa: F401
    except ImportError:
        print("Installing pdoc...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pdoc"])

    # Create output directory
    DOCS_OUTPUT.mkdir(parents=True, exist_ok=True)

    # Generate docs for the backend package
    print(f"📄 Generating API docs → {DOCS_OUTPUT}")
    subprocess.run(
        [
            sys.executable, "-m", "pdoc",
            "--html",
            "--output-dir", str(DOCS_OUTPUT),
            "--force",  # Overwrite existing docs
            "backend",
        ],
        cwd=str(ROOT),
    )

    print(f"\n✅ Documentation generated at: {DOCS_OUTPUT}")
    print(f"   Open: file:///{DOCS_OUTPUT / 'backend' / 'index.html'}")


if __name__ == "__main__":
    generate()
