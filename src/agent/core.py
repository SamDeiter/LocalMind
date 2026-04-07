"""
Agent Core — ReAct-style reasoning loop with tool calling.

This is the main agent that ties together:
- LLM inference (via Ollama or llama.cpp)
- Tool registration and execution
- Memory retrieval and persistence
- Context window management

Architecture informed by:
- arXiv:2510.03847 — SLM-default routing: start with smallest capable model
- arXiv:2512.08769 — Production agentic workflows: ReAct loop with guardrails,
  max iterations, and structured tool calling
- arXiv:2512.03571 (EnCompass) — Branchpoint pattern: when a tool call fails,
  backtrack to the last decision point rather than starting over
- arXiv:2506.02153 — NVIDIA S3 "Structured Output Enforcement": use JSON Schema
  to constrain tool calls, reducing hallucinated parameters

ReAct loop:
  1. Observe: user message + retrieved memories + tool results
  2. Think: LLM generates reasoning + optional tool call
  3. Act: execute tool, feed result back as observation
  4. Repeat until LLM generates a final answer (no tool call)
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .branchpoint import BranchpointManager
from .config import (
    OLLAMA_BASE_URL,
    BACKEND,
    LLAMACPP_URL,
    MAX_ITERATIONS,
    MAX_BACKTRACKS,
    MAX_CONTEXT_TOKENS,
    detect_hardware,
    select_model,
)
from .memory.context import ContextManager
from .memory.retrieval import MemoryRetriever
from .tools.base import Tool

logger = logging.getLogger("agent.core")


# ---------------------------------------------------------------------------
# Branchpoint helpers (loop detection)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ToolCallRecord:
    """Lightweight record of a past tool invocation for loop detection."""
    name: str
    arguments_json: str  # canonical JSON so we can compare by value


def _is_tool_failure(result: dict) -> bool:
    """Return True when a tool result should be treated as a failure."""
    if "error" in result:
        return True
    if result.get("success") is False:
        return True
    return False


def _detect_loop(history: deque[_ToolCallRecord], window: int = 3) -> bool:
    """Return True if the last *window* tool calls are identical."""
    if len(history) < window:
        return False
    recent = list(history)[-window:]
    return all(r == recent[0] for r in recent)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
AGENT_SYSTEM_PROMPT = """You are a helpful local AI agent. You can use tools to accomplish tasks.

When you need to use a tool, output a JSON tool call in this exact format:
```tool_call
{"name": "tool_name", "arguments": {"arg1": "value1"}}
```

After receiving a tool result, analyze it and either:
1. Make another tool call if more information is needed
2. Provide your final answer to the user

