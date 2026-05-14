import httpx
from typing import Optional

_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create a shared httpx.AsyncClient singleton.

    Provides connection pooling and reuse across the application to
    reduce latency and resource usage.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _client

async def close_async_client():
    """Close the shared httpx.AsyncClient singleton if it exists."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
