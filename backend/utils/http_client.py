import httpx
import logging
from typing import Optional

logger = logging.getLogger("localmind.utils.http_client")

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create a global pooled httpx.AsyncClient.

    Using a shared client significantly reduces latency for LLM and API
    requests by reusing connections and avoiding handshake overhead (~39ms).
    """
    global _async_client
    if _async_client is None:
        # ⚡ Bolt: Shared client with high pooling limits for performance.
        # 120s timeout matches llm_client.py defaults.
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True
        )
        logger.info("Initialized global pooled AsyncClient (max_conn=100)")
    return _async_client

async def close_async_client():
    """Gracefully close the global pooled AsyncClient."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Closed global pooled AsyncClient")
