import json
import logging
import re
import time
import traceback
import uuid
from typing import Optional, List, Dict, Any, AsyncIterator

from backend import config, events
from backend.logic.llm_client import LLMClient
from backend.logic.prompt_factory import PromptFactory
from backend.logic.response_cache import SemanticCache
from backend.logic.token_manager import TokenManager
from backend.logic.complexity_router import classify_complexity

logger = logging.getLogger("localmind.logic.chat_service")

from backend.logic.summarizer import Summarizer

class ChatService:
    def __init__(self, db_factory, registry, ontology=None, autonomy_engine=None, metacog_controller=None):
        self.db_factory = db_factory
        self.registry = registry
        self.ontology = ontology
        # autonomy_engine kept as an optional arg for backward compat but
        # cross-module communication now goes through backend.events.
        self.autonomy_engine = autonomy_engine
        self.metacog_controller = metacog_controller
        self.llm = LLMClient()
        self.prompt_factory = PromptFactory()
        self.token_manager = TokenManager()
        self.summarizer = Summarizer(self.llm)
        self.response_cache = SemanticCache()

    async def handle_chat(self, body: Dict[str, Any], auto_route_enabled: bool = True) -> AsyncIterator[str]:
        """The main entry point for a chat turn. Returns an SSE stream.

        Args:
            body: The request body with message, conversation_id, model, etc.
            auto_route_enabled: When True (default) and no explicit model is
                forced, the complexity router automatically picks the best
                model from those loaded in Ollama.  Set to False to disable.
        """
        # Notify autonomy engine (and any other listeners) that the user is active
        await events.emit("chat_activity")

        message = body.get("message", "")
        conversation_id = body.get("conversation_id")
        model_override = body.get("model")
        system_prompt = body.get("system_prompt")
        learning_enabled = body.get("learning_enabled", True)
        use_cache = body.get("use_cache", True)

        # Allow per-request opt-out of auto routing
        auto_route_flag = body.get("auto_route", auto_route_enabled) and config.AUTO_ROUTE_ENABLED

        # 1. Estimate Complexity and Route Model
        task_estimate = self._estimate_complexity(message)
        model, provider = await self._route_model(task_estimate, model_override, auto_route_flag)
        
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
            rag_context=await self._get_rag_context(message)
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

        # 7b. Semantic Cache Lookup (skip for image queries or when disabled)
        if use_cache and not body.get("image"):
            cached = await self.response_cache.lookup(messages, model=model)
            if cached:
                logger.info("Cache HIT (similarity=%.4f, age=%.1fs)", cached["similarity"], cached["cache_age_s"])
                await self._save_msg(conversation_id, "user", message)
                await self._save_msg(conversation_id, "assistant", cached["response"])
                return self._cached_response_stream(conversation_id, model, provider, task_estimate, cached)

        # 8. Save User Message
        await self._save_msg(conversation_id, "user", message)

        # 9. Return Stream
        return self._agent_loop(conversation_id, model, provider, messages, task_estimate, metacog_decision)

    async def _cached_response_stream(self, conversation_id, model, provider, task_estimate, cached):
        """Yield a complete SSE stream for a cached response."""
        yield f"data: {json.dumps({'thinking': {'model': model, 'provider': provider, 'tier': task_estimate['tier'], 'cached': True}})}\n\n"
        yield f"data: {json.dumps({'token': cached['response'], 'conversation_id': conversation_id, 'cached': True})}\n\n"
        yield f"data: {json.dumps({'done': True, 'cached': True, 'analytics': {'elapsed': 0.0, 'tokens': cached.get('original_tokens', 0), 'tps': 0, 'cache_similarity': cached['similarity'], 'cache_age_s': cached['cache_age_s']}})}\n\n"

    async def _agent_loop(self, conversation_id, model, provider, messages, task_estimate, metacog_decision):
        """The core streaming and tool execution loop."""
        full_response = ""
        total_tokens = 0
        total_tool_calls = 0
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

        for iteration in range(config.MAX_AGENT_ITERATIONS):
            logger.info(f"Agent Loop iteration {iteration+1}")
            
            # Determine Context Window for Ollama
            num_ctx = config.DEFAULT_CONTEXT_WINDOW
            if task_estimate["tier"] in ("heavy", "ultra"):
                num_ctx = 16384
            
            llm_options = {"num_ctx": num_ctx, "num_gpu": 99}
            
            chunk_text = ""
            tool_calls = []

            async for chunk in self.llm.generate_stream(model, messages, provider, options=llm_options):
                if "error" in chunk:
                    yield f"data: {json.dumps({'error': chunk['error']})}\n\n"
                    return
                
                if chunk.get("token"):
                    chunk_text += chunk["token"]
                    total_tokens += 1
                    yield f"data: {json.dumps({'token': chunk['token'], 'conversation_id': conversation_id})}\n\n"
                
                if chunk.get("tool_calls"):
                    tool_calls.extend(chunk["tool_calls"])
            
            # Fallback text-based tool parsing
            if not tool_calls and provider == "ollama":
                tool_calls = self._parse_text_tools(chunk_text)

            full_response += chunk_text
            
            if not tool_calls:
                break
            
            # Execute Tools
            for tc in tool_calls:
                name = tc["function"]["name"]
                args = tc["function"]["arguments"]
                yield f"data: {json.dumps({'tool_call': {'name': name, 'arguments': args}})}\n\n"
                
                try:
                    res = await self.registry.execute_tool(name, args)
                    res_str = str(res.get("result", res)) if isinstance(res, dict) else str(res)
                except Exception as e:
                    res_str = f"Error: {str(e)}"
                
                yield f"data: {json.dumps({'tool_result': {'name': name, 'result': res_str}})}\n\n"
                total_tool_calls += 1
                
                messages.append({"role": "assistant", "content": chunk_text, "tool_calls": [tc]})
                messages.append({"role": "tool", "content": res_str})

        # Finalize
        await self._save_msg(conversation_id, "assistant", full_response)
        
        elapsed = time.time() - start_time
        elapsed_ms = elapsed * 1000
        yield f"data: {json.dumps({'done': True, 'analytics': {'elapsed': round(elapsed, 2), 'tokens': total_tokens, 'tps': round(total_tokens/elapsed, 1) if elapsed > 0 else 0}})}\n\n"

        # Store in semantic cache (skip if tool calls were used)
        if total_tool_calls == 0 and full_response.strip():
            await self.response_cache.store(
                messages=messages,
                response_text=full_response,
                model=model,
                token_count=total_tokens,
                generation_time_ms=elapsed_ms,
            )

        # Background: Auto-save, reflection, etc.
        await self.auto_save_facts(messages[-2]["content"], True)

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
        """Estimate task complexity using the heuristic classifier.

        Returns a dict with 'score' (0-10) and 'tier' (light/medium/heavy)
        for backward compatibility with the rest of the codebase.
        """
        # Use the new classifier for the tier decision
        complexity = classify_complexity(
            [{"role": "user", "content": message}],
        )

        # Map complexity label to legacy tier + approximate score
        complexity_map = {
            "simple":   {"tier": "light",  "score": 2},
            "moderate": {"tier": "medium", "score": 5},
            "complex":  {"tier": "heavy",  "score": 8},
        }
        result = complexity_map.get(complexity, {"tier": "medium", "score": 5})

        # Refine the score with the old heuristics so downstream consumers
        # (metacog threshold, context-window sizing) still see granularity.
        score = result["score"]
        if len(message) > 200:
            score += 1
        for kw in ["refactor", "architecture", "design"]:
            if kw in message.lower():
                score += 1
        result["score"] = min(score, 10)
        return result

    async def _route_model(
        self,
        estimate: Dict[str, Any],
        override: str = None,
        use_auto_route: bool = True,
    ) -> tuple:
        """Select a model and provider.

        Priority order:
        1. Explicit user override (model != "auto")
        2. Complexity router (when auto_route is enabled)
        3. Legacy tier-based fallback from config
        4. Gemini cloud escalation for heavy tasks
        """
        from backend.logic.complexity_router import (
            route_model as cr_route,
            get_available_models,
        )

        # 1. Explicit override always wins
        if override and override != "auto":
            logger.info("Model override: %s (user-specified)", override)
            return override, "ollama"

        # 2. Complexity router — maps the existing tier estimate to a
        #    complexity level and picks from actually-loaded Ollama models.
        if use_auto_route:
            try:
                tier_to_complexity = {
                    "light": "simple",
                    "medium": "moderate",
                    "heavy": "complex",
                    "ultra": "complex",
                }
                complexity = tier_to_complexity.get(estimate["tier"], "moderate")
                available = await get_available_models()

                if available:
                    routed_model, reason = cr_route(complexity, available)
                    logger.info(
                        "Complexity router: model=%s  complexity=%s  reason=%s",
                        routed_model, complexity, reason,
                    )
                    return routed_model, "ollama"
            except Exception as exc:
                logger.warning("Complexity router failed, falling back: %s", exc)

        # 3. Legacy fallback
        # Cloud escalation for heavy tasks if Gemini is available
        from backend import gemini_client
        if estimate["tier"] == "heavy" and gemini_client.is_available():
            return "gemini-1.5-pro", "gemini"

        fallback = config.MODEL_TIERS.get(estimate["tier"], "qwen2.5-coder:7b")
        logger.info("Legacy fallback: model=%s  tier=%s", fallback, estimate["tier"])
        return fallback, "ollama"

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
            from backend.tools.rag import query_documents
            res = query_documents(message, n_results=3)
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
        calls = []
        pattern = re.compile(r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{[^}]*\})\s*\}', re.DOTALL)
        for m in pattern.finditer(text):
            name = m.group(1)
            if any(t.name == name for t in self.registry.tools):
                try:
                    args = json.loads(m.group(2))
                    calls.append({"function": {"name": name, "arguments": args}})
                except: pass
        return calls
