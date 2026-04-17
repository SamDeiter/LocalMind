import httpx
from typing import Optional

# ⚡ Bolt: Global pooled HTTP client to reduce handshake latency and improve concurrency.
_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get the global pooled httpx.AsyncClient instance."""
    global _async_client
    if _async_client is None:
        # 120s timeout matches existing usage in LLMClient
        _async_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    return _async_client

async def close_async_client():
    """Close the global pooled httpx.AsyncClient instance."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
