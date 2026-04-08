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
                small_models = {"gemma3:4b", "gemma4:e4b", "qwen2.5-coder:7b"}
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

        if estimate.get("needs_tools"):
            try:
                from src.agent.config import detect_hardware, select_model
                hw = detect_hardware()
                spec = select_model(hw, task_type="tool_calling", prefer_tool_calling=True)
                logger.info(f"Hardware-aware routing: {spec.name} (tool_score={spec.tool_calling_score})")
                return spec.name, "ollama"
            except Exception as e:
                logger.warning(f"Hardware-aware routing failed, using tier defaults: {e}")

        from backend import gemini_client
        if tier == "heavy" and gemini_client.is_available():
            return "gemini-1.5-pro", "gemini"

        return config.MODEL_TIERS.get(tier, "gemma4:e4b"), "ollama"

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

        sys_prompt = await self._inject_memory(message, sys_prompt, task_estimate)

        metacog_decision = None
        if metacog_controller and task_estimate["score"] >= 5:
            metacog_decision = await metacog_controller.pre_process(message, conversation_id)
            if metacog_decision and metacog_controller.session and metacog_controller.session.active_intent:
                sys_prompt += self.prompt_factory.build_metacog_context(metacog_controller.session.active_intent)

        return sys_prompt, metacog_decision

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
            return "\n".join([f"[{r['source']}]: {r['content'][:500]}" for r in res["results"]])
        except Exception:
            return None

    async def _inject_memory(self, message: str, sys_prompt: str, estimate: Dict[str, Any]) -> str:
        try:
            mem_tool = self.registry.get_tool("recall_memories")
            if mem_tool:
                res = await mem_tool.execute(query=message, limit=5)
                text = res.get("result", "") if isinstance(res, dict) else str(res)
                if text and "No memories" not in text:
                    return sys_prompt + f"\n\n[MEMORIES]\n{text}\n[/MEMORIES]"
            return sys_prompt
        except Exception:
            return sys_prompt
