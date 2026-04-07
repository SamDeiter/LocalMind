import json
import logging
import re
import time
import traceback
import uuid
from typing import Optional, List, Dict, Any, AsyncIterator

from backend import config
from backend.logic.llm_client import LLMClient
from backend.logic.load_monitor import LoadMonitor
from backend.logic.prompt_factory import PromptFactory
from backend.logic.token_manager import TokenManager

logger = logging.getLogger("localmind.logic.chat_service")

from backend.logic.summarizer import Summarizer

class ChatService:
    def __init__(self, db_factory, registry, ontology=None, autonomy_engine=None, metacog_controller=None):
        self.db_factory = db_factory
        self.registry = registry
        self.ontology = ontology
        self.autonomy_engine = autonomy_engine
        self.metacog_controller = metacog_controller
        self.llm = LLMClient()
        self.load_monitor = LoadMonitor()
        self.prompt_factory = PromptFactory()
        self.token_manager = TokenManager()
        self.summarizer = Summarizer(self.llm)

    async def handle_chat(self, body: Dict[str, Any]) -> AsyncIterator[str]:
        """The main entry point for a chat turn. Returns an SSE stream."""
        message = body.get("message", "")
        conversation_id = body.get("conversation_id")
        model_override = body.get("model")
        system_prompt = body.get("system_prompt")
        learning_enabled = body.get("learning_enabled", True)

        # 1. Estimate Complexity and Route Model
        task_estimate = self._estimate_complexity(message)
        model, provider = await self._route_model(task_estimate, model_override)
        
        # 2. Build or Load Conversation
        if not conversation_id:
            conversation_id = await self._create_conversation(message, model, system_prompt)
        
        # 3. Load History and Build System Prompt
        history = await self._get_history(conversation_id)
        if not system_prompt and history:
            system_prompt = history[0]["content"] # Assuming first is system
            
        sys_prompt = self.prompt_factory.build_system_prompt(
            base_prompt=system_prompt if system_prompt else config.DEFAULT_SYSTEM_PROMPT,
            model_name=model,
            task_tier=task_estimate["tier"],
            editor_context=body.get("editor_context"),
            rag_context=await self._get_rag_context(message),
            needs_tools=task_estimate.get("needs_tools", False),
        )

        # 4. Inject Memory
        sys_prompt = await self._inject_memory(message, sys_prompt, task_estimate)

        # 5. Metacognitive Pre-process
        metacog_decision = None
        if self.metacog_controller and task_estimate["score"] >= 5:
            metacog_decision = await self.metacog_controller.pre_process(message, conversation_id)
            # Skip: handling ASK/ABSTAIN should likely be in the stream generator
            if metacog_decision and self.metacog_controller.session and self.metacog_controller.session.active_intent:
                sys_prompt += self.prompt_factory.build_metacog_context(self.metacog_controller.session.active_intent)

        # 6. Prepare Messages
        messages = [{"role": "system", "content": sys_prompt}]
        for h in history[1:] if history and history[0]["role"] == "system" else history:
             messages.append(h)
        
        user_msg = {"role": "user", "content": message}
        if body.get("image"):
            user_msg["images"] = [body["image"]]
        messages.append(user_msg)

        # 7. Truncate for Strict Token Processing (with Summarization)
        messages = await self.token_manager.summarize_and_truncate(
            messages, 
            max_tokens=config.MAX_CONTEXT_TOKENS,
            summarizer=self.summarizer
        )

        # 8. Save User Message
        await self._save_msg(conversation_id, "user", message)

        # 9. Choose loop: ReAct agent for complex multi-step tasks,
        #    standard streaming loop for everything else
        use_react = (
            task_estimate["score"] >= 7
            and task_estimate.get("needs_tools")
            and provider == "ollama"
            and body.get("agent_mode") != "disabled"
        )
        if use_react:
            return self._react_agent_loop(conversation_id, model, message, task_estimate)

        return self._agent_loop(conversation_id, model, provider, messages, task_estimate, metacog_decision)

    async def _react_agent_loop(self, conversation_id, model, message, task_estimate):
        """Run the ReAct agent for complex multi-step tasks, streaming results as SSE.

        The ReAct agent (src.agent.core) handles its own tool calling loop and
        memory retrieval. We wrap its execution into SSE events so the frontend
        gets the same streaming contract it expects.
        """
        start_time = time.time()

        yield f"data: {json.dumps({'thinking': {'model': model, 'provider': 'react_agent', 'tier': task_estimate['tier']}})}\n\n"

        try:
            from src.agent.core import Agent
            from src.agent.adapter import import_backend_tools

            agent = Agent(model=model)

            # Import all backend tools so the agent can use them
            for adapted in import_backend_tools(self.registry):
                agent.register_tool(adapted)

            # Also register the agent's own built-in tools
            agent.register_defaults()

            logger.info(f"ReAct agent started: model={model}, tools={list(agent.tools.keys())}")

            response = await agent.run(message)

            # Stream the response token-by-token (simulate streaming for SSE)
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

    async def _agent_loop(self, conversation_id, model, provider, messages, task_estimate, metacog_decision):
        """The core streaming and tool execution loop."""
        full_response = ""
        total_tokens = 0
        total_tool_calls = 0
        _escalated = False  # Track whether we've already tried a bigger model
        start_time = time.time()

        # Initial Metadata Events
        yield f"data: {json.dumps({'thinking': {'model': model, 'provider': provider, 'tier': task_estimate['tier']}})}\n\n"
        if metacog_decision:
            from backend.metacognition.models.actions import Action
            if metacog_decision.action == Action.ASK:
                yield f"data: {json.dumps({'token': metacog_decision.clarification_question, 'conversation_id': conversation_id, 'metacog': metacog_decision.to_dict()})}\n\n"
                await self._save_msg(conversation_id, "assistant", metacog_decision.clarification_question)
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

        # Fast-path: if we can confidently infer the tool call, skip the LLM
        # entirely so users don't see unhelpful "I can't do that" text first.
        _pre_synthetic = None
        if task_estimate.get("needs_tools") and messages:
            user_msg = messages[-1]["content"] if isinstance(messages[-1].get("content"), str) else ""
            _pre_synthetic = self._infer_tool_call(user_msg)
            if _pre_synthetic:
                logger.info(f"Fast-path synthetic tool call: {_pre_synthetic['function']['name']}({_pre_synthetic['function']['arguments']})")

        for iteration in range(config.MAX_AGENT_ITERATIONS):
            logger.info(f"Agent Loop iteration {iteration+1}")
            
            # Determine Context Window for Ollama
            num_ctx = config.DEFAULT_CONTEXT_WINDOW
            if task_estimate["tier"] in ("heavy", "ultra"):
                num_ctx = 16384
            
            llm_options = {"num_ctx": num_ctx, "num_gpu": 99}

            # Build Ollama-format tool definitions from the registry
            ollama_tools = [t.to_ollama_tool() for t in self.registry.tools]

            chunk_text = ""
            tool_calls = []
            # Buffer for detecting JSON tool calls in text output
            token_buffer = ""
            json_depth = 0
            in_json = False
            in_string = False
            escape_next = False

            # Fast-path: skip the LLM on first iteration when we already know the tool call
            _fast_path_done = False
            if _pre_synthetic and iteration == 0:
                tool_calls = [_pre_synthetic]
                _pre_synthetic = None  # consumed
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

                        # Smart buffering: detect JSON tool call blocks
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
                            # Inside a JSON block — keep buffering, don't stream yet
                            continue
                        elif in_json and json_depth == 0:
                            # JSON block closed — check if it's a tool call
                            in_json = False
                            in_string = False
                            escape_next = False
                            parsed = self._parse_text_tools(token_buffer)
                            if parsed:
                                tool_calls.extend(parsed)
                                # Strip tool JSON from buffer, stream any remaining text
                                remaining = self._strip_tool_json(token_buffer)
                                if remaining.strip():
                                    yield f"data: {json.dumps({'token': remaining, 'conversation_id': conversation_id})}\n\n"
                                token_buffer = ""
                                continue
                            else:
                                # Not a tool call — flush the entire buffer
                                yield f"data: {json.dumps({'token': token_buffer, 'conversation_id': conversation_id})}\n\n"
                                token_buffer = ""
                        else:
                            # Not in JSON — flush buffer immediately
                            yield f"data: {json.dumps({'token': token_buffer, 'conversation_id': conversation_id})}\n\n"
                            token_buffer = ""

            # Flush any remaining buffer
            if token_buffer:
                parsed = self._parse_text_tools(token_buffer)
                if parsed:
                    tool_calls.extend(parsed)
                    remaining = self._strip_tool_json(token_buffer)
                    if remaining.strip():
                        yield f"data: {json.dumps({'token': remaining, 'conversation_id': conversation_id})}\n\n"
                else:
                    yield f"data: {json.dumps({'token': token_buffer, 'conversation_id': conversation_id})}\n\n"

            full_response += chunk_text

            logger.info(f"Iteration {iteration}: tool_calls={len(tool_calls)}, needs_tools={task_estimate.get('needs_tools')}, escalated={_escalated}, chunk_text_len={len(chunk_text)}")

            if not tool_calls:
                # If the task needed tools but the model just talked, try to extract
                # the intent from the original user message and call the tool directly.
                if task_estimate.get("needs_tools") and iteration == 0 and not _escalated:
                    user_msg = messages[-1]["content"] if messages else ""
                    logger.info(f"No tool calls — trying synthetic. Last msg role={messages[-1].get('role','?') if messages else '?'}, content={user_msg[:80]}")
                    synthetic = self._infer_tool_call(user_msg)
                    if synthetic:
                        logger.info(f"Model didn't call tools — injecting synthetic call: {synthetic['function']['name']}")
                        tool_calls = [synthetic]
                        _escalated = True
                        # Don't break — fall through to tool execution below
                    else:
                        break
                else:
                    break
            
            # Execute Tools
            # Actions that require user approval before execution
            GATED_ACTIONS = {
                ("gmail", "send"), ("gmail", "reply"), ("gmail", "draft"),
            }

            for tc in tool_calls:
                name = tc["function"]["name"]
                args = tc["function"]["arguments"]
                yield f"data: {json.dumps({'tool_call': {'name': name, 'arguments': args}})}\n\n"

                # Check if this tool+action needs approval
                action = args.get("action", "")
                needs_gate = (name, action) in GATED_ACTIONS

                if needs_gate:
                    # Build a human-readable preview
                    if name == "gmail":
                        preview = f"Send email to {args.get('to', '?')}\nSubject: {args.get('subject', '(none)')}"
                    else:
                        preview = f"{name}: {action}"

                    # Emit approval card and wait for decision
                    from backend.tools.propose_action import resolve_approval, _pending, _decisions, _load_approval_log, _save_approval_log
                    import asyncio as _asyncio
                    request_id = str(uuid.uuid4())
                    event = _asyncio.Event()
                    _pending[request_id] = event

                    # Log the request so /api/approvals/pending can find it
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

                    yield f"data: {json.dumps({'approval_request': {'description': preview, 'action_type': 'web_submit', 'risk_level': 'MEDIUM', 'reason': f'Tool {name} wants to {action}. Review and approve or deny.'}})}\n\n"

                    # Wait for user to click Approve or Deny (5 min timeout)
                    try:
                        await _asyncio.wait_for(event.wait(), timeout=300)
                    except _asyncio.TimeoutError:
                        _decisions[request_id] = False
                    finally:
                        _pending.pop(request_id, None)

                    approved = _decisions.pop(request_id, False)
                    if not approved:
                        res_str = f"❌ User denied: {preview}"
                        yield f"data: {json.dumps({'tool_result': {'name': name, 'result': res_str}})}\n\n"
                        messages.append({"role": "assistant", "content": chunk_text, "tool_calls": [tc]})
                        messages.append({"role": "tool", "content": res_str})
                        total_tool_calls += 1
                        continue

                # For propose_action, emit the approval card BEFORE blocking
                if name == "propose_action":
                    yield f"data: {json.dumps({'approval_request': {'description': args.get('description', ''), 'action_type': args.get('action_type', 'unknown'), 'risk_level': args.get('risk_level', 'MEDIUM'), 'reason': args.get('reason', ''), 'estimated_cost': args.get('estimated_cost', ''), 'alternatives': args.get('alternatives', '')}})}\n\n"

                try:
                    res = await self.registry.execute_tool(name, args)
                    res_str = str(res.get("result", res)) if isinstance(res, dict) else str(res)
                except Exception as e:
                    res = {"success": False, "error": str(e)}
                    res_str = f"Error: {str(e)}"

                # Build SSE payload — include image data if the tool returned it
                tool_result_evt = {"name": name, "result": res_str}
                if isinstance(res, dict) and res.get("image_base64"):
                    tool_result_evt["image_base64"] = res["image_base64"]
                    tool_result_evt["mime_type"] = res.get("mime_type", "image/png")

                yield f"data: {json.dumps({'tool_result': tool_result_evt})}\n\n"
                total_tool_calls += 1

                messages.append({"role": "assistant", "content": chunk_text, "tool_calls": [tc]})
                messages.append({"role": "tool", "content": res_str})

            # After a fast-path synthetic call, don't loop back to the LLM —
            # the tool result is the answer.
            if _fast_path_done:
                break

        # Finalize
        await self._save_msg(conversation_id, "assistant", full_response)
        
        elapsed = time.time() - start_time
        yield f"data: {json.dumps({'analytics': {'elapsed': round(elapsed, 2), 'tokens': total_tokens, 'tps': round(total_tokens/elapsed, 1) if elapsed > 0 else 0, 'model': model, 'total_tokens': total_tokens, 'tokens_per_sec': round(total_tokens/elapsed, 1) if elapsed > 0 else 0, 'elapsed_sec': round(elapsed, 2), 'tool_calls': total_tool_calls}})}\n\n"
        yield f"data: {json.dumps({'done': True, 'conversation_id': conversation_id})}\n\n"
        
        # Background: Auto-save, reflection, etc.
        try:
            if len(messages) >= 2:
                await self.auto_save_facts(messages[-2]["content"], True)
        except Exception as e:
            logger.warning(f"Auto-save facts failed: {e}")

    async def auto_save_facts(self, last_user_message: str, enabled: bool):
        """Automatically saves facts to memory based on user messages if learning is enabled."""
        if not enabled:
            return
        mem_tool = self.registry.get_tool("save_memory")
        if mem_tool:
            # We would ideally extract a fact here using an LLM, but for stability
            # we just log that we skipped automatic extraction for now, until it's properly wired.
            pass

    # Helper methods ...
    def _estimate_complexity(self, message: str) -> Dict[str, Any]:
        score = 3
        msg_lower = message.lower()
        if len(message) > 200: score += 2
        for kw in ["code", "refactor", "bug", "error", "architecture", "design"]:
            if kw in msg_lower: score += 2

        # Tool-use keywords — models need to be larger to reliably call tools
        tool_keywords = [
            "emulator", "android", "apk", "avd", "install app", "scroll",
            "email", "gmail", "send email", "inbox", "draft",
            "browse", "navigate", "click", "website",
            "screenshot", "search the web", "look up",
            "run code", "execute", "terminal",
            "git commit", "git status", "git diff",
        ]
        needs_tools = any(kw in msg_lower for kw in tool_keywords)
        if needs_tools:
            score = max(score, 5)  # Floor at medium — small models can't tool-call

        tier = "light"
        if score >= 8: tier = "heavy"
        elif score >= 5: tier = "medium"
        return {"score": min(score, 10), "tier": tier, "needs_tools": needs_tools}

    async def _route_model(self, estimate: Dict[str, Any], override: str = None) -> (str, str):
        tier = estimate["tier"]

        if override and override != "auto":
            # If the override is a small model but the task needs tools,
            # upgrade to at least medium tier — small models can't tool-call reliably
            if estimate.get("needs_tools"):
                small_models = {"gemma3:4b", "gemma4:e4b", "qwen2.5-coder:7b"}
                if override in small_models:
                    logger.warning(f"Override '{override}' too small for tool calling — upgrading to medium tier")
                    # Fall through to normal routing below
                else:
                    return override, "ollama"
            else:
                return override, "ollama"

        # Load-aware routing: check what's already in VRAM
        gpu_state = await self.load_monitor.get_gpu_state()
        loaded = gpu_state.get("loaded_models", [])

        if loaded:
            # Try to reuse a loaded model that can handle this tier
            reuse = self.load_monitor.pick_best_model(tier, loaded)
            if reuse:
                return reuse, "ollama"

        # Hardware-aware selection: use agent config's model registry
        # to pick the best model that actually fits this machine
        if estimate.get("needs_tools"):
            try:
                from src.agent.config import detect_hardware, select_model
                hw = detect_hardware()
                spec = select_model(hw, task_type="tool_calling", prefer_tool_calling=True)
                logger.info(f"Hardware-aware routing: {spec.name} (tool_score={spec.tool_calling_score})")
                return spec.name, "ollama"
            except Exception as e:
                logger.warning(f"Hardware-aware routing failed, using tier defaults: {e}")

        # Cloud fallback for heavy tasks if available
        from backend import gemini_client
        if tier == "heavy" and gemini_client.is_available():
            return "gemini-1.5-pro", "gemini"

        return config.MODEL_TIERS.get(tier, "gemma4:e4b"), "ollama"

    @staticmethod
    def _escalate_model(current_model: str) -> str | None:
        """Return the next bigger model when the current one can't handle tool calling.

        Walks all known models sorted by capability count (weakest → strongest)
        and picks the first one that's strictly more capable than current_model.
        Returns None if already at the strongest.
        """
        normalized = current_model.replace(":latest", "")

        # Build a sorted list of models by capability breadth
        ranked = sorted(
            config.MODEL_CAPABILITIES.items(),
            key=lambda kv: len(kv[1]),
        )
        current_level = len(config.MODEL_CAPABILITIES.get(normalized, ["light"]))

        for name, caps in ranked:
            if len(caps) > current_level and name != normalized:
                return name

        return None  # Already at the top

    @staticmethod
    def _infer_tool_call(user_message: str) -> Optional[Dict]:
        """Infer a tool call from the user's message when the model fails to call tools.

        Pattern-matches common requests to the correct tool+action so the user
        doesn't have to wait for a model escalation/retry cycle.
        """
        msg = user_message.lower()

        # Android emulator patterns
        if any(kw in msg for kw in ["emulator", "android", "avd", "apk", "install app"]):
            # Check if an emulator is running — if not, launch one first
            import shutil as _shutil
            adb_bin = _shutil.which("adb") or "adb"
            try:
                import subprocess as _sp
                result = _sp.run([adb_bin, "devices"], capture_output=True, text=True, timeout=5)
                lines = [l for l in result.stdout.strip().splitlines()[1:] if l.strip() and "device" in l]
                emulator_running = len(lines) > 0
            except Exception:
                emulator_running = False

            # List AVDs — handle before the emulator-running gate so it
            # works even when no device is booted.
            if any(kw in msg for kw in ["list avd", "list emulator", "available avd", "available emulator"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "list_avds"}}}

            if not emulator_running:
                # No emulator running — launch one first
                logger.info("No emulator running — launching AVD before processing request")
                return {"function": {"name": "android_emulator", "arguments": {"action": "launch", "avd_name": "Pixel_Fold_API_35"}}}

            if any(kw in msg for kw in ["install", "apk"]):
                # Try to extract APK path from message
                import re as _re
                path_match = _re.search(r'["\']?([^\s"\']+\.apk)["\']?', user_message, _re.IGNORECASE)
                if path_match:
                    return {"function": {"name": "android_emulator", "arguments": {"action": "install", "apk_path": path_match.group(1)}}}
                # No path given — check what's already installed
                return {"function": {"name": "android_emulator", "arguments": {"action": "list_packages"}}}
            if any(kw in msg for kw in ["scroll up", "swipe up"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "scroll_up"}}}
            if any(kw in msg for kw in ["scroll down", "swipe down"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "scroll_down"}}}
            if any(kw in msg for kw in ["scroll left", "swipe left"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "scroll_left"}}}
            if any(kw in msg for kw in ["scroll right", "swipe right"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "scroll_right"}}}
            if any(kw in msg for kw in ["read screen", "what's on screen", "what is on screen", "read the screen", "what do you see", "describe screen"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "read_screen"}}}
            if any(kw in msg for kw in ["screenshot", "screen", "show", "see", "look"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "screenshot"}}}
            if any(kw in msg for kw in ["go home", "home screen", "press home"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "press_key", "keycode": "KEYCODE_HOME"}}}
            if any(kw in msg for kw in ["go back", "press back", "back button"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "press_key", "keycode": "KEYCODE_BACK"}}}
            if any(kw in msg for kw in ["launch", "start", "boot", "open emulator"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "list_avds"}}}
            if any(kw in msg for kw in ["kill", "stop", "close", "shut"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "kill"}}}
            if any(kw in msg for kw in ["list", "what app", "packages"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "list_packages"}}}
            if "tap" in msg:
                return {"function": {"name": "android_emulator", "arguments": {"action": "screenshot"}}}
            # Default: show what's on screen
            return {"function": {"name": "android_emulator", "arguments": {"action": "screenshot"}}}

        # Gmail patterns
        if any(kw in msg for kw in ["email", "gmail", "inbox", "mail"]):
            if any(kw in msg for kw in ["send", "write", "compose"]):
                return {"function": {"name": "gmail", "arguments": {"action": "draft"}}}
            if any(kw in msg for kw in ["read", "open", "check"]):
                return {"function": {"name": "gmail", "arguments": {"action": "list_messages", "max_results": 5}}}
            if "search" in msg or "find" in msg:
                return {"function": {"name": "gmail", "arguments": {"action": "list_messages", "max_results": 10}}}
            return {"function": {"name": "gmail", "arguments": {"action": "list_messages", "max_results": 5}}}

        # Web search patterns
        if any(kw in msg for kw in ["search", "look up", "google", "find out"]):
            return {"function": {"name": "web_search", "arguments": {"query": user_message}}}

        # Screenshot
        if "screenshot" in msg:
            return {"function": {"name": "take_screenshot", "arguments": {}}}

        return None

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

    async def _get_rag_context(self, message):
        try:
            import asyncio
            from backend.tools.rag import query_documents
            loop = asyncio.get_event_loop()
            res = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: query_documents(message, n_results=3)),
                timeout=5.0
            )
            if not res or not res.get("results"): return None
            return "\n".join([f"[{r['source']}]: {r['content'][:500]}" for r in res["results"]])
        except: return None

    async def _inject_memory(self, message, sys_prompt, estimate):
        try:
            if estimate["score"] >= 5:
                mem_tool = self.registry.get_tool("recall_memories")
                if mem_tool:
                    res = await mem_tool.execute(query=message, limit=5)
                    text = res.get("result", "") if isinstance(res, dict) else str(res)
                    if text and "No memories" not in text:
                        return sys_prompt + f"\n\n[MEMORIES]\n{text}\n[/MEMORIES]"
            return sys_prompt
        except: return sys_prompt

    def _parse_text_tools(self, text: str) -> List[Dict[str, Any]]:
        """Parse tool calls from model text output, handling nested JSON."""
        calls = []
        # Find all top-level JSON objects that look like tool calls
        i = 0
        while i < len(text):
            # Look for {"name": pattern
            idx = text.find('"name"', i)
            if idx == -1:
                break
            # Walk back to find the opening brace
            start = text.rfind('{', max(0, idx - 10), idx)
            if start == -1:
                i = idx + 1
                continue
            # Extract balanced JSON from this point
            obj = self._extract_json_object(text, start)
            if obj and "name" in obj and "arguments" in obj:
                name = obj["name"]
                if any(t.name == name for t in self.registry.tools):
                    args = obj["arguments"]
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except:
                            pass
                    if isinstance(args, dict):
                        calls.append({"function": {"name": name, "arguments": args}})
            i = idx + 1
        return calls

    def _extract_json_object(self, text: str, start: int) -> Optional[Dict]:
        """Extract a balanced JSON object starting at position `start`."""
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i+1])
                    except:
                        return None
        return None

    def _strip_tool_json(self, text: str) -> str:
        """Remove JSON tool call blocks from text, keeping surrounding prose."""
        result = text
        i = 0
        while i < len(result):
            idx = result.find('"name"', i)
            if idx == -1:
                break
            start = result.rfind('{', max(0, idx - 10), idx)
            if start == -1:
                i = idx + 1
                continue
            obj = self._extract_json_object(result, start)
            if obj and "name" in obj and "arguments" in obj:
                # Find the end of this JSON block
                depth = 0
                in_str = False
                esc = False
                for j in range(start, len(result)):
                    c = result[j]
                    if esc:
                        esc = False
                        continue
                    if c == '\\' and in_str:
                        esc = True
                        continue
                    if c == '"' and not esc:
                        in_str = not in_str
                        continue
                    if in_str:
                        continue
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            result = result[:start] + result[j+1:]
                            break
                i = start
            else:
                i = idx + 1
        return result
