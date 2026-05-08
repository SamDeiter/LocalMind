import httpx
import logging
from typing import Optional

logger = logging.getLogger("localmind.utils.http_client")

# Global client singleton
_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Return the shared httpx.AsyncClient singleton, creating it if necessary.

    Using a shared client enables connection pooling, which significantly reduces
    the overhead of frequent API calls (e.g., to Ollama or external search).
    """
    global _async_client
    if _async_client is None:
        # ⚡ Bolt: Configure connection pooling and long timeouts for LLM stability
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True
        )
        logger.info("Shared AsyncClient singleton initialized (pooling enabled)")
    return _async_client

async def close_async_client():
    """Close the shared AsyncClient singleton during application shutdown."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Shared AsyncClient singleton closed")
