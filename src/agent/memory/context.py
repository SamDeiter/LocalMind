"""
Context window management and summarization.

Design informed by arXiv:2512.13564 (Memory in the Age of AI Agents):
- "Context engineering" is distinct from memory — it's about what goes
  into the current prompt, not what's stored persistently.
- The paper distinguishes: working memory (context window) vs long-term
  memory (persistent store). This module manages the working memory side.

arXiv:2510.03847 — SLMs have smaller context windows (typically 4K-8K
effective). Aggressive summarization is critical to keep the agent
functional over long conversations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.memory.context")


@dataclass
class Message:
    """A single message in the conversation."""
    role: str          # "system", "user", "assistant", "tool"
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_name: str = ""  # For tool-result messages
    token_estimate: int = 0

    def __post_init__(self):
        if not self.token_estimate:
            # Rough estimate: ~4 chars per token for English
            self.token_estimate = len(self.content) // 4 + 1


class ContextManager:
    """Manages the conversation context window for the agent.

    Responsibilities:
    - Track all messages in the conversation
    - Enforce token budget by summarizing old messages
    - Inject relevant memories into the system prompt
    - Preserve the most recent exchange + system prompt

    Strategy (informed by arXiv:2510.03847):
    1. System prompt is always included (never trimmed)
    2. Most recent user message + assistant response are always included
    3. Tool call/result pairs are always kept together
    4. Older messages are summarized when budget exceeded
    """

    def __init__(self, max_tokens: int = 8192, reserve_for_response: int = 1024):
        self.max_tokens = max_tokens
        self.reserve = reserve_for_response
        self.messages: list[Message] = []
        self._summaries: list[str] = []

    @property
    def budget(self) -> int:
        """Available tokens for context (total - reserve for response)."""
        return self.max_tokens - self.reserve

    @property
    def token_count(self) -> int:
        """Estimated total tokens in current context."""
        return sum(m.token_estimate for m in self.messages)

    def add_system(self, content: str) -> None:
        """Set/replace the system prompt."""
        # Remove existing system message if any
        self.messages = [m for m in self.messages if m.role != "system"]
        self.messages.insert(0, Message(role="system", content=content))

    def add_user(self, content: str) -> None:
        """Add a user message."""
        self.messages.append(Message(role="user", content=content))
        self._maybe_compress()

    def add_assistant(self, content: str, tool_calls: list[dict] | None = None) -> None:
        """Add an assistant message."""
        self.messages.append(Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls or [],
        ))

    def add_tool_result(self, tool_name: str, result: str) -> None:
        """Add a tool result message."""
        self.messages.append(Message(
            role="tool",
            content=result,
            tool_name=tool_name,
        ))

    def get_messages(self) -> list[dict[str, str]]:
        """Export messages in the format expected by Ollama/OpenAI APIs."""
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def inject_memories(self, memories: list[str]) -> None:
        """Inject relevant memories into the system prompt.

        arXiv:2512.13564 — memories should be injected as part of the
        system context, not as user messages, to avoid confusing the
        model about who said what.
        """
        if not memories or not self.messages or self.messages[0].role != "system":
            return

        memory_block = "\n\n[RELEVANT MEMORIES]\n" + "\n".join(f"- {m}" for m in memories) + "\n[/MEMORIES]"

        # Append to system prompt
        sys_msg = self.messages[0]
        if "[RELEVANT MEMORIES]" in sys_msg.content:
            # Replace existing memory block
            idx = sys_msg.content.index("[RELEVANT MEMORIES]")
            end = sys_msg.content.index("[/MEMORIES]") + len("[/MEMORIES]")
            sys_msg.content = sys_msg.content[:idx-2] + memory_block
        else:
            sys_msg.content += memory_block

        sys_msg.token_estimate = len(sys_msg.content) // 4 + 1

    def _maybe_compress(self) -> None:
        """Compress context if over budget by summarizing older messages.

        Preserves:
        1. System prompt (index 0)
        2. Last 4 messages (current exchange)
        3. Any tool call + result pairs in the preserved window
        """
        if self.token_count <= self.budget:
            return

        if len(self.messages) <= 5:
            return  # Too few messages to compress

        # Identify messages to summarize (everything between system prompt and last 4)
        system_msg = self.messages[0]
        preserve_tail = self.messages[-4:]
        to_summarize = self.messages[1:-4]

        if not to_summarize:
            return

        # Build a summary of the compressed messages
        summary_parts = []
        for msg in to_summarize:
            if msg.role == "user":
                summary_parts.append(f"User asked: {msg.content[:100]}")
            elif msg.role == "assistant":
                summary_parts.append(f"Assistant: {msg.content[:100]}")
            elif msg.role == "tool":
                summary_parts.append(f"Tool [{msg.tool_name}]: {msg.content[:80]}")

        summary = "Previous conversation summary:\n" + "\n".join(summary_parts)
        self._summaries.append(summary)

        # Replace with compressed version
        summary_msg = Message(role="user", content=f"[Context summary: {summary}]")
        self.messages = [system_msg, summary_msg] + preserve_tail

        logger.info(f"Compressed {len(to_summarize)} messages → summary "
                     f"({self.token_count} tokens remaining)")

    def restore_from_messages(self, raw_messages: list[dict]) -> None:
        """Replace all messages with a snapshot (used by branchpoint restore).

        Accepts the same format as ``get_messages()`` returns.
        """
        self.messages = [
            Message(role=m["role"], content=m.get("content", ""),
                    tool_name=m.get("tool_name", ""))
            for m in raw_messages
        ]
        logger.info(f"Context restored to {len(self.messages)} messages")

    def clear(self) -> None:
        """Clear all messages except the system prompt."""
        system = [m for m in self.messages if m.role == "system"]
        self.messages = system
        self._summaries = []
