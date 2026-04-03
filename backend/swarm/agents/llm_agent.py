"""
LLMAgent — GPU-gated inference worker.
Must acquire the GPU semaphore before making Ollama requests.
Handles reflection, code editing, and proposal generation.
"""

import asyncio
import json
import logging
import re
import httpx

from backend.config import OLLAMA_BASE_URL
from backend.swarm.task_queue import SwarmTask, SwarmResult, TaskType
from backend.swarm.agents import BaseAgent

logger = logging.getLogger("localmind.swarm.llm")


class LLMAgent(BaseAgent):
    """GPU-gated LLM worker. Must acquire a semaphore before inference.
    
    Payload keys:
        prompt: str         — The prompt to send to Ollama
        model: str          — Model name (e.g., "qwen2.5-coder:14b")
        system: str         — Optional system prompt
        max_tokens: int     — num_predict limit (default 4096)
        temperature: float  — Generation temperature (default 0.1)
        parse_json: bool    — If True, attempt to parse response as JSON
    """

    agent_type = "llm"

    def __init__(self, gpu_semaphore: asyncio.Semaphore, ollama_url: str = OLLAMA_BASE_URL, **kwargs):
        super().__init__(**kwargs)
        self._gpu_semaphore = gpu_semaphore
        self._ollama_url = ollama_url

    async def execute(self, task: SwarmTask) -> SwarmResult:
        prompt = task.payload.get("prompt", "")
        model = task.payload.get("model", "qwen2.5-coder:14b")
        system = task.payload.get("system", "")
        max_tokens = task.payload.get("max_tokens", 4096)
        temperature = task.payload.get("temperature", 0.1)
        parse_json = task.payload.get("parse_json", False)

        if not prompt:
            return SwarmResult(
                task_id=task.id, task_type=task.type,
                success=False, error="No prompt provided",
            )

        # Gate on GPU semaphore — blocks until a slot is available
        async with self._gpu_semaphore:
            logger.info(f"[{self.agent_id}] Acquired GPU slot for {task.type.value}")
            self.heartbeat()

            try:
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})

                async with httpx.AsyncClient(timeout=600.0) as client:
                    resp = await client.post(
                        f"{self._ollama_url}/api/chat",
                        json={
                            "model": model,
                            "messages": messages,
                            "stream": False,
                            "options": {
                                "num_predict": max_tokens,
                                "num_ctx": 16384,
                                "num_gpu": 99,
                                "temperature": temperature,
                            },
                        },
                    )

                if resp.status_code != 200:
                    return SwarmResult(
                        task_id=task.id, task_type=task.type,
                        success=False, error=f"Ollama HTTP {resp.status_code}",
                    )

                raw_content = resp.json().get("message", {}).get("content", "").strip()
                eval_info = resp.json().get("eval_count", 0)

                # Optionally parse as JSON
                parsed_data = raw_content
                if parse_json:
                    parsed_data = self._try_parse_json(raw_content)

                return SwarmResult(
                    task_id=task.id,
                    task_type=task.type,
                    success=True,
                    data={
                        "content": raw_content,
                        "parsed": parsed_data,
                        "model": model,
                        "tokens": eval_info,
                    },
                )

            except httpx.TimeoutException:
                return SwarmResult(
                    task_id=task.id, task_type=task.type,
                    success=False, error="Ollama request timed out",
                )
            except Exception as exc:
                return SwarmResult(
                    task_id=task.id, task_type=task.type,
                    success=False, error=str(exc),
                )

    def _try_parse_json(self, text: str):
        """Robust JSON extraction from LLM output (handles markdown fences)."""
        # 1. Try raw parse
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # 2. Try stripping markdown fences
        text_clean = text.strip()
        if text_clean.startswith("```"):
            text_clean = re.sub(r'^```(?:json)?', '', text_clean)
            text_clean = re.sub(r'```$', '', text_clean).strip()
            try:
                return json.loads(text_clean)
            except json.JSONDecodeError:
                pass

        # 3. Try extracting code blocks
        blocks = re.findall(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
        for block in blocks:
            try:
                return json.loads(block.strip())
            except json.JSONDecodeError:
                continue

        # 4. Greedy brace extraction
        first = text.find('{')
        last = text.rfind('}')
        if first != -1 and last > first:
            try:
                return json.loads(text[first:last + 1])
            except json.JSONDecodeError:
                pass

        # 5. Try array extraction
        first_bracket = text.find('[')
        last_bracket = text.rfind(']')
        if first_bracket != -1 and last_bracket > first_bracket:
            try:
                return json.loads(text[first_bracket:last_bracket + 1])
            except json.JSONDecodeError:
                pass

        logger.debug(f"JSON parse failed for LLM output: {text[:100]}...")
        return text  # Return raw text as fallback
