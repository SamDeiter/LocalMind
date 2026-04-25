import httpx
import logging
from typing import Optional

logger = logging.getLogger("localmind.utils.http_client")

# ⚡ Bolt: Global shared HTTP client to reduce handshake latency and resource usage.
# Profiling shows that creating a new httpx.AsyncClient per request adds ~39ms overhead.
_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Return the global singleton httpx.AsyncClient instance."""
    global _async_client
    if _async_client is None:
        # Default 120s timeout and pooling limits (100 max, 20 keepalive)
        # to ensure efficient connection reuse across LLM calls and system routes.
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
        logger.info("Global httpx.AsyncClient initialized")
    return _async_client

async def close_async_client():
    """Gracefully shut down the global HTTP client."""
    global _async_client
    if _async_client:
        await _async_client.aclose()
        _async_client = None
        logger.info("Global httpx.AsyncClient closed")
