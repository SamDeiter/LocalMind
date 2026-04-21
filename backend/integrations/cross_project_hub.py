"""
cross_project_hub.py — Cross-Project Context Hub (Phase D)
============================================================
Detects the tech stack and project metadata for any local repository,
and exposes a /api/hub/context endpoint so the agent can reason 
across multiple projects.

Tech stack detection is done by scanning for signature files:
  - package.json      → Node.js / web stack
  - pyproject.toml    → Python (modern)
  - requirements.txt  → Python (legacy)
  - Cargo.toml        → Rust
  - go.mod            → Go
  - pom.xml           → Java/Maven
  - build.gradle      → Java/Gradle
  - Gemfile           → Ruby
  - composer.json     → PHP
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.integrations.cross_project_hub")

# Map: filename → (language/framework, display_name)
STACK_SIGNATURES: dict[str, tuple[str, str]] = {
    "package.json":      ("node",    "Node.js / JavaScript"),
    "pyproject.toml":    ("python",  "Python (pyproject)"),
    "requirements.txt":  ("python",  "Python (pip)"),
    "Cargo.toml":        ("rust",    "Rust"),
    "go.mod":            ("go",      "Go"),
    "pom.xml":           ("java",    "Java (Maven)"),
    "build.gradle":      ("java",    "Java (Gradle)"),
    "Gemfile":           ("ruby",    "Ruby"),
    "composer.json":     ("php",     "PHP"),
    "tsconfig.json":     ("typescript", "TypeScript"),
    "Dockerfile":        ("docker",  "Docker"),
    "docker-compose.yml": ("docker", "Docker Compose"),
    ".firebase.json":    ("firebase", "Firebase"),
    "firebase.json":     ("firebase", "Firebase"),
}


def detect_tech_stack(project_path: Path) -> list[dict]:
    """Scan a directory for tech stack signature files.
    
    Returns a list of detected stacks, each with:
      - language: str
      - display_name: str
      - signature_file: str
    """
    detected = []
    seen_langs = set()

    for filename, (language, display_name) in STACK_SIGNATURES.items():
        candidate = project_path / filename
        if candidate.exists() and language not in seen_langs:
            detected.append({
                "language": language,
                "display_name": display_name,
                "signature_file": filename,
            })
            seen_langs.add(language)

    return detected


def get_project_meta(project_path: Path) -> dict:
    """Collect project metadata from standard config files.
    
    Reads name/version from package.json or pyproject.toml if present.
    Returns a dict with: name, version, description, license
    """
    meta = {"name": project_path.name, "version": "", "description": "", "license": ""}

    # Node.js
    pkg = project_path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            meta["name"] = data.get("name", meta["name"])
            meta["version"] = data.get("version", "")
            meta["description"] = data.get("description", "")
            meta["license"] = data.get("license", "")
            return meta
        except (json.JSONDecodeError, OSError):
            pass

    # Python (pyproject.toml — basic TOML parse without dependency)
    pyproject = project_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("name ="):
                    meta["name"] = line.split("=", 1)[1].strip().strip('"\'\' \')
                elif line.startswith("version ="):
                    meta["version"] = line.split("=", 1)[1].strip().strip('"\'\' \')
                elif line.startswith("description ="):
                    meta["description"] = line.split("=", 1)[1].strip().strip('"\'\' \')
        except OSError:
            pass

    return meta


def build_hub_context(project_path: Optional[str] = None) -> dict:
    """Build the full hub context payload for a given project directory.
    
    If project_path is None, uses the LocalMind project root itself.
    
    Returns a dict suitable for /api/hub/context:
      - project_path: absolute path string
      - meta: name, version, description, license
      - stack: list of detected tech stacks
      - git_remote: git remote URL(s) if available
    """
    if project_path:
        root = Path(project_path).resolve()
    else:
        # Default: LocalMind project root (two levels up from this file)
        root = Path(__file__).resolve().parent.parent.parent

    if not root.exists():
        return {"error": f"Path does not exist: {root}"}

    meta = get_project_meta(root)
    stack = detect_tech_stack(root)

    # Try to get git remote URLs
    git_remotes = []
    try:
        import subprocess
        result = subprocess.run(
            "cmd /c git remote -v",
            cwd=str(root),
            capture_output=True, text=True, timeout=10, shell=True,
        )
        if result.returncode == 0:
            seen_remotes = set()
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and "(fetch)" in line:
                    url = parts[1]
                    if url not in seen_remotes:
                        git_remotes.append({"name": parts[0], "url": url})
                        seen_remotes.add(url)
    except Exception as exc:
        logger.debug(f"Could not read git remotes: {exc}")

    logger.info(f"Hub context built for: {root.name} — stack: {[s['language'] for s in stack]}")

    return {
        "project_path": str(root),
        "meta": meta,
        "stack": stack,
        "git_remotes": git_remotes,
    }
