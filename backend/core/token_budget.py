"""
Token Optimization — budget allocation, prompt compilation, and usage tracking.

Estimates token counts without calling the model, allocates per-node budgets
within context-window limits, compiles final prompts with token tracking, and
records actual usage to the node_attempts table for cost dashboards.

Design constraints:
- stdlib only (dataclasses, logging, json, sqlite3, re, math).
- No model API calls — estimation is heuristic (char-ratio based).
- All public interfaces are fully type-annotated.
- DB access follows the project convention: WAL + busy_timeout + foreign_keys.
"""

import json
import logging
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from backend.config import DB_PATH, DEFAULT_CONTEXT_WINDOW, MAX_CONTEXT_TOKENS

logger = logging.getLogger("localmind.core.token_budget")

# ---------------------------------------------------------------------------
# Model Context Windows
# ---------------------------------------------------------------------------

MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "qwen2.5-coder:32b": 32768,
    "qwen2.5-coder:14b": 32768,
    "qwen2.5-coder:7b": 32768,
    "qwen2.5-coder:70b": 32768,
    "gemma4:e4b": 8192,
    "gemma4:26b": 32768,
    "gemma4:31b": 32768,
    "gemma3:4b": 8192,
    "deepseek-r1:32b": 32768,
    "llama3.3:70b": 131072,
    "phi4-reasoning": 16384,
    "gemini-flash": 1_000_000,
}

# Chars-per-token ratio by model family.  English text averages ~4 chars/token
# for most transformer tokenizers; some CJK-heavy or code-heavy models differ.
_CHARS_PER_TOKEN: Dict[str, float] = {
    "qwen": 3.8,
    "gemma": 4.0,
    "deepseek": 3.6,
    "llama": 4.0,
    "phi": 4.0,
    "gemini": 4.0,
}
_DEFAULT_CHARS_PER_TOKEN = 4.0

# Safety margin as a fraction of the context window.
SAFETY_MARGIN_FRACTION = 0.10


def _chars_per_token_for(model_id: Optional[str]) -> float:
    """Return the heuristic chars-per-token ratio for a model family."""
    if not model_id:
        return _DEFAULT_CHARS_PER_TOKEN
    lower = model_id.lower()
    for prefix, ratio in _CHARS_PER_TOKEN.items():
        if prefix in lower:
            return ratio
    return _DEFAULT_CHARS_PER_TOKEN


def context_window_for(model_id: Optional[str]) -> int:
    """Return the context window size for a model, falling back to config default."""
    if model_id and model_id in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model_id]
    return DEFAULT_CONTEXT_WINDOW


# ---------------------------------------------------------------------------
# 1. TokenEstimator
# ---------------------------------------------------------------------------

