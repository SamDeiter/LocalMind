"""Context building for chat: complexity estimation, model routing, RAG, and memory injection.

Extracted from ChatService to keep each module focused on a single concern.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from backend import config
from backend.logic.load_monitor import LoadMonitor
from backend.logic.prompt_factory import PromptFactory

logger = logging.getLogger("localmind.logic.context_builder")


class ContextBuilder:
    def __init__(self, registry, prompt_factory: PromptFactory = None, load_monitor: LoadMonitor = None):
        self.registry = registry
        self.prompt_factory = prompt_factory or PromptFactory()
        self.load_monitor = load_monitor or LoadMonitor()

    # ------------------------------------------------------------------
    # Complexity estimation
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_complexity(message: str) -> Dict[str, Any]:
        score = 3
        msg_lower = message.lower()
        if len(message) > 200:
            score += 2
        for kw in ["code", "refactor", "bug", "error", "architecture", "design"]:
            if kw in msg_lower:
                score += 2

        tool_keywords = [
            "emulator", "android", "apk", "avd", "install app", "scroll",
            "email", "gmail", "send email", "inbox", "draft",
            "browse", "navigate", "click", "website",
            "screenshot", "search the web", "look up",
            "run code", "execute", "terminal",
            "git commit", "git status", "git diff",
            "powerpoint", "power point", "pptx", "presentation", "slide deck", "slides",
            "create a file", "write a file", "save a file", "make a file",
            "read file", "open file", "list files",
            "web search", "google",
        ]
        needs_tools = any(kw in msg_lower for kw in tool_keywords)
        if needs_tools:
            score = max(score, 5)

        tier = "light"
        if score >= 8:
            tier = "heavy"
        elif score >= 5:
            tier = "medium"
        return {"score": min(score, 10), "tier": tier, "needs_tools": needs_tools}

    # ------------------------------------------------------------------
    # Model routing
    # ------------------------------------------------------------------

    async def route_model(self, estimate: Dict[str, Any], override: str = None) -> Tuple[str, str]:
        tier = estimate["tier"]

        if override and override != "auto":
            if estimate.get("needs_tools"):
                small_models = set(config.MODELS_NO_NATIVE_TOOLS)
                if override in small_models:
                    logger.warning(f"Override '{override}' too small for tool calling — upgrading to medium tier")
                else:
                    return override, "ollama"
            else:
                return override, "ollama"

        gpu_state = await self.load_monitor.get_gpu_state()
        loaded = gpu_state.get("loaded_models", [])

        if loaded:
            reuse = self.load_monitor.pick_best_model(tier, loaded)
            if reuse:
                return reuse, "ollama"

        # For tool-calling tasks, use at least medium tier (small models lack
        # reliable tool-call formatting).
        if estimate.get("needs_tools") and tier == "light":
            tier = "medium"

        return config.MODEL_TIERS.get(tier, "gemma3:4b"), "ollama"

    @staticmethod
    def escalate_model(current_model: str) -> Optional[str]:
        """Return the next bigger model, or None if already at the top."""
        normalized = current_model.replace(":latest", "")
        ranked = sorted(
            config.MODEL_CAPABILITIES.items(),
            key=lambda kv: len(kv[1]),
        )
        current_level = len(config.MODEL_CAPABILITIES.get(normalized, ["light"]))

        for name, caps in ranked:
            if len(caps) > current_level and name != normalized:
                return name
        return None

    # ------------------------------------------------------------------
    # System prompt assembly
    # ------------------------------------------------------------------

    async def build_context(
        self,
        message: str,
        task_estimate: Dict[str, Any],
        system_prompt: Optional[str],
        editor_context: Optional[str],
        metacog_controller=None,
        conversation_id: str = None,
        turn_count: int = 0,
    ) -> Tuple[str, Any]:
        """Build the full system prompt with RAG, memory, and metacognitive context.

        Returns (system_prompt, metacog_decision).
        """
        sys_prompt = self.prompt_factory.build_system_prompt(
            base_prompt=system_prompt if system_prompt else config.DEFAULT_SYSTEM_PROMPT,
            model_name="",
            task_tier=task_estimate["tier"],
            editor_context=editor_context,
            rag_context=await self._get_rag_context(message),
            needs_tools=task_estimate.get("needs_tools", False),
        )

        sys_prompt = self._inject_identity(sys_prompt)
        sys_prompt = await self._inject_memory(message, sys_prompt, task_estimate)
        sys_prompt = self._inject_user_profile(sys_prompt, message, turn_count)

        metacog_decision = None
        if metacog_controller and task_estimate["score"] >= 5:
            metacog_decision = await metacog_controller.pre_process(message, conversation_id)
            if metacog_decision and metacog_controller.session and metacog_controller.session.active_intent:
                sys_prompt += self.prompt_factory.build_metacog_context(metacog_controller.session.active_intent)

        return sys_prompt, metacog_decision

    def _inject_identity(self, sys_prompt: str) -> str:
        """Prepend the bot's chosen name + persona so it speaks as itself.

        The identity is mutable at runtime — the bot can call `update_identity`
        to rename itself or refine its persona, and the next system prompt
        reflects the change. This is what lets LocalMind become whoever the
        user (or the bot) decides it should be.
        """
        try:
            from backend.identity.store import get_identity
            ident = get_identity()
            block = (
                f"[YOUR IDENTITY]\n"
                f"Name: {ident.name}\n"
                f"Persona: {ident.persona}\n"
                f"Voice: {ident.voice}\n"
                f"You may evolve this identity over time by calling the "
                f"`update_identity` tool.\n"
                f"[/YOUR IDENTITY]\n\n"
            )
            return block + sys_prompt
        except Exception as exc:
            logger.debug("Identity injection skipped: %s", exc)
            return sys_prompt

    def _inject_user_profile(self, sys_prompt: str, message: str, turn_count: int) -> str:
        """Append the [USER_PROFILE] block + an optional [CURIOSITY] hint.

        - USER_PROFILE: facts the AI has already learned about this user.
        - CURIOSITY: at most one prompt to ask a natural question to learn more.
          The AI is the learner; the user is the source of truth about themselves.
        """
        try:
            from backend.metacognition.memory_manager import get_memory_manager
            from backend.logic.curiosity import build_curiosity_hint, render_profile_block

            mm = get_memory_manager()
            prefs = mm.read_preferences()
            profile = {p.key: p.value for p in prefs}

            block = render_profile_block(profile)
            if block:
                sys_prompt += "\n\n" + block

            hint = build_curiosity_hint(
                known_keys=set(profile.keys()),
                user_message=message,
                turn_count=turn_count,
            )
            if hint:
                sys_prompt += f"\n\n[CURIOSITY]\n{hint}\n[/CURIOSITY]"
        except Exception as exc:
            logger.debug("User profile / curiosity injection skipped: %s", exc)
        return sys_prompt

    # ------------------------------------------------------------------
    # RAG + Memory
    # ------------------------------------------------------------------

    async def _get_rag_context(self, message: str) -> Optional[str]:
        try:
            import asyncio
            from backend.tools.rag import query_documents
            loop = asyncio.get_event_loop()
            res = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: query_documents(message, n_results=3)),
                timeout=5.0
            )
            if not res or not res.get("results"):
                return None
            # Filter out low-relevance chunks (cosine similarity < 0.3)
            results = [r for r in res["results"] if r.get("relevance") is None or r.get("relevance", 0) >= 0.3]
            if not results:
                return None
            return "\n".join([f"[{r['source']} chunk {r.get('chunk_index', '?')}]: {r['content']}" for r in results])
        except Exception:
            return None

    async def _inject_memory(self, message: str, sys_prompt: str, estimate: Dict[str, Any]) -> str:
        # ── Tier-2: FTS5 recall (semantic short-term) ───────────────────
        try:
            mem_tool = self.registry.get_tool("recall_memories")
            if mem_tool:
                res = await mem_tool.execute(query=message, limit=5)
                text = res.get("result", "") if isinstance(res, dict) else str(res)
                if text and "No memories" not in text:
                    sys_prompt += f"\n\n[MEMORIES]\n{text}\n[/MEMORIES]"
        except Exception:
            pass

        # ── Tier-3: MemPalace wake-up context (palace L0 + L1) ──────────
        try:
            from backend.memory.palace_manager import is_ready, get_wakeup_context, search
            if is_ready():
                # Wake-up: ~170 tokens of critical facts (always loaded)
                wakeup = get_wakeup_context()
                if wakeup:
                    sys_prompt += f"\n\n[PALACE_CONTEXT]\n{wakeup}\n[/PALACE_CONTEXT]"

                # On-demand: semantic search for query-relevant palace memories
                palace_results = search(query=message, limit=3)
                if palace_results:
                    snippets = []
                    for r in palace_results:
                        loc = "/".join(filter(None, [r.get("wing", ""), r.get("room", "")]))
                        content = r.get("content", r.get("document", ""))[:300]
                        snippets.append(f"[{loc}]: {content}")
                    sys_prompt += f"\n\n[PALACE_SEARCH]\n" + "\n".join(snippets) + "\n[/PALACE_SEARCH]"
        except Exception as _mp_exc:
            logger.debug("MemPalace memory injection skipped (non-fatal): %s", _mp_exc)

        return sys_prompt
