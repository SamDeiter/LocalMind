import logging
from typing import List, Dict, Any

logger = logging.getLogger("localmind.logic.token_manager")

class TokenManager:
    """Manages token counting and context truncation for LLM requests.
    
    Ensures the AI is only given the exact necessary information, 
    minimizing overhead and maximizing focus.
    """
    
    # Heuristic: 1 token ~= 4 characters for English text
    CHARS_PER_TOKEN = 4

    @classmethod
    def count_tokens(cls, text: str) -> int:
        """Estimate token count based on character length."""
        if not text:
            return 0
        return len(text) // cls.CHARS_PER_TOKEN

    @classmethod
    async def summarize_and_truncate(cls, messages: List[Dict[str, str]], max_tokens: int, summarizer: Any) -> List[Dict[str, str]]:
        """Prunes conversation history and summarizes the evicted part.
        
        Ensures the AI has a 'memory' of what was removed.
        """
        if not messages or len(messages) <= 4: # Too short to summarize
            return cls.truncate_history(messages, max_tokens)

        system_msg = messages[0]
        latest_msgs = messages[-3:] # Keep last 3 messages for immediate context flow
        
        evict_threshold = max_tokens // 2
        history_to_keep = messages[1:-3]
        
        current_tokens = sum(cls.count_tokens(m["content"]) for m in [system_msg] + latest_msgs)
        
        kept_history = []
        evicted_history = []
        
        # Split history into kept and evicted
        for msg in reversed(history_to_keep):
            msg_tokens = cls.count_tokens(msg["content"])
            if current_tokens + msg_tokens <= max_tokens:
                kept_history.insert(0, msg)
                current_tokens += msg_tokens
            else:
                evicted_history.insert(0, msg)

        if not evicted_history:
            return [system_msg] + kept_history + latest_msgs

        # Summarize the 'lost' part
        summary = await summarizer.summarize_history(evicted_history)
        summary_msg = {
            "role": "system", 
            "content": f"[CONVERSATION SUMMARY OF EARLIER TURNS]: {summary}"
        }
        
        return [system_msg, summary_msg] + kept_history + latest_msgs

    @classmethod
    def truncate_text(cls, text: str, max_tokens: int) -> str:
        """Truncates a single block of text (e.g., RAG context) to a token limit."""
        if not text:
            return ""
        
        limit_chars = max_tokens * cls.CHARS_PER_TOKEN
        if len(text) <= limit_chars:
            return text
            
        return text[:limit_chars] + "... [Context Truncated]"
