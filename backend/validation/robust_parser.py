"""
robust_parser.py — Cascading JSON parser for LLM outputs.

Addresses the AgentFixer paper's core finding: 38% of agentic task failures
are parsing-related. Implements a 5-layer fallback chain:

  1. Strict json.loads()
  2. Markdown fence extraction (```json ... ```)
  3. Regex JSON block extraction ({...} or [...])
  4. Fuzzy JSON repair (trailing commas, single quotes, unquoted keys)
  5. LLM self-repair (ask the model to fix its own malformed output)

Usage:
    from backend.validation.robust_parser import parse_json
    result = parse_json(llm_output_text)
    # result is a dict/list or None if all strategies failed
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("localmind.validation.parser")


@dataclass
class ParseResult:
    """Result of a cascading parse attempt."""
    data: Optional[Any] = None
    success: bool = False
    strategy_used: str = ""
    attempts: list = field(default_factory=list)
    raw_input: str = ""

    @property
    def failed(self) -> bool:
        return not self.success


class CascadingParser:
    """
    5-layer cascading JSON parser.

    Each layer is tried in order. The first successful parse wins.
    All attempts are logged for diagnostics.
    """

    def __init__(self, llm_repair_fn=None):
        """
        Args:
            llm_repair_fn: Optional async callable(text: str) -> str
                           that asks an LLM to fix malformed JSON.
                           If not provided, Layer 5 is skipped.
        """
        self._llm_repair_fn = llm_repair_fn

    def parse(self, text: str) -> ParseResult:
        """
        Attempt to parse JSON from LLM output using cascading strategies.

        Args:
            text: Raw LLM output that should contain JSON.

        Returns:
            ParseResult with data if successful, None data if all failed.
        """
        result = ParseResult(raw_input=text[:500])

        if not text or not text.strip():
            result.attempts.append(("empty_input", False, "Input is empty"))
            return result

        text = text.strip()

        # Layer 1: Strict parse
        data = self._try_strict(text)
        result.attempts.append(("strict", data is not None, ""))
        if data is not None:
            result.data = data
            result.success = True
            result.strategy_used = "strict"
            return result

        # Layer 2: Markdown fence extraction
        data = self._try_markdown_fence(text)
        result.attempts.append(("markdown_fence", data is not None, ""))
        if data is not None:
            result.data = data
            result.success = True
            result.strategy_used = "markdown_fence"
            return result

        # Layer 3: Regex block extraction
        data = self._try_regex_block(text)
        result.attempts.append(("regex_block", data is not None, ""))
        if data is not None:
            result.data = data
            result.success = True
            result.strategy_used = "regex_block"
            return result

        # Layer 4: Fuzzy repair
        data = self._try_fuzzy_repair(text)
        result.attempts.append(("fuzzy_repair", data is not None, ""))
        if data is not None:
            result.data = data
            result.success = True
            result.strategy_used = "fuzzy_repair"
            return result

        logger.warning(
            "All 4 synchronous parse strategies failed. "
            f"Input preview: {text[:120]}..."
        )
        return result

    async def parse_async(self, text: str) -> ParseResult:
        """
        Same as parse() but includes Layer 5 (LLM self-repair) if available.
        """
        result = self.parse(text)
        if result.success:
            return result

        # Layer 5: LLM self-repair
        if self._llm_repair_fn:
            try:
                repaired_text = await self._llm_repair_fn(text)
                if repaired_text:
                    data = self._try_strict(repaired_text)
                    if data is None:
                        data = self._try_regex_block(repaired_text)
                    result.attempts.append(("llm_repair", data is not None, ""))
                    if data is not None:
                        result.data = data
                        result.success = True
                        result.strategy_used = "llm_repair"
                        logger.info("Layer 5 LLM self-repair recovered JSON.")
                        return result
            except Exception as e:
                result.attempts.append(("llm_repair", False, str(e)))
                logger.warning(f"Layer 5 LLM repair failed: {e}")

        return result

    # ── Layer 1: Strict ─────────────────────────────────────────

    @staticmethod
    def _try_strict(text: str) -> Optional[Any]:
        """Direct json.loads() on the raw text."""
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

    # ── Layer 2: Markdown Fence ─────────────────────────────────

    @staticmethod
    def _try_markdown_fence(text: str) -> Optional[Any]:
        """Extract JSON from ```json ... ``` or ``` ... ``` fences."""
        # Try ```json first, then bare ```
        patterns = [
            r"```json\s*\n(.*?)```",
            r"```\s*\n(.*?)```",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                candidate = match.group(1).strip()
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    continue
        return None

    # ── Layer 3: Regex Block ────────────────────────────────────

    @staticmethod
    def _try_regex_block(text: str) -> Optional[Any]:
        """Find the outermost { ... } or [ ... ] block via bracket balancing."""
        for open_char, close_char in [('{', '}'), ('[', ']')]:
            start = text.find(open_char)
            if start == -1:
                continue

            depth = 0
            in_string = False
            escape = False
            end = -1

            for i in range(start, len(text)):
                ch = text[i]

                if escape:
                    escape = False
                    continue
                if ch == '\\':
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue

                if ch == open_char:
                    depth += 1
                elif ch == close_char:
                    depth -= 1
                    if depth == 0:
                        end = i
                        break

            if end > start:
                candidate = text[start:end + 1]
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    pass

        return None

    # ── Layer 4: Fuzzy Repair ───────────────────────────────────

    @staticmethod
    def _try_fuzzy_repair(text: str) -> Optional[Any]:
        """
        Attempt common JSON repair strategies for LLM mistakes:
          - Trailing commas before } or ]
          - Single quotes → double quotes
          - Unquoted keys
          - Missing quotes on string values
          - Python True/False/None → true/false/null
        """
        # First, try to extract a JSON-like block
        candidate = text
        for open_char in ('{', '['):
            idx = text.find(open_char)
            if idx != -1:
                candidate = text[idx:]
                break

        # Apply repairs in sequence
        repaired = candidate

        # Fix Python booleans/None
        repaired = re.sub(r'\bTrue\b', 'true', repaired)
        repaired = re.sub(r'\bFalse\b', 'false', repaired)
        repaired = re.sub(r'\bNone\b', 'null', repaired)

        # Fix trailing commas: ,} or ,]
        repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

        # Fix single quotes → double quotes (careful with apostrophes)
        # Only transform if it looks like JSON with single quotes
        if "'" in repaired and '"' not in repaired:
            repaired = repaired.replace("'", '"')

        # Fix unquoted keys: { key: "value" } → { "key": "value" }
        repaired = re.sub(
            r'(?<=[\{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
            r' "\1":',
            repaired
        )

        try:
            return json.loads(repaired)
        except (json.JSONDecodeError, ValueError):
            pass

        # Last resort: try to find the balanced block from the repaired text
        for open_char, close_char in [('{', '}'), ('[', ']')]:
            start = repaired.find(open_char)
            if start == -1:
                continue
            # Find the last matching close
            end = repaired.rfind(close_char)
            if end > start:
                try:
                    return json.loads(repaired[start:end + 1])
                except (json.JSONDecodeError, ValueError):
                    pass

        return None


# ── Module-level convenience function ────────────────────────────

_default_parser = CascadingParser()


def parse_json(text: str) -> Optional[Any]:
    """
    Parse JSON from LLM output using cascading strategies.

    Convenience wrapper around CascadingParser for the common synchronous case.
    Returns the parsed data or None if all strategies fail.
    """
    result = _default_parser.parse(text)
    if result.success:
        if result.strategy_used != "strict":
            logger.debug(
                f"JSON recovered via strategy: {result.strategy_used}"
            )
        return result.data
    return None


def parse_json_verbose(text: str) -> ParseResult:
    """
    Parse JSON with full diagnostic info.

    Returns a ParseResult with all attempt details for debugging.
    """
    return _default_parser.parse(text)
