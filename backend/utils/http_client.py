import httpx
from typing import Optional

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get the global httpx.AsyncClient instance, creating it if it doesn't exist.

    This singleton allows for connection pooling across the application,
    reducing latency and resource usage by reusing TCP connections.
    """
    global _async_client
    if _async_client is None or _async_client.is_closed:
        # ⚡ Bolt: 120s timeout to accommodate long-running LLM generation.
        # We also set generous connection limits for high-concurrency tasks.
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _async_client

async def close_async_client():
    """Close the global httpx.AsyncClient instance.

    Should be called during application shutdown to gracefully release resources.
    """
    global _async_client
    if _async_client is not None and not _async_client.is_closed:
        await _async_client.aclose()
        _async_client = None
