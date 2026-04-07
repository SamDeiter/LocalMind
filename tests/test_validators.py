"""
Tests for output validators — OutputSchemaValidator, TokenAnomalyDetector,
PythonSyntaxValidator, ReasoningActionAligner.

No network calls; ReasoningActionAligner sync path is tested via logger spy.
"""
import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.validation.output_validators import (
    OutputSchemaValidator,
    TokenAnomalyDetector,
    PythonSyntaxValidator,
    ReasoningActionAligner,
)


# ── OutputSchemaValidator ───────────────────────────────────────

class TestOutputSchemaValidator:
    def setup_method(self):
        self.v = OutputSchemaValidator()

    def test_valid_proposal(self):
        data = {
            "title": "Add logging",
            "category": "enhancement",
            "description": "Add structured logging to all endpoints.",
            "files_affected": ["app.py", "config.py"],
        }
        result = self.v.validate({
            "output": json.dumps(data),
            "schema_name": "proposal",
        })
        assert result.passed

    def test_missing_required_keys(self):
        data = {"title": "Incomplete"}
        result = self.v.validate({
            "output": json.dumps(data),
            "schema_name": "proposal",
        })
        assert not result.passed
        assert "Missing required keys" in result.message

    def test_type_mismatch(self):
        data = {
            "title": "Ok",
            "category": "bug",
            "description": "Fix it",
            "files_affected": "not-a-list",  # should be list
        }
        result = self.v.validate({
            "output": json.dumps(data),
            "schema_name": "proposal",
        })
        assert not result.passed
        assert "Type mismatch" in result.message

    def test_non_json_input(self):
        result = self.v.validate({
            "output": "This is plain text, not JSON.",
            "schema_name": "proposal",
        })
        assert not result.passed
        assert "not valid JSON" in result.message

    def test_unknown_schema_passes(self):
        result = self.v.validate({
            "output": '{"anything": true}',
            "schema_name": "unknown_schema",
        })
        assert result.passed  # No schema defined, so it skips

    def test_json_array_instead_of_object(self):
        result = self.v.validate({
            "output": "[1, 2, 3]",
            "schema_name": "proposal",
        })
        assert not result.passed
        assert "not a dict" in result.message


# ── TokenAnomalyDetector ───────────────────────────────────────

class TestTokenAnomalyDetector:
    def setup_method(self):
        self.v = TokenAnomalyDetector()

    def test_clean_output_passes(self):
        result = self.v.validate({"output": "This is a perfectly normal response."})
        assert result.passed

    def test_control_characters_detected(self):
        text = "Normal text\x00with\x07control\x1fchars"
        result = self.v.validate({"output": text})
        assert not result.passed
        assert any("control character" in a for a in result.details.get("anomalies", []))

    def test_excessive_repetition_detected(self):
        text = "abc" * 100  # "abcabcabc..." repeated 100 times
        result = self.v.validate({"output": text})
        assert not result.passed
        assert any("repetition" in a.lower() for a in result.details.get("anomalies", []))

    def test_empty_output_passes(self):
        result = self.v.validate({"output": ""})
        assert result.passed

    def test_long_line_detected(self):
        text = "x" * 3000
        result = self.v.validate({"output": text})
        assert not result.passed
        assert any("long line" in a.lower() for a in result.details.get("anomalies", []))


# ── PythonSyntaxValidator ──────────────────────────────────────

class TestPythonSyntaxValidator:
    def setup_method(self):
        self.v = PythonSyntaxValidator()

    def test_valid_python_in_fence(self):
        output = '```python\ndef hello():\n    return "world"\n```'
        result = self.v.validate({"output": output})
        assert result.passed
        assert "syntax valid" in result.message.lower()

    def test_syntax_error_detected(self):
        output = '```python\ndef broken(\n    return\n```'
        result = self.v.validate({"output": output})
        assert not result.passed
        assert "syntax error" in result.message.lower()

    def test_no_python_code_passes(self):
        result = self.v.validate({"output": "Just a plain text response."})
        assert result.passed
        assert "No Python code" in result.message

    def test_direct_code_param(self):
        result = self.v.validate({"code": "x = 1 + 2"})
        assert result.passed

    def test_eval_exec_flagged(self):
        result = self.v.validate({"code": "result = eval('1+1')"})
        assert not result.passed
        assert "eval" in result.message.lower()

    def test_bare_except_flagged(self):
        code = "try:\n    pass\nexcept:\n    pass"
        result = self.v.validate({"code": code})
        assert not result.passed
        assert "except" in result.message.lower()


# ── ReasoningActionAligner (sync stub) ──────────────────────────

class TestReasoningActionAligner:
    def test_sync_validate_logs_warning(self):
        aligner = ReasoningActionAligner()
        with patch("backend.validation.output_validators.logger") as mock_logger:
            result = aligner.validate({"output": "some reasoning and action"})
            assert result.passed
            mock_logger.warning.assert_called_once()
            call_msg = mock_logger.warning.call_args[0][0]
            assert "synchronous" in call_msg.lower()
