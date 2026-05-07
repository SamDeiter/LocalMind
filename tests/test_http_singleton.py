import pytest
import httpx
from backend.utils.http_client import get_async_client, close_async_client

@pytest.mark.asyncio
async def test_async_client_singleton():
    """Verify that get_async_client returns the same instance and it is not closed."""
    client1 = get_async_client()
    client2 = get_async_client()

    assert client1 is client2
    assert not client1.is_closed
    assert isinstance(client1, httpx.AsyncClient)

@pytest.mark.asyncio
async def test_close_async_client():
    """Verify that close_async_client closes the client and subsequent calls create a new one."""
    client1 = get_async_client()
    assert not client1.is_closed

    await close_async_client()
    assert client1.is_closed

    client2 = get_async_client()
    assert client2 is not client1
    assert not client2.is_closed

    await close_async_client()
