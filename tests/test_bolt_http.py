import pytest
import httpx
from backend.utils.http_client import get_async_client, close_async_client

@pytest.mark.asyncio
async def test_shared_client_singleton():
    """Verify that get_async_client always returns the same instance."""
    client1 = get_async_client()
    client2 = get_async_client()

    assert client1 is client2
    assert isinstance(client1, httpx.AsyncClient)
    assert not client1.is_closed

@pytest.mark.asyncio
async def test_shared_client_lifecycle():
    """Verify that the client can be closed and re-initialized."""
    client1 = get_async_client()
    assert not client1.is_closed

    await close_async_client()
    assert client1.is_closed

    # Re-initializing should create a new open client
    client2 = get_async_client()
    assert client2 is not client1
    assert not client2.is_closed

    # Cleanup
    await close_async_client()
