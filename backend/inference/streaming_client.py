"""
streaming_client.py - Resilient Ollama token streaming (Phase D5)
==================================================================
Solves the token-drop problem: when a model connection drops mid-stream,
tokens already received are preserved in a checkpoint buffer and the stream
can be resumed from a fallback provider or retried.

Key features:
  - Per-chunk token checkpoint buffer
  - Automatic retry on connection drops (up to MAX_RETRIES)
  - Yields partial content to the caller immediately (no buffering delay)
  - Falls back to non-streaming single-shot if all retries fail
  - Heartbeat timeout detection (model hung vs. model dropped)

Usage:
    from backend.inference.streaming_client import stream_ollama_chat

    async for chunk in stream_ollama_chat(messages, model):
        if chunk["type"] == "token":
            print(chunk["content"], end="", flush=True)
        elif chunk["type"] == "done":
            break
        elif chunk["type"] == "error":
            print("Error:", chunk["error"])
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

import httpx

from backend.config import OLLAMA_BASE_URL

logger = logging.getLogger("localmind.inference.streaming")

# --- Configuration ---
MAX_RETRIES = 3
RETRY_DELAY_S = 1.5       # seconds between retries
CHUNK_TIMEOUT_S = 45.0    # if no token for 45s, consider model hung
CONNECT_TIMEOUT_S = 15.0  # connection establishment timeout


class TokenCheckpoint:
    """
    Accumulates received tokens so partial content survives connection drops.

    On retry, the caller can inject the checkpoint content back as an assistant
    prefix so the model continues from where it left off.
    """

    def __init__(self):
        self._chunks: list[str] = []
        self._token_count: int = 0

    def record(self, token: str) -> None:
        self._chunks.append(token)
        self._token_count += 1

    @property
    def content(self) -> str:
        return "".join(self._chunks)

    @property
    def token_count(self) -> int:
        return self._token_count

    @property
    def is_empty(self) -> bool:
        return self._token_count == 0

    def clear(self) -> None:
        self._chunks.clear()
        self._token_count = 0


async def stream_ollama_chat(
    messages: list[dict],
    model: str,
    ollama_url: str = OLLAMA_BASE_URL,
    options: Optional[dict] = None,
    max_retries: int = MAX_RETRIES,
) -> AsyncGenerator[dict, None]:
    """
    Stream a chat completion from Ollama with resilient token checkpointing.

    Yields dicts:
      {"type": "token",   "content": "...", "token_count": N}
      {"type": "done",    "total_tokens": N, "retries": N}
      {"type": "warning", "message": "..."}   - on retry
      {"type": "error",   "error": "..."}     - on unrecoverable failure

    Args:
        messages:   Full message list (system + user + assistant history)
        model:      Ollama model name (e.g. "qwen2.5-coder:7b")
        ollama_url: Base Ollama URL (defaults to config)
        options:    Optional Ollama generation options dict
        max_retries: Number of reconnect attempts on drop
    """
    checkpoint = TokenCheckpoint()
    attempt = 0
    start_time = time.time()

    while attempt <= max_retries:
        attempt += 1
        if attempt > 1:
            logger.warning(
                f"Stream retry {attempt - 1}/{max_retries} for model {model} "
                f"(tokens already received: {checkpoint.token_count})"
            )
            yield {
                "type": "warning",
                "message": f"Model connection dropped. Retrying... ({attempt - 1}/{max_retries})",
                "tokens_saved": checkpoint.token_count,
            }
            await asyncio.sleep(RETRY_DELAY_S)

        # On retry: inject already-received content as assistant message prefix
        # so the model continues coherently from where it dropped
        effective_messages = list(messages)
        if not checkpoint.is_empty:
            effective_messages.append({
                "role": "assistant",
                "content": checkpoint.content,
            })
            # Ask model to continue from where it left off
            effective_messages.append({
                "role": "user",
                "content": "[SYSTEM: Continue your previous response from exactly where you stopped. Do not repeat anything.]",
            })

        payload = {
            "model": model,
            "messages": effective_messages,
            "stream": True,
        }
        if options:
            payload["options"] = options

        try:
            timeout = httpx.Timeout(
                connect=CONNECT_TIMEOUT_S,
                read=CHUNK_TIMEOUT_S,
                write=10.0,
                pool=5.0,
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{ollama_url}/api/chat",
                    json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise httpx.HTTPStatusError(
                            f"Ollama returned {resp.status_code}: {body.decode()[:200]}",
                            request=resp.request,
                            response=resp,
                        )

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            logger.debug(f"Non-JSON stream line skipped: {line[:80]}")
                            continue

                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            checkpoint.record(token)
                            yield {
                                "type": "token",
                                "content": token,
                                "token_count": checkpoint.token_count,
                            }

                        if chunk.get("done", False):
                            elapsed = time.time() - start_time
                            logger.info(
                                f"Stream complete: {checkpoint.token_count} tokens, "
                                f"{elapsed:.1f}s, {attempt - 1} retries"
                            )
                            yield {
                                "type": "done",
                                "total_tokens": checkpoint.token_count,
                                "elapsed_s": round(elapsed, 2),
                                "retries": attempt - 1,
                            }
                            return

            # If we exit the stream loop without a "done" chunk, treat as a drop
            logger.warning(f"Stream ended without done=True on attempt {attempt}")

        except httpx.ReadTimeout:
            logger.warning(
                f"Chunk timeout after {CHUNK_TIMEOUT_S}s on attempt {attempt}. "
                f"Tokens saved: {checkpoint.token_count}"
            )
            if attempt > max_retries:
                break
            continue  # retry

        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.HTTPStatusError) as exc:
            logger.warning(f"Stream connection error on attempt {attempt}: {exc}")
            if attempt > max_retries:
                break
            continue  # retry

        except Exception as exc:
            logger.error(f"Unexpected stream error: {exc}", exc_info=True)
            break

    # --- All retries exhausted ---
    if not checkpoint.is_empty:
        # We have partial content — yield what we have and signal partial completion
        logger.warning(
            f"All {max_retries} stream retries failed. "
            f"Returning {checkpoint.token_count} partially recovered tokens."
        )
        yield {
            "type": "warning",
            "message": f"Stream failed after {max_retries} retries. Returning partial response ({checkpoint.token_count} tokens received).",
        }
        yield {
            "type": "done",
            "total_tokens": checkpoint.token_count,
            "partial": True,
            "retries": attempt - 1,
        }
    else:
        # Nothing at all received — fall back to non-streaming single-shot
        logger.warning(f"Stream failed with zero tokens. Attempting non-streaming fallback.")
        yield {
            "type": "warning",
            "message": "Streaming failed. Falling back to single-shot mode...",
        }
        async for event in _non_streaming_fallback(messages, model, ollama_url, options):
            yield event


async def _non_streaming_fallback(
    messages: list[dict],
    model: str,
    ollama_url: str,
    options: Optional[dict],
) -> AsyncGenerator[dict, None]:
    """Last-resort single-shot non-streaming call when streaming fails entirely."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
            resp = await client.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    **({"options": options} if options else {}),
                },
            )
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            if content:
                yield {"type": "token", "content": content, "token_count": len(content.split())}
                yield {"type": "done", "total_tokens": len(content.split()), "fallback": True, "retries": MAX_RETRIES}
                return
    except Exception as exc:
        logger.error(f"Non-streaming fallback also failed: {exc}")

    yield {
        "type": "error",
        "error": "Both streaming and non-streaming attempts failed. Ollama may be down or the model is unavailable.",
    }
