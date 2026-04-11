"""
MemPalace Tools — LocalMind tool plugin.

Exposes 3 tools to the agent loop via the existing ToolRegistry auto-discovery:

  mempalace_search  — semantic search across the palace (verbatim content)
  mempalace_save    — store a verbatim memory in the palace
  mempalace_status  — show palace stats / verify it's operational

These tools complement the existing save_memory / recall_memories (FTS5 Tier-2)
by adding a Tier-3 semantic ChromaDB layer using the MemPalace palace structure
(wings → halls → rooms → closets → drawers).

Reference: https://github.com/milla-jovovich/mempalace
Benchmark:  96.6% LongMemEval R@5, raw mode, zero API calls.
"""

import logging
from typing import Optional

from .base import BaseTool
from backend.memory.palace_manager import initialize_palace, search, save, get_status

logger = logging.getLogger("localmind.tools.mempalace")

# Initialize palace on module load (non-blocking — safe to fail)
_palace_ready = initialize_palace()


class MemPalaceSearchTool(BaseTool):
    """Tier-3 semantic memory search via MemPalace (ChromaDB palace structure)."""

    @property
    def name(self) -> str:
        return "mempalace_search"

    @property
    def description(self) -> str:
        return (
            "Search long-term memory using MemPalace (96.6% LongMemEval accuracy). "
            "Searches verbatim conversation history, decisions, and project knowledge "
            "organized in a Wing > Hall > Room palace structure. Use this for deep "
            "recollection of past decisions, debugging sessions, or architecture "
            "debates that may be months old. Complements recall_memories (FTS5)."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for. Natural language works best — e.g. 'why did we switch to GraphQL'",
                },
                "wing": {
                    "type": "string",
                    "description": (
                        "Optional: filter by palace wing (person or project). "
                        "e.g. 'wing_localmind', 'wing_general'. Leave blank to search all wings."
                    ),
                },
                "room": {
                    "type": "string",
                    "description": (
                        "Optional: filter by room topic within a wing. "
                        "e.g. 'auth-migration', 'graphql-switch'. Leave blank to search all rooms."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of memories to return (default: 5, max: 20).",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str = "", wing: Optional[str] = None,
                      room: Optional[str] = None, limit: int = 5, **kwargs) -> dict:
        if not query.strip():
            return {"success": False, "error": "Query cannot be empty"}

        limit = min(max(1, limit), 20)

        results = search(query=query, wing=wing, room=room, limit=limit)

        if not results:
            return {
                "success": True,
                "result": "No memories found in the palace for that query.",
                "memories": [],
                "source": "mempalace",
            }

        formatted_parts = []
        for r in results:
            location = "/".join(filter(None, [
                r.get("wing", ""),
                r.get("hall", ""),
                r.get("room", ""),
            ])) or "palace"
            dist = r.get("distance", r.get("relevance", "?"))
            dist_str = f"{(1 - float(dist)):.0%}" if isinstance(dist, (int, float)) else ""
            content = r.get("content", r.get("document", str(r)))
            formatted_parts.append(
                f"[{location}]{f' ({dist_str})' if dist_str else ''}: {content}"
            )

        return {
            "success": True,
            "result": "\n".join(formatted_parts),
            "memories": results,
            "count": len(results),
            "source": "mempalace",
        }


class MemPalaceSaveTool(BaseTool):
    """Tier-3 verbatim memory save to MemPalace palace structure."""

    @property
    def name(self) -> str:
        return "mempalace_save"

    @property
    def description(self) -> str:
        return (
            "Save verbatim content to MemPalace long-term memory (Tier-3). "
            "Unlike save_memory (which stores short facts), this stores full "
            "verbatim content in a palace Wing > Hall > Room structure for "
            "high-accuracy future retrieval. Use for important decisions, "
            "debugging sessions, architecture changes, or anything the user "
            "wants to recall months from now."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The verbatim content to store. Can be a full conversation excerpt, decision log, etc.",
                },
                "wing": {
                    "type": "string",
                    "description": (
                        "Palace wing — the person or project this memory belongs to. "
                        "e.g. 'wing_localmind', 'wing_general', 'wing_sam'. Default: 'wing_general'."
                    ),
                    "default": "wing_general",
                },
                "hall": {
                    "type": "string",
                    "description": (
                        "Memory type hall. One of: 'hall_facts' (decisions), 'hall_events' (sessions), "
                        "'hall_discoveries' (breakthroughs), 'hall_preferences' (habits/opinions), "
                        "'hall_advice' (recommendations). Default: 'hall_facts'."
                    ),
                    "enum": ["hall_facts", "hall_events", "hall_discoveries", "hall_preferences", "hall_advice"],
                    "default": "hall_facts",
                },
                "room": {
                    "type": "string",
                    "description": (
                        "Specific topic room within the wing. e.g. 'auth-migration', 'ui-redesign'. "
                        "Use lowercase-hyphenated names. Default: 'general'."
                    ),
                    "default": "general",
                },
            },
            "required": ["content"],
        }

    async def execute(self, content: str = "", wing: str = "wing_general",
                      hall: str = "hall_facts", room: str = "general", **kwargs) -> dict:
        if not content.strip():
            return {"success": False, "error": "Content cannot be empty"}

        result = save(content=content, wing=wing, hall=hall, room=room)

        if result.get("success"):
            return {
                "success": True,
                "result": f"Saved to palace: {wing}/{hall}/{room} (id={result.get('id', 'unknown')})",
                "id": result.get("id"),
                "source": "mempalace",
            }
        return {
            "success": False,
            "error": result.get("error", "Unknown save error"),
            "source": "mempalace",
        }


class MemPalaceStatusTool(BaseTool):
    """Check MemPalace palace status and drawer count."""

    @property
    def name(self) -> str:
        return "mempalace_status"

    @property
    def description(self) -> str:
        return (
            "Check the status of the MemPalace long-term memory system. "
            "Shows whether the palace is initialized, how many memories are indexed, "
            "and the palace path. Call this if memory search returns no results "
            "or if the user asks about the memory system."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, **kwargs) -> dict:
        status = get_status()
        message = status.get("message", "")
        ready = status.get("ready", False)

        if ready:
            result_text = (
                f"MemPalace is operational.\n"
                f"  Path: {status.get('palace_path')}\n"
                f"  Drawers: {status.get('drawer_count', 'unknown')}\n"
                f"  {message}\n\n"
                f"To add memories: use mempalace_save\n"
                f"To search: use mempalace_search\n"
                f"To mine past conversations: run 'mempalace mine ~/chats/ --mode convos' in terminal."
            )
        else:
            result_text = (
                f"MemPalace is not yet set up.\n"
                f"  {message}\n\n"
                f"Quick start:\n"
                f"  pip install mempalace chromadb\n"
                f"  mempalace init ~/projects/localmind\n"
                f"  mempalace mine ~/projects/localmind --mode convos"
            )

        return {
            "success": True,
            "result": result_text,
            "status": status,
            "source": "mempalace",
        }
