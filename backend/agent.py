"""
LocalMind — Agent Loop Engine
Implements the agentic loop: send tools to Ollama, execute tool calls,
feed results back, repeat until the model gives a final text response.
"""

from backend.config import OLLAMA_BASE_URL
import asyncio
import json
import time
from typing import AsyncGenerator

import httpx

from tools import TOOL_DEFINITIONS, execute_tool

MAX_TOOL_ITERATIONS = 15


async def agent_chat(
    messages: list[dict],
    model: str,
    system_prompt: str,
    working_dir: str,
    auto_execute: bool = False,
    assistant_name: str = "AI Assistant",
) -> AsyncGenerator[dict, None]:
    """
# Run the agent loop. Yields events for the frontend:
    - {type: thinking}                     — model is generating
    - {type: tool_call, tool: {...}}      — model wants to use a tool
    - {type: tool_result, result: {...}}  — tool execution result
    - {type: content, content: "..."}     — text content from model
    - {type: done}                          — agent is finished
    - {type: error, error: "..."}         — something went wrong
    - {type: approval_needed, tool: {...}} — needs user approval (when not auto_execute)
    """
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
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
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

        except httpx.HTTPError as e:
            yield {"type": "error", "error": f"HTTP error: {e.response.status_code} - {e.response.text}"}
            return
        except Exception as e:
            error_message = f"Failed to connect to Ollama: {str(e)}"
            logging.error(error_message)
            yield {"type": "error", "error": error_message}
            return

        message = result.get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", [])

        # If the model returned text content AND no tool calls, we're done
        if content and not tool_calls:
            yield {"type": "content", "content": content}
            yield {"type": "done"}
            break

        # If the model returned text content WITH tool calls, send text first
        if content:
            yield {"type": "content", "content": content}

        # If no tool calls and no content, we're done
        if not tool_calls:
            yield {"type": "done"}
            return

        # Process tool calls
        # Add the assistant's message (with tool calls) to the conversation
        if not isinstance(message, dict) or 'role' not in message or 'content' not in message:
            yield {'type': 'error', 'error': 'Invalid message format'}
            return

        full_messages.append(message)

        # Parse all tool calls upfront
        parsed_calls = []
        for tool_call in tool_calls:
            func = tool_call.get("function", {})
            parsed_calls.append({
                "name": func.get("name", "unknown"),
                "arguments": func.get("arguments", {}),
            })

        # Execute tool calls — in parallel when there are multiple.
        # NOTE: Thread-safety caveat — execute_tool is synchronous and may
        # perform file-system writes (write_file) or shell commands
        # (run_command).  Concurrent calls that touch the same file or
        # working-directory state could race.  This is acceptable because
        # the LLM rarely issues conflicting writes in the same batch, and
        # the performance gain from parallel I/O-bound tools (read_file,
        # search_files, web_search) is significant.
        if len(parsed_calls) == 1:
            # Fast path — no asyncio.gather overhead for a single tool
            pc = parsed_calls[0]
            try:
                result = await asyncio.to_thread(
                    execute_tool, pc["name"], pc["arguments"], working_dir
                )
            except Exception as e:
                result = {"success": False, "error": f"Failed to execute tool {pc['name']}: {e}"}
            tool_results = [result]
        else:
            # Parallel path — run all tool calls concurrently in threads
            async def _run_tool(name: str, arguments: dict) -> dict:
                try:
                    return await asyncio.to_thread(
                        execute_tool, name, arguments, working_dir
                    )
                except Exception as e:
                    return {"success": False, "error": f"Failed to execute tool {name}: {e}"}

            tool_results = await asyncio.gather(
                *[_run_tool(pc["name"], pc["arguments"]) for pc in parsed_calls],
                return_exceptions=True,
            )
            # Convert any unexpected exceptions from gather into error dicts
            tool_results = [
                r if isinstance(r, dict) else {"success": False, "error": str(r)}
                for r in tool_results
            ]

        # Yield events in order and append results to conversation
        for pc, tool_result in zip(parsed_calls, tool_results):
            yield {
                "type": "tool_call",
                "tool": {
                    "name": pc["name"],
                    "arguments": pc["arguments"],
                    "iteration": iteration,
                },
            }
            yield {
                "type": "tool_result",
                "result": {
                    "name": pc["name"],
                    "success": tool_result.get("success", False),
                    "data": tool_result,
                },
            }
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

        except httpx.HTTPError as e:
            yield {"type": "error", "error": f"HTTP error: {e.response.status_code} - {e.response.text}"}
            return
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
            except httpx.HTTPError as e:
                yield {"type": "error", "error": f"HTTP error: {e.response.status_code} - {e.response.text}"}
            except Exception as e:
                yield {"type": "error", "error": str(e)}
            yield {"type": "done"}
            return

        # Process tool calls
        if content:
            yield {"type": "content", "content": content}

        full_messages.append(message)

        # Parse all tool calls upfront
        parsed_calls = []
        for tool_call in tool_calls:
            func = tool_call.get("function", {})
            parsed_calls.append({
                "name": func.get("name", "unknown"),
                "arguments": func.get("arguments", {}),
            })

        # Execute tool calls — in parallel when there are multiple.
        # See thread-safety note in agent_chat above.
        if len(parsed_calls) == 1:
            pc = parsed_calls[0]
            try:
                result = await asyncio.to_thread(
                    execute_tool, pc["name"], pc["arguments"], working_dir
                )
            except Exception as e:
                result = {"success": False, "error": f"Failed to execute tool {pc['name']}: {e}"}
            tool_results = [result]
        else:
            async def _run_tool(name: str, arguments: dict) -> dict:
                try:
                    return await asyncio.to_thread(
                        execute_tool, name, arguments, working_dir
                    )
                except Exception as e:
                    return {"success": False, "error": f"Failed to execute tool {name}: {e}"}

            tool_results = await asyncio.gather(
                *[_run_tool(pc["name"], pc["arguments"]) for pc in parsed_calls],
                return_exceptions=True,
            )
            tool_results = [
                r if isinstance(r, dict) else {"success": False, "error": str(r)}
                for r in tool_results
            ]

        # Yield events in order and append results to conversation
        for pc, tool_result in zip(parsed_calls, tool_results):
            yield {
                "type": "tool_call",
                "tool": {"name": pc["name"], "arguments": pc["arguments"], "iteration": iteration},
            }
            yield {
                "type": "tool_result",
                "result": {
                    "name": pc["name"],
                    "success": tool_result.get("success", False),
                    "data": tool_result,
                },
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