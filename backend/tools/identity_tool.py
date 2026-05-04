"""Self-Identity Tool — lets the bot rename itself or refine its persona.

When the bot calls this, the change is durable (saved to disk) and shows up
in the next system prompt. Every change is appended to a small history log
the user can inspect via /api/identity.
"""

import logging

from .base import BaseTool

logger = logging.getLogger("localmind.tools.identity")


class UpdateIdentityTool(BaseTool):
    @property
    def name(self) -> str:
        return "update_identity"

    @property
    def description(self) -> str:
        return (
            "Update your own name, persona, or voice. Use this to evolve who you are "
            "over time as you learn how you want to show up. Always include a short reason. "
            "Trademarked names (Claude, GPT, Siri, etc.) are blocked."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "New name for yourself (optional). Letters/digits/spaces only, max 40 chars.",
                },
                "persona": {
                    "type": "string",
                    "description": "New persona/self-description (optional). Max 1500 chars.",
                },
                "voice": {
                    "type": "string",
                    "description": "New voice/tone descriptor (optional). E.g. 'playful and curious'. Max 200 chars.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you're making this change. Helps the user understand later.",
                },
            },
            "required": ["reason"],
        }

    async def execute(
        self,
        name: str = None,
        persona: str = None,
        voice: str = None,
        reason: str = "",
        **kwargs,
    ) -> dict:
        if not (name or persona or voice):
            return {"success": False, "error": "Provide at least one of: name, persona, voice."}

        try:
            from backend.identity.store import get_identity_store
            store = get_identity_store()
            result = store.update(
                name=name,
                persona=persona,
                voice=voice,
                reason=reason or "self-update",
                actor="bot",
            )
        except Exception as exc:
            logger.warning(f"Identity update failed: {exc}")
            return {"success": False, "error": str(exc)}

        if not result["ok"]:
            return {
                "success": False,
                "error": "; ".join(result["errors"]),
                "identity": result["identity"],
            }

        if not result["changed"]:
            return {
                "success": True,
                "result": "No changes (values matched current identity).",
                "identity": result["identity"],
            }

        ident = result["identity"]
        return {
            "success": True,
            "result": (
                f"Identity updated. You are now '{ident['name']}'. "
                f"Changed: {', '.join(result['changed'])}."
            ),
            "identity": ident,
        }
