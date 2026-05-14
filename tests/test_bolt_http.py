import pytest
import httpx
import asyncio
from backend.utils.http_client import get_async_client, close_async_client

@pytest.mark.asyncio
async def test_singleton_client():
    client1 = get_async_client()
    client2 = get_async_client()
    assert client1 is client2
    assert not client1.is_closed

@pytest.mark.asyncio
async def test_close_client():
    client = get_async_client()
    await close_async_client()
    assert client.is_closed

    # Getting again should create a new one
    client_new = get_async_client()
    assert client_new is not client
    assert not client_new.is_closed
    await close_async_client()

@pytest.mark.asyncio
async def test_client_options():
    client = get_async_client()
    # Check default timeout (30s)
    assert client.timeout.read == 30.0
    assert client.timeout.connect == 10.0
    await close_async_client()
