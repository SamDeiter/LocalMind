"""
routes/chat.py — Chat & Agent Loop Router
===========================================
The core of LocalMind. Handles the main chat endpoint which:

1. Routes to the right model based on task complexity (auto-routing)
2. Builds the conversation context (system prompt + history + memory)
3. Streams responses from Ollama via Server-Sent Events (SSE)
4. Detects and executes tool calls (agent loop, max 10 iterations)
5. Auto-saves personal facts from user messages (heuristic fallback)

The agent loop works like this:
  User message → Model generates response → If tool call detected →
  Execute tool → Feed result back to model → Model continues → ...
  
This repeats until the model responds without requesting tools,
or 10 iterations are reached (safety limit).

TASK-SPECIFIC PROMPTS:
The system uses different prompts based on detected task type:
- General conversation: warm, personality-driven prompt
- Coding tasks: focused, technical prompt
- The model can also suggest switching to a better-suited model

MODEL SELF-AWARENESS:
The AI knows which model it's running on and can suggest the user
switch to a heavier model if the task requires deeper analysis.
"""

import json
import logging
import re
import time
import traceback
import uuid

import httpx
from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

logger = logging.getLogger("localmind.routes.chat")

# Create router — the main chat endpoint
router = APIRouter(prefix="/api", tags=["chat"])

# ── Dependencies (injected by server.py via configure()) ──────────────
# We use dependency injection to avoid circular imports. server.py owns
# the database, tools registry, model routing, and system prompts.
_get_db = None
_registry = None
_OLLAMA_BASE_URL = "http://localhost:11434"
_DEFAULT_SYSTEM_PROMPT = ""
_estimate_task_complexity = None
_route_model_hybrid = None
_gemini_is_available = None
_learning_enabled_fn = None  # Function to check learning state

# ── Task-Specific System Prompts ──────────────────────────────────────
# Different prompts optimize the AI's behavior for different tasks.
# The model router detects the task type and selects the right prompt.

CODING_PROMPT_SUFFIX = """

CODING MODE ACTIVE:
- You are helping with a programming task. Be precise and technical.
- Show complete, working code. Don't abbreviate or use placeholders.
- Explain your reasoning briefly, then show the solution.
- If the code has dependencies, mention them.
- Use proper error handling and follow best practices.
"""

MODEL_AWARENESS_SUFFIX = """

MODEL SELF-AWARENESS:
- You are currently running as {model_name}.
- If a task seems too complex for your capabilities (e.g., you're a 7B model
  asked to do deep architecture analysis), TELL the user:
  "This task might benefit from a larger model. You can switch to the 32B model
  using the model selector for better results."
- Be honest about your limitations. It's better to suggest a better tool
  than to give a mediocre answer.
"""

SELF_IMPROVEMENT_SUFFIX = """

SELF-IMPROVEMENT CAPABILITIES:
- You can read your own source code with self_read and self_list.
- You can edit your own source code with self_edit — but you MUST call
  propose_action FIRST and get user approval before any edit.
- After editing, ALWAYS call self_test to run pytest and validate your changes.
- Use self_reflect to log improvement ideas for future sessions.
- When making self-edits, use git_branch to create a feature branch first.
  NEVER edit code directly on main.
- You can view your pending improvement proposals with list_proposals.
"""


def configure(
    get_db_func,
    registry,
    ollama_base_url: str,
    default_system_prompt: str,
    estimate_task_complexity_func,
    route_model_hybrid_func,
    gemini_is_available_func,
    learning_enabled_func,
):
    """Called by server.py to inject all dependencies.
    
    This avoids circular imports and keeps the chat router focused
    on request handling rather than initialization.
    """
    global _get_db, _registry, _OLLAMA_BASE_URL, _DEFAULT_SYSTEM_PROMPT
    global _estimate_task_complexity, _route_model_hybrid, _gemini_is_available
    global _learning_enabled_fn
    _get_db = get_db_func
    _registry = registry
    _OLLAMA_BASE_URL = ollama_base_url
    _DEFAULT_SYSTEM_PROMPT = default_system_prompt
    _estimate_task_complexity = estimate_task_complexity_func
    _route_model_hybrid = route_model_hybrid_func
    _gemini_is_available = gemini_is_available_func
    _learning_enabled_fn = learning_enabled_func


