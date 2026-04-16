import httpx
import logging

logger = logging.getLogger("localmind.utils.http_client")

_async_client: httpx.AsyncClient = None

def get_async_client() -> httpx.AsyncClient:
    """Get or create the global pooled AsyncClient for connection reuse.

    Bolt Optimization: Reusing a single AsyncClient enables connection pooling,
    reducing latency by avoiding repeated TCP/TLS handshakes.
    """
    global _async_client
    if _async_client is None:
        # 120s total timeout, 10s connect timeout (matches LLMClient needs)
        timeout = httpx.Timeout(120.0, connect=10.0)
        _async_client = httpx.AsyncClient(timeout=timeout)
        logger.info("Initialized global pooled AsyncClient")
    return _async_client

async def close_async_client():
    """Gracefully close the global pooled AsyncClient during shutdown."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Closed global pooled AsyncClient")