Think step by step. Explain your reasoning briefly before each tool call.
When you have enough information, provide a clear, complete answer."""


class Agent:
    """Local-first AI agent with ReAct-style reasoning.

    Usage:
        agent = Agent()
        agent.register_tool(FileReadTool())
        agent.register_tool(ShellTool())

        response = await agent.run("Find all Python files that import requests")
        print(response)
    """

    def __init__(
        self,
        model: Optional[str] = None,
        ollama_url: str = OLLAMA_BASE_URL,
        system_prompt: str = AGENT_SYSTEM_PROMPT,
        max_iterations: int = MAX_ITERATIONS,
        max_backtracks: int = MAX_BACKTRACKS,
    ):
        # Auto-select model based on hardware if not specified
        if model is None:
            hw = detect_hardware()
            spec = select_model(hw, prefer_tool_calling=True)
            self.model = spec.name
            logger.info(f"Auto-selected model: {self.model} (hardware tier: {hw.tier})")
        else:
            self.model = model

        self.ollama_url = ollama_url.rstrip("/")
        self.max_iterations = max_iterations
        self.max_backtracks = max_backtracks

        # Subsystems
        self.tools: dict[str, Tool] = {}
        self.context = ContextManager(max_tokens=MAX_CONTEXT_TOKENS)
        self.memory = MemoryRetriever()

        # Set system prompt
        self.context.add_system(system_prompt)

    # ------------------------------------------------------------------
    # Tool management
    # ------------------------------------------------------------------

    def register_tool(self, tool: Tool) -> None:
        """Register a tool for the agent to use."""
        self.tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")

    def register_defaults(self) -> None:
        """Register the default tool set."""
        from .tools.file_ops import FileReadTool, FileWriteTool, FileSearchTool, FileListTool
        from .tools.shell import ShellTool
        from .tools.web import WebFetchTool, WebSearchTool

        for tool_cls in [FileReadTool, FileWriteTool, FileSearchTool, FileListTool,
                         ShellTool, WebFetchTool, WebSearchTool]:
            self.register_tool(tool_cls())

        # Register ToolMaker — lets the agent create new tools at runtime
        from .tools.toolmaker import ToolMakerTool
        self.register_tool(ToolMakerTool(agent=self))

        # Restore any previously created custom tools
        from .tools.dynamic_loader import load_custom_tools
        for tool in load_custom_tools():
            self.register_tool(tool)

    def _get_tool_descriptions(self) -> str:
        """Format tool descriptions for the system prompt."""
        lines = ["Available tools:"]
        for tool in self.tools.values():
            params = tool.parameters.get("properties", {})
            param_str = ", ".join(
                f"{k}: {v.get('type', 'any')}" for k, v in params.items()
            )
            required = tool.parameters.get("required", [])
            req_str = f" (required: {', '.join(required)})" if required else ""
            lines.append(f"  - {tool.name}: {tool.description}")
            lines.append(f"    Parameters: {param_str}{req_str}")
        return "\n".join(lines)

    def _get_tool_schemas(self) -> list[dict]:
        """Get JSON Schema definitions for all tools (Ollama native tool calling)."""
        return [tool.to_schema() for tool in self.tools.values()]

    # ------------------------------------------------------------------
    # Main agent loop
    # ------------------------------------------------------------------

    async def run(self, user_message: str) -> str:
        """Run the agent on a user message and return the final response.

        Implements the ReAct loop:
        1. Inject relevant memories into context
        2. Send to LLM with tool definitions
        3. Parse response for tool calls
        4. Execute tools, feed results back
        5. Repeat until final answer or max iterations
        """
        start_time = time.time()

        # Inject tool descriptions into system prompt
        tool_desc = self._get_tool_descriptions()
        sys_content = self.context.messages[0].content
        if "Available tools:" not in sys_content:
            self.context.messages[0].content = sys_content + "\n\n" + tool_desc
            self.context.messages[0].token_estimate = len(self.context.messages[0].content) // 4

        # Retrieve and inject relevant memories
        memories = self.memory.retrieve_passive(user_message)
        if memories:
            self.context.inject_memories(memories)
            logger.info(f"Injected {len(memories)} memories")

        # Add user message
        self.context.add_user(user_message)

        # ReAct loop with branchpoint tracking
        bp = BranchpointManager()
        backtracks_used = 0
        recent_tools: deque[_ToolCallRecord] = deque(maxlen=6)

        final_response = ""
        for iteration in range(self.max_iterations):
            logger.info(f"Agent iteration {iteration + 1}/{self.max_iterations}")

            # Call LLM
            messages = self.context.get_messages()
            llm_response = await self._call_llm(messages)

            if not llm_response:
                final_response = "I encountered an error generating a response. Please try again."
                break

            # Parse for tool calls
            tool_calls = self._extract_tool_calls(llm_response)
            text_response = self._strip_tool_calls(llm_response)

            if not tool_calls:
                # No tool calls — this is the final answer
                final_response = llm_response
                self.context.add_assistant(llm_response)
                break

            # Execute tool calls
            self.context.add_assistant(text_response, tool_calls=[
                {"name": tc["name"], "arguments": tc["arguments"]} for tc in tool_calls
            ])

            did_backtrack = False
            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                logger.info(f"Tool call: {tool_name}({json.dumps(tool_args)[:100]})")

                # Save branchpoint BEFORE tool execution
                bp.save(f"pre_tool_{iteration}", messages, iteration)

                tool = self.tools.get(tool_name)
                if not tool:
                    result = {"success": False, "error": f"Unknown tool '{tool_name}'. Available: {', '.join(self.tools.keys())}"}
                    result_str = f"Error: {result['error']}"
                else:
                    result = await tool.execute(**tool_args)
                    result_str = self._format_tool_result(result)

                # Track for loop detection
                recent_tools.append(_ToolCallRecord(
                    name=tool_name,
                    arguments_json=json.dumps(tool_args, sort_keys=True),
                ))

                # Check for failure or loop → maybe backtrack
                should_backtrack = False
                backtrack_reason = ""

                if _is_tool_failure(result):
                    should_backtrack = True
                    backtrack_reason = f"Previous tool call '{tool_name}' failed: {result.get('error', 'returned failure')}. Try a different approach."
                elif _detect_loop(recent_tools):
                    should_backtrack = True
                    backtrack_reason = f"Loop detected: '{tool_name}' called with same arguments 3 times. Try a completely different approach."

                if should_backtrack and backtracks_used < self.max_backtracks:
                    latest = bp.latest_label()
                    if latest is not None:
                        backtracks_used += 1
                        logger.info(f"Backtracking to '{latest}' ({backtracks_used}/{self.max_backtracks}): {backtrack_reason}")
                        restored_messages = bp.restore(latest)
                        # Reset context to restored state
                        self.context.restore_from_messages(restored_messages)
                        self.context.add_system(backtrack_reason)
                        did_backtrack = True
                        break  # Exit tool loop, re-enter main loop

                self.context.add_tool_result(tool_name, result_str)
                logger.info(f"Tool result: {result_str[:200]}")

            if did_backtrack:
                continue  # Skip to next iteration with restored context

            # If this was the last iteration, force a response
            if iteration == self.max_iterations - 1:
                final_response = text_response or "I've reached my iteration limit. Here's what I found so far."

        elapsed = time.time() - start_time
        logger.info(f"Agent completed in {elapsed:.1f}s ({iteration + 1} iterations)")

        # Save episodic memory of this interaction
        try:
            self.memory.save_from_conversation(
                content=f"User asked: {user_message[:200]}",
                category="episodic",
                subcategory="interaction",
                source="agent_run",
            )
        except Exception as e:
            logger.warning(f"Failed to save episodic memory: {e}")

        return final_response

    # ------------------------------------------------------------------
    # LLM communication
    # ------------------------------------------------------------------

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Call the LLM and return the complete response text.

        Tries Ollama native tool calling first. If the model doesn't support
        it, falls back to text-based tool call extraction.
        """
        if BACKEND == "llamacpp":
            return await self._call_llamacpp(messages)
        return await self._call_ollama(messages)

    async def _call_ollama(self, messages: list[dict[str, str]]) -> str:
        """Call Ollama's /api/chat endpoint."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": MAX_CONTEXT_TOKENS,
                "num_gpu": 99,
            },
        }

        # Include tool schemas for native tool calling
        tool_schemas = self._get_tool_schemas()
        if tool_schemas:
            payload["tools"] = tool_schemas

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{self.ollama_url}/api/chat", json=payload)
                if resp.status_code != 200:
                    logger.error(f"Ollama error: {resp.status_code} {resp.text[:200]}")
                    return ""

                data = resp.json()
                msg = data.get("message", {})
                content = msg.get("content", "")

                # Handle native tool calls from Ollama
                native_tool_calls = msg.get("tool_calls", [])
                if native_tool_calls:
                    # Convert native tool calls to our text format so the
                    # extraction logic handles them uniformly
                    for tc in native_tool_calls:
                        fn = tc.get("function", {})
                        tc_json = json.dumps({
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", {}),
                        })
                        content += f"\n```tool_call\n{tc_json}\n```"

                return content

        except Exception as e:
            logger.error(f"Ollama call failed: {e}")
            return ""

    async def _call_llamacpp(self, messages: list[dict[str, str]]) -> str:
        """Call llama.cpp server's /completion endpoint."""
        # Convert chat messages to a single prompt
        prompt_parts = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                prompt_parts.append(f"<|system|>\n{msg['content']}")
            elif role == "user":
                prompt_parts.append(f"<|user|>\n{msg['content']}")
            elif role == "assistant":
                prompt_parts.append(f"<|assistant|>\n{msg['content']}")
            elif role == "tool":
                prompt_parts.append(f"<|tool|>\n{msg['content']}")
        prompt_parts.append("<|assistant|>")
        prompt = "\n".join(prompt_parts)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{LLAMACPP_URL}/completion",
                    json={"prompt": prompt, "n_predict": 2048, "temperature": 0.7},
                )
                if resp.status_code != 200:
                    return ""
                return resp.json().get("content", "")
        except Exception as e:
            logger.error(f"llama.cpp call failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Tool call parsing
    # ------------------------------------------------------------------

    def _extract_tool_calls(self, text: str) -> list[dict[str, Any]]:
        """Extract tool calls from LLM output.

        Supports multiple formats that SLMs tend to produce:
        1. Fenced: ```tool_call\n{...}\n```
        2. JSON block: {"name": "...", "arguments": {...}}
        3. Ollama native (already converted to format 1 above)
        """
        calls = []

        # Pattern 1: fenced tool calls
        for match in re.finditer(r"```tool_call\s*\n(.*?)\n```", text, re.DOTALL):
            try:
                obj = json.loads(match.group(1).strip())
                if self._validate_tool_call(obj):
                    calls.append(obj)
            except json.JSONDecodeError:
                continue

        if calls:
            return calls

        # Pattern 2: bare JSON objects with "name" and "arguments"
        for match in re.finditer(r'\{[^{}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}[^}]*\}', text):
            try:
                obj = json.loads(match.group())
                if self._validate_tool_call(obj):
                    calls.append(obj)
            except json.JSONDecodeError:
                continue

        if calls:
            return calls

        # Pattern 3: nested JSON (handle balanced braces)
        idx = 0
        while idx < len(text):
            pos = text.find('"name"', idx)
            if pos == -1:
                break
            # Walk back to find opening brace
            start = text.rfind('{', max(0, pos - 15), pos)
            if start == -1:
                idx = pos + 1
                continue
            obj = self._extract_balanced_json(text, start)
            if obj and self._validate_tool_call(obj):
                calls.append(obj)
            idx = pos + 1

        return calls

    def _validate_tool_call(self, obj: dict) -> bool:
        """Check if a parsed JSON object is a valid tool call."""
        if "name" not in obj or "arguments" not in obj:
            return False
        if obj["name"] not in self.tools:
            return False
        if not isinstance(obj["arguments"], dict):
            return False
        return True

    @staticmethod
    def _extract_balanced_json(text: str, start: int) -> Optional[dict]:
        """Extract a balanced JSON object starting at a given position."""
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_str:
                escape = True
                continue
            if c == '"' and not escape:
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return None
        return None

    def _strip_tool_calls(self, text: str) -> str:
        """Remove tool call blocks from text, keeping surrounding prose."""
        # Remove fenced blocks
        text = re.sub(r"```tool_call\s*\n.*?\n```", "", text, flags=re.DOTALL)
        # Remove bare JSON tool calls
        text = re.sub(r'\{[^{}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}[^}]*\}', "", text)
        return text.strip()

    @staticmethod
    def _format_tool_result(result: dict[str, Any]) -> str:
        """Format a tool result for injection into the conversation."""
        if not result.get("success", False):
            return f"Error: {result.get('error', 'Unknown error')}"

        r = result.get("result", "")
        if isinstance(r, (dict, list)):
            return json.dumps(r, indent=2, default=str)[:5000]
        return str(r)[:5000]


# ---------------------------------------------------------------------------
# Convenience: run the agent from the command line
# ---------------------------------------------------------------------------

async def main():
    """Interactive agent REPL for testing."""
    import sys

    logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

    agent = Agent()
    agent.register_defaults()

    print(f"LocalMind Agent (model: {agent.model})")
    print(f"Tools: {', '.join(agent.tools.keys())}")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        response = await agent.run(user_input)
        print(f"\nAgent: {response}\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
