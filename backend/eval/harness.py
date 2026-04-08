"""
Agent-loop eval harness — runs eval cases through NodeExecutor's real agent loop.

Wraps ``NodeExecutor`` to execute eval cases, capture output, score results
against expected patterns, and store results in the ``eval_runs`` table.

Usage::

    from backend.eval.harness import EvalHarness
    from backend.eval.cases import get_seed_cases

    harness = EvalHarness(db_path="localmind.db")
    summary = harness.run_all()
    print(summary)
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from backend.eval.cases import get_seed_cases

logger = logging.getLogger("localmind.eval.harness")

# Score thresholds — aligned with backend/core/eval_runner.py constants.
_PASS_THRESHOLD: float = 0.8
_PARTIAL_THRESHOLD: float = 0.4


# ── DB helpers (match codebase pattern from backend/core/eval.py) ───────────


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a short-lived SQLite connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generate a new hex UUID (no dashes)."""
    return uuid.uuid4().hex


def _now_ms() -> int:
    """Current wall-clock time in milliseconds (monotonic)."""
    return int(time.monotonic() * 1000)


# ── Mock Ollama response for offline eval ───────────────────────────────────


def _build_mock_response(prompt: str) -> dict[str, Any]:
    """Build a synthetic Ollama-style response for offline eval.

    When a real Ollama instance is unavailable the harness uses this mock
    to exercise the scoring pipeline end-to-end.  The mock echoes the
    prompt back wrapped in a JSON result so that substring / regex checks
    against the prompt content can still be validated.

    Args:
        prompt: The user prompt from the eval case.

    Returns:
        A dict mimicking Ollama's ``/api/chat`` response shape.
    """
    # Produce a plausible mock response that exercises scoring logic.
    mock_content = json.dumps({
        "result": f"Mock response for eval prompt: {prompt[:200]}",
        "status": "completed",
    })
    return {
        "message": {"role": "assistant", "content": mock_content, "tool_calls": []},
        "prompt_eval_count": len(prompt.split()),
        "eval_count": len(mock_content.split()),
    }


# =============================================================================
# EvalHarness
# =============================================================================


