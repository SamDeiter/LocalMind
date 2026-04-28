import asyncio
import httpx
from backend.utils.http_client import get_async_client, close_async_client
from backend.logic.llm_client import LLMClient
import pytest

@pytest.mark.asyncio
async def test_shared_client_reuse():
    client1 = get_async_client()
    client2 = get_async_client()
    assert client1 is client2
    await close_async_client()

@pytest.mark.asyncio
async def test_llm_client_uses_shared_client(mocker):
    # Mock the shared client
    shared_client = get_async_client()
    mock_post = mocker.patch.object(shared_client, 'post', autospec=True)
    mock_post.return_value = mocker.Mock(status_code=200, json=lambda: {"message": {"content": "test"}})

    llm = LLMClient()
    await llm.generate("test-model", [{"role": "user", "content": "hi"}])

    assert mock_post.called
    # Check that it was called with the shared client (implicit since we patched it)
    await close_async_client()
