"""
LocalMind Inference — Advanced model selection, sampling, and adapter management.

This package provides three key capabilities:

  :class:`ModelSelector`
      Picks the right model + LoRA for each job node based on task type,
      complexity, and priority.

  :class:`BestOfNSampler`
      Generates N completions in parallel and picks the best using
      LLM-as-judge or heuristic scoring.  Implements test-time compute
      scaling (best-of-N, with future PRM integration).

  :class:`LoRAManager`
      Registry and lifecycle management for LoRA adapters.  Adapters are
      persisted in SQLite and hot-swapped on top of base models (<10ms
      overhead).

Architecture:
  ModelSelector uses LoRAManager to find task-specific adapters.
  BestOfNSampler is called by the executor when best_of_n > 1.
  All three are independently testable with clean interfaces.
"""

from backend.inference.best_of_n import BestOfNSampler, SampleResult, ProcessRewardModel
from backend.inference.lora_manager import LoRAAdapter, LoRAManager
from backend.inference.model_selector import ModelSelection, ModelSelector

__all__ = [
    "BestOfNSampler",
    "LoRAAdapter",
    "LoRAManager",
    "ModelSelection",
    "ModelSelector",
    "ProcessRewardModel",
    "SampleResult",
]
