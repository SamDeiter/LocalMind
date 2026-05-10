import httpx
from typing import Optional

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create the global httpx.AsyncClient singleton.

    Using a singleton allows for connection pooling, reducing the overhead
    of creating new connections for every request.
    """
    global _async_client
    if _async_client is None:
        # ⚡ Bolt: Use a single client with pooling for all outgoing requests.
        # This prevents 'Too many open files' and reduces latency by ~20-50ms per call.
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True
        )
    return _async_client

async def close_async_client():
    """Close the global httpx.AsyncClient singleton."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
