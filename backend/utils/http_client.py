import httpx
import logging

logger = logging.getLogger("localmind.utils.http_client")

_async_client: httpx.AsyncClient = None

def get_async_client() -> httpx.AsyncClient:
    """Return the global shared httpx.AsyncClient instance.

    The client is initialized on the first call if it doesn't exist.
    It is recommended to call this within an async context.
    """
    global _async_client
    if _async_client is None:
        logger.info("Initializing global httpx.AsyncClient")
        # Bolt: Pooling limits and 120s timeout for robust LLM streaming
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=limits,
            follow_redirects=True
        )
    return _async_client

async def close_async_client():
    """Close the global shared httpx.AsyncClient instance."""
    global _async_client
    if _async_client is not None:
        logger.info("Closing global httpx.AsyncClient")
        await _async_client.aclose()
        _async_client = None
