"""
Task Executor — Routes proposals to the right execution handler.
Supports: code_edit, research, data_task, general (agent-driven).
"""
import asyncio
import json
import logging
import time
import httpx
from pathlib import Path

logger = logging.getLogger("localmind.autonomy.task_executor")


async def execute_task(engine, proposal) -> bool:
    """
    Dispatch a proposal to the appropriate handler based on task_type.
    Returns True if execution was attempted (success or fail).
    """
    task_type = proposal.get("task_type", "code_edit")
    category = proposal.get("category", "")

    # Auto-classify if no explicit task_type
    if task_type == "code_edit" and category in ("research", "documentation"):
        task_type = "research"
    elif task_type == "code_edit" and category in ("data", "spreadsheet", "csv"):
        task_type = "data_task"

    engine._emit_activity(
        "task_dispatch",
        f"📋 Dispatching [{task_type}]: {proposal.get('title', '?')}"
    )

    try:
        if task_type == "code_edit":
            return await _execute_code_edit(engine, proposal)
        elif task_type == "research":
            return await _execute_with_agent(engine, proposal, "research")
        elif task_type == "data_task":
            return await _execute_with_agent(engine, proposal, "data")
        else:
            return await _execute_with_agent(engine, proposal, "general")
    except Exception as exc:
        logger.error(f"Task execution failed: {exc}")
        engine.proposals.mark_failed(proposal, str(exc)[:200])
        return True


async def _execute_code_edit(engine, proposal) -> bool:
    """Original code-edit execution path (unchanged)."""
    from backend.autonomy.execution import execute_proposal_cycle
    # The existing execution.py handles code edits
    # We just proxy to it
    return await execute_proposal_cycle(engine)


async def _execute_with_agent(engine, proposal, mode: str) -> bool:
    """
    Use the agent loop to complete a task. The model picks which tools to use.
    This is what enables research, spreadsheet filling, and general tasks.
    """
    title = proposal.get("title", "Untitled task")
    description = proposal.get("description", "")

    # Build a focused system prompt based on task type
    system_prompts = {
        "research": (
            "You are LocalMind's autonomous research agent. Your job is to research "
            "the given topic thoroughly using web_search, then write a clear report "
            "to a markdown file in the project. Be thorough and cite sources."
        ),
        "data": (
            "You are LocalMind's data automation agent. Your job is to create or fill "
            "data files (CSV, JSON, markdown tables) based on the given task. Use "
            "web_search to find data if needed, then write_file to save results."
        ),
        "general": (
            "You are LocalMind's autonomous task agent. Complete the given task using "
            "the available tools. Be thorough, verify your work, and report results."
        ),
    }

    system_prompt = system_prompts.get(mode, system_prompts["general"])

    user_prompt = f"TASK: {title}\n\nDETAILS: {description}"
    if proposal.get("context"):
        user_prompt += f"\n\nRESEARCH CONTEXT & ANALYSIS:\n{proposal['context']}"
    if proposal.get("files_affected"):
        user_prompt += f"\n\nRELEVANT FILES: {', '.join(proposal['files_affected'])}"

    messages = [{"role": "user", "content": user_prompt}]

    engine._emit_activity("agent_task", f"🤖 Agent working on: {title}")
    engine.proposals.update_status(proposal["id"], "executing")

    try:
        # Use Ollama agent loop
        results = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            # Multi-turn agent loop (up to 10 iterations)
            for iteration in range(10):
                resp = await client.post(
                    f"{engine.ollama_url}/api/chat",
                    json={
                        "model": engine.editing_model or engine.default_model,
                        "messages": [{"role": "system", "content": system_prompt}] + messages,
                        "stream": False,
                    },
                )

                if resp.status_code != 200:
                    raise Exception(f"Ollama returned {resp.status_code}")

                data = resp.json()
                msg = data.get("message", {})
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls", [])

                if content and not tool_calls:
                    # Final response — task complete
                    results.append(content)
                    break

                if content:
                    results.append(content)

                if not tool_calls:
                    break

                # Execute tool calls
                messages.append(msg)
                from backend.tools import execute_tool
                workspace = str(Path(__file__).parent.parent.parent)

                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    tool_args = func.get("arguments", {})

                    engine._emit_activity(
                        "agent_tool",
                        f"🔧 Using {tool_name}..."
                    )

                    result = execute_tool(tool_name, tool_args, workspace)
                    messages.append({
                        "role": "tool",
                        "content": json.dumps(result),
                    })

        # Mark success
        proposal["status"] = "completed"
        proposal["result"] = "\n".join(results)[:2000]
        engine.status["execution"]["proposals_executed"] += 1
        engine.success_tracker.record_outcome(proposal, success=True)
        engine._emit_activity(
            "task_complete",
            f"✅ Completed: {title}"
        )
        return True

    except Exception as exc:
        logger.error(f"Agent task failed: {exc}")
        engine.proposals.mark_failed(proposal, f"Agent error: {str(exc)[:150]}")
        engine.success_tracker.record_outcome(proposal, success=False)
        engine._emit_activity("task_failed", f"❌ Failed: {title} — {str(exc)[:80]}")
        return True
