import httpx
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from httpx import AsyncClient

# ⚡ Bolt: Global httpx.AsyncClient singleton to enable connection pooling.
# Reusing connections across the app reduces latency by avoiding repeated
# TCP/TLS handshakes, especially critical for LLM and RAG operations.
_async_client: Optional['httpx.AsyncClient'] = None

def get_async_client() -> 'httpx.AsyncClient':
    """Get or create the global httpx.AsyncClient.

    The client is configured with connection limits suitable for a
    multi-user agentic backend.
    """
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(
            # Generous default timeout; specific calls can override this.
            timeout=httpx.Timeout(60.0, connect=10.0),
            # Allow up to 100 concurrent connections, keeping 20 alive.
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _async_client

async def close_async_client():
    """Gracefully close the global async client during application shutdown."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
