import httpx
from typing import Optional

_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """Get the global httpx.AsyncClient singleton."""
    global _async_client
    if _async_client is None:
        # We don't initialize it here because we want it to be created
        # within an async context (e.g., during FastAPI lifespan).
        # However, for robustness, we can provide a default if it's called
        # before the server has properly initialized it.
        _async_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    return _async_client

async def close_async_client():
    """Close the global httpx.AsyncClient."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
