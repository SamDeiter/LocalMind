import httpx
from typing import Optional

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create a global httpx.AsyncClient singleton.

    This enables connection pooling across the entire application,
    reducing the overhead of TCP/TLS handshakes for every request.
    """
    global _async_client
    if _async_client is None:
        # Standard timeout for LLM tasks: 120s total, 10s connect.
        timeout = httpx.Timeout(120.0, connect=10.0)
        _async_client = httpx.AsyncClient(timeout=timeout)
    return _async_client

async def close_async_client():
    """Gracefully close the global httpx.AsyncClient singleton."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
