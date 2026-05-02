import httpx
import logging
from typing import Optional

logger = logging.getLogger("localmind.utils.http_client")

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create a global httpx.AsyncClient singleton for connection pooling."""
    global _async_client
    if _async_client is None or _async_client.is_closed:
        # Default timeout of 60s for most operations, can be overridden per request
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True
        )
        logger.info("Created global httpx.AsyncClient singleton")
    return _async_client

async def close_async_client():
    """Close the global httpx.AsyncClient singleton."""
    global _async_client
    if _async_client is not None and not _async_client.is_closed:
        await _async_client.aclose()
        logger.info("Closed global httpx.AsyncClient singleton")
    _async_client = None
