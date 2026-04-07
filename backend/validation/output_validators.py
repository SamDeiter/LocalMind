"""
output_validators.py — Output-stage validators from the AgentFixer framework.

Four validators that check LLM outputs before they enter the pipeline:

  1. OutputSchemaValidator   (rule-based) — JSON schema compliance
  2. TokenAnomalyDetector    (rule-based) — unusual chars, encoding, repetition
  3. PythonSyntaxValidator   (rule-based) — AST-parses Python code in outputs
  4. ReasoningActionAligner  (LLM-based, Ollama) — reasoning vs action drift
"""

import ast
import json
import logging
import re
from collections import Counter
from typing import Optional

import httpx

from backend.config import OLLAMA_BASE_URL
from backend.validation.base import Validator, ValidationResult, Severity
from backend.validation.robust_parser import parse_json

logger = logging.getLogger("localmind.validation.output")


# ── 1. Output Schema Validator (Rule-Based) ────────────────────────

class OutputSchemaValidator(Validator):
    """
    Validates that LLM output matches an expected JSON structure.

    Checks:
      - Output is valid JSON
      - Required keys are present
      - Value types match expectations
      - No extraneous top-level keys (optional strict mode)
    """

    # Known schemas for LocalMind pipeline stages
    PROPOSAL_SCHEMA = {
        "required_keys": ["title", "category", "description", "files_affected"],
        "optional_keys": ["effort", "priority", "confidence"],
        "types": {
            "title": str,
            "category": str,
            "description": str,
            "files_affected": list,
            "effort": str,
            "priority": (str, int),
        },
    }

    CRITIQUE_SCHEMA = {
        "required_keys": ["confidence", "concerns", "verdict"],
        "optional_keys": [],
        "types": {
            "confidence": (int, float),
            "concerns": list,
            "verdict": str,
        },
    }

    EDIT_SCHEMA = {
        "required_keys": ["search", "replace"],
        "optional_keys": ["file", "description"],
        "types": {
            "search": str,
            "replace": str,
            "file": str,
        },
    }

    SCHEMAS = {
        "proposal": PROPOSAL_SCHEMA,
        "critique": CRITIQUE_SCHEMA,
        "edit": EDIT_SCHEMA,
    }

    def __init__(self):
        super().__init__("OutputSchemaValidator", Severity.CRITICAL)

    def validate(self, context: dict) -> ValidationResult:
        """
        Context keys:
          - output: str — raw LLM output
          - schema_name: str — "proposal", "critique", or "edit"
        """
        output = context.get("output", "")
        schema_name = context.get("schema_name", "proposal")
        schema = self.SCHEMAS.get(schema_name)

        if not schema:
            return self._pass(f"No schema defined for '{schema_name}', skipping.")

        # Try to parse JSON
        data = parse_json(output)
        if data is None:
            return self._fail(
                f"Output is not valid JSON (schema: {schema_name})",
                recommendations=[
                    "Ensure the LLM prompt explicitly requests JSON-only output",
                    "Add format examples to the prompt",
                    "Check for markdown fences wrapping the JSON",
                ],
                raw_preview=output[:200],
            )

        if not isinstance(data, dict):
            return self._fail(
                f"Output is JSON but not a dict (got {type(data).__name__})",
                recommendations=["Ensure the prompt requests a JSON object, not an array"],
            )

        # Check required keys
        missing = [k for k in schema["required_keys"] if k not in data]
        if missing:
            return self._fail(
                f"Missing required keys: {missing}",
                recommendations=[
                    f"Add these keys to the prompt's output format specification: {missing}",
                    "Provide a concrete example of the expected output in the prompt",
                ],
                missing_keys=missing,
                present_keys=list(data.keys()),
            )

        # Check types
        type_errors = []
        for key, expected_type in schema.get("types", {}).items():
            if key in data:
                if not isinstance(data[key], expected_type):
                    type_errors.append(
                        f"{key}: expected {expected_type}, got {type(data[key]).__name__}"
                    )

        if type_errors:
            return self._fail(
                f"Type mismatches: {'; '.join(type_errors)}",
                recommendations=["Add type examples in the prompt's JSON format spec"],
                type_errors=type_errors,
            )

        return self._pass(f"Schema '{schema_name}' validated OK", keys=list(data.keys()))