class TokenEstimator:
    """
    Estimate token counts from raw text without calling the model.

    Uses a character-ratio heuristic (~4 chars/token for English) with
    per-model-family adjustments.  Accuracy is within ~15% for English prose
    and code — sufficient for budget allocation, not billing.
    """

    @staticmethod
    def estimate_tokens(text: str, model_id: Optional[str] = None) -> int:
        """
        Estimate the number of tokens in *text*.

        Parameters
        ----------
        text:
            The raw text to estimate.
        model_id:
            Optional model identifier for family-specific ratio adjustment.

        Returns
        -------
        int
            Estimated token count (always >= 0).
        """
        if not text:
            return 0
        ratio = _chars_per_token_for(model_id)
        return max(1, math.ceil(len(text) / ratio))

    @staticmethod
    def estimate_prompt_tokens(
        system_prompt: str = "",
        instructions: str = "",
        tool_schemas: str = "",
        input_data: str = "",
        model_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        Estimate token counts for each prompt layer, returning a breakdown.

        Parameters
        ----------
        system_prompt:
            The system-level prompt text.
        instructions:
            Node-specific instructions.
        tool_schemas:
            JSON-serialized tool schema text.
        input_data:
            Serialized input data (document text, extracted content, etc.).
        model_id:
            Optional model identifier for ratio adjustment.

        Returns
        -------
        dict
            Keys: ``system``, ``tools``, ``instructions``, ``input``, ``total``.
        """
        est = TokenEstimator.estimate_tokens
        s = est(system_prompt, model_id)
        t = est(tool_schemas, model_id)
        i = est(instructions, model_id)
        d = est(input_data, model_id)
        return {
            "system": s,
            "tools": t,
            "instructions": i,
            "input": d,
            "total": s + t + i + d,
        }


# ---------------------------------------------------------------------------
# 2. TokenBudgetAllocator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenBudget:
    """
    Per-node token budget.

    Attributes
    ----------
    system_tokens:
        Tokens reserved for the system prompt.
    tool_tokens:
        Tokens reserved for tool schemas.
    instruction_tokens:
        Tokens reserved for the node's instructions.
    input_tokens:
        Tokens available for input data (may be truncated to fit).
    output_tokens:
        Tokens reserved for the model's output generation.
    safety_margin:
        Tokens withheld as a buffer (typically 10% of context window).
    total:
        Sum of all allocated tokens (excluding safety margin).
    context_window:
        The model's total context window.
    budget_remaining:
        Tokens remaining after allocation and safety margin.
    """

    system_tokens: int
    tool_tokens: int
    instruction_tokens: int
    input_tokens: int
    output_tokens: int
    safety_margin: int
    total: int
    context_window: int
    budget_remaining: int


class TokenBudgetAllocator:
    """
    Allocate token budgets across prompt layers for a job node.

    Guarantees system prompt and tool schemas are never truncated.
    If the total exceeds the context window minus safety margin, input data
    is truncated and a warning is logged.
    """

    def __init__(self, estimator: Optional[TokenEstimator] = None):
        self._est = estimator or TokenEstimator()

    def allocate_budget(
        self,
        node: Dict[str, Any],
        context_window: Optional[int] = None,
        model_id: Optional[str] = None,
    ) -> TokenBudget:
        """
        Compute a token budget for a single pipeline node.

        Parameters
        ----------
        node:
            A dict (or Row-like) with keys that may include:
            ``instructions``, ``tools_allowed``, ``input_json``,
            ``expected_output``, ``id``.
        context_window:
            Override context window.  Defaults to ``context_window_for(model_id)``.
        model_id:
            Model being used for this node.

        Returns
        -------
        TokenBudget
            The computed budget allocation.
        """
        ctx = context_window or context_window_for(model_id)
        safety = math.ceil(ctx * SAFETY_MARGIN_FRACTION)
        usable = ctx - safety

        # Extract text from node fields.
        system_text = node.get("system_prompt", "")
        instructions_text = node.get("instructions", "") or ""
        tools_text = node.get("tools_allowed", "") or ""
        input_text = node.get("input_json", "") or ""
        expected_output = node.get("expected_output", "") or ""

        est = self._est.estimate_tokens

        system_tokens = est(system_text, model_id)
        tool_tokens = est(tools_text, model_id)
        instruction_tokens = est(instructions_text, model_id)
        input_tokens = est(input_text, model_id)

        # Reserve output tokens: heuristic — at least 25% of usable or the
        # estimated expected output size, whichever is larger, capped at 50%.
        output_estimate = est(expected_output, model_id) if expected_output else 0
        output_tokens = max(output_estimate, usable // 4)
        output_tokens = min(output_tokens, usable // 2)

        # Fixed costs: system + tools + instructions + output are non-negotiable.
        fixed = system_tokens + tool_tokens + instruction_tokens + output_tokens
        remaining_for_input = usable - fixed

        truncated = False
        if remaining_for_input < 0:
            # Even without input, the fixed costs exceed usable window.
            # This is a pathological case — log a critical warning.
            logger.critical(
                "Fixed prompt costs (%d) exceed usable context (%d) for node %s. "
                "Output quality will be severely degraded.",
                fixed,
                usable,
                node.get("id", "?"),
            )
            remaining_for_input = 0

        if input_tokens > remaining_for_input:
            logger.warning(
                "Input data (%d tokens) exceeds budget (%d tokens) for node %s — "
                "input will be truncated.",
                input_tokens,
                remaining_for_input,
                node.get("id", "?"),
            )
            input_tokens = max(0, remaining_for_input)
            truncated = True

        total = system_tokens + tool_tokens + instruction_tokens + input_tokens + output_tokens
        budget_remaining = usable - total

        budget = TokenBudget(
            system_tokens=system_tokens,
            tool_tokens=tool_tokens,
            instruction_tokens=instruction_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            safety_margin=safety,
            total=total,
            context_window=ctx,
            budget_remaining=max(0, budget_remaining),
        )

        logger.debug(
            "allocate_budget: node=%s ctx=%d safety=%d total=%d remaining=%d truncated=%s",
            node.get("id", "?"),
            ctx,
            safety,
            total,
            budget.budget_remaining,
            truncated,
        )
        return budget


# ---------------------------------------------------------------------------
# 3. PromptCompiler
# ---------------------------------------------------------------------------

@dataclass
class CompiledPrompt:
    """
    Assembled prompt ready for model invocation, with full token accounting.

    Attributes
    ----------
    prompt_text:
        The final prompt string sent to the model.
    token_counts:
        Breakdown by layer: ``{system, tools, instructions, input, total}``.
    budget_remaining:
        Tokens remaining before hitting the context window limit.
    truncated:
        Whether input data was truncated to fit.
    truncation_warning:
        Human-readable description of what was truncated, or None.
    """

    prompt_text: str
    token_counts: Dict[str, int]
    budget_remaining: int
    truncated: bool
    truncation_warning: Optional[str] = None


class PromptCompiler:
    """
    Assemble final prompts from node definitions with token tracking.

    Supports selective context via ``input_filter`` and compacts tool schemas
    by stripping examples and verbose descriptions.
    """

    def __init__(
        self,
        estimator: Optional[TokenEstimator] = None,
        allocator: Optional[TokenBudgetAllocator] = None,
    ):
        self._est = estimator or TokenEstimator()
        self._alloc = allocator or TokenBudgetAllocator(self._est)

    # -- Public API ----------------------------------------------------------

    def compile(
        self,
        node: Dict[str, Any],
        input_data: Dict[str, Any],
        model_id: Optional[str] = None,
        context_window: Optional[int] = None,
    ) -> CompiledPrompt:
        """
        Compile a complete prompt for a pipeline node.

        Parameters
        ----------
        node:
            Node definition dict.  Expected keys: ``instructions``,
            ``tools_allowed``, ``system_prompt``.  Optional: ``input_filter``.
        input_data:
            The full input data dict for this node.
        model_id:
            Model identifier (for token estimation and context window lookup).
        context_window:
            Override context window size.

        Returns
        -------
        CompiledPrompt
            Ready-to-send prompt with token accounting.
        """
        # 1. Apply input filter (selective context).
        filtered_input = self._apply_input_filter(
            input_data, node.get("input_filter")
        )

        # 2. Compact tool schemas.
        tools_text = self._compact_tools(node.get("tools_allowed", "") or "")

        # 3. Build a budget-aware node dict for allocation.
        input_text = json.dumps(filtered_input, default=str) if filtered_input else ""
        budget_node = {
            **node,
            "tools_allowed": tools_text,
            "input_json": input_text,
        }

        budget = self._alloc.allocate_budget(
            budget_node, context_window=context_window, model_id=model_id
        )

        # 4. Truncate input if needed.
        truncated = False
        truncation_warning = None
        if budget.input_tokens < self._est.estimate_tokens(input_text, model_id):
            input_text, truncation_warning = self._truncate_text(
                input_text, budget.input_tokens, model_id
            )
            truncated = True

        # 5. Assemble prompt text.
        sections: List[str] = []

        system_prompt = node.get("system_prompt", "")
        if system_prompt:
            sections.append(f"[SYSTEM]\n{system_prompt}")

        instructions = node.get("instructions", "") or ""
        if instructions:
            sections.append(f"[INSTRUCTIONS]\n{instructions}")

        if tools_text:
            sections.append(f"[TOOLS]\n{tools_text}")

        if input_text:
            sections.append(f"[INPUT]\n{input_text}")

        prompt_text = "\n\n".join(sections)

        token_counts = self._est.estimate_prompt_tokens(
            system_prompt=system_prompt,
            instructions=instructions,
            tool_schemas=tools_text,
            input_data=input_text,
            model_id=model_id,
        )

        return CompiledPrompt(
            prompt_text=prompt_text,
            token_counts=token_counts,
            budget_remaining=budget.budget_remaining,
            truncated=truncated,
            truncation_warning=truncation_warning,
        )

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _apply_input_filter(
        input_data: Dict[str, Any],
        input_filter: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Apply selective context: keep only specified portions of input_data.

        Supported filter shapes:
        - ``{"slides": [3, 5, 7]}`` — keep only items at those indices from
          the ``slides`` key.
        - ``{"sections": ["intro", "conclusion"]}`` — keep only named keys.
        - ``None`` — return input_data unchanged.

        Parameters
        ----------
        input_data:
            The full input dict.
        input_filter:
            Filter specification, or None for no filtering.

        Returns
        -------
        dict
            The filtered input data.
        """
        if not input_filter or not input_data:
            return input_data

        result: Dict[str, Any] = {}
        for key, selector in input_filter.items():
            source = input_data.get(key)
            if source is None:
                continue

            if isinstance(selector, list) and isinstance(source, list):
                # Index-based filter: selector is a list of int indices.
                if selector and isinstance(selector[0], int):
                    result[key] = [
                        source[i] for i in selector
                        if 0 <= i < len(source)
                    ]
                else:
                    # String-based filter on a list of dicts (by name/key).
                    result[key] = [
                        item for item in source
                        if any(
                            str(v) in selector
                            for v in (item.values() if isinstance(item, dict) else [item])
                        )
                    ]
            elif isinstance(selector, list) and isinstance(source, dict):
                # Keep only specified sub-keys.
                result[key] = {k: v for k, v in source.items() if k in selector}
            else:
                # Passthrough for unrecognised filter shapes.
                result[key] = source

        # Preserve any keys not mentioned in the filter.
        for key in input_data:
            if key not in input_filter:
                result[key] = input_data[key]

        return result

    @staticmethod
    def _compact_tools(tools_text: str) -> str:
        """
        Strip examples and long descriptions from JSON tool schemas.

        Reduces token footprint of tool definitions by removing ``"examples"``,
        ``"example"``, and truncating ``"description"`` fields longer than 120
        characters.

        Parameters
        ----------
        tools_text:
            JSON-encoded tool schema string, or a plain comma-separated list.

        Returns
        -------
        str
            Compacted schema text.
        """
        if not tools_text:
            return tools_text

        # If it does not look like JSON, return as-is (e.g. "web_search,read_file").
        stripped = tools_text.strip()
        if not stripped.startswith(("{", "[")):
            return tools_text

        try:
            schemas = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return tools_text

        def _compact_obj(obj: Any) -> Any:
            if isinstance(obj, dict):
                cleaned: Dict[str, Any] = {}
                for k, v in obj.items():
                    # Drop example keys.
                    if k in ("examples", "example"):
                        continue
                    # Truncate long descriptions.
                    if k == "description" and isinstance(v, str) and len(v) > 120:
                        cleaned[k] = v[:117] + "..."
                        continue
                    cleaned[k] = _compact_obj(v)
                return cleaned
            if isinstance(obj, list):
                return [_compact_obj(item) for item in obj]
            return obj

        compacted = _compact_obj(schemas)
        return json.dumps(compacted, separators=(",", ":"))

    def _truncate_text(
        self,
        text: str,
        max_tokens: int,
        model_id: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        Truncate *text* to fit within *max_tokens*.

        Attempts to break on a sentence or word boundary to avoid garbled output.

        Returns
        -------
        tuple[str, str]
            (truncated_text, warning_message)
        """
        if max_tokens <= 0:
            return "", "Input data was fully truncated (no budget available)."

        ratio = _chars_per_token_for(model_id)
        max_chars = int(max_tokens * ratio)

        if len(text) <= max_chars:
            return text, "Input was trimmed slightly to fit budget."

        truncated = text[:max_chars]

        # Try to break at the last sentence boundary.
        last_period = truncated.rfind(". ")
        last_newline = truncated.rfind("\n")
        break_at = max(last_period, last_newline)

        if break_at > max_chars * 0.5:
            truncated = truncated[: break_at + 1]

        original_tokens = self._est.estimate_tokens(text, model_id)
        kept_tokens = self._est.estimate_tokens(truncated, model_id)
        pct = round(100 * kept_tokens / original_tokens) if original_tokens else 100

        warning = (
            f"Input truncated from ~{original_tokens} to ~{kept_tokens} tokens "
            f"({pct}% retained) to fit context window."
        )
        logger.warning(warning)

        return truncated, warning


# ---------------------------------------------------------------------------
# 4. RetryContextOptimizer
# ---------------------------------------------------------------------------

class RetryContextOptimizer:
    """
    Produce slimmed-down prompts for retry attempts.

    Instead of resending the full document, only the error message, the
    relevant input section, and the original instructions are included.
    Estimated 40-70% token savings on retries.
    """

    def __init__(
        self,
        compiler: Optional[PromptCompiler] = None,
    ):
        self._compiler = compiler or PromptCompiler()

    def compile_retry_prompt(
        self,
        node: Dict[str, Any],
        error_message: str,
        failed_input_section: Optional[str] = None,
        original_instructions: Optional[str] = None,
        model_id: Optional[str] = None,
        context_window: Optional[int] = None,
    ) -> CompiledPrompt:
        """
        Build a token-optimised retry prompt.

        Parameters
        ----------
        node:
            The original node definition.
        error_message:
            The error from the failed attempt.
        failed_input_section:
            The specific portion of input that caused the failure, or None
            to include a minimal summary of the full input.
        original_instructions:
            Override instructions (e.g. with added formatting hints).
            Defaults to the node's own instructions.
        model_id:
            Model identifier for token estimation.
        context_window:
            Override context window.

        Returns
        -------
        CompiledPrompt
            A slimmed-down prompt suitable for the retry attempt.
        """
        instructions = original_instructions or node.get("instructions", "") or ""

        # Prepend error context to instructions.
        retry_instructions = (
            f"RETRY — The previous attempt failed with the following error:\n"
            f"{error_message}\n\n"
            f"Please fix the issue described above and try again.\n\n"
            f"Original instructions:\n{instructions}"
        )

        # Build minimal input.
        if failed_input_section:
            retry_input = {"failed_section": failed_input_section}
        else:
            # Include a truncated summary of the original input.
            raw_input = node.get("input_json", "") or ""
            summary = raw_input[:500] + ("..." if len(raw_input) > 500 else "")
            retry_input = {"input_summary": summary}

        retry_node = {
            **node,
            "instructions": retry_instructions,
        }

        compiled = self._compiler.compile(
            retry_node,
            input_data=retry_input,
            model_id=model_id,
            context_window=context_window,
        )

        logger.info(
            "compile_retry_prompt: node=%s original_input_est=%d retry_total=%d "
            "(estimated savings: %d tokens)",
            node.get("id", "?"),
            TokenEstimator.estimate_tokens(node.get("input_json", "") or "", model_id),
            compiled.token_counts["total"],
            max(
                0,
                TokenEstimator.estimate_tokens(node.get("input_json", "") or "", model_id)
                - compiled.token_counts["total"],
            ),
        )

        return compiled


# ---------------------------------------------------------------------------
# 5. TokenUsageTracker
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Open a WAL-mode connection to the LocalMind database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


class TokenUsageTracker:
    """
    Track actual token usage against the node_attempts table.

    Records per-attempt usage (tokens in/out, estimated cost) and provides
    aggregate queries by job, workspace, and time range.  Also supports
    budget alerting when a node exceeds 2x its budgeted tokens.
    """

    # -- Recording -----------------------------------------------------------

    @staticmethod
    def record_usage(
        job_id: str,
        node_id: str,
        attempt_id: str,
        model_id: str,
        tokens_in: int,
        tokens_out: int,
        estimated_cost_cents: float = 0.0,
    ) -> None:
        """
        Record actual token usage for a node attempt.

        Updates the existing ``node_attempts`` row identified by *attempt_id*.
        If no matching row exists, inserts a new one (defensive — the runner
        should have already created the row).

        Parameters
        ----------
        job_id:
            The parent job identifier (for logging/alerting context).
        node_id:
            The node identifier.
        attempt_id:
            The unique attempt identifier (PK of node_attempts).
        model_id:
            The model that was actually used.
        tokens_in:
            Actual prompt/input tokens reported by the model.
        tokens_out:
            Actual completion/output tokens reported by the model.
        estimated_cost_cents:
            Estimated cost in cents (for local models this is typically
            a compute-time proxy, not a billing charge).
        """
        conn = _get_conn()
        try:
            # Try UPDATE first — the row should already exist.
            cur = conn.execute(
                """
                UPDATE node_attempts
                SET tokens_in = ?, tokens_out = ?, cost_cents = ?, model_used = ?
                WHERE id = ?
                """,
                (tokens_in, tokens_out, estimated_cost_cents, model_id, attempt_id),
            )
            if cur.rowcount == 0:
                # Defensive insert — create the attempt row.
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO node_attempts
                        (id, node_id, attempt_number, status, started_at,
                         tokens_in, tokens_out, cost_cents, model_used)
                    VALUES (?, ?, 1, 'completed', ?, ?, ?, ?, ?)
                    """,
                    (attempt_id, node_id, now, tokens_in, tokens_out,
                     estimated_cost_cents, model_id),
                )
                logger.debug(
                    "record_usage: inserted new attempt row id=%s", attempt_id
                )
            conn.commit()
        except sqlite3.Error:
            logger.exception(
                "record_usage: failed for attempt=%s node=%s job=%s",
                attempt_id, node_id, job_id,
            )
        finally:
            conn.close()

        logger.debug(
            "record_usage: job=%s node=%s attempt=%s model=%s in=%d out=%d cost=%.2f",
            job_id, node_id, attempt_id, model_id, tokens_in, tokens_out,
            estimated_cost_cents,
        )

    # -- Queries -------------------------------------------------------------

    @staticmethod
    def get_job_usage(job_id: str) -> Dict[str, Any]:
        """
        Aggregate token usage for a complete job.

        Parameters
        ----------
        job_id:
            The job identifier.

        Returns
        -------
        dict
            Structure::

                {
                    "job_id": str,
                    "total_tokens_in": int,
                    "total_tokens_out": int,
                    "total_cost_cents": float,
                    "by_node": [
                        {
                            "node_id": str,
                            "tokens_in": int,
                            "tokens_out": int,
                            "cost_cents": float,
                            "attempts": int,
                        },
                        ...
                    ],
                    "by_attempt": [
                        {
                            "attempt_id": str,
                            "node_id": str,
                            "model_used": str,
                            "tokens_in": int,
                            "tokens_out": int,
                            "cost_cents": float,
                        },
                        ...
                    ],
                }
        """
        conn = _get_conn()
        try:
            # All attempts for nodes belonging to this job.
            rows = conn.execute(
                """
                SELECT na.id AS attempt_id, na.node_id, na.model_used,
                       COALESCE(na.tokens_in, 0)  AS tokens_in,
                       COALESCE(na.tokens_out, 0)  AS tokens_out,
                       COALESCE(na.cost_cents, 0.0) AS cost_cents
                FROM node_attempts na
                JOIN job_nodes jn ON jn.id = na.node_id
                WHERE jn.job_id = ?
                ORDER BY jn.sequence, na.attempt_number
                """,
                (job_id,),
            ).fetchall()

            by_attempt: List[Dict[str, Any]] = []
            by_node_map: Dict[str, Dict[str, Any]] = {}
            total_in = 0
            total_out = 0
            total_cost = 0.0

            for r in rows:
                by_attempt.append({
                    "attempt_id": r["attempt_id"],
                    "node_id": r["node_id"],
                    "model_used": r["model_used"],
                    "tokens_in": r["tokens_in"],
                    "tokens_out": r["tokens_out"],
                    "cost_cents": r["cost_cents"],
                })
                total_in += r["tokens_in"]
                total_out += r["tokens_out"]
                total_cost += r["cost_cents"]

                nid = r["node_id"]
                if nid not in by_node_map:
                    by_node_map[nid] = {
                        "node_id": nid,
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "cost_cents": 0.0,
                        "attempts": 0,
                    }
                by_node_map[nid]["tokens_in"] += r["tokens_in"]
                by_node_map[nid]["tokens_out"] += r["tokens_out"]
                by_node_map[nid]["cost_cents"] += r["cost_cents"]
                by_node_map[nid]["attempts"] += 1

            return {
                "job_id": job_id,
                "total_tokens_in": total_in,
                "total_tokens_out": total_out,
                "total_cost_cents": round(total_cost, 4),
                "by_node": list(by_node_map.values()),
                "by_attempt": by_attempt,
            }
        finally:
            conn.close()

    @staticmethod
    def get_workspace_usage(
        workspace_id: str,
        since: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate token usage for a workspace over a time range.

        Parameters
        ----------
        workspace_id:
            The workspace identifier.
        since:
            ISO-8601 timestamp.  If provided, only jobs created after this
            time are included.  Defaults to all time.

        Returns
        -------
        dict
            Structure::

                {
                    "workspace_id": str,
                    "since": str | None,
                    "total_tokens_in": int,
                    "total_tokens_out": int,
                    "total_cost_cents": float,
                    "job_count": int,
                    "daily": [{"date": str, "tokens_in": int, "tokens_out": int, "cost_cents": float}, ...],
                }
        """
        conn = _get_conn()
        try:
            params: List[Any] = [workspace_id]
            since_clause = ""
            if since:
                since_clause = "AND j.created_at >= ?"
                params.append(since)

            # Per-day aggregation.
            daily_rows = conn.execute(
                f"""
                SELECT DATE(j.created_at) AS day,
                       SUM(COALESCE(na.tokens_in, 0))   AS tokens_in,
                       SUM(COALESCE(na.tokens_out, 0))   AS tokens_out,
                       SUM(COALESCE(na.cost_cents, 0.0)) AS cost_cents
                FROM jobs j
                JOIN job_nodes jn ON jn.job_id = j.id
                JOIN node_attempts na ON na.node_id = jn.id
                WHERE j.workspace_id = ?
                {since_clause}
                GROUP BY day
                ORDER BY day
                """,
                params,
            ).fetchall()

            total_in = 0
            total_out = 0
            total_cost = 0.0
            daily: List[Dict[str, Any]] = []

            for r in daily_rows:
                daily.append({
                    "date": r["day"],
                    "tokens_in": r["tokens_in"],
                    "tokens_out": r["tokens_out"],
                    "cost_cents": round(r["cost_cents"], 4),
                })
                total_in += r["tokens_in"]
                total_out += r["tokens_out"]
                total_cost += r["cost_cents"]

            # Job count.
            count_params: List[Any] = [workspace_id]
            count_clause = ""
            if since:
                count_clause = "AND created_at >= ?"
                count_params.append(since)

            job_count = conn.execute(
                f"""
                SELECT COUNT(*) AS cnt FROM jobs
                WHERE workspace_id = ? {count_clause}
                """,
                count_params,
            ).fetchone()["cnt"]

            return {
                "workspace_id": workspace_id,
                "since": since,
                "total_tokens_in": total_in,
                "total_tokens_out": total_out,
                "total_cost_cents": round(total_cost, 4),
                "job_count": job_count,
                "daily": daily,
            }
        finally:
            conn.close()

    @staticmethod
    def check_budget_alert(
        node_id: str,
        tokens_used: int,
        budget: TokenBudget,
    ) -> bool:
        """
        Return True if a node has exceeded 2x its budgeted total tokens.

        When triggered, logs a warning that can feed into alerting.

        Parameters
        ----------
        node_id:
            The node being checked.
        tokens_used:
            Actual total tokens consumed (in + out).
        budget:
            The budget that was allocated.

        Returns
        -------
        bool
            True if tokens_used > 2 * budget.total.
        """
        threshold = 2 * budget.total
        if tokens_used > threshold:
            logger.warning(
                "BUDGET ALERT: node=%s used %d tokens, budget was %d (%.1fx over budget)",
                node_id,
                tokens_used,
                budget.total,
                tokens_used / budget.total if budget.total else float("inf"),
            )
            return True
        return False
