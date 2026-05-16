import logging
from typing import Optional

import httpx

logger = logging.getLogger("localmind.utils.http_client")

_async_client: Optional[httpx.AsyncClient] = None


def get_async_client() -> httpx.AsyncClient:
    """Get or create a shared httpx.AsyncClient singleton.

    Configured with optimized connection pooling and standard timeouts.
    """
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        logger.info("Shared httpx.AsyncClient initialized")
    return _async_client


async def close_async_client():
    """Gracefully close the shared httpx.AsyncClient."""
    global _async_client
    if _async_client is not None and not _async_client.is_closed:
        await _async_client.aclose()
        logger.info("Shared httpx.AsyncClient closed")
