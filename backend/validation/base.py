"""
base.py — Validator base class and result types for the validation framework.

All validators inherit from Validator and return ValidationResult instances.
ValidationReport aggregates results from multiple validators for a single
pipeline execution.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("localmind.validation.base")


class Severity(str, Enum):
    """Validation issue severity levels from the AgentFixer paper."""
    CRITICAL = "critical"   # Syntax errors, schema violations — blocks execution
    MODERATE = "moderate"   # Reasoning mismatches, format violations
    MINOR = "minor"         # Instruction adherence, coverage gaps, token anomalies


@dataclass
class ValidationResult:
    """Result of a single validator check."""
    validator_name: str
    passed: bool
    severity: Severity = Severity.MINOR
    message: str = ""
    recommendations: list = field(default_factory=list)
    details: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "validator": self.validator_name,
            "passed": self.passed,
            "severity": self.severity.value,
            "message": self.message,
            "recommendations": self.recommendations,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class ValidationReport:
    """Aggregated results from all validators for a single pipeline execution."""
    results: list = field(default_factory=list)
    context_id: str = ""           # proposal ID or execution trace ID
    pipeline_stage: str = ""       # "reflection", "critique", "execution", etc.
    timestamp: float = field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        """True if no critical failures."""
        return not any(
            r.severity == Severity.CRITICAL and not r.passed
            for r in self.results
        )

    @property
    def critical_failures(self) -> list:
        return [r for r in self.results if r.severity == Severity.CRITICAL and not r.passed]

    @property
    def moderate_failures(self) -> list:
        return [r for r in self.results if r.severity == Severity.MODERATE and not r.passed]

    @property
    def all_failures(self) -> list:
        return [r for r in self.results if not r.passed]

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 1.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def add(self, result: ValidationResult):
        self.results.append(result)

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        critical = len(self.critical_failures)
        moderate = len(self.moderate_failures)
        return (
            f"Validation: {passed}/{total} passed | "
            f"{critical} critical | {moderate} moderate | "
            f"Stage: {self.pipeline_stage}"
        )

    def to_dict(self) -> dict:
        return {
            "context_id": self.context_id,
            "pipeline_stage": self.pipeline_stage,
            "timestamp": self.timestamp,
            "pass_rate": self.pass_rate,
            "total_checks": len(self.results),
            "critical_failures": len(self.critical_failures),
            "moderate_failures": len(self.moderate_failures),
            "results": [r.to_dict() for r in self.results],
        }


class Validator(ABC):
    """
    Base class for all validators.

    Subclasses implement validate() which receives a context dict and
    returns a ValidationResult. Context keys vary by validator type:

    For Output validators:
      - "output": str — raw LLM output text
      - "system_prompt": str — the system prompt used
      - "expected_schema": dict — optional JSON schema

    For Prompt validators:
      - "system_prompt": str — the prompt to analyze

    For Cross-Stage validators:
      - "input": dict — the input passed to the LLM
      - "output": str — the LLM's response
      - "system_prompt": str — the system prompt
      - "proposal": dict — the proposal being validated
    """

    def __init__(self, name: str, severity: Severity = Severity.MODERATE):
        self.name = name
        self.severity = severity

    @abstractmethod
    def validate(self, context: dict) -> ValidationResult:
        """
        Run this validator against the provided context.

        Args:
            context: Dict containing the artifacts to validate.
                     Keys depend on validator type.

        Returns:
            ValidationResult with pass/fail status and diagnostics.
        """
        ...

    def _pass(self, message: str = "OK", **details) -> ValidationResult:
        """Convenience: create a passing result."""
        return ValidationResult(
            validator_name=self.name,
            passed=True,
            severity=self.severity,
            message=message,
            details=details,
        )

    def _fail(self, message: str, recommendations: list = None, **details) -> ValidationResult:
        """Convenience: create a failing result."""
        return ValidationResult(
            validator_name=self.name,
            passed=False,
            severity=self.severity,
            message=message,
            recommendations=recommendations or [],
            details=details,
        )
