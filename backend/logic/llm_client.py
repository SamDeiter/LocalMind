import json
import logging
import httpx
import asyncio
from typing import Optional, List, Dict, Any, AsyncIterator
from backend import config, gemini_client

logger = logging.getLogger("localmind.logic.llm_client")


class LLMClient:
    """Unified client for interacting with both local (Ollama) and cloud (Gemini) models.

    Provides a consistent interface for streaming and non-streaming requests,
    handling retries, and formatting outputs for tools.
    """

    def __init__(self, ollama_base_url: str = config.OLLAMA_BASE_URL):
        self.ollama_url = ollama_base_url.rstrip("/")
        self.timeout = httpx.Timeout(180.0, connect=10.0)
        # Shared client — reuses TCP connections across calls instead of
        # opening/closing a new connection per request.
        self._client: httpx.AsyncClient | None = None
        # Track last warmed model to skip redundant warm calls.
        self._warm_model_name: str | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def warm_model(self, model: str) -> None:
        """Pre-load a model into Ollama VRAM before the first real call.

        Sends a generate request with ``num_predict=0`` so Ollama loads the
        model but produces zero tokens.  Skips if same model is already warm.
        """
        if model == self._warm_model_name:
            return
        import time
        url = f"{self.ollama_url}/api/generate"
        payload = {
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": "10m",
            "options": {"num_predict": 0},
        }
        logger.info("Pre-warming model '%s' for chat…", model)
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=10.0)
            ) as warm_client:
                resp = await warm_client.post(url, json=payload)
            elapsed = time.monotonic() - t0
            if resp.status_code == 200:
                self._warm_model_name = model
                logger.info("Chat model '%s' warm in %.1fs.", model, elapsed)
            else:
                logger.warning("Chat warm HTTP %d (%.1fs)", resp.status_code, elapsed)
        except httpx.TimeoutException:
            logger.error("Chat model warm timed out for '%s'.", model)
        except Exception as exc:
            logger.warning("Chat model warm failed for '%s': %s", model, exc)

    @staticmethod
    def _build_tool_hint(tools: list) -> str:
        """Build a text description of available tools for non-native-tool models.

        This lets the model know what tools exist so it can attempt to call them
        using JSON that parse_text_tools() can extract.
        """
        if not tools:
            return ""
        lines = ["\n\nAVAILABLE TOOLS (call by outputting JSON with \"name\" and \"arguments\" keys):"]
        for tool in tools[:20]:  # Cap to avoid bloating context
            func = tool.get("function", tool) if isinstance(tool, dict) else tool
            name = func.get("name", "?")
            desc = func.get("description", "")[:120]
            params = func.get("parameters", {}).get("properties", {})
            param_names = ", ".join(params.keys()) if params else ""
            lines.append(f"- {name}({param_names}): {desc}")
        lines.append(
            '\nTo call a tool, output: {"name": "<tool_name>", "arguments": {"param": "value"}}'
        )
        return "\n".join(lines)

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
        # Pass tool definitions for native tool calling (skip for models that don't support it)
        if "tools" in kwargs:
            tools = kwargs.pop("tools")
            if model not in config.MODELS_NO_NATIVE_TOOLS:
                payload["tools"] = tools
            else:
                # Inject tool descriptions into system prompt so the model knows
                # what tools exist and can attempt JSON-formatted tool calls that
                # parse_text_tools() can catch.
                tool_hint = self._build_tool_hint(tools)
                if tool_hint and payload.get("messages"):
                    for msg in payload["messages"]:
                        if msg.get("role") == "system":
                            msg["content"] += tool_hint
                            break
                logger.info(f"Injected tool hint into system prompt for {model} (no native tools)")
        payload.update(kwargs)

        max_retries = 3
        backoff_factor = 1

        logger.info(f"Ollama request: model={payload.get('model')}, messages={len(payload.get('messages',[]))}, keys={list(payload.keys())}")

        # Pre-warm: ensure the model is in VRAM before the streaming timeout
        # starts.  This prevents model-swap latency from causing timeouts.
        await self.warm_model(model)

        client = await self._get_client()
        for attempt in range(max_retries):
            try:
                async with client.stream("POST", url, json=payload, timeout=self.timeout) as response:
                    if response.status_code != 200:
                        err = await response.aread()
                        logger.error(f"Ollama stream error: {err.decode()}")
                        yield {"error": f"Ollama error {response.status_code}"}
                        return

                    line_count = 0
                    _in_think = False
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

                            # Filter out qwen3-style <think>…</think> blocks
                            if "<think>" in token:
                                _in_think = True
                                token = token.split("<think>")[0]
                            if _in_think:
                                if "</think>" in token:
                                    _in_think = False
                                    token = token.split("</think>", 1)[-1]
                                else:
                                    token = ""

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

        client = await self._get_client()
        try:
            r = await client.post(url, json=payload)
            data = r.json()
            return {
                "content": data.get("message", {}).get("content", ""),
                "tool_calls": data.get("message", {}).get("tool_calls", []),
            }
        except Exception as e:
            return {"error": str(e)}
