import httpx
from typing import Optional

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create a global httpx.AsyncClient singleton.

    This reduces latency by reusing connections and avoids the overhead
    of creating a new client (approx. 39ms) for every request.
    """
    global _async_client
    if _async_client is None:
        # Default timeout of 120s to match LLMClient requirements.
        # pooling: 100 max connections, 20 keepalive.
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=limits
        )
    return _async_client

async def close_async_client():
    """Gracefully close the global httpx.AsyncClient."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
