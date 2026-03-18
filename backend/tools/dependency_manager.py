"""
Dependency Lifecycle Manager — Tracks installed packages, detects
idle dependencies, and offers conversational cleanup prompts.

Every package install goes through the approval flow first, then gets
tracked here. When a task finishes, the AI can suggest cleanup. After
7 days of inactivity, it proactively suggests uninstalling.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools.dependency_manager")

# Persistent storage for tracked dependencies
_deps_path = Path.home() / "LocalMind_Workspace" / ".dependencies.json"


def _load_deps() -> list[dict]:
    """Load tracked dependencies from disk."""
    if _deps_path.exists():
        try:
            return json.loads(_deps_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_deps(deps: list[dict]) -> None:
    """Persist tracked dependencies to disk."""
    _deps_path.parent.mkdir(parents=True, exist_ok=True)
    _deps_path.write_text(
        json.dumps(deps, indent=2, default=str), encoding="utf-8"
    )


def get_all_dependencies() -> list[dict]:
    """Return all tracked dependencies (for the API/UI)."""
    return _load_deps()


def get_idle_dependencies(idle_days: int = 7) -> list[dict]:
    """Return dependencies not used in the last N days."""
    cutoff = time.time() - (idle_days * 86400)
    deps = _load_deps()
    return [
        d for d in deps
        if d.get("status") != "PINNED"
        and d.get("last_used_at", 0) < cutoff
    ]


def mark_dependency_used(package: str) -> None:
    """Update the last_used_at timestamp for a package."""
    deps = _load_deps()
    for d in deps:
        if d["package"].lower() == package.lower():
            d["last_used_at"] = time.time()
            break
    _save_deps(deps)


def pin_dependency(package: str) -> bool:
    """Pin a dependency so it won't be suggested for cleanup."""
    deps = _load_deps()
    for d in deps:
        if d["package"].lower() == package.lower():
            d["status"] = "PINNED"
            _save_deps(deps)
            return True
    return False


class InstallPackageTool(BaseTool):
    """Install a Python package and track it in the dependency manager.
    
    NOTE: The AI should call propose_action FIRST to get user approval,
    then call this tool to actually install.
    """

    @property
    def name(self) -> str:
        return "install_package"

    @property
    def description(self) -> str:
        return (
            "Install a Python package via pip and track it for lifecycle "
            "management. IMPORTANT: You must call propose_action first to "
            "get user approval before calling this tool."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": "Package name (e.g. 'pandas', 'requests==2.31.0')",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this package is needed for the current task",
                },
            },
            "required": ["package", "reason"],
        }

    async def execute(self, package: str = "", reason: str = "", **kwargs) -> dict[str, Any]:
        if not package.strip():
            return {"success": False, "error": "Package name required"}

        try:
            # Install via pip (shell=False for security)
            result = subprocess.run(
                ["pip", "install", package],
                capture_output=True,
                text=True,
                timeout=120,
                shell=False,
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"pip install failed: {result.stderr[:500]}",
                }

            # Extract installed version from pip output
            version = "unknown"
            for line in result.stdout.splitlines():
                if "Successfully installed" in line:
                    # Parse "Successfully installed pandas-2.1.0"
                    parts = line.split()
                    for part in parts:
                        if package.split("==")[0].lower() in part.lower():
                            version = part.split("-")[-1] if "-" in part else "unknown"
                            break

            # Track the dependency
            deps = _load_deps()
            # Check if already tracked
            existing = next(
                (d for d in deps if d["package"].lower() == package.split("==")[0].lower()),
                None,
            )
            if existing:
                existing["last_used_at"] = time.time()
                existing["status"] = "ACTIVE"
                existing["reason"] = reason
            else:
                deps.append({
                    "package": package.split("==")[0],
                    "version": version,
                    "reason": reason,
                    "installed_at": time.time(),
                    "last_used_at": time.time(),
                    "status": "ACTIVE",
                })
            _save_deps(deps)

            logger.info(f"Installed and tracked: {package} (reason: {reason})")
            return {
                "success": True,
                "result": f"✅ Installed {package} — tracked for lifecycle management.",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Installation timed out (120s)"}
        except Exception as exc:
            return {"success": False, "error": f"Install failed: {exc}"}


class UninstallPackageTool(BaseTool):
    """Uninstall a Python package and remove it from tracking."""

    @property
    def name(self) -> str:
        return "uninstall_package"

    @property
    def description(self) -> str:
        return (
            "Uninstall a Python package and remove it from dependency "
            "tracking. Use this for cleanup after a task is complete."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": "Package name to uninstall",
                },
            },
            "required": ["package"],
        }

    async def execute(self, package: str = "", **kwargs) -> dict[str, Any]:
        if not package.strip():
            return {"success": False, "error": "Package name required"}

        try:
            result = subprocess.run(
                ["pip", "uninstall", "-y", package],
                capture_output=True,
                text=True,
                timeout=60,
                shell=False,
            )

            # Remove from tracking
            deps = _load_deps()
            deps = [d for d in deps if d["package"].lower() != package.lower()]
            _save_deps(deps)

            logger.info(f"Uninstalled and untracked: {package}")
            return {
                "success": True,
                "result": f"🧹 Uninstalled {package} — removed from tracking.",
            }
        except Exception as exc:
            return {"success": False, "error": f"Uninstall failed: {exc}"}


class ListDependenciesTool(BaseTool):
    """List all tracked dependencies with their status."""

    @property
    def name(self) -> str:
        return "list_dependencies"

    @property
    def description(self) -> str:
        return (
            "List all tracked Python packages with their install date, "
            "last used date, and status (ACTIVE/IDLE/PINNED)."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        deps = _load_deps()
        if not deps:
            return {"success": True, "result": "No tracked dependencies.", "dependencies": []}

        # Update statuses based on last_used_at
        idle_cutoff = time.time() - (7 * 86400)  # 7 days
        for d in deps:
            if d.get("status") != "PINNED" and d.get("last_used_at", 0) < idle_cutoff:
                d["status"] = "IDLE"

        lines = []
        for d in deps:
            status_icon = {"ACTIVE": "🟢", "IDLE": "🟡", "PINNED": "📌"}.get(d["status"], "⚪")
            lines.append(
                f"{status_icon} {d['package']} v{d.get('version', '?')} — "
                f"{d.get('reason', 'no reason')} "
                f"(last used: {_time_ago(d.get('last_used_at', 0))})"
            )

        return {
            "success": True,
            "result": "\n".join(lines),
            "dependencies": deps,
        }


def _time_ago(timestamp: float) -> str:
    """Human-readable time ago string."""
    if not timestamp:
        return "never"
    diff = time.time() - timestamp
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    elif diff < 86400:
        return f"{int(diff / 3600)}h ago"
    else:
        return f"{int(diff / 86400)}d ago"
