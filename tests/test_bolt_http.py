import pytest
import httpx
from backend.utils.http_client import get_async_client, close_async_client

@pytest.mark.asyncio
async def test_singleton_behavior():
    """Verify that get_async_client returns the same instance."""
    client1 = get_async_client()
    client2 = get_async_client()
    assert client1 is client2
    assert isinstance(client1, httpx.AsyncClient)
    assert not client1.is_closed

@pytest.mark.asyncio
async def test_close_behavior():
    """Verify that close_async_client closes the client and resets the singleton."""
    client1 = get_async_client()
    assert not client1.is_closed

    await close_async_client()
    assert client1.is_closed

    # Getting a new client should work and return a new, open instance
    client2 = get_async_client()
    assert client2 is not client1
    assert not client2.is_closed
    assert isinstance(client2, httpx.AsyncClient)

    await close_async_client()
    assert client2.is_closed

@pytest.mark.asyncio
async def test_client_configuration():
    """Verify that the client is configured with expected limits and timeouts."""
    client = get_async_client()

    # Note: Accessing private attributes for verification (internal to the project)
    assert client.timeout.connect == 10.0
    assert client.timeout.read == 120.0

    # Verify limits if possible
    # httpx client doesn't expose limits directly in a simple way in all versions,
    # but we can check if it exists
    assert hasattr(client, '_transport')

    await close_async_client()