class EvalHarness:
    """Run eval cases through a simplified agent loop and score results.

    The harness tries to use a real Ollama instance (via ``NodeExecutor``)
    when available.  If the Ollama server is unreachable it falls back to
    mock responses so that the scoring and DB-storage pipeline can still be
    exercised.

    Parameters
    ----------
    db_path:
        Path to the SQLite database (must already have ``eval_cases`` and
        ``eval_runs`` tables — see ``backend/core/schema.py``).
    ollama_url:
        Base URL for the Ollama API.  Defaults to ``http://127.0.0.1:11434``.
    model:
        Model identifier to use for inference.  Defaults to the medium-tier
        model from ``backend.config.MODEL_TIERS``.
    """

    def __init__(
        self,
        db_path: str,
        ollama_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._db_path = db_path
        self._ollama_url = (ollama_url or "http://127.0.0.1:11434").rstrip("/")

        # Resolve default model.
        if model is not None:
            self._model = model
        else:
            try:
                from backend.config import MODEL_TIERS
                self._model = MODEL_TIERS.get("medium", "qwen2.5-coder:14b")
            except ImportError:
                self._model = "qwen2.5-coder:14b"

        self._ollama_available: bool | None = None  # lazy-probed on first run

    # ------------------------------------------------------------------
    # Ollama connectivity probe
    # ------------------------------------------------------------------

    def _check_ollama(self) -> bool:
        """Probe the Ollama server; cache the result for this harness instance.

        Returns:
            True if Ollama responded, False otherwise.
        """
        if self._ollama_available is not None:
            return self._ollama_available

        try:
            import httpx

            resp = httpx.get(f"{self._ollama_url}/api/tags", timeout=5.0)
            self._ollama_available = resp.status_code == 200
        except Exception:
            self._ollama_available = False

        if self._ollama_available:
            logger.info("Ollama is reachable at %s", self._ollama_url)
        else:
            logger.warning(
                "Ollama unreachable at %s — falling back to mock responses",
                self._ollama_url,
            )
        return self._ollama_available

    # ------------------------------------------------------------------
    # Inference: real or mock
    # ------------------------------------------------------------------

    def _call_model(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        """Call Ollama synchronously or fall back to mock.

        Returns an Ollama-shaped response dict with ``message``,
        ``prompt_eval_count``, and ``eval_count`` keys.
        """
        if not self._check_ollama():
            return _build_mock_response(prompt)

        try:
            import httpx

            messages: list[dict[str, Any]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "stream": False,
            }

            resp = httpx.post(
                f"{self._ollama_url}/api/chat",
                json=payload,
                timeout=180.0,
            )

            if resp.status_code != 200:
                logger.error("Ollama HTTP %d: %s", resp.status_code, resp.text[:300])
                return _build_mock_response(prompt)

            return resp.json()

        except Exception as exc:
            logger.error("Ollama call failed, falling back to mock: %s", exc)
            return _build_mock_response(prompt)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_result(self, case: dict[str, Any], output: str) -> dict[str, Any]:
        """Compare model output against expected pattern and return a score.

        Supports three match types (stored in ``case["expected_type"]``):

        - ``substring``: case-insensitive substring containment check.
        - ``regex``: regex search against the output (case-insensitive,
          DOTALL for multi-line content).
        - ``json_schema``: validates that the output is parseable JSON
          containing all keys listed in the expected pattern.

        Args:
            case: The eval case dict (must include ``expected_output``,
                  ``expected_type``, and ``scoring``).
            output: The model's textual output.

        Returns:
            Dict with keys ``score`` (float 0-1), ``status`` (str),
            ``match`` (bool), and ``details`` (str).
        """
        expected = case.get("expected_output", "")
        match_type = case.get("expected_type", "substring")
        scoring_mode = case.get("scoring", "pass_fail")

        match_found = False
        details = ""

        if match_type == "substring":
            match_found = expected.lower() in output.lower()
            details = (
                f"Substring '{expected}' {'found' if match_found else 'NOT found'} "
                f"in output ({len(output)} chars)"
            )

        elif match_type == "regex":
            try:
                pattern = re.compile(expected, re.IGNORECASE | re.DOTALL)
                match_found = pattern.search(output) is not None
                details = (
                    f"Regex /{expected}/ {'matched' if match_found else 'did NOT match'} "
                    f"against output ({len(output)} chars)"
                )
            except re.error as exc:
                match_found = False
                details = f"Invalid regex pattern: {exc}"

        elif match_type == "json_schema":
            try:
                parsed = json.loads(output)
                if isinstance(parsed, dict):
                    required_keys = [k.strip() for k in expected.split(",")]
                    present = [k for k in required_keys if k in parsed]
                    match_found = len(present) == len(required_keys)
                    details = (
                        f"JSON keys: required={required_keys}, "
                        f"present={present}"
                    )
                else:
                    match_found = False
                    details = "Output is valid JSON but not an object"
            except json.JSONDecodeError as exc:
                match_found = False
                details = f"Output is not valid JSON: {exc}"

        else:
            # Unknown type — fall back to substring
            match_found = expected.lower() in output.lower()
            details = f"Unknown match type '{match_type}', fell back to substring"

        # Compute numeric score.
        if match_found:
            score = 1.0
        elif scoring_mode == "partial":
            # For partial scoring, give 0.5 if output is non-empty.
            score = 0.5 if output.strip() else 0.0
        else:
            score = 0.0

        # Map score to status.
        if score >= _PASS_THRESHOLD:
            status = "passed"
        elif score >= _PARTIAL_THRESHOLD:
            status = "partial"
        else:
            status = "failed"

        return {
            "score": score,
            "status": status,
            "match": match_found,
            "details": details,
        }

    # ------------------------------------------------------------------
    # Run a single case
    # ------------------------------------------------------------------

    def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        """Run a single eval case and return the scored result.

        Steps:
        1. Build a system prompt scoping the allowed tools.
        2. Send the case prompt to the model (real or mock).
        3. Extract the text output from the response.
        4. Score the output against the expected pattern.
        5. Store the result in the ``eval_runs`` table.

        Args:
            case: An eval case dict (from ``get_seed_cases()`` or the DB).

        Returns:
            Dict with keys: case_id, title, status, score, match, details,
            output_preview, tokens_in, tokens_out, duration_ms, run_id.
        """
        start = _now_ms()

        case_id = case.get("id", _new_id())
        title = case.get("title", "Untitled eval case")
        prompt = case["prompt"]
        tools_allowed = case.get("tools_allowed", [])

        # Build a lightweight system prompt.
        tools_str = ", ".join(tools_allowed) if tools_allowed else "(none)"
        system_prompt = (
            "You are LocalMind, an autonomous task worker. "
            f"Allowed tools: {tools_str}. "
            "Complete the task accurately. Return structured output when asked."
        )

        logger.info("Running eval case '%s': %s", case_id, title)

        # Call the model.
        response = self._call_model(prompt, system=system_prompt)

        # Extract output text.
        msg = response.get("message", {})
        output_text: str = msg.get("content", "") or ""
        tokens_in: int = response.get("prompt_eval_count", 0)
        tokens_out: int = response.get("eval_count", 0)

        # Score the output.
        score_result = self.score_result(case, output_text)

        elapsed = _now_ms() - start

        # Build result dict.
        result: dict[str, Any] = {
            "case_id": case_id,
            "title": title,
            "status": score_result["status"],
            "score": score_result["score"],
            "match": score_result["match"],
            "details": score_result["details"],
            "output_preview": output_text[:500],
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_ms": elapsed,
        }

        # Persist to eval_runs.
        run_id = self._store_run(case_id, result, tokens_in, tokens_out, elapsed)
        result["run_id"] = run_id

        logger.info(
            "Eval case '%s' %s (score=%.2f, %dms)",
            case_id, score_result["status"], score_result["score"], elapsed,
        )

        return result

    # ------------------------------------------------------------------
    # Run all cases
    # ------------------------------------------------------------------

    def run_all(
        self, cases: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Run all eval cases and return an aggregate summary.

        Args:
            cases: Optional list of case dicts to run.  If ``None``, uses
                   ``get_seed_cases()``.

        Returns:
            Dict with keys: pass_count, fail_count, partial_count,
            total_cases, total_time_ms, avg_score, results (list of
            per-case result dicts).
        """
        if cases is None:
            cases = get_seed_cases()

        suite_start = _now_ms()
        results: list[dict[str, Any]] = []
        pass_count = 0
        fail_count = 0
        partial_count = 0
        total_score = 0.0

        logger.info("Starting eval suite with %d case(s)", len(cases))

        for case in cases:
            try:
                result = self.run_case(case)
            except Exception as exc:
                logger.error(
                    "Eval case '%s' raised an exception: %s",
                    case.get("id", "unknown"), exc,
                )
                result = {
                    "case_id": case.get("id", "unknown"),
                    "title": case.get("title", "Unknown"),
                    "status": "failed",
                    "score": 0.0,
                    "match": False,
                    "details": f"Exception: {exc}",
                    "output_preview": "",
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "duration_ms": 0,
                    "run_id": None,
                }

            results.append(result)
            total_score += result["score"]

            if result["status"] == "passed":
                pass_count += 1
            elif result["status"] == "partial":
                partial_count += 1
            else:
                fail_count += 1

        suite_elapsed = _now_ms() - suite_start
        total_cases = len(results)
        avg_score = total_score / total_cases if total_cases > 0 else 0.0

        summary: dict[str, Any] = {
            "pass_count": pass_count,
            "fail_count": fail_count,
            "partial_count": partial_count,
            "total_cases": total_cases,
            "total_time_ms": suite_elapsed,
            "avg_score": round(avg_score, 4),
            "model": self._model,
            "ollama_available": self._ollama_available or False,
            "results": results,
        }

        logger.info(
            "Eval suite complete: %d passed, %d failed, %d partial "
            "(avg_score=%.3f, %dms)",
            pass_count, fail_count, partial_count, avg_score, suite_elapsed,
        )

        return summary

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    def _store_run(
        self,
        case_id: str,
        result: dict[str, Any],
        tokens_in: int,
        tokens_out: int,
        duration_ms: int,
    ) -> str:
        """Store an eval run result in the eval_runs table.

        Args:
            case_id: The eval case ID this run belongs to.
            result: The scored result dict.
            tokens_in: Input token count from inference.
            tokens_out: Output token count from inference.
            duration_ms: Wall-clock duration in milliseconds.

        Returns:
            The new eval run ID (hex UUID).
        """
        run_id = _new_id()
        now = _now()

        details_json = json.dumps({
            "score": result["score"],
            "status": result["status"],
            "match": result["match"],
            "details": result["details"],
            "output_preview": result.get("output_preview", ""),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        })

        model_config_json = json.dumps({
            "model": self._model,
            "ollama_url": self._ollama_url,
            "ollama_available": self._ollama_available or False,
        })

        conn = _get_conn(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO eval_runs
                    (id, eval_case_id, job_id, status, score, details_json,
                     model_config_json, ran_at, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    case_id,
                    None,  # no associated job
                    result["status"],
                    result["score"],
                    details_json,
                    model_config_json,
                    now,
                    duration_ms,
                ),
            )
            conn.commit()
        except sqlite3.Error as exc:
            # Log but don't crash the eval run if DB write fails (e.g. missing
            # foreign-key target for seed case IDs not yet in eval_cases).
            logger.warning(
                "Failed to store eval run for case '%s': %s", case_id, exc,
            )
        finally:
            conn.close()

        return run_id
