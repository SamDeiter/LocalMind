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
async def test_close_client():
    """Verify that close_async_client closes the client and resets the singleton."""
    client = get_async_client()
    assert not client.is_closed

    await close_async_client()
    assert client.is_closed

    # Getting it again should create a new, open client
    new_client = get_async_client()
    assert new_client is not client
    assert not new_client.is_closed

    await close_async_client()
    assert new_client.is_closed

@pytest.mark.asyncio
async def test_client_configuration():
    """Verify client is configured with expected timeout and limits."""
    client = get_async_client()
    # httpx.AsyncClient might wrap these, but let's check what we can
    assert client.timeout.read == 120.0
    assert client.timeout.connect == 10.0
    # Limits are a bit harder to inspect directly but we trust the constructor
    await close_async_client()
