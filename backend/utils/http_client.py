import httpx
from typing import Optional

# ⚡ Bolt: Global shared AsyncClient to enable connection pooling and reuse.
# Reduces overhead by avoiding repeated TCP/TLS handshakes.
_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Return the global httpx.AsyncClient singleton."""
    global _async_client
    if _async_client is None:
        # Default timeout of 120s covers long LLM requests.
        # Can be overridden per-request (e.g. client.get(..., timeout=5.0)).
        timeout = httpx.Timeout(120.0, connect=10.0)
        _async_client = httpx.AsyncClient(timeout=timeout)
    return _async_client

async def close_async_client():
    """Close the global httpx.AsyncClient singleton."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
