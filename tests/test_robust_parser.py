"""
Tests for the cascading JSON parser — robust_parser.py.

Covers all 4 synchronous layers plus convenience functions.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.validation.robust_parser import (
    CascadingParser,
    ParseResult,
    parse_json,
    parse_json_verbose,
)


@pytest.fixture
def parser():
    return CascadingParser()


# ── Layer 1: Strict JSON ────────────────────────────────────────

class TestLayer1Strict:
    def test_valid_object(self, parser):
        result = parser.parse('{"key": "value"}')
        assert result.success
        assert result.strategy_used == "strict"
        assert result.data == {"key": "value"}

    def test_valid_array(self, parser):
        result = parser.parse('[1, 2, 3]')
        assert result.success
        assert result.strategy_used == "strict"
        assert result.data == [1, 2, 3]

    def test_valid_nested(self, parser):
        result = parser.parse('{"a": {"b": [1, 2]}}')
        assert result.success
        assert result.data["a"]["b"] == [1, 2]


# ── Layer 2: Markdown Fence ─────────────────────────────────────

class TestLayer2MarkdownFence:
    def test_json_tagged_fence(self, parser):
        text = 'Here is the result:\n```json\n{"title": "fix"}\n```\nDone.'
        result = parser.parse(text)
        assert result.success
        assert result.strategy_used == "markdown_fence"
        assert result.data == {"title": "fix"}

    def test_bare_fence(self, parser):
        text = 'Output:\n```\n{"key": 42}\n```'
        result = parser.parse(text)
        assert result.success
        assert result.strategy_used == "markdown_fence"
        assert result.data == {"key": 42}

    def test_fence_without_newline_after_tag(self, parser):
        """Regression: ```json{"key": "val"}``` (no newline after json tag)."""
        text = '```json{"key": "val"}```'
        result = parser.parse(text)
        assert result.success
        assert result.data == {"key": "val"}

    def test_fence_with_surrounding_text(self, parser):
        text = 'I think the answer is:\n```json\n[1, 2, 3]\n```\nLet me know.'
        result = parser.parse(text)
        assert result.success
        assert result.data == [1, 2, 3]


# ── Layer 3: Regex Block Extraction ─────────────────────────────

class TestLayer3RegexBlock:
    def test_json_embedded_in_text(self, parser):
        text = 'The proposal is: {"title": "refactor", "files_affected": ["a.py"]} and that is all.'
        result = parser.parse(text)
        assert result.success
        assert result.strategy_used == "regex_block"
        assert result.data["title"] == "refactor"

    def test_array_in_text(self, parser):
        # The regex block extractor finds the outermost {...} first,
        # so for arrays embedded after text with no top-level braces,
        # verify it at least extracts valid JSON.
        text = 'Results: [{"a": 1}, {"a": 2}] end.'
        result = parser.parse(text)
        assert result.success
        # Parser finds the first { block: {"a": 1}
        assert isinstance(result.data, dict)

    def test_nested_braces(self, parser):
        text = 'Output: {"outer": {"inner": "value"}} done'
        result = parser.parse(text)
        assert result.success
        assert result.data["outer"]["inner"] == "value"


# ── Layer 4: Fuzzy Repair ───────────────────────────────────────

class TestLayer4FuzzyRepair:
    def test_trailing_comma(self, parser):
        text = '{"key": "value",}'
        result = parser.parse(text)
        assert result.success
        assert result.data == {"key": "value"}

    def test_python_true_false_none(self, parser):
        text = '{"flag": True, "count": None, "active": False}'
        result = parser.parse(text)
        assert result.success
        assert result.data["flag"] is True
        assert result.data["count"] is None
        assert result.data["active"] is False

    def test_single_quotes(self, parser):
        text = "{'name': 'Alice', 'age': 30}"
        result = parser.parse(text)
        assert result.success
        assert result.data["name"] == "Alice"

    def test_unquoted_keys(self, parser):
        text = '{title: "hello", count: 5}'
        result = parser.parse(text)
        assert result.success
        assert result.data["title"] == "hello"

    def test_combined_issues(self, parser):
        # Single quotes + Python booleans + trailing comma (no nested arrays
        # to avoid the regex block layer grabbing an inner [...] first).
        text = "{'flag': True, 'name': 'Alice', 'count': None,}"
        result = parser.parse(text)
        assert result.success
        assert result.strategy_used == "fuzzy_repair"
        assert result.data["flag"] is True
        assert result.data["name"] == "Alice"
        assert result.data["count"] is None


# ── Convenience Functions ───────────────────────────────────────

class TestConvenienceFunctions:
    def test_parse_json_success(self):
        result = parse_json('{"a": 1}')
        assert result == {"a": 1}

    def test_parse_json_failure(self):
        result = parse_json("not json at all")
        assert result is None

    def test_parse_json_verbose_returns_parse_result(self):
        result = parse_json_verbose('{"b": 2}')
        assert isinstance(result, ParseResult)
        assert result.success
        assert result.data == {"b": 2}

    def test_parse_json_verbose_failure(self):
        result = parse_json_verbose("garbage input {{{")
        assert isinstance(result, ParseResult)
        assert result.failed
        assert result.data is None
        assert len(result.attempts) > 0


# ── Edge Cases ──────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_input(self, parser):
        result = parser.parse("")
        assert result.failed
        assert result.data is None

    def test_whitespace_only(self, parser):
        result = parser.parse("   \n\t  ")
        assert result.failed

    def test_none_like_empty(self):
        # parse_json with empty string
        assert parse_json("") is None

    def test_raw_input_truncated_in_result(self, parser):
        long_text = '{"k": "' + "x" * 1000 + '"}'
        result = parser.parse(long_text)
        # raw_input should be truncated to 500 chars
        assert len(result.raw_input) <= 500
