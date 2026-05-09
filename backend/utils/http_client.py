import httpx
from typing import Optional

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get the global shared httpx.AsyncClient singleton.

    This client is pooled and intended for reuse across the application
    to avoid the overhead of repeated TCP/TLS handshakes.
    """
    global _async_client
    if _async_client is None:
        # ⚡ Bolt: Using connection pooling to reduce latency.
        # Limits: 100 total connections, 20 keep-alive connections.
        # Default timeout: 120s (matching LLMClient's requirement).
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True
        )
    return _async_client

async def close_async_client():
    """Gracefully close the shared httpx.AsyncClient singleton."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
