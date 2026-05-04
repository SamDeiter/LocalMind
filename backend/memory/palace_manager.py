"""
MemPalace Manager — LocalMind integration layer.

Wraps the milla-jovovich/mempalace library as a singleton so the rest of
LocalMind can interact with it without managing init details.

Architecture fit:
  - Session cache (session_cache.py) → ephemeral Tier-1 (unchanged)
  - FTS5 store (src/agent/memory/)  → existing Tier-2 (unchanged)
  - MemPalace (this module)          → persistent Tier-3  ← NEW
    ChromaDB vector search with Wing/Hall/Room palace structure.
    96.6% LongMemEval R@5 in raw verbatim mode — zero API calls.

Palace path: ~/.mempalace/palace  (overridable via MEMPALACE_DIR env var)
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.memory.palace_manager")

# --- Singleton state ----------------------------------------------------------
_palace_initialized: bool = False
_palace_path: Optional[Path] = None
_searcher = None    # mempalace.searcher module (lazy)
_layers = None      # mempalace.layers module (lazy)


def _get_palace_path() -> Path:
    """Resolve the palace storage directory (env override or default)."""
    env_dir = os.environ.get("MEMPALACE_DIR", "")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return Path.home() / ".mempalace" / "palace"


def initialize_palace() -> bool:
    """
    Initialize MemPalace on first call.  Safe to call multiple times.
    Returns True if palace is ready, False if mempalace is not installed.
    """
    global _palace_initialized, _palace_path, _searcher, _layers

    if _palace_initialized:
        return True

    try:
        import mempalace  # noqa: F401 — confirms package is importable
        from mempalace import searcher as _s, layers as _l

        _palace_path = _get_palace_path()
        _palace_path.mkdir(parents=True, exist_ok=True)

        _searcher = _s
        _layers = _l
        _palace_initialized = True
        logger.info("MemPalace initialized at %s", _palace_path)
        return True

    except ImportError:
        logger.warning(
            "mempalace package not found — palace memory tier disabled. "
            "Install with: pip install mempalace chromadb"
        )
        return False
    except Exception as exc:
        logger.warning("MemPalace init failed (non-fatal): %s", exc)
        return False


def is_ready() -> bool:
    """Return True if the palace has been successfully initialized."""
    return _palace_initialized and _palace_path is not None


def get_palace_path() -> Optional[Path]:
    """Return the resolved palace path, or None if not initialized."""
    return _palace_path


# --- Search -------------------------------------------------------------------

def search(query: str, wing: Optional[str] = None, room: Optional[str] = None,
           limit: int = 5) -> list[dict]:
    """
    Semantic search across the palace.

    Args:
        query:  Natural-language query string.
        wing:   Optional wing filter (e.g. 'wing_localmind').
        room:   Optional room filter (e.g. 'auth-migration').
        limit:  Max results to return.

    Returns:
        List of result dicts with 'content', 'wing', 'room', 'distance' keys.
        Returns empty list on any failure — never raises.
    """
    if not is_ready():
        logger.debug("search() called but palace not ready")
        return []

    try:
        results = _searcher.search_memories(
            query=query,
            palace_path=str(_palace_path),
            n_results=limit,
            wing=wing,
            room=room,
        )
        return results if results else []
    except Exception as exc:
        logger.warning("MemPalace search failed: %s", exc)
        return []


# --- Save (add drawer) --------------------------------------------------------

def save(content: str, wing: str = "wing_general",
         hall: str = "hall_facts", room: str = "general") -> dict:
    """
    Store verbatim content in the palace.

    Args:
        content: The exact text to store (raw verbatim, no summarization).
        wing:    Palace wing (person or project, e.g. 'wing_localmind').
        hall:    Memory type ('hall_facts', 'hall_events', 'hall_preferences',
                              'hall_discoveries', 'hall_advice').
        room:    Specific topic within the wing.

    Returns:
        dict with 'success' bool and optional 'id' or 'error'.
    """
    if not is_ready():
        return {"success": False, "error": "MemPalace not initialized"}

    try:
        # Use the searcher's add_drawer if available, else write directly
        if hasattr(_searcher, "add_drawer"):
            result_id = _searcher.add_drawer(
                content=content,
                palace_path=str(_palace_path),
                wing=wing,
                hall=hall,
                room=room,
            )
        else:
            # Fallback: use ChromaDB collection directly
            import chromadb
            import hashlib
            import time as _time
            client = chromadb.PersistentClient(path=str(_palace_path))
            collection = client.get_or_create_collection("mempalace_drawers")
            doc_id = hashlib.sha1(
                f"{content[:80]}{_time.time()}".encode()
            ).hexdigest()[:12]
            collection.add(
                documents=[content],
                ids=[doc_id],
                metadatas=[{"wing": wing, "hall": hall, "room": room}],
            )
            result_id = doc_id

        logger.info("MemPalace saved to %s/%s/%s (id=%s)", wing, hall, room, result_id)
        return {"success": True, "id": result_id}

    except Exception as exc:
        logger.warning("MemPalace save failed: %s", exc)
        return {"success": False, "error": str(exc)}


# --- Wake-up context (L0 + L1 layer load) ------------------------------------

def get_wakeup_context(wing: Optional[str] = None) -> str:
    """
    Load the MemPalace wake-up context (~170 tokens of critical facts).
    Suitable for prepending to a system prompt.

    Returns empty string if palace not ready or wake-up fails.
    """
    if not is_ready():
        return ""

    try:
        if _layers and hasattr(_layers, "load_wakeup_context"):
            return _layers.load_wakeup_context(
                palace_path=str(_palace_path),
                wing=wing,
            ) or ""
    except Exception as exc:
        logger.debug("Wake-up context load failed (non-fatal): %s", exc)

    return ""


# --- Status -------------------------------------------------------------------

def get_status() -> dict:
    """Return a summary of the palace state for API/UI consumption."""
    if not is_ready():
        return {
            "ready": False,
            "palace_path": None,
            "message": "MemPalace not initialized — run 'mempalace init' to set up your palace.",
        }

    try:
        # Try to get collection stats from ChromaDB
        import chromadb
        client = chromadb.PersistentClient(path=str(_palace_path))
        collection = client.get_or_create_collection("mempalace_drawers")
        count = collection.count()
        return {
            "ready": True,
            "palace_path": str(_palace_path),
            "drawer_count": count,
            "message": f"Palace ready — {count} drawers indexed.",
        }
    except Exception as exc:
        return {
            "ready": True,
            "palace_path": str(_palace_path),
            "drawer_count": "unknown",
            "message": f"Palace ready (stats unavailable: {exc})",
        }
