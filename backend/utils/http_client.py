import httpx
import asyncio
from typing import Optional

_client: Optional[httpx.AsyncClient] = None
_lock = asyncio.Lock()

async def get_async_client() -> httpx.AsyncClient:
    """Get the shared httpx.AsyncClient instance, creating it if necessary.

    ⚡ Bolt: Connection pooling via a shared client reduces latency and resource
    overhead by reusing TCP connections for multiple requests.
    """
    global _client
    if _client is None or _client.is_closed:
        async with _lock:
            # Double-check inside lock
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
                )
    return _client

async def close_async_client():
    """Close the shared httpx.AsyncClient instance."""
    global _client
    async with _lock:
        if _client is not None and not _client.is_closed:
            await _client.aclose()
            _client = None
