"""
Self-Reflect Tool — Log improvement ideas for future sessions.

Writes structured JSON proposals to ~/LocalMind_Workspace/proposals/.
The AI can later read these proposals and act on them.
"""

import json
import logging
import time
import uuid
from pathlib import Path

from .base import BaseTool

logger = logging.getLogger("localmind.tools.self_reflect")

PROPOSALS_DIR = Path.home() / "LocalMind_Workspace" / "proposals"


class SelfReflectTool(BaseTool):
    """Log an improvement idea for a future session."""

    @property
    def name(self) -> str:
        return "self_reflect"

    @property
    def description(self) -> str:
        return (
            "Log an improvement idea or observation about yourself for future sessions. "
            "Use this when you notice something that could be better — a slow function, "
            "a missing feature, a code smell, or a UX improvement. "
            "Proposals are saved and can be reviewed later with 'show me your improvement proposals'."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title for the improvement (e.g., 'Cache model list API response')",
                },
                "category": {
                    "type": "string",
                    "description": "Category: performance, feature, bugfix, ux, security, code_quality",
                    "enum": ["performance", "feature", "bugfix", "ux", "security", "code_quality"],
                },
                "description": {
                    "type": "string",
                    "description": "Detailed description of what should be improved and why",
                },
                "files_affected": {
                    "type": "string",
                    "description": "Comma-separated list of files that would need to change",
                },
                "effort": {
                    "type": "string",
                    "description": "Estimated effort: small (< 30 min), medium (1-3 hours), large (3+ hours)",
                    "enum": ["small", "medium", "large"],
                },
                "priority": {
                    "type": "string",
                    "description": "Priority: low, medium, high, critical",
                    "enum": ["low", "medium", "high", "critical"],
                },
            },
            "required": ["title", "category", "description"],
        }

    async def execute(self, **kwargs) -> dict:
        title = kwargs.get("title", "Untitled")
        category = kwargs.get("category", "feature")
        description = kwargs.get("description", "")
        files_affected = kwargs.get("files_affected", "")
        effort = kwargs.get("effort", "medium")
        priority = kwargs.get("priority", "medium")

        proposal = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "category": category,
            "description": description,
            "files_affected": [f.strip() for f in files_affected.split(",") if f.strip()],
            "effort": effort,
            "priority": priority,
            "status": "proposed",
            "created_at": time.time(),
            "created_at_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        try:
            PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
            filepath = PROPOSALS_DIR / f"{proposal['id']}_{category}.json"
            filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

            logger.info(f"Self-reflection logged: {title} [{category}]")

            return {
                "success": True,
                "result": (
                    f"💡 Improvement proposal logged:\n"
                    f"  Title: {title}\n"
                    f"  Category: {category}\n"
                    f"  Priority: {priority}\n"
                    f"  Effort: {effort}\n"
                    f"  Saved to: proposals/{filepath.name}"
                ),
            }
        except Exception as exc:
            return {"success": False, "error": f"Failed to save proposal: {exc}"}


class ListProposalsTool(BaseTool):
    """List all pending improvement proposals."""

    @property
    def name(self) -> str:
        return "list_proposals"

    @property
    def description(self) -> str:
        return (
            "List all improvement proposals that have been logged. "
            "Use this to review what improvements are pending and decide what to work on."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter by category (or 'all' for everything)",
                    "default": "all",
                }
            },
        }

    async def execute(self, category: str = "all", **kwargs) -> dict:
        if not PROPOSALS_DIR.exists():
            return {"success": True, "result": "No proposals yet. Use self_reflect to log ideas."}

        proposals = []
        for f in sorted(PROPOSALS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if category == "all" or data.get("category") == category:
                    proposals.append(data)
            except Exception:
                continue

        if not proposals:
            return {"success": True, "result": f"No proposals found (category: {category})."}

        lines = []
        for p in proposals:
            status_icon = {"proposed": "📋", "in_progress": "🔧", "done": "✅"}.get(p.get("status"), "❓")
            priority_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(p.get("priority"), "⚪")
            lines.append(
                f"{status_icon} {priority_icon} [{p['category']}] {p['title']} "
                f"(effort: {p.get('effort', '?')}, id: {p['id']})"
            )

        return {
            "success": True,
            "result": f"📋 {len(proposals)} improvement proposals:\n\n" + "\n".join(lines),
        }
