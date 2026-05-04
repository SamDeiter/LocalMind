import httpx
import logging
from typing import Optional

logger = logging.getLogger("localmind.utils.http_client")

# Global singleton instance
_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create a global httpx.AsyncClient singleton.

    This enables connection pooling across the application, reducing
    latency for high-frequency LLM and system metadata requests.
    """
    global _async_client
    if _async_client is None:
        # Default timeout of 120s for LLM generations, with a 10s connect timeout.
        timeout = httpx.Timeout(120.0, connect=10.0)
        _async_client = httpx.AsyncClient(timeout=timeout)
        logger.info("Global AsyncClient singleton initialized")
    return _async_client

async def close_async_client():
    """Gracefully shut down the global AsyncClient singleton."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Global AsyncClient singleton closed")
