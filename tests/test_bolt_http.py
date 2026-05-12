import pytest
import httpx
from backend.utils.http_client import get_async_client, close_async_client

@pytest.mark.asyncio
async def test_shared_client_singleton():
    """Verify that get_async_client returns the same instance."""
    client1 = get_async_client()
    client2 = get_async_client()

    assert client1 is client2
    assert isinstance(client1, httpx.AsyncClient)
    assert not client1.is_closed

@pytest.mark.asyncio
async def test_shared_client_lifecycle():
    """Verify that the client can be closed and re-initialized."""
    client_old = get_async_client()
    assert not client_old.is_closed

    await close_async_client()
    assert client_old.is_closed

    # Re-initialization
    client_new = get_async_client()
    assert client_new is not client_old
    assert not client_new.is_closed

    await close_async_client()
    assert client_new.is_closed

@pytest.mark.asyncio
async def test_shared_client_configuration():
    """Verify pooled client limits."""
    client = get_async_client()
    # Accessing private transport pool to verify configuration
    # In httpx 0.28+, limits are stored in the transport's pool
    pool = client._transport._pool
    assert pool._max_connections == 100
    assert pool._max_keepalive_connections == 20
    await close_async_client()
