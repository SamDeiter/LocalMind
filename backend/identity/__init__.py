"""AI identity — the bot's name, persona, and self-description.

The bot can rewrite this file via the `update_identity` tool. Persisted to
~/LocalMind_Workspace/ai_identity.json so it survives restarts.
"""

from backend.identity.store import IdentityStore, get_identity, get_identity_store

__all__ = ["IdentityStore", "get_identity", "get_identity_store"]