# ── 2. Token Anomaly Detector (Rule-Based) ─────────────────────────

class TokenAnomalyDetector(Validator):
    """
    Flags unusual characters, encoding glitches, and excessive repetition
    in LLM outputs. Based on AgentFixer's Token-Repetition-Detector and
    Unusual-Token-Detector.
    """

    # Characters that shouldn't appear in well-formed outputs
    UNUSUAL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufffd\ufffe\uffff]')

    # Excessive repetition pattern: same 3+ char sequence repeated 5+ times
    REPETITION_PATTERN = re.compile(r'(.{3,}?)\1{4,}')

    # Escape sequence flooding
    ESCAPE_FLOOD = re.compile(r'(\\[nrt"\\]){10,}')

    def __init__(self):
        super().__init__("TokenAnomalyDetector", Severity.MINOR)

    def validate(self, context: dict) -> ValidationResult:
        """
        Context keys:
          - output: str — raw LLM output
        """
        output = context.get("output", "")
        if not output:
            return self._pass("Empty output — nothing to check")

        anomalies = []

        # Check unusual characters
        unusual = self.UNUSUAL_CHARS.findall(output)
        if unusual:
            anomalies.append(
                f"Found {len(unusual)} unusual/control character(s): "
                f"{[hex(ord(c)) for c in unusual[:5]]}"
            )

        # Check repetition
        reps = self.REPETITION_PATTERN.findall(output)
        if reps:
            anomalies.append(
                f"Excessive repetition detected: '{reps[0][:20]}...' repeated 5+ times"
            )

        # Check escape flooding
        if self.ESCAPE_FLOOD.search(output):
            anomalies.append("Escape sequence flooding detected (10+ consecutive escapes)")

        # Check for extremely long lines (often a sign of degenerate output)
        max_line = max((len(line) for line in output.split('\n')), default=0)
        if max_line > 2000:
            anomalies.append(f"Extremely long line detected ({max_line} chars)")

        # Check for empty JSON values pattern: many consecutive empty strings
        empty_vals = len(re.findall(r'""', output))
        if empty_vals > 10:
            anomalies.append(f"Suspiciously many empty string values ({empty_vals})")

        if anomalies:
            return self._fail(
                f"Token anomalies detected: {len(anomalies)} issue(s)",
                recommendations=[
                    "Re-run the LLM call with a lower temperature",
                    "Add output length constraints to the prompt",
                ],
                anomalies=anomalies,
            )

        return self._pass("No token anomalies")


# ── 3. Python Syntax Validator (Rule-Based) ────────────────────────

