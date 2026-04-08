"""
best_of_n.py — Best-of-N sampling with LLM-as-judge or heuristic scoring.
==========================================================================

Generates N completions in parallel and picks the best one.  This
implements the test-time compute scaling strategy from PLAN.md:

  - easy tasks:  N=1 (single pass, no overhead)
  - medium:      N=4, score with LLM-judge or heuristics
  - hard:        N=8-16, PRM-guided pruning (future)

Scoring priority:
  1. scorer_model set  -> LLM-as-judge (rate 1-10)
  2. no scorer         -> heuristic scoring (length, tool use, JSON validity)
  3. future            -> PRM integration (stub interface provided)

Performance:
  - On RTX 4090: 8B model at 60 tok/s, N=16 takes ~2.5 min per step
  - Quality scales logarithmically — most gains by N=16

Usage::

    sampler = BestOfNSampler(
        ollama_url="http://127.0.0.1:11434",
        scorer_model="gemma4:e4b",  # optional
    )
    result = await sampler.sample(
        messages=[{"role": "user", "content": "Write a function..."}],
        tools=None,
        model="qwen2.5-coder:14b",
        n=4,
        temperature=0.7,
    )
    print(result.response, result.score)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

logger = logging.getLogger("localmind.inference.best_of_n")

# Timeout for a single Ollama chat call (seconds).
_OLLAMA_CALL_TIMEOUT: float = 120.0

# Maximum concurrent Ollama calls (prevent GPU overload).
_MAX_CONCURRENT: int = 4


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SampleResult:
    """The winning completion from a best-of-N sampling run."""

    response: str
    score: float
    reasoning: str
    tokens_in: int = 0
    tokens_out: int = 0
    n_generated: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": self.response,
            "score": self.score,
            "reasoning": self.reasoning,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "n_generated": self.n_generated,
            "duration_ms": self.duration_ms,
        }


@dataclass
class _Candidate:
    """Internal: a single completion candidate with its metadata."""

    response: str
    tool_calls: list[dict[str, Any]]
    tokens_in: int = 0
    tokens_out: int = 0
    score: float = 0.0
    score_reasoning: str = ""


# ---------------------------------------------------------------------------
# PRM interface (future integration point)
# ---------------------------------------------------------------------------

@runtime_checkable
class ProcessRewardModel(Protocol):
    """Protocol for Process Reward Model integration.

    A PRM scores each intermediate step, not just the final answer.
    This enables pruning bad branches early — 4x more efficient than
    blind best-of-N (see PLAN.md).

    To integrate a PRM, implement this protocol and pass the instance
    to :class:`BestOfNSampler` via the ``prm`` parameter (future).
    """

    async def score_step(
        self,
        messages: list[dict[str, Any]],
        response: str,
    ) -> float:
        """Score a single response step.  Returns 0.0-1.0."""
        ...


# ---------------------------------------------------------------------------
# BestOfNSampler
# ---------------------------------------------------------------------------

class BestOfNSampler:
    """Generate N completions in parallel, score each, return the best.

    Parameters
    ----------
    ollama_url:
        Base URL for the Ollama API (e.g. ``http://127.0.0.1:11434``).
    scorer_model:
        Optional model ID used as LLM-as-judge.  If set, each
        candidate is scored by asking this model to rate it 1-10.
        If ``None``, heuristic scoring is used instead.
    max_concurrent:
        Maximum parallel Ollama calls.  Defaults to 4 to avoid
        GPU memory pressure from overlapping KV caches.
    """

    def __init__(
        self,
        ollama_url: str,
        scorer_model: str | None = None,
        max_concurrent: int = _MAX_CONCURRENT,
    ) -> None:
        self._ollama_url = ollama_url.rstrip("/")
        self._scorer_model = scorer_model
        self._semaphore = asyncio.Semaphore(max_concurrent)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def sample(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None,
        model: str,
        n: int = 4,
        temperature: float = 0.7,
    ) -> SampleResult:
        """Generate N completions, score each, return the best.

        If N=1, this is equivalent to a single Ollama call (no overhead).

        Args:
            messages: Chat messages (system + user + history).
            tools:    Ollama-format tool schemas, or None.
            model:    Ollama model tag.
            n:        Number of completions to generate.
            temperature: Sampling temperature (higher = more diverse).

        Returns:
            A :class:`SampleResult` with the best response.

        Raises:
            RuntimeError: If all N completions fail.
        """
        if n < 1:
            n = 1

        start_ms = int(time.monotonic() * 1000)

        # Generate N candidates in parallel
        candidates = await self._generate_candidates(
            messages=messages,
            tools=tools,
            model=model,
            n=n,
            temperature=temperature,
        )

        if not candidates:
            raise RuntimeError(
                f"All {n} candidate completions failed — no valid response"
            )

        # Score candidates
        scored = await self._score_candidates(candidates, messages)

        # Pick the best
        scored.sort(key=lambda c: c.score, reverse=True)
        winner = scored[0]

        elapsed = int(time.monotonic() * 1000) - start_ms

        total_in = sum(c.tokens_in for c in scored)
        total_out = sum(c.tokens_out for c in scored)

        logger.info(
            "Best-of-%d complete: winner score=%.2f, %d candidates, %dms "
            "(in=%d, out=%d tokens total)",
            n, winner.score, len(scored), elapsed, total_in, total_out,
        )

        return SampleResult(
            response=winner.response,
            score=winner.score,
            reasoning=winner.score_reasoning,
            tokens_in=total_in,
            tokens_out=total_out,
            n_generated=len(scored),
            duration_ms=elapsed,
        )

    # ------------------------------------------------------------------
    # Candidate generation
    # ------------------------------------------------------------------

    async def _generate_candidates(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None,
        model: str,
        n: int,
        temperature: float,
    ) -> list[_Candidate]:
        """Fire off N parallel Ollama calls and collect results."""

        async def _generate_one(idx: int) -> _Candidate | None:
            async with self._semaphore:
                return await self._call_ollama(
                    messages=messages,
                    tools=tools,
                    model=model,
                    temperature=temperature,
                    candidate_idx=idx,
                )

        tasks = [_generate_one(i) for i in range(n)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates: list[_Candidate] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "Candidate %d/%d failed: %s", i + 1, n, result
                )
            elif result is not None:
                candidates.append(result)

        return candidates

    async def _call_ollama(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None,
        model: str,
        temperature: float,
        candidate_idx: int,
    ) -> _Candidate | None:
        """POST to Ollama /api/chat (non-streaming) for one candidate."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        if tools:
            payload["tools"] = tools

        url = f"{self._ollama_url}/api/chat"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_OLLAMA_CALL_TIMEOUT, connect=10.0)
            ) as client:
                resp = await client.post(url, json=payload)

            if resp.status_code != 200:
                body = resp.text[:500]
                logger.error(
                    "Candidate %d: Ollama HTTP %d: %s",
                    candidate_idx, resp.status_code, body,
                )
                return None

            data = resp.json()

            if "error" in data:
                logger.error(
                    "Candidate %d: Ollama error: %s",
                    candidate_idx, data["error"],
                )
                return None

            msg = data.get("message", {})
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []

            return _Candidate(
                response=content,
                tool_calls=tool_calls,
                tokens_in=data.get("prompt_eval_count", 0),
                tokens_out=data.get("eval_count", 0),
            )

        except httpx.TimeoutException as exc:
            logger.error("Candidate %d: Ollama timeout: %s", candidate_idx, exc)
            return None
        except Exception as exc:
            logger.error("Candidate %d: Ollama call failed: %s", candidate_idx, exc)
            return None

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    async def _score_candidates(
        self,
        candidates: list[_Candidate],
        original_messages: list[dict[str, Any]],
    ) -> list[_Candidate]:
        """Score all candidates using the best available method.

        Priority:
          1. LLM-as-judge (if scorer_model is set)
          2. Heuristic scoring (fallback)
        """
        if self._scorer_model:
            try:
                return await self._score_with_llm_judge(
                    candidates, original_messages
                )
            except Exception as exc:
                logger.warning(
                    "LLM-judge scoring failed, falling back to heuristics: %s",
                    exc,
                )

        return self._score_with_heuristics(candidates)

    # ------------------------------------------------------------------
    # LLM-as-judge scoring
    # ------------------------------------------------------------------

    async def _score_with_llm_judge(
        self,
        candidates: list[_Candidate],
        original_messages: list[dict[str, Any]],
    ) -> list[_Candidate]:
        """Use a separate LLM to rate each candidate 1-10.

        The judge sees the original prompt and the candidate response,
        then produces a numeric score with brief reasoning.
        """
        # Extract the user's request from messages
        user_request = ""
        for msg in reversed(original_messages):
            if msg.get("role") == "user":
                user_request = msg.get("content", "")
                break

        async def _judge_one(candidate: _Candidate) -> None:
            judge_prompt = (
                "You are a quality judge. Rate the following AI response "
                "on a scale of 1-10 based on: accuracy, completeness, "
                "helpfulness, and clarity.\n\n"
                f"USER REQUEST:\n{user_request}\n\n"
                f"AI RESPONSE:\n{candidate.response}\n\n"
                "Reply with ONLY a JSON object: "
                '{"score": <1-10>, "reasoning": "<brief explanation>"}'
            )

            payload = {
                "model": self._scorer_model,
                "messages": [{"role": "user", "content": judge_prompt}],
                "stream": False,
                "options": {"temperature": 0.1},
            }

            try:
                async with self._semaphore:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(60.0, connect=10.0)
                    ) as client:
                        resp = await client.post(
                            f"{self._ollama_url}/api/chat",
                            json=payload,
                        )

                if resp.status_code != 200:
                    raise RuntimeError(f"Judge HTTP {resp.status_code}")

                data = resp.json()
                judge_text = data.get("message", {}).get("content", "")

                # Parse the judge's score
                score, reasoning = _parse_judge_response(judge_text)
                candidate.score = score
                candidate.score_reasoning = reasoning

            except Exception as exc:
                logger.warning("Judge scoring failed for candidate: %s", exc)
                # Fallback to heuristic for this candidate
                self._score_single_heuristic(candidate)

        # Score all candidates in parallel (through the semaphore)
        await asyncio.gather(*[_judge_one(c) for c in candidates])
        return candidates

    # ------------------------------------------------------------------
    # Heuristic scoring
    # ------------------------------------------------------------------

    def _score_with_heuristics(
        self, candidates: list[_Candidate]
    ) -> list[_Candidate]:
        """Score candidates using simple heuristics.

        Scoring signals:
          - Response length (not too short, not too long)
          - JSON validity (bonus for well-formed structured output)
          - Tool use count (using tools is usually productive)
          - Completeness indicators (no truncation markers)
        """
        for c in candidates:
            self._score_single_heuristic(c)
        return candidates

    def _score_single_heuristic(self, candidate: _Candidate) -> None:
        """Apply heuristic scoring to a single candidate."""
        score = 5.0  # Start at neutral
        reasons: list[str] = []

        text = candidate.response.strip()

        # Length scoring: reward medium-length responses
        length = len(text)
        if length == 0:
            score -= 4.0
            reasons.append("empty response")
        elif length < 20:
            score -= 2.0
            reasons.append("very short")
        elif length < 100:
            score -= 0.5
            reasons.append("short")
        elif 100 <= length <= 2000:
            score += 1.0
            reasons.append("good length")
        elif length > 5000:
            score -= 0.5
            reasons.append("very long")

        # JSON validity bonus
        if _is_valid_json(text):
            score += 1.5
            reasons.append("valid JSON")

        # Tool calls bonus
        if candidate.tool_calls:
            tool_count = len(candidate.tool_calls)
            score += min(tool_count * 0.5, 2.0)
            reasons.append(f"{tool_count} tool call(s)")

        # Completeness check — penalize truncated responses
        truncation_markers = ["...", "[truncated]", "[continued]"]
        if any(marker in text[-50:] for marker in truncation_markers):
            score -= 1.0
            reasons.append("possibly truncated")

        # Error indicator penalty
        error_patterns = ["i cannot", "i'm unable", "i don't know how", "error:"]
        lower_text = text.lower()
        if any(pat in lower_text for pat in error_patterns):
            score -= 1.5
            reasons.append("error/refusal detected")

        # Clamp to [0, 10]
        candidate.score = max(0.0, min(10.0, score))
        candidate.score_reasoning = "; ".join(reasons) if reasons else "neutral"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_judge_response(text: str) -> tuple[float, str]:
    """Extract a numeric score and reasoning from the LLM judge response.

    Tries JSON parsing first, then regex fallback.
    """
    text = text.strip()

    # Try JSON parse
    try:
        data = json.loads(text)
        score = float(data.get("score", 5))
        reasoning = str(data.get("reasoning", ""))
        return max(0.0, min(10.0, score)), reasoning
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Try extracting JSON from markdown fences
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1))
            score = float(data.get("score", 5))
            reasoning = str(data.get("reasoning", ""))
            return max(0.0, min(10.0, score)), reasoning
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Regex fallback: look for a number 1-10
    score_match = re.search(r"\b(\d{1,2})\s*/\s*10\b", text)
    if not score_match:
        score_match = re.search(r"score[:\s]*(\d{1,2})", text, re.IGNORECASE)
    if not score_match:
        score_match = re.search(r"\b([1-9]|10)\b", text)

    if score_match:
        score = float(score_match.group(1))
        return max(0.0, min(10.0, score)), text[:200]

    # Complete fallback
    return 5.0, "could not parse judge response"


def _is_valid_json(text: str) -> bool:
    """Check if text is valid JSON (object or array)."""
    stripped = text.strip()
    if not stripped:
        return False
    try:
        parsed = json.loads(stripped)
        return isinstance(parsed, (dict, list))
    except (json.JSONDecodeError, ValueError):
        return False