# ── Helper: Auto-Save Heuristic ──────────────────────────────────────
# Many small/coding models don't reliably call save_memory on their own.
# This regex-based heuristic catches obvious personal facts and saves
# them automatically, so we don't rely on the model to decide.

_AUTO_SAVE_PATTERNS = [
    # "my name is Sam" / "I'm called Sam" / "call me Sam"
    (re.compile(r"\b(?:my name is|i'?m called|call me|i am)\s+([A-Z][a-z]+)", re.IGNORECASE),
     "fact", "User's name is {}"),
    # "I'm 25 years old"
    (re.compile(r"\bi(?:'m| am)\s+(\d{1,3})\s*(?:years? old|yo)\b", re.IGNORECASE),
     "fact", "User is {} years old"),
    # "I work as a developer" / "I am at Google"
    (re.compile(r"\bi (?:work|am) (?:as |at |a |an )(.+?)(?:\.|$)", re.IGNORECASE),
     "fact", "User works as/at {}"),
    # "I love Python" / "I prefer dark mode"
    (re.compile(r"\bi (?:love|prefer|like|enjoy)\s+(.+?)(?:\.|,|$)", re.IGNORECASE),
     "preference", "User likes/prefers {}"),
    # "my favorite language is Python"
    (re.compile(r"\bmy (?:favorite|fav|favourite)\s+(.+?)(?:is|:)\s*(.+?)(?:\.|,|$)", re.IGNORECASE),
     "preference", "User's favorite {} is {}"),
]


async def _auto_save_facts(message: str):
    """Detect and save personal facts from user messages.
    
    This is a safety net for coding-focused models (like qwen2.5-coder)
    that don't reliably call save_memory on their own. It uses simple
    regex patterns to detect names, ages, jobs, and preferences.
    
    This runs BEFORE the model sees the message, so memories are saved
    even if the model doesn't choose to call save_memory itself.
    """
    try:
        save_tool = _registry.get_tool("save_memory")
        if not save_tool or not _learning_enabled_fn():
            return

        for pattern, category, template in _AUTO_SAVE_PATTERNS:
            match = pattern.search(message)
            if match:
                groups = match.groups()
                content = template.format(*groups)
                await save_tool.execute(content=content, category=category)
                logger.info(f"AUTO-SAVED memory: [{category}] {content}")
    except Exception as e:
        logger.warning(f"Auto-save heuristic failed (non-fatal): {e}")


# ── Helper: Text-Based Tool Call Parser ───────────────────────────────
# Some models don't support native tool calling but will emit tool
# calls as JSON text in their response. This parser detects those
# and converts them into the same format as native tool calls.

def _make_text_tool_parser(known_tool_names: set):
    """Create a parser that finds tool call JSON in model text output.
    
    Returns a function that takes text and returns a list of tool calls
    in the same format as Ollama's native tool_calls field.
    """
    def parse(text: str) -> list:
        calls = []
        pattern = re.compile(
            r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{[^}]*\})\s*\}',
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            name = match.group(1)
            if name in known_tool_names:
                try:
                    args = json.loads(match.group(2))
                    calls.append({"function": {"name": name, "arguments": args}})
                except json.JSONDecodeError:
                    pass  # Malformed JSON — skip this match
        return calls
    return parse


# ── Main Chat Endpoint ────────────────────────────────────────────────