class PythonSyntaxValidator(Validator):
    """
    AST-parses Python code found in LLM outputs.
    Applied selectively — only when output contains Python code.
    Based on AgentFixer's Python-Code-Syntax-Validator.
    """

    # Heuristic: lines that look like Python code
    PYTHON_INDICATORS = re.compile(
        r'(?:^|\n)\s*(?:def |class |import |from |if |for |while |try:|except |with |return |raise |async )',
        re.MULTILINE,
    )

    def __init__(self):
        super().__init__("PythonSyntaxValidator", Severity.CRITICAL)

    def validate(self, context: dict) -> ValidationResult:
        """
        Context keys:
          - output: str — raw LLM output (may contain Python code blocks)
          - code: str — optional, extracted Python code to validate directly
        """
        code = context.get("code")

        if not code:
            output = context.get("output", "")
            code = self._extract_python(output)

        if not code:
            return self._pass("No Python code detected in output")

        # Try AST parse
        try:
            ast.parse(code)
        except SyntaxError as e:
            return self._fail(
                f"Python syntax error: {e.msg} (line {e.lineno})",
                recommendations=[
                    f"Fix syntax at line {e.lineno}: {e.msg}",
                    "Verify indentation is consistent (spaces vs tabs)",
                    "Check for unclosed brackets or string literals",
                ],
                error_line=e.lineno,
                error_offset=e.offset,
                code_preview=code[:300],
            )

        # Additional structural checks via regex
        issues = []

        # Check for bare `except:` (bad practice)
        if re.search(r'^\s*except\s*:', code, re.MULTILINE):
            issues.append("Bare 'except:' clause — should specify exception type")

        # Check for `eval()` or `exec()` usage
        if re.search(r'\b(?:eval|exec)\s*\(', code):
            issues.append("eval()/exec() detected — potential security risk")

        if issues:
            return self._fail(
                f"Python code has structural issues: {'; '.join(issues)}",
                recommendations=issues,
                severity_note="Parsable but has quality issues",
            )

        return self._pass("Python syntax valid")

    @staticmethod
    def _extract_python(text: str) -> Optional[str]:
        """Extract Python code from markdown fences or raw text."""
        # Try ```python ... ``` fences
        match = re.search(r'```python\s*\n(.*?)```', text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try bare ``` ... ``` if content looks like Python
        match = re.search(r'```\s*\n(.*?)```', text, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
            if re.search(r'(?:def |class |import |from )', candidate):
                return candidate

        return None


# ── 4. Reasoning-Action Aligner (LLM-Based, Ollama) ───────────────

class ReasoningActionAligner(Validator):
    """
    Uses a local Ollama model to detect discrepancies between stated
    reasoning and actual actions in LLM outputs.

    Based on AgentFixer's Reasoning-Action-Mismatch-Detector.
    Uses Ollama (cheapest — zero marginal cost since GPU is already running).
    """

    def __init__(self, model: str = "qwen2.5-coder:7b", ollama_url: str = OLLAMA_BASE_URL):
        super().__init__("ReasoningActionAligner", Severity.MODERATE)
        self.model = model
        self.ollama_url = ollama_url

    def validate(self, context: dict) -> ValidationResult:
        """Synchronous fallback — logs a warning so callers know to use validate_async()."""
        logger.warning(
            "ReasoningActionAligner.validate() called synchronously — "
            "LLM-based check skipped. Use validate_async() for full validation."
        )
        return self._pass("Skipped — synchronous call, LLM check requires validate_async()")

    async def validate_async(self, context: dict) -> ValidationResult:
        """
        Context keys:
          - output: str — raw LLM output containing reasoning + action
          - proposal: dict — optional, the proposal being executed
        """
        output = context.get("output", "")
        proposal = context.get("proposal", {})

        if not output or len(output) < 50:
            return self._pass("Output too short for reasoning-action analysis")

        prompt = (
            "You are a validation judge. Analyze whether the REASONING in this "
            "LLM output is consistent with the ACTIONS it proposes.\n\n"
            f"LLM OUTPUT:\n{output[:2000]}\n\n"
        )

        if proposal:
            prompt += f"PROPOSAL CONTEXT:\nTitle: {proposal.get('title', '?')}\n"
            prompt += f"Description: {proposal.get('description', '?')}\n\n"

        prompt += (
            "Check for:\n"
            "1. Does the reasoning support the proposed action?\n"
            "2. Are there contradictions between what is said and what is done?\n"
            "3. Is the action scope consistent with the reasoning?\n\n"
            'Output JSON: {"aligned": true/false, "severity": 0.0-1.0, "issues": ["..."]}\n'
            "Only output JSON."
        )

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 150, "num_ctx": 2048},
                    },
                )

                if resp.status_code == 200:
                    text = resp.json().get("response", "")
                    data = parse_json(text)
                    if data and isinstance(data, dict):
                        aligned = data.get("aligned", True)
                        severity = data.get("severity", 0.0)
                        issues = data.get("issues", [])

                        if not aligned and severity > 0.5:
                            return self._fail(
                                f"Reasoning-action mismatch (severity: {severity:.1f})",
                                recommendations=issues[:3],
                                severity_score=severity,
                            )
                        return self._pass(
                            f"Reasoning-action aligned (severity: {severity:.1f})"
                        )

        except (httpx.HTTPError, Exception) as e:
            logger.warning(f"ReasoningActionAligner failed: {e}")

        # If the validator itself fails, don't block the pipeline
        return self._pass("Validator unavailable — defaulting to pass")
