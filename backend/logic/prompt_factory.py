from backend.logic.token_manager import TokenManager

class PromptFactory:
    """Generates and manages system prompts for different models and contexts.
    
    Ensures consistency across modules while enforcing strict token limits.
    """

    @staticmethod
    def build_system_prompt(
        base_prompt: str,
        model_name: str,
        task_tier: str = "light",
        editor_context: str = None,
        rag_context: str = None,
        max_context_tokens: int = 2000
    ) -> str:
        """Assembles a comprehensive system prompt string with strict truncation."""
        prompt = base_prompt if base_prompt else ""

        # 1. Inject RAG context (Strictly truncated)
        if rag_context:
            truncated_rag = TokenManager.truncate_text(rag_context, max_tokens=500)
            prompt += f"\n\nRelevant document context:\n{truncated_rag}"

        # 2. Add task-specific suffix (Coding)
        if task_tier in ("medium", "heavy", "ultra"):
            prompt += config.CODING_PROMPT_SUFFIX

        # 3. Add model self-awareness
        prompt += config.MODEL_AWARENESS_SUFFIX.format(model_name=model_name)

        # 4. Add self-improvement capabilities
        prompt += config.SELF_IMPROVEMENT_SUFFIX

        # 5. Inject Editor Context (Strictly truncated)
        if editor_context:
            truncated_editor = TokenManager.truncate_text(editor_context, max_tokens=1000)
            prompt += f"\n\n[EDITOR CONTEXT — The user currently has this file open in their editor]\n{truncated_editor}\n[/EDITOR CONTEXT]"

        return prompt

    @staticmethod
    def build_metacog_context(intent) -> str:
        """Builds the snippet for meta-cognitive intent context."""
        if not intent:
            return ""
            
        context = f"\n\n[META-COGNITIVE CONTEXT]\n"
        context += f"User's actual goal: {intent.inferred_goal}\n"
        if intent.constraints:
            context += f"Constraints: {', '.join(intent.constraints)}\n"
        if intent.forbidden_actions:
            context += f"Do NOT: {', '.join(intent.forbidden_actions)}\n"
        if intent.preferred_output_style:
            context += f"Style: {intent.preferred_output_style}\n"
        context += f"[/META-COGNITIVE CONTEXT]"
        return context
