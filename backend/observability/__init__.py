"""Observability — what the bot just did, in a form you can audit.

The activity log is a process-wide ring buffer that records every:
  - Tool call (auto-recorded by ToolRegistry.execute_tool)
  - Identity self-change (rename, persona, voice)
  - User-fact extraction
  - Theme rewrite
  - Memory save / forget

Read the buffer via /api/agent/activity. The UI shows it as a real-time
panel so the user can see the bot acting without having to tail logs.
"""

from backend.observability.activity_log import (
    ActivityKind,
    get_activity_log,
    record,
)

__all__ = ["ActivityKind", "get_activity_log", "record"]
