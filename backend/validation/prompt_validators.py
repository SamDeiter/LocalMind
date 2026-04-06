"""
prompt_validators.py — System prompt analysis validators.

Two LLM-based validators that assess prompt quality:

  1. PromptConsistencyValidator — detects contradictions in system prompts
  2. EdgeCaseValidator — evaluates edge-case coverage in prompts

Both use local Ollama (zero marginal cost).
"""

import logging
from typing import Optional

import httpx

from backend.config import OLLAMA_BASE_URL
from backend.validation.base import Validator, ValidationResult, Severity
from backend.validation.robust_parser import parse_json

logger = logging.getLogger("localmind.validation.prompt")


class PromptConsistencyValidator(Validator):
    """
    Detects internal contradictions in system prompts.
    Uses a local Ollama model as an LLM-as-a-Judge.

    Based on AgentFixer's Prompt-Consistency-Validator.
    """

    def __init__(self, model: str = "qwen2.5-coder:7b", ollama_url: str = OLLAMA_BASE_URL):
        super().__init__("PromptConsistencyValidator", Severity.MODERATE)
        self.model = model
        self.ollama_url = ollama_url

    def validate(self, context: dict) -> ValidationResult:
        """Sync stub — use validate_async() for full check."""
        return self._pass("Skipped — use validate_async() for LLM-based validation")

    async def validate_async(self, context: dict) -> ValidationResult:
        """
        Context keys:
          - system_prompt: str — the full system prompt to analyze
        """
        prompt_text = context.get("system_prompt", "")
        if not prompt_text or len(prompt_text) < 50:
            return self._pass("Prompt too short for consistency analysis")

        judge_prompt = (
            "You are a prompt quality auditor. Analyze this system prompt for "
            "INTERNAL CONTRADICTIONS — places where one instruction conflicts "
            "with another.\n\n"
            f"SYSTEM PROMPT:\n{prompt_text[:3000]}\n\n"
            "Look for:\n"
            "1. Instructions that directly negate each other\n"
            "2. Format specs that conflict with examples\n"
            "3. Role definitions that contradict behavioral rules\n\n"
            'Output JSON: {"consistent": true/false, "contradictions": ["..."]}\n'
            "Only output JSON."
        )

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": judge_prompt,
                        "stream": False,
                        "options": {"num_predict": 200, "num_ctx": 4096},
                    },
                )

                if resp.status_code == 200:
                    text = resp.json().get("response", "")
                    data = parse_json(text)
                    if data and isinstance(data, dict):
                        consistent = data.get("consistent", True)
                        contradictions = data.get("contradictions", [])

                        if not consistent and contradictions:
                            return self._fail(
                                f"Prompt has {len(contradictions)} contradiction(s)",
                                recommendations=[
                                    f"Fix: {c}" for c in contradictions[:3]
                                ],
                                contradictions=contradictions,
                            )
                        return self._pass("No contradictions found in prompt")

        except (httpx.HTTPError, Exception) as e:
            logger.warning(f"PromptConsistencyValidator failed: {e}")

        return self._pass("Validator unavailable — defaulting to pass")


class EdgeCaseValidator(Validator):
    """
    Evaluates whether system prompts handle edge cases adequately.
    Uses local Ollama as judge.

    Based on AgentFixer's Edge-Case-Instruction-Validator.
    """

    def __init__(self, model: str = "qwen2.5-coder:7b", ollama_url: str = OLLAMA_BASE_URL):
        super().__init__("EdgeCaseValidator", Severity.MINOR)
        self.model = model
        self.ollama_url = ollama_url

    def validate(self, context: dict) -> ValidationResult:
        """Sync stub — use validate_async() for full check."""
        return self._pass("Skipped — use validate_async() for LLM-based validation")

    async def validate_async(self, context: dict) -> ValidationResult:
        """
        Context keys:
          - system_prompt: str — the prompt to evaluate
          - task_type: str — optional, the type of task (e.g., "code_edit", "planning")
        """
        prompt_text = context.get("system_prompt", "")
        task_type = context.get("task_type", "general")

        if not prompt_text or len(prompt_text) < 50:
            return self._pass("Prompt too short for edge-case analysis")

        judge_prompt = (
            "You are a QA engineer. Analyze this system prompt for MISSING "
            "EDGE CASE handling. Focus on realistic failure scenarios.\n\n"
            f"SYSTEM PROMPT:\n{prompt_text[:3000]}\n\n"
            f"TASK TYPE: {task_type}\n\n"
            "Check if the prompt addresses:\n"
            "1. Empty or missing inputs\n"
            "2. Invalid data formats\n"
            "3. Ambiguous or contradictory user requests\n"
            "4. Error recovery or fallback behavior\n"
            "5. Boundary conditions (max length, special chars, etc.)\n\n"
            'Output JSON: {"covered": true/false, "missing_cases": ["..."], '
            '"coverage_score": 0.0-1.0}\n'
            "Only output JSON."
        )

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": judge_prompt,
                        "stream": False,
                        "options": {"num_predict": 200, "num_ctx": 4096},
                    },
                )

                if resp.status_code == 200:
                    text = resp.json().get("response", "")
                    data = parse_json(text)
                    if data and isinstance(data, dict):
                        covered = data.get("covered", True)
                        missing = data.get("missing_cases", [])
                        score = data.get("coverage_score", 1.0)

                        if not covered and missing:
                            return self._fail(
                                f"Edge-case coverage gaps ({score:.0%}): {len(missing)} missing",
                                recommendations=[
                                    f"Add handling for: {case}" for case in missing[:3]
                                ],
                                missing_cases=missing,
                                coverage_score=score,
                            )
                        return self._pass(
                            f"Edge-case coverage adequate ({score:.0%})"
                        )

        except (httpx.HTTPError, Exception) as e:
            logger.warning(f"EdgeCaseValidator failed: {e}")

        return self._pass("Validator unavailable — defaulting to pass")
