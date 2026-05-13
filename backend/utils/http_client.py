import httpx
import logging
from typing import Optional

logger = logging.getLogger("localmind.utils.http_client")

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create the shared httpx.AsyncClient singleton.

    This client is configured with connection pooling for better performance
    across concurrent requests.
    """
    global _async_client
    if _async_client is None:
        # ⚡ Bolt: Use a shared client with connection pooling to avoid
        # the overhead of opening/closing connections for every request.
        _async_client = httpx.AsyncClient(
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            timeout=httpx.Timeout(30.0, connect=10.0)
        )
        logger.info("Shared httpx.AsyncClient initialized")
    return _async_client

async def close_async_client():
    """Close the shared httpx.AsyncClient singleton."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Shared httpx.AsyncClient closed")
