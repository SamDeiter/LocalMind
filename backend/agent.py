"""
LocalMind — Agent Loop Engine
Implements the agentic loop: send tools to Ollama, execute tool calls,
feed results back, repeat until the model gives a final text response.
"""

import json
import time
from typing import AsyncGenerator

import httpx

from tools import TOOL_DEFINITIONS, execute_tool

OLLAMA_BASE_URL = "http://localhost:11434"
MAX_TOOL_ITERATIONS = 15


async def agent_chat(
    messages: list[dict],
    model: str,
    system_prompt: str,
    working_dir: str,
    auto_execute: bool = False,
) -> AsyncGenerator[dict, None]:
    """
    Run the agent loop. Yields events for the frontend:
    - {"type": "thinking"}                     — model is generating
    - {"type": "tool_call", "tool": {...}}      — model wants to use a tool
    - {"type": "tool_result", "result": {...}}  — tool execution result
    - {"type": "content", "content": "..."}     — text content from model
    - {"type": "done"}                          — agent is finished
    - {"type": "error", "error": "..."}         — something went wrong
    - {"type": "approval_needed", "tool": {...}} — needs user approval (when not auto_execute)
    """

    # Build the full messages list with system prompt
    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    iteration = 0

    while iteration < MAX_TOOL_ITERATIONS:
        iteration += 1
        yield {"type": "thinking", "iteration": iteration}

        try:
            # Call Ollama with tools
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json={
                        "model": model,
                        "messages": full_messages,
                        "tools": TOOL_DEFINITIONS,
                        "stream": False,  # Non-streaming for tool calls
                    },
                )
                result = response.json()

        except Exception as e:
            yield {"type": "error", "error": f"Failed to connect to Ollama: {e}"}
            return

        message = result.get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", [])

        # If the model returned text content AND no tool calls, we're done
        if content and not tool_calls:
            yield {"type": "content", "content": content}
            yield {"type": "done"}
            return

        # If the model returned text content WITH tool calls, send text first
        if content:
            yield {"type": "content", "content": content}

        # If no tool calls and no content, we're done
        if not tool_calls:
            yield {"type": "done"}
            return

        # Process tool calls
        # Add the assistant's message (with tool calls) to the conversation
        full_messages.append(message)

        for tool_call in tool_calls:
            func = tool_call.get("function", {})
            tool_name = func.get("name", "unknown")
            tool_args = func.get("arguments", {})

            # Yield tool call event for the frontend
            tool_event = {
                "type": "tool_call",
                "tool": {
                    "name": tool_name,
                    "arguments": tool_args,
                    "iteration": iteration,
                },
            }
            yield tool_event

            # Execute the tool
            tool_result = execute_tool(tool_name, tool_args, working_dir)

            # Yield the result
            yield {
                "type": "tool_result",
                "result": {
                    "name": tool_name,
                    "success": tool_result.get("success", False),
                    "data": tool_result,
                },
            }

            # Add the tool result to the conversation for the next iteration
            full_messages.append({
                "role": "tool",
                "content": json.dumps(tool_result),
            })

    # If we hit the iteration limit
    yield {
        "type": "content",
        "content": "I've reached the maximum number of tool iterations. Here's what I've done so far — please let me know if you'd like me to continue.",
    }
    yield {"type": "done"}


async def agent_chat_streaming(
    messages: list[dict],
    model: str,
    system_prompt: str,
    working_dir: str,
    auto_execute: bool = False,
) -> AsyncGenerator[dict, None]:
    """
    Like agent_chat, but streams the final text response token by token.
    Uses non-streaming for tool-calling rounds, then streams the final response.
    """

    # Build the full messages list with system prompt
    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    iteration = 0

    while iteration < MAX_TOOL_ITERATIONS:
        iteration += 1
        yield {"type": "thinking", "iteration": iteration}

        try:
            # First, try with tools (non-streaming to get tool calls)
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json={
                        "model": model,
                        "messages": full_messages,
                        "tools": TOOL_DEFINITIONS,
                        "stream": False,
                    },
                )
                result = response.json()

        except Exception as e:
            yield {"type": "error", "error": f"Failed to connect to Ollama: {e}"}
            return

        message = result.get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", [])

        # No tool calls — stream the final response
        if not tool_calls:
            if content:
                # We already got non-streamed content, send it
                yield {"type": "content", "content": content}
                yield {"type": "done"}
                return

            # Try again without tools to get a streaming response
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                    async with client.stream(
                        "POST",
                        f"{OLLAMA_BASE_URL}/api/chat",
                        json={
                            "model": model,
                            "messages": full_messages,
                            "stream": True,
                        },
                    ) as resp:
                        async for line in resp.aiter_lines():
                            if line.strip():
                                try:
                                    chunk = json.loads(line)
                                    text = chunk.get("message", {}).get("content", "")
                                    if text:
                                        yield {"type": "content", "content": text}
                                    if chunk.get("done", False):
                                        break
                                except json.JSONDecodeError:
                                    continue
            except Exception as e:
                yield {"type": "error", "error": str(e)}
            yield {"type": "done"}
            return

        # Process tool calls
        if content:
            yield {"type": "content", "content": content}

        full_messages.append(message)

        for tool_call in tool_calls:
            func = tool_call.get("function", {})
            tool_name = func.get("name", "unknown")
            tool_args = func.get("arguments", {})

            yield {
                "type": "tool_call",
                "tool": {"name": tool_name, "arguments": tool_args, "iteration": iteration},
            }

            tool_result = execute_tool(tool_name, tool_args, working_dir)

            yield {
                "type": "tool_result",
                "result": {"name": tool_name, "success": tool_result.get("success", False), "data": tool_result},
            }

            full_messages.append({
                "role": "tool",
                "content": json.dumps(tool_result),
            })

    yield {
        "type": "content",
        "content": "I've reached the maximum number of tool iterations. Here's what I've done so far.",
    }
    yield {"type": "done"}
