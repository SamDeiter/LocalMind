import pytest
import httpx
from backend.utils.http_client import get_async_client, close_async_client

@pytest.mark.asyncio
async def test_singleton_behavior():
    """Verify that multiple calls to get_async_client return the same instance."""
    client1 = get_async_client()
    client2 = get_async_client()

    assert client1 is client2
    assert isinstance(client1, httpx.AsyncClient)

    # Clean up for other tests
    await close_async_client()

@pytest.mark.asyncio
async def test_client_reinitialization():
    """Verify that the client can be reinitialized after closing."""
    client1 = get_async_client()
    await close_async_client()

    client2 = get_async_client()
    assert client1 is not client2
    assert isinstance(client2, httpx.AsyncClient)

    await close_async_client()

@pytest.mark.asyncio
async def test_concurrent_access():
    """Verify that the shared client handles concurrent requests (simulated)."""
    import asyncio

    client = get_async_client()

    async def dummy_task():
        return get_async_client()

    results = await asyncio.gather(*(dummy_task() for _ in range(10)))

    for r in results:
        assert r is client

    await close_async_client()
