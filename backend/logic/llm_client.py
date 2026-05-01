import json
import logging
import httpx
import asyncio
from typing import Optional, List, Dict, Any, AsyncIterator
from backend import config, gemini_client
from backend.utils.http_client import get_async_client

logger = logging.getLogger("localmind.logic.llm_client")


class LLMClient:
    """Unified client for interacting with both local (Ollama) and cloud (Gemini) models.

    Provides a consistent interface for streaming and non-streaming requests,
    handling retries, and formatting outputs for tools.
    """

    def __init__(self, ollama_base_url: str = config.OLLAMA_BASE_URL):
        self.ollama_url = ollama_base_url.rstrip("/")
        self.timeout = httpx.Timeout(120.0, connect=10.0)

    async def generate_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        provider: str = "ollama",
        **kwargs
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream tokens from the selected provider."""
        if provider == "gemini":
            async for chunk in self._stream_gemini(model, messages, **kwargs):
                yield chunk
        else:
            async for chunk in self._stream_ollama(model, messages, **kwargs):
                yield chunk

    async def _stream_ollama(
        self, model: str, messages: List[Dict[str, str]], **kwargs
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream from Ollama with retry logic."""
        url = f"{self.ollama_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        # Merge extra options (num_ctx, num_gpu, etc.)
        if "options" in kwargs:
            payload["options"] = kwargs.pop("options")
        # Pass tool definitions for native tool calling
        if "tools" in kwargs:
            payload["tools"] = kwargs.pop("tools")
        payload.update(kwargs)

        max_retries = 3
        backoff_factor = 1

        logger.info(f"Ollama request: model={payload.get('model')}, messages={len(payload.get('messages',[]))}, keys={list(payload.keys())}")

        client = get_async_client()
        for attempt in range(max_retries):
            try:
                # ⚡ Bolt: Use the shared global client.stream() within an 'async with' context.
                async with client.stream("POST", url, json=payload, timeout=self.timeout) as response:
                    if response.status_code != 200:
                        err = await response.aread()
                        logger.error(f"Ollama stream error: {err.decode()}")
                        yield {"error": f"Ollama error {response.status_code}"}
                        return

                    line_count = 0
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        line_count += 1
                        try:
                            data = json.loads(line)
                            token = ""
                            if "message" in data:
                                token = data["message"].get("content", "")
                            elif "response" in data:
                                token = data.get("response", "")

                            yield {
                                "token": token,
                                "done": data.get("done", False),
                                "tool_calls": data.get("message", {}).get("tool_calls", []),
                            }
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to decode JSON: {line[:100]}")
                            continue
                logger.info(f"Ollama stream completed: {line_count} lines received")
                return

            except Exception as e:
                    if attempt < max_retries - 1:
                        wait_time = backoff_factor * (2 ** attempt)
                        logger.warning(f"Ollama attempt {attempt+1} failed: {e}. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"Ollama stream failed after {max_retries} attempts: {e}")
                        yield {"error": str(e)}

    async def _stream_gemini(
        self, model: str, messages: List[Dict[str, str]], **kwargs
    ) -> AsyncIterator[Dict[str, Any]]:
        """Wrapper for Gemini streaming."""
        try:
            async for chunk in gemini_client.stream_chat(messages, model=model):
                yield {
                    "token": chunk,
                    "done": False,
                    "tool_calls": [],
                }
            yield {"token": "", "done": True, "tool_calls": []}
        except Exception as e:
            logger.error(f"Gemini stream failed: {e}")
            yield {"error": str(e)}

    async def generate(
        self,
        model: str,
        messages: List[Dict[str, str]],
        provider: str = "ollama",
        **kwargs
    ) -> Dict[str, Any]:
        """Non-streaming generation."""
        if provider == "gemini":
            try:
                content = await gemini_client.generate_content(messages, model=model)
                return {"content": content, "tool_calls": []}
            except Exception as e:
                return {"error": str(e)}

        url = f"{self.ollama_url}/api/chat"
        payload = {"model": model, "messages": messages, "stream": False}
        if "options" in kwargs:
            payload["options"] = kwargs.pop("options")
        payload.update(kwargs)

        client = get_async_client()
        try:
            # ⚡ Bolt: Use the shared global client.post() for connection reuse.
            r = await client.post(url, json=payload, timeout=self.timeout)
            data = r.json()
            return {
                "content": data.get("message", {}).get("content", ""),
                "tool_calls": data.get("message", {}).get("tool_calls", []),
            }
        except Exception as e:
            return {"error": str(e)}