@router.post("/chat")
async def chat(request: Request):
    """Main chat endpoint — the heart of LocalMind.
    
    Flow:
    1. Parse request (message, model, conversation_id, etc.)
    2. Auto-route to the best model if mode is "auto"
    3. Load/create conversation in SQLite
    4. Build message context (system prompt + history + memories)
    5. Auto-save any detected personal facts (heuristic)
    6. Stream response from Ollama via SSE
    7. Execute any tool calls the model requests (agent loop)
    8. Save final response to database
    
    Returns: Server-Sent Events (SSE) stream with tokens, tool calls,
    and analytics data.
    """
    body = await request.json()
    message = body.get("message", "")
    user_message = message  # Keep original for memory recall
    model = body.get("model", "qwen2.5-coder:7b")
    conversation_id = body.get("conversation_id")
    system_prompt = body.get("system_prompt", "")

    # ── Step 1: Auto Model Routing ────────────────────────────────────
    # If model is "auto", analyze the message complexity and pick the
    # best model. This balances speed (7B for simple tasks) vs quality
    # (32B for complex analysis).
    task_estimate = None
    if model == "auto":
        task_estimate = _estimate_task_complexity(message, len(body.get("messages", [])))

        # Check if Gemini (cloud) should handle this task
        hybrid = _route_model_hybrid(
            complexity_score=task_estimate["score"],
            force_local=body.get("force_local", False),
            gemini_available=_gemini_is_available(),
            cloud_approved=body.get("cloud_approved", False),
        )

        if hybrid.get("provider") == "gemini" and hybrid.get("needs_approval"):
            # Cloud model suggested but needs user approval first
            model = task_estimate["model"]
            task_estimate["cloud_suggested"] = hybrid["name"]
            logger.info(f"AUTO-ROUTE: Cloud model {hybrid['name']} suggested but needs approval, using local {model}")
        elif hybrid.get("provider") == "gemini":
            model = hybrid["name"]
            task_estimate["provider"] = "gemini"
            logger.info(f"AUTO-ROUTE: Using cloud model {model} (pre-approved)")
        else:
            model = task_estimate["model"]
            logger.info(f"AUTO-ROUTE: score={task_estimate['score']} tier={task_estimate['tier']} → {model}")

    logger.info(f"CHAT REQUEST: model={model}, msg_len={len(message)}, conv_id={conversation_id}")

    # ── Step 2: Load or Build System Prompt ───────────────────────────
    if not system_prompt and conversation_id:
        db_tmp = _get_db()
        row_tmp = db_tmp.execute(
            "SELECT system_prompt FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        db_tmp.close()
        if row_tmp and row_tmp["system_prompt"]:
            system_prompt = row_tmp["system_prompt"]

    if not system_prompt:
        system_prompt = _DEFAULT_SYSTEM_PROMPT

        # Inject RAG context if documents are indexed
        try:
            from backend.tools.rag import query_documents as _rag_query
            rag_results = _rag_query(user_message, n_results=3)
            if rag_results.get("results"):
                rag_context = "\n\nRelevant document context:\n"
                for r in rag_results["results"]:
                    rag_context += f"[From {r['source']}]: {r['content'][:500]}\n"
                system_prompt += rag_context
        except Exception:
            pass  # RAG not available or query failed

    # Add task-specific prompt suffix based on detected complexity
    if task_estimate and task_estimate.get("tier") in ("medium", "heavy"):
        system_prompt += CODING_PROMPT_SUFFIX

    # Add model self-awareness so the AI knows its own capabilities
    system_prompt += MODEL_AWARENESS_SUFFIX.format(model_name=model)

    # Add self-improvement capabilities so the AI knows it can edit itself
    system_prompt += SELF_IMPROVEMENT_SUFFIX

    # ── Step 3: Handle Image and Editor Context ───────────────────────
    image_base64 = body.get("image")  # Optional base64 image from webcam
    editor_context = body.get("editor_context")  # Optional: file open in editor

    # If the user has a file open in the editor, inject it so the AI
    # knows what they're working on — enables contextual assistance
    if editor_context:
        system_prompt += f"\n\n[EDITOR CONTEXT — The user currently has this file open in their editor]\n{editor_context}\n[/EDITOR CONTEXT]"

    # ── Step 4: Create or Load Conversation ───────────────────────────
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        title = message[:50] + ("..." if len(message) > 50 else "")
        db = _get_db()
        now = time.time()
        db.execute(
            "INSERT INTO conversations (id, title, model, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, title, model, system_prompt, now, now),
        )
        db.commit()
        db.close()

    # Load conversation history from database
    db = _get_db()
    rows = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conversation_id,),
    ).fetchall()
    db.close()

    # Build the messages list for Ollama (system prompt + history + current message)
    ollama_messages = [{"role": "system", "content": system_prompt}]
    for row in rows:
        ollama_messages.append({"role": row["role"], "content": row["content"]})

    # Add current user message (with optional image for vision models)
    user_msg = {"role": "user", "content": message}
    if image_base64:
        user_msg["images"] = [image_base64]
    ollama_messages.append(user_msg)

    # ── Step 5: Save User Message to DB ───────────────────────────────
    db = _get_db()
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, "user", message, time.time()),
    )
    db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time(), conversation_id))
    db.commit()
    db.close()

    # ── Step 6: Auto-Save Personal Facts (DEFERRED) ───────────────────
    # Moved to AFTER streaming so the user gets their response immediately.
    # The heuristic runs in the background after the SSE stream ends.
    # (see _pending_auto_save below)
    _pending_auto_save_msg = message  # Save for post-stream processing

    # ── Step 7: Inject Memory Context (FAST PATH) ─────────────────────
    # PERF: We avoid calling recall_memories for simple tasks because it
    # uses nomic-embed-text embeddings, which forces Ollama to swap models
    # (unload 7B → load embed → unload embed → reload 7B = ~8s delay!).
    #
    # Instead, for simple messages we use get_recent_memories() which does
    # a direct ChromaDB read without embeddings — no model swap needed.
    # Full semantic recall is only used for complex/memory-related queries.
    t_mem_start = time.time()
    complexity_score = task_estimate["score"] if task_estimate else 5
    memory_keywords = ["remember", "recall", "my name", "who am i", "what do you know",
                       "told you", "last time", "previously", "forgot", "memory"]
    needs_semantic_recall = (
        complexity_score >= 5 or
        any(kw in message.lower() for kw in memory_keywords)
    )

    try:
        if needs_semantic_recall:
            # Full semantic search — only when the user is actually asking about memories
            memory_tool = _registry.get_tool("recall_memories")
            if memory_tool:
                memories = await memory_tool.execute(query=message, limit=5)
                mem_text = memories.get("result", "") if isinstance(memories, dict) else str(memories)
                if mem_text and "No memories" not in mem_text and "No relevant" not in mem_text:
                    memory_context = f"\n\n[REMEMBERED CONTEXT]\n{mem_text}\n[/REMEMBERED CONTEXT]\n"
                    ollama_messages[0]["content"] += memory_context
                    logger.info(f"Injected SEMANTIC memory context ({time.time() - t_mem_start:.2f}s): {mem_text[:200]}")
        else:
            # Fast path: direct DB read, no embeddings, no model swap
            from backend.tools.memory import get_recent_memories
            recent = get_recent_memories(n=5)
            if recent:
                mem_lines = [f"- [{m['category']}]: {m['content']}" for m in recent]
                memory_context = f"\n\n[REMEMBERED CONTEXT]\n" + "\n".join(mem_lines) + "\n[/REMEMBERED CONTEXT]\n"
                ollama_messages[0]["content"] += memory_context
                logger.info(f"Injected FAST memory context ({time.time() - t_mem_start:.2f}s): {len(recent)} memories")
    except Exception as e:
        logger.warning(f"Memory recall failed (non-fatal): {e}")
    logger.info(f"Memory phase completed in {time.time() - t_mem_start:.3f}s (semantic={needs_semantic_recall})")

    # ── Step 8: Prepare Tool Definitions ──────────────────────────────
    # Get available tool definitions for the model. Not all models
    # support native tool calling — we handle fallback via text parsing.
    tool_defs = _registry.get_ollama_tools()
    known_tool_names = {t.name for t in _registry.tools}
    extract_text_tool_call = _make_text_tool_parser(known_tool_names)

    # ── Step 9: Stream Response (Agent Loop) ──────────────────────────
    async def stream_response():
        """SSE generator that streams tokens and handles tool calls.
        
        The agent loop runs up to 10 iterations:
        1. Send messages to Ollama
        2. Stream response tokens to frontend
        3. If model requests tool calls → execute them
        4. Feed tool results back to model → goto 1
        5. If no tool calls → done
        """
        nonlocal ollama_messages
        full_response = ""
        include_tools = bool(tool_defs)
        stream_start = time.time()
        total_tokens = 0
        total_tool_calls = 0

        # Send thinking event so frontend shows "thinking..." indicator
        context_chars = sum(len(m.get('content', '')) for m in ollama_messages)
        yield f"data: {json.dumps({'thinking': {'model': model, 'messages': len(ollama_messages), 'context_chars': context_chars, 'tools_enabled': include_tools}})}\n\n"

        # Send task estimation if auto-routing was used
        if task_estimate:
            yield f"data: {json.dumps({'task_estimate': task_estimate})}\n\n"

        # Agent loop: max 10 iterations (safety limit)
        for iteration in range(10):
            logger.info(f"Agent loop iteration {iteration + 1}")

            # Build Ollama API payload
            # PERF: Dynamic context window based on current task.
            # Smaller context = more model layers in VRAM = faster inference.
            # On a 10GB GPU, the 7B model at 32K context spills to CPU (~6 tok/s).
            # At 4K context, it stays fully in VRAM (~50+ tok/s).
            context_chars = sum(len(m.get("content", "")) for m in ollama_messages)
            has_editor = bool(body.get("editor_context"))
            tier = task_estimate.get("tier", "light") if task_estimate else "light"

            # Scale context to what the task actually needs:
            #   - Quick chat: 2048 (fast, snappy responses)
            #   - Normal conversation: 4096 (enough for multi-turn)
            #   - Editor open / long history: 8192 (code needs more room)
            #   - Complex analysis: 16384 (deep reasoning tasks)
            if tier == "heavy" or context_chars > 12000:
                num_ctx = 16384
            elif has_editor or context_chars > 6000 or tier == "medium":
                num_ctx = 8192
            elif context_chars > 2000:
                num_ctx = 4096
            else:
                num_ctx = 2048

            logger.info(f"Context window: num_ctx={num_ctx} (tier={tier}, chars={context_chars}, editor={has_editor})")

            payload = {
                "model": model,
                "messages": ollama_messages,
                "stream": True,
                "keep_alive": "30m",  # Keep model warm in VRAM between requests
                "options": {
                    "num_ctx": num_ctx,
                    "num_gpu": 99,     # Push all layers to GPU
                },
            }
            if include_tools:
                payload["tools"] = tool_defs

            chunk_text = ""
            tool_calls_found = []

            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                    async with client.stream("POST", f"{_OLLAMA_BASE_URL}/api/chat", json=payload) as response:
                        if response.status_code != 200:
                            error_body = (await response.aread()).decode()
                            logger.error(f"Ollama error: {error_body}")

                            # Some models don't support tools — disable and retry
                            if "does not support tools" in error_body and include_tools:
                                logger.info("Model doesn't support tools — disabling and retrying")
                                include_tools = False
                                continue

                            yield f"data: {json.dumps({'error': error_body})}\n\n"
                            return

                        # Stream tokens line by line from Ollama
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                                msg = data.get("message", {})
                                content = msg.get("content", "")
                                if content:
                                    chunk_text += content
                                    total_tokens += 1
                                    yield f"data: {json.dumps({'token': content, 'conversation_id': conversation_id})}\n\n"

                                # Detect native tool calls from the model
                                if msg.get("tool_calls"):
                                    tool_calls_found.extend(msg["tool_calls"])

                                if data.get("done"):
                                    logger.info(f"Stream done. text={len(chunk_text)}, tools={len(tool_calls_found)}")
                            except json.JSONDecodeError:
                                continue

                full_response += chunk_text

                # Fallback: detect tool calls embedded as text in response
                # (for models that don't support native tool calling)
                if not tool_calls_found and chunk_text.strip():
                    parsed = extract_text_tool_call(chunk_text)
                    if parsed:
                        tool_calls_found.extend(parsed)
                        logger.info(f"Parsed {len(parsed)} tool call(s) from text output")

                # No tool calls detected → model is done responding
                if not tool_calls_found:
                    break

                # ── Execute Tool Calls ────────────────────────────────
                for tc in tool_calls_found:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "unknown")
                    tool_args = func.get("arguments", {})

                    # Notify frontend about the tool call
                    yield f"data: {json.dumps({'tool_call': {'name': tool_name, 'arguments': tool_args}})}\n\n"

                    # If this is a propose_action, emit approval request
                    # so the frontend can render an approval card
                    if tool_name == "propose_action":
                        yield f"data: {json.dumps({'approval_request': tool_args})}\n\n"

                    # Execute the tool
                    try:
                        if tool_name == "save_memory" and not _learning_enabled_fn():
                            result = "Learning is paused. Memory not saved."
                        else:
                            result = await _registry.execute_tool(tool_name, tool_args)
                            if isinstance(result, dict):
                                result = result.get("result", json.dumps(result))
                            result = str(result)
                    except Exception as e:
                        result = f"Error: {str(e)}"

                    # Send tool result to frontend
                    yield f"data: {json.dumps({'tool_result': {'name': tool_name, 'result': result}})}\n\n"
                    total_tool_calls += 1

                    # Feed tool result back to model for next iteration
                    ollama_messages.append({
                        "role": "assistant",
                        "content": chunk_text,
                        "tool_calls": [tc],
                    })
                    ollama_messages.append({
                        "role": "tool",
                        "content": result,
                    })

            except Exception as e:
                logger.error(f"Stream error: {e}")
                logger.error(traceback.format_exc())
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                break

        # ── Save Assistant Response to DB ─────────────────────────────
        if full_response:
            db = _get_db()
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, "assistant", full_response, time.time()),
            )
            db.commit()
            db.close()

        # ── Auto-Save Personal Facts (DEFERRED from Step 6) ──────────
        # Run AFTER streaming so the user already has their response.
        # This may trigger an embedding call, but it won't block the UX.
        try:
            await _auto_save_facts(_pending_auto_save_msg)
        except Exception as e:
            logger.warning(f"Deferred auto-save failed (non-fatal): {e}")

        # ── Send Analytics ────────────────────────────────────────────
        elapsed = round(time.time() - stream_start, 2)
        tps = round(total_tokens / elapsed, 1) if elapsed > 0 else 0
        yield f"data: {json.dumps({'done': True, 'conversation_id': conversation_id, 'analytics': {'elapsed_sec': elapsed, 'total_tokens': total_tokens, 'tokens_per_sec': tps, 'tool_calls': total_tool_calls, 'model': model}})}\n\n"

        # ── Autonomous Reflection ─────────────────────────────────────
        # After conversations with ≥3 exchanges, auto-reflect on potential
        # self-improvements. Runs inline but is non-blocking (best-effort).
        try:
            msg_count = len(ollama_messages)
            if msg_count >= 6:  # ≥3 exchanges = 6 messages (user+assistant pairs)
                reflect_tool = _registry.get_tool("self_reflect")
                if reflect_tool:
                    summary = f"Conversation about: {user_message[:100]}. " \
                              f"Model: {model}, {total_tokens} tokens, {total_tool_calls} tool calls. " \
                              f"Exchanges: {msg_count // 2}."
                    await reflect_tool.execute(
                        observation=summary,
                        category="auto_reflection",
                        priority="low",
                    )
                    logger.info(f"AUTO-REFLECT: Logged reflection for conv {conversation_id}")
        except Exception as e:
            logger.debug(f"Auto-reflection skipped (non-fatal): {e}")

    return StreamingResponse(stream_response(), media_type="text/event-stream")
