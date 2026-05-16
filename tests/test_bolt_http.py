import httpx
import pytest

from backend.utils.http_client import close_async_client, get_async_client


@pytest.mark.asyncio
async def test_async_client_singleton():
    """Verify that get_async_client returns the same instance and handles lifecycle correctly."""
    client1 = get_async_client()
    client2 = get_async_client()

    assert client1 is client2
    assert isinstance(client1, httpx.AsyncClient)
    assert not client1.is_closed

    await close_async_client()
    assert client1.is_closed

    # Should create a new one after closing
    client3 = get_async_client()
    assert client3 is not client1
    assert not client3.is_closed

    await close_async_client()
    assert client3.is_closed


@pytest.mark.asyncio
async def test_async_client_config():
    """Verify the shared client has expected configuration."""
    client = get_async_client()

    # Check limits via internal pool
    assert client._transport._pool._max_connections == 100
    assert client._transport._pool._max_keepalive_connections == 20

    # Check default timeout (30.0s)
    # Note: httpx.Timeout object might not be directly comparable easily depending on version,
    # but we can check the attributes.
    assert client.timeout.read == 30.0
    assert client.timeout.connect == 10.0

    await close_async_client()
