"""
backend.validation — AgentFixer-inspired validation framework for LocalMind.

Provides systematic failure detection, classification, and repair across
the autonomy engine pipeline. Based on arXiv:2603.29848 (AgentFixer).

Components:
  - robust_parser: Cascading JSON parser with 5 fallback strategies
  - base: Validator base class and ValidationResult dataclass
  - output_validators: Schema, token, syntax, and reasoning validators
  - prompt_validators: Prompt consistency and edge-case validators
  - cross_stage_validators: Cross-pipeline integrity validators
"""

from .robust_parser import parse_json, CascadingParser
from .base import Validator, ValidationResult, ValidationReport

__all__ = [
    "parse_json",
    "CascadingParser",
    "Validator",
    "ValidationResult",
    "ValidationReport",
]
