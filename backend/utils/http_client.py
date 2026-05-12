import httpx
import logging
from typing import Optional

logger = logging.getLogger("localmind.utils.http_client")

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Return a shared httpx.AsyncClient singleton for the application.

    The client is configured with connection pooling to improve performance
    across multiple requests to external services like Ollama.
    """
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True,
        )
        logger.info("Shared AsyncClient initialized (max_connections=100)")
    return _async_client

async def close_async_client():
    """Gracefully close the shared AsyncClient singleton."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Shared AsyncClient closed")
