"""
model_selector.py — Intelligent model + LoRA selection for job nodes.
=====================================================================

Picks the right model, optional LoRA adapter, and best-of-N sample
count for each node in the job pipeline.  Replaces the simple
priority-based ``_pick_model_for_node()`` in the scheduler with a
richer signal set: task type keywords, node tools, job priority,
and estimated complexity.

Selection heuristics (from PLAN.md Model Strategy):
  - Research keywords  -> research model tier
  - Code tools         -> coder model tier
  - Document tools     -> document model tier
  - High-priority jobs -> larger models
  - best_of_n: easy=1, medium=4, hard=8-16
  - LoRA adapters matched by task type when available

Usage::

    selector = ModelSelector(
        model_tiers=config.MODEL_TIERS,
        lora_manager=lora_mgr,   # optional
    )
    selection = selector.select_model(node_dict, job_dict)
    # selection.model_id, selection.lora_id, selection.best_of_n
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("localmind.inference.model_selector")


# ---------------------------------------------------------------------------
# Keyword sets for task-type classification
# ---------------------------------------------------------------------------

_RESEARCH_KEYWORDS: frozenset[str] = frozenset({
    "research", "investigate", "analyze", "study", "survey", "literature",
    "review paper", "find sources", "summarize findings", "report",
    "deep dive", "explore", "synthesis", "academic",
})

_CODE_KEYWORDS: frozenset[str] = frozenset({
    "code", "implement", "program", "develop", "debug", "refactor",
    "function", "class", "module", "api", "endpoint", "script",
    "compile", "build", "test", "unittest", "pytest",
})

_DOCUMENT_KEYWORDS: frozenset[str] = frozenset({
    "document", "slide", "presentation", "powerpoint", "pptx",
    "spreadsheet", "excel", "xlsx", "word", "docx", "pdf",
    "report", "memo", "draft", "write up", "template",
})

# Tool names that indicate code-related work
_CODE_TOOLS: frozenset[str] = frozenset({
    "run_code", "git_status", "git_diff", "git_log", "git_commit",
    "project_context", "read_file", "write_file",
})

# Tool names that indicate document-related work
_DOCUMENT_TOOLS: frozenset[str] = frozenset({
    "pptx", "pptx_create", "pptx_edit", "pptx_read",
    "excel_create", "excel_edit", "excel_read",
    "docx_create", "docx_edit", "docx_read",
    "pdf_read", "pdf_create",
})

# Tool names that indicate research/web work
_RESEARCH_TOOLS: frozenset[str] = frozenset({
    "web_search", "browser",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelSelection:
    """The result of model selection for a single node."""

    model_id: str          # e.g. "qwen3:8b"
    lora_id: str | None    # e.g. "research-lora-v2"
    vram_mb: int           # estimated VRAM needed
    best_of_n: int         # how many samples (1 = no sampling)
    reasoning: str         # why this model was chosen

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "lora_id": self.lora_id,
            "vram_mb": self.vram_mb,
            "best_of_n": self.best_of_n,
            "reasoning": self.reasoning,
        }


# ---------------------------------------------------------------------------
# VRAM estimation (mirrors scheduler._VRAM_ESTIMATES)
# ---------------------------------------------------------------------------

_VRAM_ESTIMATES: dict[str, int] = {
    "gemma3:4b": 2800,
    "gemma4:e4b": 2800,
    "gemma4:26b": 6000,
    "gemma4:31b": 7000,
    "qwen2.5-coder:7b": 4500,
    "qwen2.5-coder:14b": 8500,
    "qwen2.5-coder:32b": 18000,
    "qwen2.5-coder:70b": 40000,
    "qwen3:8b": 5000,
    "llama3.3:70b": 40000,
    "phi4-reasoning": 8000,
}

_LORA_OVERHEAD_MB: int = 256


def _estimate_vram(model_id: str, lora_id: str | None = None) -> int:
    """Estimate VRAM in MB for a model + optional LoRA."""
    base = _VRAM_ESTIMATES.get(model_id, 4000)
    return base + (_LORA_OVERHEAD_MB if lora_id else 0)


# ---------------------------------------------------------------------------
# ModelSelector
# ---------------------------------------------------------------------------

class ModelSelector:
    """Select the best model + LoRA + sampling strategy for a job node.

    Parameters
    ----------
    model_tiers:
        Dict mapping tier names (light/medium/heavy/ultra) to model IDs.
        Typically ``config.MODEL_TIERS``.
    lora_manager:
        Optional :class:`LoRAManager` instance.  When provided, the
        selector checks for task-specific adapters and includes them
        in the selection.
    """

    def __init__(
        self,
        model_tiers: dict[str, str],
        lora_manager: Any | None = None,
    ) -> None:
        self._tiers = model_tiers
        self._lora_manager = lora_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_model(
        self,
        node: dict[str, Any],
        job: dict[str, Any],
    ) -> ModelSelection:
        """Pick the best model, LoRA, and sampling count for a node.

        Args:
            node: Dict with at least ``title``, ``instructions``,
                  ``tools_allowed``.  Typically ``node.to_dict()`` or
                  the scheduler's candidate dict.
            job:  Dict with at least ``priority``.  Typically
                  ``job.to_dict()`` or the scheduler's job metadata.

        Returns:
            A :class:`ModelSelection` with model_id, lora_id,
            vram_mb, best_of_n, and reasoning.
        """
        task_type = self._classify_task(node)
        priority = int(job.get("priority", 0))
        complexity = self._estimate_complexity(node, job)

        # Pick base model tier
        model_id, tier_name = self._pick_tier(task_type, priority, complexity)

        # Check for LoRA adapter
        lora_id = self._find_lora(task_type, model_id)
        lora_adapter = None
        if lora_id and self._lora_manager:
            lora_adapter = self._lora_manager.get_adapter_by_id(lora_id)

        # Determine best-of-N sample count
        best_of_n = self._pick_best_of_n(complexity)

        # Estimate VRAM
        lora_overhead = lora_adapter.vram_overhead_mb if lora_adapter else 0
        vram_mb = _estimate_vram(model_id) + (lora_overhead if lora_id else 0)

        # Build reasoning string
        reasons: list[str] = []
        reasons.append(f"task_type={task_type}")
        reasons.append(f"tier={tier_name}")
        reasons.append(f"priority={priority}")
        reasons.append(f"complexity={complexity}")
        if lora_id:
            reasons.append(f"lora={lora_id}")
        reasoning = "; ".join(reasons)

        selection = ModelSelection(
            model_id=model_id,
            lora_id=lora_id,
            vram_mb=vram_mb,
            best_of_n=best_of_n,
            reasoning=reasoning,
        )

        logger.info(
            "Model selection for node '%s': model=%s, lora=%s, "
            "best_of_n=%d, vram=%dMB (%s)",
            node.get("title", node.get("node_id", "?")),
            selection.model_id,
            selection.lora_id or "none",
            selection.best_of_n,
            selection.vram_mb,
            selection.reasoning,
        )

        return selection

    # ------------------------------------------------------------------
    # Task classification
    # ------------------------------------------------------------------

    def _classify_task(self, node: dict[str, Any]) -> str:
        """Classify the node's task type from title, instructions, and tools.

        Returns one of: "research", "code", "document", "general".
        """
        title = (node.get("title") or "").lower()
        instructions = (node.get("instructions") or "").lower()
        text = f"{title} {instructions}"

        tools = node.get("tools_allowed") or []
        tool_set = frozenset(t.lower() for t in tools) if tools else frozenset()

        # Score each category
        scores: dict[str, float] = {
            "research": 0.0,
            "code": 0.0,
            "document": 0.0,
        }

        # Keyword matching (partial match for multi-word keywords)
        for kw in _RESEARCH_KEYWORDS:
            if kw in text:
                scores["research"] += 1.0

        for kw in _CODE_KEYWORDS:
            if kw in text:
                scores["code"] += 1.0

        for kw in _DOCUMENT_KEYWORDS:
            if kw in text:
                scores["document"] += 1.0

        # Tool matching (stronger signal — 2x weight)
        for tool in tool_set:
            if tool in _RESEARCH_TOOLS:
                scores["research"] += 2.0
            if tool in _CODE_TOOLS:
                scores["code"] += 2.0
            if tool in _DOCUMENT_TOOLS:
                scores["document"] += 2.0

        # Find the winner
        max_score = max(scores.values())
        if max_score == 0:
            return "general"

        # Return the highest-scoring category
        for category, score in scores.items():
            if score == max_score:
                return category

        return "general"

    # ------------------------------------------------------------------
    # Model tier selection
    # ------------------------------------------------------------------

    def _pick_tier(
        self,
        task_type: str,
        priority: int,
        complexity: int,
    ) -> tuple[str, str]:
        """Select a model tier based on task type, priority, and complexity.

        Returns (model_id, tier_name).
        """
        # Priority override — very high priority always gets heavy+
        if priority >= 8:
            tier = "heavy"
        elif priority >= 5 or complexity >= 7:
            tier = "medium"
        elif complexity >= 4:
            # Medium complexity: task-type-aware selection
            if task_type == "code":
                tier = "medium"  # Code benefits from larger models
            elif task_type == "research":
                tier = "medium"
            else:
                tier = "light"
        else:
            tier = "light"

        # Task-type-specific overrides for hard tasks
        if complexity >= 7 and task_type == "code":
            tier = "heavy"

        model_id = self._tiers.get(tier, self._tiers.get("light", "gemma4:e4b"))
        return model_id, tier

    # ------------------------------------------------------------------
    # Complexity estimation
    # ------------------------------------------------------------------

    def _estimate_complexity(
        self,
        node: dict[str, Any],
        job: dict[str, Any],
    ) -> int:
        """Estimate task complexity on a 1-10 scale.

        Factors:
          - Number of tools allowed (more tools = more complex)
          - Instruction length (longer = more complex)
          - Presence of output schema (structured output = harder)
          - Priority as a proxy for importance/difficulty
          - Presence of depends_on (dependencies = workflow complexity)

        Returns an integer from 1 to 10.
        """
        score = 1  # baseline

        # Tool count
        tools = node.get("tools_allowed") or []
        if len(tools) >= 5:
            score += 3
        elif len(tools) >= 3:
            score += 2
        elif len(tools) >= 1:
            score += 1

        # Instruction length
        instructions = node.get("instructions") or ""
        if len(instructions) > 1000:
            score += 2
        elif len(instructions) > 300:
            score += 1

        # Output schema complexity
        if node.get("output_schema_json"):
            score += 1

        # Dependencies suggest multi-step reasoning
        depends_on = node.get("depends_on") or []
        if len(depends_on) >= 2:
            score += 1

        # Priority as complexity proxy (high-priority tasks tend to be harder)
        priority = int(job.get("priority", 0))
        if priority >= 7:
            score += 1

        return min(score, 10)

    # ------------------------------------------------------------------
    # LoRA lookup
    # ------------------------------------------------------------------

    def _find_lora(self, task_type: str, model_id: str) -> str | None:
        """Check the LoRA manager for a matching adapter.

        Returns the adapter_id if found, None otherwise.
        """
        if not self._lora_manager:
            return None

        try:
            adapter = self._lora_manager.get_adapter(
                task_type=task_type,
                base_model=model_id,
            )
            if adapter:
                return adapter.adapter_id
        except Exception as exc:
            logger.warning(
                "LoRA lookup failed for task_type=%s, model=%s: %s",
                task_type, model_id, exc,
            )

        return None

    # ------------------------------------------------------------------
    # Best-of-N count
    # ------------------------------------------------------------------

    def _pick_best_of_n(self, complexity: int) -> int:
        """Determine the best-of-N sample count based on complexity.

        From PLAN.md:
          - easy (1-3):   single pass (N=1)
          - medium (4-6): best-of-N=4
          - hard (7+):    best-of-N=8-16

        The exact count within the hard range scales with complexity.
        """
        if complexity <= 3:
            return 1
        if complexity <= 6:
            return 4
        if complexity <= 8:
            return 8
        return 16
