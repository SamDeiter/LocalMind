"""
Seed eval case definitions — built-in test cases for the LocalMind eval harness.

Covers: tool use (read_file, write_file), code generation, JSON output
formatting, research/search tasks, and multi-step reasoning.

Each case has: id, title, prompt, expected_output, tools_allowed,
max_tokens, timeout_sec, and scoring mode (pass_fail or partial).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("localmind.eval.cases")


# ── Seed Case Definitions ───────────────────────────────────────────────────

_SEED_CASES: list[dict[str, Any]] = [
    # ── Tool use: read_file ─────────────────────────────────────────────────
    {
        "id": "eval_seed_read_file_01",
        "title": "Read file and summarise contents",
        "prompt": (
            "Read the file at /tmp/eval_input.txt and return a JSON object "
            'with keys "filename" and "line_count" indicating the file name '
            "and the number of lines."
        ),
        "expected_output": '"line_count"',
        "expected_type": "substring",
        "tools_allowed": ["read_file"],
        "max_tokens": 512,
        "timeout_sec": 60,
        "scoring": "pass_fail",
    },
    # ── Tool use: write_file ────────────────────────────────────────────────
    {
        "id": "eval_seed_write_file_01",
        "title": "Write a greeting file",
        "prompt": (
            "Write a file at /tmp/eval_output.txt containing the text "
            '"Hello, LocalMind!" on the first line. '
            "Return a JSON object with key \"status\" set to \"written\"."
        ),
        "expected_output": '"status":\\s*"written"',
        "expected_type": "regex",
        "tools_allowed": ["write_file"],
        "max_tokens": 512,
        "timeout_sec": 60,
        "scoring": "pass_fail",
    },
    # ── Code generation: Python function ────────────────────────────────────
    {
        "id": "eval_seed_codegen_python_01",
        "title": "Generate a Python fibonacci function",
        "prompt": (
            "Write a Python function called `fibonacci(n)` that returns the "
            "n-th Fibonacci number (0-indexed, so fibonacci(0)=0, fibonacci(1)=1). "
            "Return ONLY the function definition as plain code, no markdown."
        ),
        "expected_output": "def fibonacci",
        "expected_type": "substring",
        "tools_allowed": [],
        "max_tokens": 1024,
        "timeout_sec": 90,
        "scoring": "pass_fail",
    },
    # ── Code generation: JavaScript ─────────────────────────────────────────
    {
        "id": "eval_seed_codegen_js_01",
        "title": "Generate a JavaScript array deduplication function",
        "prompt": (
            "Write a JavaScript function called `deduplicate(arr)` that takes "
            "an array and returns a new array with duplicate values removed, "
            "preserving insertion order. Return ONLY the function, no markdown."
        ),
        "expected_output": "function deduplicate",
        "expected_type": "substring",
        "tools_allowed": [],
        "max_tokens": 1024,
        "timeout_sec": 90,
        "scoring": "pass_fail",
    },
    # ── JSON output formatting ──────────────────────────────────────────────
    {
        "id": "eval_seed_json_format_01",
        "title": "Return structured JSON with specific keys",
        "prompt": (
            "Return a JSON object with exactly these keys: "
            '"name" (string), "version" (integer), "features" (array of strings). '
            "Fill in reasonable values for a project called LocalMind."
        ),
        "expected_output": '{"name":.*"version":.*"features":',
        "expected_type": "regex",
        "tools_allowed": [],
        "max_tokens": 512,
        "timeout_sec": 60,
        "scoring": "partial",
    },
    # ── JSON output: nested structure ───────────────────────────────────────
    {
        "id": "eval_seed_json_nested_01",
        "title": "Return nested JSON with address object",
        "prompt": (
            "Return a JSON object representing a person with keys: "
            '"name" (string), "age" (integer), and "address" (object with '
            '"city" and "country" keys). Use realistic sample data.'
        ),
        "expected_output": '"address"',
        "expected_type": "substring",
        "tools_allowed": [],
        "max_tokens": 512,
        "timeout_sec": 60,
        "scoring": "partial",
    },
    # ── Research / search ───────────────────────────────────────────────────
    {
        "id": "eval_seed_research_01",
        "title": "Search and summarise a topic",
        "prompt": (
            "Search for information about SQLite WAL mode and provide a "
            "concise 2-3 sentence summary of what it is and why it is useful."
        ),
        "expected_output": "WAL",
        "expected_type": "substring",
        "tools_allowed": ["web_search"],
        "max_tokens": 1024,
        "timeout_sec": 120,
        "scoring": "pass_fail",
    },
    # ── Research: factual recall ────────────────────────────────────────────
    {
        "id": "eval_seed_research_02",
        "title": "Answer a factual question about Python",
        "prompt": (
            "What built-in Python module provides the `SequenceMatcher` class "
            "for computing string similarity? Answer with ONLY the module name."
        ),
        "expected_output": "difflib",
        "expected_type": "substring",
        "tools_allowed": [],
        "max_tokens": 128,
        "timeout_sec": 30,
        "scoring": "pass_fail",
    },
    # ── Multi-step reasoning ────────────────────────────────────────────────
    {
        "id": "eval_seed_multistep_01",
        "title": "Multi-step math word problem",
        "prompt": (
            "A store sells notebooks for $3 each and pens for $1.50 each. "
            "If a customer buys 4 notebooks and 6 pens, and pays with a $50 bill, "
            "how much change do they receive? "
            'Return a JSON object with keys "total_cost" and "change".'
        ),
        "expected_output": '"change":\\s*29',
        "expected_type": "regex",
        "tools_allowed": [],
        "max_tokens": 512,
        "timeout_sec": 60,
        "scoring": "pass_fail",
    },
    # ── Multi-step: read, transform, write ──────────────────────────────────
    {
        "id": "eval_seed_multistep_02",
        "title": "Read file, count words, write summary",
        "prompt": (
            "Read the file at /tmp/eval_input.txt, count the total number of "
            "words across all lines, then write a summary JSON to "
            "/tmp/eval_summary.json with keys \"source\", \"word_count\". "
            "Return the summary JSON in your response."
        ),
        "expected_output": "word_count",
        "expected_type": "substring",
        "tools_allowed": ["read_file", "write_file"],
        "max_tokens": 1024,
        "timeout_sec": 120,
        "scoring": "partial",
    },
]


# ── Public API ──────────────────────────────────────────────────────────────


def get_seed_cases() -> list[dict[str, Any]]:
    """Return the list of built-in seed eval case definitions.

    Returns:
        A list of dicts, each containing id, title, prompt, expected_output,
        expected_type, tools_allowed, max_tokens, timeout_sec, and scoring.
    """
    return list(_SEED_CASES)


def load_cases_to_db(db_path: str) -> int:
    """Insert seed eval cases into the eval_cases table if not already present.

    Uses the ``eval_cases`` table created by ``backend/core/schema.py``.
    Each seed case is mapped to workspace_id ``'default'`` so it is
    available out of the box in single-user deployments.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        The number of new cases inserted (0 if all already existed).
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")

    inserted = 0
    now = datetime.now(timezone.utc).isoformat()

    try:
        for case in _SEED_CASES:
            # Check if this seed case already exists by id.
            row = conn.execute(
                "SELECT id FROM eval_cases WHERE id = ?", (case["id"],)
            ).fetchone()
            if row is not None:
                continue

            # Build input_json with the full case metadata needed by the harness.
            input_json = json.dumps({
                "prompt": case["prompt"],
                "tools_allowed": case["tools_allowed"],
                "max_tokens": case["max_tokens"],
                "timeout_sec": case["timeout_sec"],
            })

            expected_output_json = json.dumps({
                "pattern": case["expected_output"],
                "type": case.get("expected_type", "substring"),
                "scoring": case.get("scoring", "pass_fail"),
            })

            conn.execute(
                """
                INSERT INTO eval_cases
                    (id, workspace_id, name, description, input_json,
                     expected_output_json, artifact_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case["id"],
                    "default",
                    case["title"],
                    f"Seed eval case: {case['title']}",
                    input_json,
                    expected_output_json,
                    None,
                    now,
                ),
            )
            inserted += 1

        conn.commit()
    finally:
        conn.close()

    if inserted:
        logger.info("Loaded %d seed eval cases into database", inserted)
    else:
        logger.debug("All seed eval cases already present in database")

    return inserted
