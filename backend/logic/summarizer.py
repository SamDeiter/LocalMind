import logging
from typing import List, Dict, Any
from backend.logic.llm_client import LLMClient

logger = logging.getLogger("localmind.logic.summarizer")

class Summarizer:
    """Service to condense large blocks of text or history into concise summaries.
    
    Used by the TokenManager to ensure no context is lost during strict pruning.
    """
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.model = "qwen2.5-coder:3b" # Default fast model for utility tasks

    async def summarize_history(self, messages: List[Dict[str, str]]) -> str:
        """Condenses a list of chat messages into a single narrative summary."""
        if not messages:
            return ""
            
        history_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
        prompt = [
            {"role": "system", "content": "You are a concise summarizer. Summarize the following conversation history into a single, dense paragraph. Preserve all key facts, user preferences, and pending tasks. Do not lose critical technical details."},
            {"role": "user", "content": history_text}
        ]
        
        res = await self.llm.generate(model=self.model, messages=prompt)
        if "error" in res:
            logger.warning(f"Summarization failed: {res['error']}")
            return "[Summary failed, history was pruned due to length]"
            
        return res.get("content", "").strip()

    async def summarize_text(self, text: str, max_words: int = 100) -> str:
        """Condenses a block of text (RAG/Editor) into a smaller representative summary."""
        if not text:
            return ""
            
        prompt = [
            {"role": "system", "content": f"Summarize the following text in under {max_words} words. Preserve core meaning and important entities."},
            {"role": "user", "content": text}
        ]
        
        res = await self.llm.generate(model=self.model, messages=prompt)
        return res.get("content", "").strip()
