"""Chat orchestration — the entry point for a chat turn.

Delegates context building to ContextBuilder and tool-call
heuristics to ToolDispatcher. This file owns the streaming
loops and conversation persistence.
"""

import json
import logging
import time
import uuid
from typing import Optional, Dict, Any, AsyncIterator

from backend import config
from backend.logic.llm_client import LLMClient
from backend.logic.context_builder import ContextBuilder
from backend.logic.tool_dispatcher import ToolDispatcher
from backend.logic.token_manager import TokenManager
from backend.logic.summarizer import Summarizer
from backend.memory.session_cache import get_session_cache
from backend.autonomy.services.reflection_service import ReflectionService

logger = logging.getLogger("localmind.logic.chat_service")


class ChatService:
    def __init__(self, db_factory, registry, ontology=None, metacog_controller=None, **kwargs):
        self.db_factory = db_factory
        self.registry = registry
        self.ontology = ontology
        self.metacog_controller = metacog_controller
        self.llm = LLMClient()
        self.ctx = ContextBuilder(registry)
        self.tools = ToolDispatcher(registry)
        self.token_manager = TokenManager()
        self.summarizer = Summarizer(self.llm)
        self.reflection = ReflectionService(config.OLLAMA_BASE_URL)

    # ------------------------------------------------------------------
    # Backward-compatible static methods (used by tests and system.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_tool_call(user_message: str) -> Optional[Dict]:
        return ToolDispatcher.infer_tool_call(user_message)

    @staticmethod
    def _escalate_model(current_model: str) -> Optional[str]:
        return ContextBuilder.escalate_model(current_model)

    @staticmethod
    def _estimate_complexity(message: str) -> Dict[str, Any]:
        return ContextBuilder.estimate_complexity(message)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def handle_chat(self, body: Dict[str, Any]) -> AsyncIterator[str]:
        """The main entry point for a chat turn. Returns an SSE stream."""
        message = body.get("message", "")
        conversation_id = body.get("conversation_id")
        model_override = body.get("model")
        system_prompt = body.get("system_prompt")

        # 1. Estimate complexity and route model
        task_estimate = ContextBuilder.estimate_complexity(message)
        model, provider = await self.ctx.route_model(task_estimate, model_override)

        # 2. Build or load conversation
        if not conversation_id:
            conversation_id = await self._create_conversation(message, model, system_prompt)

        # 3. Load history
        history = await self._get_history(conversation_id)
        if not system_prompt and history:
            system_prompt = history[0]["content"]

        # 4. Build full system prompt (RAG + memory + metacognition)
        sys_prompt, metacog_decision = await self.ctx.build_context(
            message=message,
            task_estimate=task_estimate,
            system_prompt=system_prompt,
            editor_context=body.get("editor_context"),
            metacog_controller=self.metacog_controller,
            conversation_id=conversation_id,
        )

        # 5. Prepare messages
        messages = [{"role": "system", "content": sys_prompt}]
        for h in history[1:] if history and history[0]["role"] == "system" else history:
            messages.append(h)

        user_msg = {"role": "user", "content": message}
        if body.get("image"):
            user_msg["images"] = [body["image"]]
        messages.append(user_msg)

        # 6. Truncate with summarization
        messages = await self.token_manager.summarize_and_truncate(
            messages,
            max_tokens=config.MAX_CONTEXT_TOKENS,
            summarizer=self.summarizer,
        )

        # 7. Save user message
        await self._save_msg(conversation_id, "user", message)

        # 7b. Cache user intent in session memory (Tier 1)
        try:
            cache = get_session_cache()
            intent_key = f"user_intent:{conversation_id}:{int(time.time())}"
            cache.put(intent_key, message[:500], category="user_intent")
        except Exception as e:
            logger.debug("Session cache user_intent save failed (non-fatal): %s", e)

        # 8. Choose loop
        use_react = (
            task_estimate["score"] >= 5
            and task_estimate.get("needs_tools")
            and provider == "ollama"
            and body.get("agent_mode") != "disabled"
        )
        if use_react:
            return self._react_agent_loop(conversation_id, model, message, task_estimate)

        return self._agent_loop(conversation_id, model, provider, messages, task_estimate, metacog_decision)

    # ------------------------------------------------------------------
    # ReAct agent loop
    # ------------------------------------------------------------------

    async def _react_agent_loop(self, conversation_id, model, message, task_estimate):
        start_time = time.time()
        yield f"data: {json.dumps({'thinking': {'model': model, 'provider': 'react_agent', 'tier': task_estimate['tier']}})}\n\n"

        try:
            from src.agent.core import Agent
            from src.agent.adapter import import_backend_tools

            agent = Agent(model=model)
            for adapted in import_backend_tools(self.registry):
                agent.register_tool(adapted)
            agent.register_defaults()
            logger.info(f"ReAct agent started: model={model}, tools={list(agent.tools.keys())}")

            response = await agent.run(message)

            chunk_size = 8
            for i in range(0, len(response), chunk_size):
                chunk = response[i:i + chunk_size]
                yield f"data: {json.dumps({'token': chunk, 'conversation_id': conversation_id})}\n\n"

            await self._save_msg(conversation_id, "assistant", response)

        except Exception as e:
            logger.error(f"ReAct agent failed: {e}", exc_info=True)
            error_msg = f"Agent encountered an error: {e}"
            yield f"data: {json.dumps({'token': error_msg, 'conversation_id': conversation_id})}\n\n"
            await self._save_msg(conversation_id, "assistant", error_msg)

        elapsed = time.time() - start_time
        yield f"data: {json.dumps({'analytics': {'elapsed': round(elapsed, 2), 'model': model, 'provider': 'react_agent', 'tier': task_estimate['tier']}})}\n\n"
        yield f"data: {json.dumps({'done': True, 'conversation_id': conversation_id})}\n\n"

    # ------------------------------------------------------------------
    # Standard streaming + tool execution loop
    # ------------------------------------------------------------------

    async def _agent_loop(self, conversation_id, model, provider, messages, task_estimate, metacog_decision):
        full_response = ""
        total_tokens = 0
        total_tool_calls = 0
        _escalated = False
        start_time = time.time()

        yield f"data: {json.dumps({'thinking': {'model': model, 'provider': provider, 'tier': task_estimate['tier']}})}\n\n"
        if metacog_decision:
            from backend.metacognition.models.actions import Action
            if metacog_decision.action == Action.ASK:
                yield f"data: {json.dumps({'token': metacog_decision.clarification_question, 'conversation_id': conversation_id, 'metacog': metacog_decision.to_dict()})}\n\n"
                await self._save_msg(conversation_id, "assistant", metacog_decision.clarification_question)
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

        # Fast-path synthetic tool call
        _pre_synthetic = None
        if task_estimate.get("needs_tools") and messages:
            user_msg = messages[-1]["content"] if isinstance(messages[-1].get("content"), str) else ""
            _pre_synthetic = self.tools.infer_tool_call(user_msg)
            if _pre_synthetic:
                logger.info(f"Fast-path synthetic tool call: {_pre_synthetic['function']['name']}({_pre_synthetic['function']['arguments']})")

        for iteration in range(config.MAX_AGENT_ITERATIONS):
            logger.info(f"Agent Loop iteration {iteration + 1}")

            num_ctx = config.DEFAULT_CONTEXT_WINDOW
            if task_estimate["tier"] in ("heavy", "ultra"):
                num_ctx = 16384

            llm_options = {"num_ctx": num_ctx, "num_gpu": 99}
            ollama_tools = [t.to_ollama_tool() for t in self.registry.tools]

            chunk_text = ""
            tool_calls = []
            token_buffer = ""
            json_depth = 0
            in_json = False
            in_string = False
            escape_next = False

            _fast_path_done = False
            if _pre_synthetic and iteration == 0:
                tool_calls = [_pre_synthetic]
                _pre_synthetic = None
                _escalated = True
                _fast_path_done = True
            else:
                async for chunk in self.llm.generate_stream(model, messages, provider, options=llm_options, tools=ollama_tools):
                    if "error" in chunk:
                        yield f"data: {json.dumps({'error': chunk['error']})}\n\n"
                        return

                    if chunk.get("tool_calls"):
                        tool_calls.extend(chunk["tool_calls"])

                    if chunk.get("token"):
                        token = chunk["token"]
                        chunk_text += token
                        total_tokens += 1

                        token_buffer += token
                        for ch in token:
                            if escape_next:
                                escape_next = False
                                continue
                            if ch == '\\' and in_string:
                                escape_next = True
                                continue
                            if ch == '"':
                                in_string = not in_string
                                continue
                            if in_string:
                                continue
                            if ch == '{':
                                json_depth += 1
                                in_json = True
                            elif ch == '}':
                                json_depth = max(0, json_depth - 1)

                        if in_json and json_depth > 0:
                            continue
                        elif in_json and json_depth == 0:
                            in_json = False
                            in_string = False
                            escape_next = False
                            parsed = self.tools.parse_text_tools(token_buffer)
                            if parsed:
                                tool_calls.extend(parsed)
                                remaining = self.tools.strip_tool_json(token_buffer)
                                if remaining.strip():
                                    yield f"data: {json.dumps({'token': remaining, 'conversation_id': conversation_id})}\n\n"
                                token_buffer = ""
                                continue
                            else:
                                # Suppress template/placeholder tool call output
                                if self.tools._looks_like_tool_template(token_buffer):
                                    logger.warning("Suppressed template tool call output from model")
                                    token_buffer = ""
                                    continue
                                yield f"data: {json.dumps({'token': token_buffer, 'conversation_id': conversation_id})}\n\n"
                                token_buffer = ""
                        else:
                            yield f"data: {json.dumps({'token': token_buffer, 'conversation_id': conversation_id})}\n\n"
                            token_buffer = ""

            # Flush remaining buffer
            if token_buffer:
                parsed = self.tools.parse_text_tools(token_buffer)
                if parsed:
                    tool_calls.extend(parsed)
                    remaining = self.tools.strip_tool_json(token_buffer)
                    if remaining.strip():
                        yield f"data: {json.dumps({'token': remaining, 'conversation_id': conversation_id})}\n\n"
                elif not self.tools._looks_like_tool_template(token_buffer):
                    yield f"data: {json.dumps({'token': token_buffer, 'conversation_id': conversation_id})}\n\n"
                else:
                    logger.warning("Suppressed template tool call in flush buffer")

            full_response += chunk_text

            logger.info(f"Iteration {iteration}: tool_calls={len(tool_calls)}, needs_tools={task_estimate.get('needs_tools')}, escalated={_escalated}, chunk_text_len={len(chunk_text)}")

            if not tool_calls:
                if task_estimate.get("needs_tools") and iteration == 0 and not _escalated:
                    user_msg = messages[-1]["content"] if messages else ""
                    logger.info(f"No tool calls — trying synthetic. Last msg role={messages[-1].get('role', '?') if messages else '?'}, content={user_msg[:80]}")
                    synthetic = self.tools.infer_tool_call(user_msg)
                    if synthetic:
                        logger.info(f"Model didn't call tools — injecting synthetic call: {synthetic['function']['name']}")
                        tool_calls = [synthetic]
                        _escalated = True
                    else:
                        break
                else:
                    break

            # Execute tools
            async for evt in self._execute_tools(tool_calls, chunk_text, messages, conversation_id):
                yield evt
                total_tool_calls += 1

            if _fast_path_done:
                break

        # Finalize
                # Phase 3: Automated Root Cause Analysis if task failed or exhausted iterations
        if iteration >= config.MAX_AGENT_ITERATIONS - 1:
            logger.warning(f"Conversation {conversation_id} exhausted max iterations. Triggering RCA.")
            rca_result = await self.reflection.analyze_failure(conversation_id)
            if rca_result and "error" not in rca_result:
                rca_msg = f"\n\n[Root Cause Analysis]: {rca_result.get('root_cause', 'Unknown')}\n[Suggested Recovery]: {rca_result.get('proposed_fix', 'Contact support')}"
                full_response += rca_msg
                yield f"data: {json.dumps({'token': rca_msg, 'conversation_id': conversation_id})}\n\n"
        
        await self._save_msg(conversation_id, "assistant", full_response)

        elapsed = time.time() - start_time
        yield f"data: {json.dumps({'analytics': {'elapsed': round(elapsed, 2), 'tokens': total_tokens, 'tps': round(total_tokens / elapsed, 1) if elapsed > 0 else 0, 'model': model, 'total_tokens': total_tokens, 'tokens_per_sec': round(total_tokens / elapsed, 1) if elapsed > 0 else 0, 'elapsed_sec': round(elapsed, 2), 'tool_calls': total_tool_calls}})}\n\n"
        yield f"data: {json.dumps({'done': True, 'conversation_id': conversation_id})}\n\n"

        try:
            if len(messages) >= 2:
                await self._auto_save_facts(messages[-2]["content"], True)
        except Exception as e:
            logger.warning(f"Auto-save facts failed: {e}")

    # ------------------------------------------------------------------
    # Tool execution (extracted from inner loop for clarity)
    # ------------------------------------------------------------------

    GATED_ACTIONS = {
        ("gmail", "send"), ("gmail", "reply"), ("gmail", "draft"),
    }

    async def _execute_tools(self, tool_calls, chunk_text, messages, conversation_id):
        """Execute tool calls and yield SSE events for each."""
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"]["arguments"]
            yield f"data: {json.dumps({'tool_call': {'name': name, 'arguments': args}})}\n\n"

            action = args.get("action", "")
            needs_gate = (name, action) in self.GATED_ACTIONS

            if needs_gate:
                approved = await self._gate_tool(name, action, args, conversation_id)
                if not approved:
                    preview = self._build_gate_preview(name, action, args)
                    res_str = f"\u274c User denied: {preview}"
                    yield f"data: {json.dumps({'tool_result': {'name': name, 'result': res_str}})}\n\n"
                    messages.append({"role": "assistant", "content": chunk_text, "tool_calls": [tc]})
                    messages.append({"role": "tool", "content": res_str})
                    continue

            if name == "propose_action":
                yield f"data: {json.dumps({'approval_request': {'description': args.get('description', ''), 'action_type': args.get('action_type', 'unknown'), 'risk_level': args.get('risk_level', 'MEDIUM'), 'reason': args.get('reason', ''), 'estimated_cost': args.get('estimated_cost', ''), 'alternatives': args.get('alternatives', '')}})}\n\n"

            try:
                res = await self.registry.execute_tool(name, args)
                res_str = str(res.get("result", res)) if isinstance(res, dict) else str(res)
            except Exception as e:
                res = {"success": False, "error": str(e)}
                res_str = f"Error: {str(e)}"

            tool_result_evt = {"name": name, "result": res_str}
            if isinstance(res, dict) and not res.get("success", True):
                self.reflection.log_step_failure(
                    task_id=conversation_id,
                    stage="tool_execution",
                    validator=name,
                    error_msg=res_str
                )
            if isinstance(res, dict) and res.get("image_base64"):
                tool_result_evt["image_base64"] = res["image_base64"]
                tool_result_evt["mime_type"] = res.get("mime_type", "image/png")

            yield f"data: {json.dumps({'tool_result': tool_result_evt})}\n\n"

            # Cache tool result in session memory (Tier 1)
            try:
                cache = get_session_cache()
                tool_key = f"tool_result:{name}:{int(time.time())}"
                cache.put(tool_key, res_str[:1000], category="tool_result")
            except Exception as e:
                logger.debug("Session cache tool_result save failed (non-fatal): %s", e)

            messages.append({"role": "assistant", "content": chunk_text, "tool_calls": [tc]})
            messages.append({"role": "tool", "content": res_str})

    @staticmethod
    def _build_gate_preview(name, action, args):
        if name == "gmail":
            return f"Send email to {args.get('to', '?')}\nSubject: {args.get('subject', '(none)')}"
        return f"{name}: {action}"

    async def _gate_tool(self, name, action, args, conversation_id):
        """Approval gate for sensitive tool actions. Returns True if approved."""
        from backend.tools.propose_action import _pending, _decisions, _load_approval_log, _save_approval_log
        import asyncio

        preview = self._build_gate_preview(name, action, args)
        request_id = str(uuid.uuid4())
        event = asyncio.Event()
        _pending[request_id] = event

        log_entry = {
            "request_id": request_id,
            "action_type": "web_submit",
            "description": preview,
            "reason": f"Tool '{name}' wants to {action}",
            "risk_level": "medium",
            "decision": "pending",
            "requested_at": time.time(),
        }
        log = _load_approval_log()
        log.append(log_entry)
        _save_approval_log(log)

        try:
            await asyncio.wait_for(event.wait(), timeout=300)
        except asyncio.TimeoutError:
            _decisions[request_id] = False
        finally:
            _pending.pop(request_id, None)

        return _decisions.pop(request_id, False)

    # ------------------------------------------------------------------
    # Conversation persistence
    # ------------------------------------------------------------------

    async def _get_history(self, conversation_id: str):
        db = self.db_factory()
        rows = db.execute("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at", (conversation_id,)).fetchall()
        db.close()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    async def _save_msg(self, conversation_id, role, content):
        db = self.db_factory()
        now = time.time()
        db.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)", (conversation_id, role, content, now))
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        db.commit()
        db.close()

    async def _create_conversation(self, message, model, system_prompt):
        cid = str(uuid.uuid4())
        db = self.db_factory()
        now = time.time()
        title = message[:50] + "..." if len(message) > 50 else message
        db.execute("INSERT INTO conversations (id, title, model, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (cid, title, model, system_prompt or config.DEFAULT_SYSTEM_PROMPT, now, now))
        db.commit()
        db.close()
        return cid

    async def _auto_save_facts(self, last_user_message: str, enabled: bool):
        if not enabled:
            return
        from backend.tools.memory import _get_retriever
        retriever = _get_retriever()
        if retriever:
            try:
                retriever.save_from_conversation(
                    content=last_user_message[:500],
                    category="episodic",
                    subcategory="interaction",
                    source="chat",
                )
            except Exception as e:
                logger.warning(f"Episodic memory save failed: {e}")
