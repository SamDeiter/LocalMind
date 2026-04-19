import httpx
import logging
from typing import Optional

logger = logging.getLogger("localmind.utils.http_client")

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Return the global pooled httpx.AsyncClient instance.

    This singleton approach allows for connection pooling, reducing
    handshake latency and resource overhead across the application.
    """
    global _async_client
    if _async_client is None:
        # Default 120s timeout for LLM and background tasks
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
        logger.info("Initialized global pooled AsyncClient")
    return _async_client

async def close_async_client():
    """Gracefully close the global pooled AsyncClient during shutdown."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Closed global pooled AsyncClient")
