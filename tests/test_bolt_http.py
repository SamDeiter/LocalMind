import pytest
import httpx
from backend.utils.http_client import get_async_client, close_async_client

@pytest.mark.asyncio
async def test_shared_client_singleton():
    client1 = get_async_client()
    client2 = get_async_client()
    assert client1 is client2
    assert isinstance(client1, httpx.AsyncClient)
    assert not client1.is_closed

@pytest.mark.asyncio
async def test_shared_client_close():
    client = get_async_client()
    await close_async_client()
    assert client.is_closed

    # Getting a new client after close should work and return a new open client
    client3 = get_async_client()
    assert client3 is not client
    assert not client3.is_closed
    await close_async_client()
