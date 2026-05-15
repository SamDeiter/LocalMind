import httpx
import logging
from typing import Optional

logger = logging.getLogger("localmind.utils.http_client")

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get the shared httpx.AsyncClient singleton.

    Using a shared client enables connection pooling, reducing the overhead
    of TCP/TLS handshakes for repeated requests.
    """
    global _async_client
    if _async_client is None:
        # We use a generous timeout by default, but callers can override it
        # in specific requests (e.g. LLM streaming).
        # Limits are set to optimize connection reuse.
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
        logger.info("Shared httpx.AsyncClient initialized")
    return _async_client

async def close_async_client():
    """Gracefully close the shared httpx.AsyncClient."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Shared httpx.AsyncClient closed")
