import pytest
import httpx
from backend.utils.http_client import get_async_client, close_async_client

@pytest.mark.asyncio
async def test_shared_client_singleton():
    """Verify that get_async_client returns the same instance."""
    client1 = await get_async_client()
    client2 = await get_async_client()
    assert client1 is client2
    assert not client1.is_closed

@pytest.mark.asyncio
async def test_shared_client_recreation_after_close():
    """Verify that a new client is created if the old one is closed."""
    client1 = await get_async_client()
    await close_async_client()
    assert client1.is_closed

    client2 = await get_async_client()
    assert client2 is not client1
    assert not client2.is_closed
    await close_async_client()

@pytest.mark.asyncio
async def test_shared_client_settings():
    """Verify the shared client has expected timeout and limits."""
    client = await get_async_client()
    # Check timeout (httpx.Timeout is an object)
    assert client.timeout.connect == 10.0
    assert client.timeout.read == 30.0

    assert isinstance(client, httpx.AsyncClient)
