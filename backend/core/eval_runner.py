"""
Continuous Evaluation & Model Quality Monitoring — LocalMind enterprise task worker.

Provides three core capabilities:

1. **EvalRunner** — offline eval harness that runs before model/prompt/LoRA
   changes are promoted.  Runs eval cases against model configs, collects
   scored results, and compares baseline vs candidate configurations to
   detect regressions.

2. **QualityMonitor** — production quality sampling.  Randomly samples
   completed jobs, scores them via LLM-judge or deterministic checks, and
   tracks quality trends over time with alerting on degradation.

3. **ChangeGate** — gates model/prompt/template changes behind eval.
   Returns a GateResult with pass/fail, regression count, and a
   recommendation (auto_promote | human_review | reject).

Tables (created by backend/core/schema.py):
    eval_cases, eval_runs, template_versions, prompt_versions, model_registry

Scoring helpers:
    score_text_similarity  — SequenceMatcher-based text similarity [0, 1]
    score_json_match       — structural JSON comparison [0, 1]
    score_file_exists      — 1.0 if file exists, 0.0 otherwise
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

import httpx

from backend.config import DB_PATH, OLLAMA_BASE_URL, MODEL_TIERS

logger = logging.getLogger("localmind.core.eval_runner")

# ── Ollama call settings ─────────────────────────────────────────────────────

_OLLAMA_EVAL_TIMEOUT: float = 180.0  # seconds per eval inference call

# Threshold below which a quality drop triggers an alert (fraction, e.g. 0.10 = 10%)
_QUALITY_DROP_ALERT_THRESHOLD: float = 0.10

# Pass threshold: an eval case is considered "passed" at or above this score
_PASS_THRESHOLD: float = 0.8

# Partial threshold: below pass but above this is "partial"
_PARTIAL_THRESHOLD: float = 0.4


# ── DB helpers ───────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Open a short-lived SQLite connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Return a new hex UUID (32 chars, no hyphens)."""
    return uuid.uuid4().hex


def _now_ms() -> int:
    """Current wall-clock time in milliseconds (monotonic)."""
    return int(time.monotonic() * 1000)


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    """Convert a sqlite3.Row to a plain dict, or return None."""
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    """Convert a list of sqlite3.Row objects to a list of dicts."""
    return [dict(r) for r in rows]


# ── Scoring helpers ──────────────────────────────────────────────────────────


def score_text_similarity(expected: str, actual: str) -> float:
    """Compute text similarity between expected and actual strings.

    Uses ``difflib.SequenceMatcher`` for a ratio in [0.0, 1.0].
    Both inputs are stripped and lowercased before comparison to reduce
    sensitivity to trivial whitespace/case differences.

    Args:
        expected: The reference / gold-standard text.
        actual: The text produced by the model under evaluation.

    Returns:
        A float in [0.0, 1.0] where 1.0 means identical (after normalisation).
    """
    if not expected and not actual:
        return 1.0
    if not expected or not actual:
        return 0.0
    norm_expected = expected.strip().lower()
    norm_actual = actual.strip().lower()
    return SequenceMatcher(None, norm_expected, norm_actual).ratio()


def score_json_match(expected_json: Any, actual_json: Any) -> float:
    """Structural JSON comparison returning a score in [0.0, 1.0].

    Scoring rules:
    - If both are dicts: score = (matched_keys / total_unique_keys), where
      each key's value is recursively scored.
    - If both are lists: score = average of pairwise element scores (by index),
      penalised for length mismatch.
    - If both are scalars: 1.0 if equal, 0.0 otherwise.
    - If types differ: 0.0.

    Args:
        expected_json: The reference JSON structure (already parsed).
        actual_json: The candidate JSON structure (already parsed).

    Returns:
        A float in [0.0, 1.0].
    """
    if expected_json is None and actual_json is None:
        return 1.0
    if expected_json is None or actual_json is None:
        return 0.0

    if type(expected_json) != type(actual_json):
        # Type mismatch -- attempt string comparison as last resort
        return score_text_similarity(str(expected_json), str(actual_json)) * 0.5

    if isinstance(expected_json, dict):
        if not expected_json and not actual_json:
            return 1.0
        all_keys = set(expected_json.keys()) | set(actual_json.keys())
        if not all_keys:
            return 1.0
        total_score = 0.0
        for key in all_keys:
            if key in expected_json and key in actual_json:
                total_score += score_json_match(expected_json[key], actual_json[key])
            # Missing key in either side contributes 0.0
        return total_score / len(all_keys)

    if isinstance(expected_json, list):
        if not expected_json and not actual_json:
            return 1.0
        max_len = max(len(expected_json), len(actual_json))
        if max_len == 0:
            return 1.0
        total_score = 0.0
        for i in range(max_len):
            if i < len(expected_json) and i < len(actual_json):
                total_score += score_json_match(expected_json[i], actual_json[i])
            # Out-of-range index contributes 0.0
        return total_score / max_len

    # Scalar comparison
    if expected_json == actual_json:
        return 1.0
    # For numbers, allow small floating-point tolerance
    if isinstance(expected_json, (int, float)) and isinstance(actual_json, (int, float)):
        if expected_json == 0 and actual_json == 0:
            return 1.0
        max_abs = max(abs(expected_json), abs(actual_json), 1e-9)
        relative_diff = abs(expected_json - actual_json) / max_abs
        if relative_diff < 0.01:
            return 1.0
        if relative_diff < 0.1:
            return 0.8
        return 0.0
    # Strings: use text similarity
    if isinstance(expected_json, str):
        return score_text_similarity(expected_json, actual_json)
    return 0.0


def score_file_exists(expected_path: str) -> float:
    """Return 1.0 if the file at expected_path exists, 0.0 otherwise.

    Args:
        expected_path: Filesystem path to check.

    Returns:
        1.0 if the path exists as a file, 0.0 otherwise.
    """
    try:
        return 1.0 if Path(expected_path).is_file() else 0.0
    except (OSError, TypeError):
        return 0.0


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class EvalCaseResult:
    """Result of running a single eval case.

    Attributes:
        case_id: The eval_cases.id that was run.
        status: One of 'passed', 'failed', 'partial'.
        score: Numeric score in [0.0, 1.0].
        details: Free-form dict with scoring breakdown, model output, etc.
        model_config: The model configuration dict used for this run.
        duration_ms: Wall-clock time in milliseconds.
    """
    case_id: str
    status: str  # passed | failed | partial
    score: float
    details: dict[str, Any] = field(default_factory=dict)
    model_config: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "score": self.score,
            "details": self.details,
            "model_config": self.model_config,
            "duration_ms": self.duration_ms,
        }


@dataclass
class EvalSuiteResult:
    """Aggregate result of running an eval suite (multiple cases).

    Attributes:
        pass_rate: Fraction of cases that passed [0.0, 1.0].
        avg_score: Average score across all cases.
        total_cases: Number of eval cases run.
        passed: Count of cases with status 'passed'.
        failed: Count of cases with status 'failed'.
        partial: Count of cases with status 'partial'.
        results: Per-case results.
        duration_ms: Total wall-clock time for the entire suite.
    """
    pass_rate: float
    avg_score: float
    total_cases: int
    passed: int
    failed: int
    partial: int
    results: list[EvalCaseResult] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_rate": self.pass_rate,
            "avg_score": self.avg_score,
            "total_cases": self.total_cases,
            "passed": self.passed,
            "failed": self.failed,
            "partial": self.partial,
            "results": [r.to_dict() for r in self.results],
            "duration_ms": self.duration_ms,
        }


@dataclass
class ComparisonResult:
    """Result of comparing two model/prompt configurations on the same eval suite.

    Attributes:
        baseline_results: EvalSuiteResult for the baseline config.
        candidate_results: EvalSuiteResult for the candidate config.
        regressions: List of case_ids that passed baseline but failed candidate.
        improvements: List of case_ids that failed baseline but passed candidate.
        recommendation: One of 'auto_promote', 'human_review', 'reject'.
    """
    baseline_results: EvalSuiteResult
    candidate_results: EvalSuiteResult
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    recommendation: str = "human_review"

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_results": self.baseline_results.to_dict(),
            "candidate_results": self.candidate_results.to_dict(),
            "regressions": self.regressions,
            "improvements": self.improvements,
            "recommendation": self.recommendation,
        }


@dataclass
class GateResult:
    """Result of gating a model/prompt/template change behind eval.

    Attributes:
        passed: True if the change can be promoted.
        regression_count: Number of eval cases that regressed.
        improvement_count: Number of eval cases that improved.
        recommendation: One of 'auto_promote', 'human_review', 'reject'.
        eval_run_id: The eval_runs.id for the primary eval run.
    """
    passed: bool
    regression_count: int
    improvement_count: int
    recommendation: str
    eval_run_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "regression_count": self.regression_count,
            "improvement_count": self.improvement_count,
            "recommendation": self.recommendation,
            "eval_run_id": self.eval_run_id,
        }


# ── Ollama inference helper ──────────────────────────────────────────────────


async def _call_ollama_generate(
    prompt: str,
    model: str,
    system: str | None = None,
    ollama_url: str | None = None,
) -> dict[str, Any]:
    """Call Ollama /api/chat for a single non-streaming inference.

    Returns the full Ollama response dict on success, or a dict with an
    ``"error"`` key on failure.
    """
    base_url = (ollama_url or OLLAMA_BASE_URL).rstrip("/")
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    url = f"{base_url}/api/chat"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_OLLAMA_EVAL_TIMEOUT, connect=10.0)
        ) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            body = resp.text[:500]
            logger.error("Ollama eval call returned HTTP %d: %s", resp.status_code, body)
            return {"error": f"Ollama HTTP {resp.status_code}: {body}"}
        return resp.json()
    except httpx.TimeoutException as exc:
        logger.error("Ollama eval call timed out: %s", exc)
        return {"error": f"Ollama call timed out: {exc}"}
    except Exception as exc:
        logger.error("Ollama eval call failed: %s", exc)
        return {"error": str(exc)}


def _extract_response_text(ollama_response: dict[str, Any]) -> str:
    """Extract the assistant's text from an Ollama chat response."""
    msg = ollama_response.get("message", {})
    return (msg.get("content") or "").strip()


# ── EvalRunner ───────────────────────────────────────────────────────────────


class EvalRunner:
    """Offline eval harness that runs before model/prompt/LoRA changes are promoted.

    Loads eval cases from the ``eval_cases`` table, runs each one by invoking
    the configured model via Ollama, scores the output against the expected
    result, and persists results to the ``eval_runs`` table.

    Typical workflow::

        runner = EvalRunner()
        suite = await runner.run_eval_suite("ws-abc123")
        if suite.pass_rate < 0.9:
            logger.warning("Eval pass rate too low: %.1f%%", suite.pass_rate * 100)
    """

    def __init__(self, ollama_url: str | None = None) -> None:
        self._ollama_url = (ollama_url or OLLAMA_BASE_URL).rstrip("/")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_eval_suite(
        self,
        workspace_id: str,
        eval_case_ids: list[str] | None = None,
        model_config: dict[str, Any] | None = None,
    ) -> EvalSuiteResult:
        """Run a suite of eval cases and return aggregate results.

        Args:
            workspace_id: Scope eval cases to this workspace.
            eval_case_ids: Optional list of specific case IDs to run.
                If None, all cases for the workspace are run.
            model_config: Optional model configuration dict.  Must include
                a ``"model"`` key with the Ollama model name.  Defaults to
                the medium-tier model from config.

        Returns:
            An ``EvalSuiteResult`` with pass_rate, avg_score, per-case results,
            and total duration.
        """
        suite_start = _now_ms()
        cases = self._load_eval_cases(workspace_id, eval_case_ids)

        if not cases:
            logger.warning(
                "No eval cases found for workspace=%s (filter=%s)",
                workspace_id, eval_case_ids,
            )
            return EvalSuiteResult(
                pass_rate=0.0, avg_score=0.0, total_cases=0,
                passed=0, failed=0, partial=0, results=[], duration_ms=0,
            )

        logger.info(
            "Running eval suite: workspace=%s cases=%d model_config=%s",
            workspace_id, len(cases), model_config,
        )

        # Run all cases concurrently (bounded by asyncio's event loop)
        tasks = [
            self.run_single_eval(case["id"], model_config)
            for case in cases
        ]
        results: list[EvalCaseResult] = await asyncio.gather(*tasks)

        # Aggregate
        passed = sum(1 for r in results if r.status == "passed")
        failed = sum(1 for r in results if r.status == "failed")
        partial = sum(1 for r in results if r.status == "partial")
        total = len(results)
        avg_score = sum(r.score for r in results) / total if total > 0 else 0.0
        pass_rate = passed / total if total > 0 else 0.0
        suite_duration = _now_ms() - suite_start

        suite_result = EvalSuiteResult(
            pass_rate=pass_rate,
            avg_score=avg_score,
            total_cases=total,
            passed=passed,
            failed=failed,
            partial=partial,
            results=results,
            duration_ms=suite_duration,
        )

        logger.info(
            "Eval suite complete: pass_rate=%.2f avg_score=%.3f total=%d "
            "passed=%d failed=%d partial=%d duration=%dms",
            pass_rate, avg_score, total, passed, failed, partial, suite_duration,
        )
        return suite_result

    async def run_single_eval(
        self,
        eval_case_id: str,
        model_config: dict[str, Any] | None = None,
    ) -> EvalCaseResult:
        """Run a single eval case, score the output, and persist the result.

        Args:
            eval_case_id: The ID of the eval case to run.
            model_config: Optional model configuration dict.  Defaults to
                the medium-tier model.

        Returns:
            An ``EvalCaseResult`` with status, score, and details.

        Raises:
            ValueError: If the eval case does not exist.
        """
        case_start = _now_ms()
        config = model_config or {"model": MODEL_TIERS.get("medium", "qwen2.5-coder:14b")}
        model_name = config.get("model", MODEL_TIERS.get("medium", "qwen2.5-coder:14b"))

        # Load the case from DB
        case = self._load_eval_case(eval_case_id)
        if case is None:
            raise ValueError(f"Eval case not found: {eval_case_id}")

        input_data = json.loads(case["input_json"]) if case["input_json"] else {}
        expected_output = json.loads(case["expected_output_json"]) if case["expected_output_json"] else {}

        # Build the eval prompt
        prompt = self._build_eval_prompt(input_data, case.get("description", ""))

        # Call the model
        logger.debug(
            "Running eval case=%s model=%s", eval_case_id, model_name,
        )
        response = await _call_ollama_generate(
            prompt=prompt,
            model=model_name,
            system="You are an evaluation assistant. Follow the instructions precisely and return your answer.",
            ollama_url=self._ollama_url,
        )

        duration_ms = _now_ms() - case_start

        if "error" in response:
            logger.error(
                "Eval case %s failed: model error: %s", eval_case_id, response["error"],
            )
            result = EvalCaseResult(
                case_id=eval_case_id,
                status="failed",
                score=0.0,
                details={"error": response["error"]},
                model_config=config,
                duration_ms=duration_ms,
            )
            self._persist_eval_run(result)
            return result

        # Extract and score
        actual_text = _extract_response_text(response)
        score, scoring_details = self._score_output(expected_output, actual_text, case)

        # Determine status
        if score >= _PASS_THRESHOLD:
            status = "passed"
        elif score >= _PARTIAL_THRESHOLD:
            status = "partial"
        else:
            status = "failed"

        result = EvalCaseResult(
            case_id=eval_case_id,
            status=status,
            score=score,
            details={
                "actual_output": actual_text[:2000],  # Truncate for storage
                "scoring": scoring_details,
                "tokens_in": response.get("prompt_eval_count", 0),
                "tokens_out": response.get("eval_count", 0),
            },
            model_config=config,
            duration_ms=duration_ms,
        )

        self._persist_eval_run(result)

        logger.info(
            "Eval case %s: status=%s score=%.3f duration=%dms",
            eval_case_id, status, score, duration_ms,
        )
        return result

    async def compare_configs(
        self,
        workspace_id: str,
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
        eval_case_ids: list[str] | None = None,
    ) -> ComparisonResult:
        """Run the same eval cases with both configs and compare results.

        Any case that passed the baseline but fails the candidate is flagged
        as a regression (BLOCKER).  The recommendation is computed as:

        - ``reject`` if any regressions exist
        - ``auto_promote`` if candidate pass_rate >= baseline pass_rate and
          no regressions
        - ``human_review`` otherwise (improvements exist but need review)

        Args:
            workspace_id: Workspace scope for eval cases.
            baseline_config: The current/known-good model configuration.
            candidate_config: The proposed new model configuration.
            eval_case_ids: Optional subset of case IDs to compare.

        Returns:
            A ``ComparisonResult`` with per-config results, regressions,
            improvements, and recommendation.
        """
        logger.info(
            "Comparing configs: workspace=%s baseline=%s candidate=%s",
            workspace_id,
            baseline_config.get("model", "?"),
            candidate_config.get("model", "?"),
        )

        # Run both suites concurrently
        baseline_task = self.run_eval_suite(workspace_id, eval_case_ids, baseline_config)
        candidate_task = self.run_eval_suite(workspace_id, eval_case_ids, candidate_config)
        baseline_results, candidate_results = await asyncio.gather(
            baseline_task, candidate_task,
        )

        # Build lookup maps: case_id -> status
        baseline_map: dict[str, str] = {
            r.case_id: r.status for r in baseline_results.results
        }
        candidate_map: dict[str, str] = {
            r.case_id: r.status for r in candidate_results.results
        }

        regressions: list[str] = []
        improvements: list[str] = []

        for case_id in baseline_map:
            b_status = baseline_map.get(case_id, "failed")
            c_status = candidate_map.get(case_id, "failed")

            if b_status == "passed" and c_status != "passed":
                regressions.append(case_id)
            elif b_status != "passed" and c_status == "passed":
                improvements.append(case_id)

        # Determine recommendation
        if regressions:
            recommendation = "reject"
        elif (
            candidate_results.pass_rate >= baseline_results.pass_rate
            and not regressions
        ):
            recommendation = "auto_promote"
        else:
            recommendation = "human_review"

        logger.info(
            "Config comparison: regressions=%d improvements=%d "
            "baseline_pass=%.2f candidate_pass=%.2f recommendation=%s",
            len(regressions), len(improvements),
            baseline_results.pass_rate, candidate_results.pass_rate,
            recommendation,
        )

        return ComparisonResult(
            baseline_results=baseline_results,
            candidate_results=candidate_results,
            regressions=regressions,
            improvements=improvements,
            recommendation=recommendation,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_eval_cases(
        self,
        workspace_id: str,
        eval_case_ids: list[str] | None = None,
    ) -> list[dict]:
        """Load eval cases from the database."""
        conn = _get_conn()
        try:
            if eval_case_ids:
                placeholders = ",".join("?" for _ in eval_case_ids)
                rows = conn.execute(
                    f"SELECT * FROM eval_cases WHERE workspace_id = ? AND id IN ({placeholders})",
                    [workspace_id] + eval_case_ids,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM eval_cases WHERE workspace_id = ? ORDER BY created_at",
                    (workspace_id,),
                ).fetchall()
        finally:
            conn.close()
        return _rows_to_dicts(rows)

    def _load_eval_case(self, eval_case_id: str) -> Optional[dict]:
        """Load a single eval case by ID."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM eval_cases WHERE id = ?",
                (eval_case_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_dict(row)

    def _build_eval_prompt(self, input_data: dict, description: str) -> str:
        """Build the user prompt for an eval case."""
        parts: list[str] = []
        if description:
            parts.append(f"Task: {description}")
        if input_data:
            parts.append(f"Input:\n{json.dumps(input_data, indent=2, ensure_ascii=False)}")
        parts.append(
            "Produce your output. If the expected output is JSON, "
            "return valid JSON only with no surrounding text."
        )
        return "\n\n".join(parts)

    def _score_output(
        self,
        expected: Any,
        actual_text: str,
        case: dict,
    ) -> tuple[float, dict[str, Any]]:
        """Score the model's actual output against the expected output.

        Uses a combination of scoring strategies depending on the eval case's
        artifact_type and expected output structure.

        Returns:
            A tuple of (score, details_dict).
        """
        details: dict[str, Any] = {}
        artifact_type = case.get("artifact_type", "")

        # Try to parse actual output as JSON
        actual_parsed: Any = None
        try:
            actual_parsed = json.loads(actual_text)
        except (json.JSONDecodeError, TypeError):
            pass

        scores: list[float] = []

        # Strategy 1: JSON structural match (if both are structured)
        if isinstance(expected, dict) and isinstance(actual_parsed, dict):
            json_score = score_json_match(expected, actual_parsed)
            scores.append(json_score)
            details["json_match"] = json_score
        elif isinstance(expected, list) and isinstance(actual_parsed, list):
            json_score = score_json_match(expected, actual_parsed)
            scores.append(json_score)
            details["json_match"] = json_score

        # Strategy 2: Text similarity (always computed as fallback)
        expected_text = json.dumps(expected, ensure_ascii=False) if not isinstance(expected, str) else expected
        text_score = score_text_similarity(expected_text, actual_text)
        scores.append(text_score)
        details["text_similarity"] = text_score

        # Strategy 3: File existence check (if artifact_type involves files)
        if artifact_type in ("file", "file_output") and isinstance(expected, dict):
            file_path = expected.get("file_path", "")
            if file_path:
                file_score = score_file_exists(file_path)
                scores.append(file_score)
                details["file_exists"] = file_score

        # Final score: max of all strategies (be generous)
        final_score = max(scores) if scores else 0.0
        details["final_score"] = final_score
        details["strategy_count"] = len(scores)

        return final_score, details

    def _persist_eval_run(self, result: EvalCaseResult) -> str:
        """Persist an eval run result to the eval_runs table.

        Returns:
            The newly created eval_runs.id.
        """
        run_id = _new_id()
        now = _now()
        conn = _get_conn()
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
                    result.case_id,
                    None,  # No job_id for offline eval runs
                    result.status,
                    result.score,
                    json.dumps(result.details),
                    json.dumps(result.model_config),
                    now,
                    result.duration_ms,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.debug("Persisted eval run: id=%s case=%s status=%s", run_id, result.case_id, result.status)
        return run_id


# ── QualityMonitor ───────────────────────────────────────────────────────────


class QualityMonitor:
    """Production quality sampling and trend monitoring.

    Randomly samples completed jobs, scores them using deterministic checks
    or LLM-judge, and tracks quality trends over time.  Alerts if quality
    drops more than 10% over the trailing window.

    Usage::

        monitor = QualityMonitor()
        samples = await monitor.sample_and_score(sample_rate=0.05)
        trend = monitor.get_quality_trend("ws-abc123", days=7)
        if trend.get("alert"):
            notify_ops(trend["alert"])
    """

    def __init__(self, ollama_url: str | None = None) -> None:
        self._ollama_url = (ollama_url or OLLAMA_BASE_URL).rstrip("/")

    async def sample_and_score(
        self,
        sample_rate: float = 0.05,
    ) -> list[dict]:
        """Randomly sample completed jobs and score their quality.

        For each sampled job, the method:
        1. Loads the job's result_summary and node outputs.
        2. Runs deterministic quality checks (completeness, format).
        3. Optionally invokes an LLM-judge for subjective quality.
        4. Persists results to eval_runs for trend tracking.

        Args:
            sample_rate: Fraction of completed jobs to sample (0.0 - 1.0).

        Returns:
            A list of dicts, one per sampled job, with keys:
            ``job_id``, ``workspace_id``, ``quality_score``, ``checks``.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, workspace_id, result_summary, title, mode, updated_at
                FROM jobs
                WHERE status = 'done'
                ORDER BY updated_at DESC
                LIMIT 1000
                """,
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            logger.info("No completed jobs to sample")
            return []

        # Random sample
        jobs = _rows_to_dicts(rows)
        sample_size = max(1, int(len(jobs) * sample_rate))
        sampled = random.sample(jobs, min(sample_size, len(jobs)))

        logger.info(
            "Quality sampling: %d/%d jobs (rate=%.2f)",
            len(sampled), len(jobs), sample_rate,
        )

        results: list[dict] = []
        for job in sampled:
            score_result = await self._score_job_quality(job)
            results.append(score_result)

            # Persist as an eval_run for trend tracking
            self._persist_quality_sample(score_result)

        return results

    def get_quality_trend(
        self,
        workspace_id: str,
        days: int = 7,
    ) -> dict:
        """Compute 7-day moving average quality score with alert on degradation.

        Reads eval_runs for the given workspace over the trailing ``days``
        window, groups by day, computes daily averages, and checks for a
        >10% drop from the period's peak.

        Args:
            workspace_id: Workspace to analyse.
            days: Number of trailing days to include.

        Returns:
            A dict with keys:
            ``workspace_id``, ``days``, ``daily_scores`` (list of
            ``{date, avg_score, count}``), ``moving_average``,
            ``peak_score``, ``current_score``, ``alert`` (str or None).
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    DATE(er.ran_at) AS day,
                    AVG(er.score) AS avg_score,
                    COUNT(*) AS cnt
                FROM eval_runs er
                JOIN eval_cases ec ON er.eval_case_id = ec.id
                WHERE ec.workspace_id = ?
                  AND er.ran_at >= ?
                GROUP BY DATE(er.ran_at)
                ORDER BY day
                """,
                (workspace_id, cutoff),
            ).fetchall()
        finally:
            conn.close()

        daily_scores: list[dict] = []
        all_scores: list[float] = []

        for row in rows:
            day_data = {
                "date": row["day"],
                "avg_score": float(row["avg_score"]) if row["avg_score"] is not None else 0.0,
                "count": int(row["cnt"]),
            }
            daily_scores.append(day_data)
            all_scores.append(day_data["avg_score"])

        moving_average = sum(all_scores) / len(all_scores) if all_scores else 0.0
        peak_score = max(all_scores) if all_scores else 0.0
        current_score = all_scores[-1] if all_scores else 0.0

        # Alert if current score drops >10% from peak
        alert: str | None = None
        if peak_score > 0 and (peak_score - current_score) / peak_score > _QUALITY_DROP_ALERT_THRESHOLD:
            drop_pct = ((peak_score - current_score) / peak_score) * 100
            alert = (
                f"Quality degradation detected: current={current_score:.3f} "
                f"peak={peak_score:.3f} drop={drop_pct:.1f}%"
            )
            logger.warning("Quality alert for workspace=%s: %s", workspace_id, alert)

        return {
            "workspace_id": workspace_id,
            "days": days,
            "daily_scores": daily_scores,
            "moving_average": moving_average,
            "peak_score": peak_score,
            "current_score": current_score,
            "alert": alert,
        }

    def get_model_quality(self, workspace_id: str) -> dict:
        """Compute per-model quality scores to identify degrading models.

        Queries eval_runs grouped by model_config_json to produce per-model
        quality metrics.

        Args:
            workspace_id: Workspace to analyse.

        Returns:
            A dict with keys ``workspace_id`` and ``models`` (list of dicts
            each containing ``model``, ``avg_score``, ``total_runs``,
            ``pass_rate``, ``recent_trend``).
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    er.model_config_json,
                    AVG(er.score) AS avg_score,
                    COUNT(*) AS total_runs,
                    SUM(CASE WHEN er.status = 'passed' THEN 1 ELSE 0 END) AS passed_count,
                    er.status
                FROM eval_runs er
                JOIN eval_cases ec ON er.eval_case_id = ec.id
                WHERE ec.workspace_id = ?
                GROUP BY er.model_config_json
                ORDER BY avg_score DESC
                """,
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()

        models: list[dict] = []
        for row in rows:
            config_str = row["model_config_json"] or "{}"
            try:
                config = json.loads(config_str)
            except (json.JSONDecodeError, TypeError):
                config = {}
            model_name = config.get("model", "unknown")
            total = int(row["total_runs"])
            passed = int(row["passed_count"])
            avg = float(row["avg_score"]) if row["avg_score"] is not None else 0.0

            models.append({
                "model": model_name,
                "avg_score": avg,
                "total_runs": total,
                "pass_rate": passed / total if total > 0 else 0.0,
            })

        # Compute recent trend for each model (last 10 runs vs prior 10)
        for model_entry in models:
            trend = self._compute_model_trend(workspace_id, model_entry["model"])
            model_entry["recent_trend"] = trend

        return {
            "workspace_id": workspace_id,
            "models": models,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _score_job_quality(self, job: dict) -> dict:
        """Score a single completed job's quality.

        Applies deterministic checks:
        - Has a non-empty result_summary
        - result_summary length is reasonable (>20 chars)
        - No obvious error patterns in the summary

        Returns a quality result dict.
        """
        checks: dict[str, float] = {}
        summary = job.get("result_summary") or ""

        # Check 1: Has result summary
        checks["has_summary"] = 1.0 if summary.strip() else 0.0

        # Check 2: Summary length (reasonable output)
        if len(summary) > 20:
            checks["summary_length"] = 1.0
        elif len(summary) > 5:
            checks["summary_length"] = 0.5
        else:
            checks["summary_length"] = 0.0

        # Check 3: No error patterns
        error_patterns = ["error", "failed", "exception", "traceback", "could not"]
        summary_lower = summary.lower()
        has_errors = any(p in summary_lower for p in error_patterns)
        checks["no_errors"] = 0.0 if has_errors else 1.0

        # Check 4: Completeness — check that job nodes are all completed
        conn = _get_conn()
        try:
            node_rows = conn.execute(
                """
                SELECT status FROM job_nodes WHERE job_id = ?
                """,
                (job["id"],),
            ).fetchall()
        finally:
            conn.close()

        if node_rows:
            completed = sum(1 for r in node_rows if r["status"] == "completed")
            checks["nodes_completed"] = completed / len(node_rows)
        else:
            checks["nodes_completed"] = 0.5  # No nodes = neutral

        # Aggregate: weighted average
        weights = {
            "has_summary": 0.25,
            "summary_length": 0.15,
            "no_errors": 0.30,
            "nodes_completed": 0.30,
        }
        quality_score = sum(
            checks[k] * weights.get(k, 0.0) for k in checks
        )

        return {
            "job_id": job["id"],
            "workspace_id": job["workspace_id"],
            "quality_score": quality_score,
            "checks": checks,
        }

    def _persist_quality_sample(self, score_result: dict) -> None:
        """Persist a quality sample as a synthetic eval_run for trend tracking."""
        run_id = _new_id()
        now = _now()

        # We need a valid eval_case_id; use a sentinel quality-monitor case
        case_id = self._ensure_quality_monitor_case(score_result["workspace_id"])

        status = "passed" if score_result["quality_score"] >= _PASS_THRESHOLD else "failed"

        conn = _get_conn()
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
                    score_result["job_id"],
                    status,
                    score_result["quality_score"],
                    json.dumps(score_result["checks"]),
                    json.dumps({"model": "quality_monitor", "type": "production_sample"}),
                    now,
                    0,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _ensure_quality_monitor_case(self, workspace_id: str) -> str:
        """Ensure a sentinel eval_case exists for quality monitoring samples.

        Returns the case ID (creates one if it does not exist).
        """
        sentinel_name = "__quality_monitor__"
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM eval_cases WHERE workspace_id = ? AND name = ?",
                (workspace_id, sentinel_name),
            ).fetchone()

            if row is not None:
                return row["id"]

            case_id = _new_id()
            now = _now()
            conn.execute(
                """
                INSERT INTO eval_cases
                    (id, workspace_id, name, description, input_json,
                     expected_output_json, artifact_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    workspace_id,
                    sentinel_name,
                    "Sentinel eval case for production quality monitoring",
                    "{}",
                    "{}",
                    "quality_monitor",
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Created quality monitor sentinel case: id=%s workspace=%s",
            case_id, workspace_id,
        )
        return case_id

    def _compute_model_trend(self, workspace_id: str, model_name: str) -> str:
        """Compute whether a model's quality is improving, stable, or degrading.

        Compares the average score of the last 10 runs against the prior 10.

        Returns:
            One of 'improving', 'stable', 'degrading'.
        """
        model_config_pattern = f'%"model": "{model_name}"%'

        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT er.score
                FROM eval_runs er
                JOIN eval_cases ec ON er.eval_case_id = ec.id
                WHERE ec.workspace_id = ?
                  AND er.model_config_json LIKE ?
                ORDER BY er.ran_at DESC
                LIMIT 20
                """,
                (workspace_id, model_config_pattern),
            ).fetchall()
        finally:
            conn.close()

        if len(rows) < 4:
            return "stable"  # Not enough data

        scores = [float(r["score"]) for r in rows]
        recent = scores[:min(10, len(scores) // 2)]
        prior = scores[min(10, len(scores) // 2):]

        if not recent or not prior:
            return "stable"

        recent_avg = sum(recent) / len(recent)
        prior_avg = sum(prior) / len(prior)

        if prior_avg == 0:
            return "stable"

        change = (recent_avg - prior_avg) / prior_avg

        if change > 0.05:
            return "improving"
        elif change < -0.05:
            return "degrading"
        return "stable"


# ── ChangeGate ───────────────────────────────────────────────────────────────


class ChangeGate:
    """Gate model/prompt/template changes behind eval to prevent regressions.

    Before promoting a LoRA, prompt, or template change, runs the eval suite
    comparing baseline (current production config) against the candidate
    (proposed change).  Returns a ``GateResult`` indicating whether the
    change can be promoted.

    Usage::

        gate = ChangeGate()
        result = await gate.gate_prompt_change(
            prompt_name="coding_assistant",
            new_content="You are a coding assistant...",
            eval_case_ids=["case1", "case2"],
        )
        if result.passed:
            promote_prompt(...)
    """

    def __init__(self, ollama_url: str | None = None) -> None:
        self._eval_runner = EvalRunner(ollama_url=ollama_url)
        self._ollama_url = (ollama_url or OLLAMA_BASE_URL).rstrip("/")

    async def gate_lora_change(
        self,
        workspace_id: str,
        lora_id: str,
        eval_case_ids: list[str],
    ) -> GateResult:
        """Gate a LoRA adapter change behind eval.

        Compares the baseline (no LoRA) against the candidate (with LoRA)
        on the specified eval cases.

        Args:
            workspace_id: Workspace scope.
            lora_id: The LoRA adapter identifier to test.
            eval_case_ids: Eval case IDs to run the comparison on.

        Returns:
            A ``GateResult`` with pass/fail and recommendation.
        """
        logger.info("Gating LoRA change: workspace=%s lora=%s", workspace_id, lora_id)

        baseline_config = {
            "model": MODEL_TIERS.get("medium", "qwen2.5-coder:14b"),
            "lora": None,
        }
        candidate_config = {
            "model": MODEL_TIERS.get("medium", "qwen2.5-coder:14b"),
            "lora": lora_id,
        }

        comparison = await self._eval_runner.compare_configs(
            workspace_id=workspace_id,
            baseline_config=baseline_config,
            candidate_config=candidate_config,
            eval_case_ids=eval_case_ids,
        )

        eval_run_id = _new_id()
        self._persist_gate_result(eval_run_id, "lora", lora_id, comparison)

        return GateResult(
            passed=comparison.recommendation != "reject",
            regression_count=len(comparison.regressions),
            improvement_count=len(comparison.improvements),
            recommendation=comparison.recommendation,
            eval_run_id=eval_run_id,
        )

    async def gate_prompt_change(
        self,
        prompt_name: str,
        new_content: str,
        eval_case_ids: list[str],
    ) -> GateResult:
        """Gate a system/node prompt change behind eval.

        Loads the current prompt version from ``prompt_versions``, runs
        eval with the current content as baseline and the new content as
        candidate.

        Args:
            prompt_name: The prompt name (prompt_versions.name).
            new_content: The proposed new prompt content.
            eval_case_ids: Eval case IDs to run.

        Returns:
            A ``GateResult`` with pass/fail and recommendation.
        """
        logger.info("Gating prompt change: prompt=%s", prompt_name)

        # Load current prompt version
        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT content, workspace_id FROM prompt_versions pv
                JOIN eval_cases ec ON 1=1
                WHERE pv.name = ? AND pv.lifecycle_state = 'active'
                ORDER BY pv.version_number DESC
                LIMIT 1
                """,
                (prompt_name,),
            ).fetchone()
        finally:
            conn.close()

        current_content = row["content"] if row else ""

        # Determine workspace from eval cases
        workspace_id = self._get_workspace_from_cases(eval_case_ids)

        baseline_config = {
            "model": MODEL_TIERS.get("medium", "qwen2.5-coder:14b"),
            "prompt_override": current_content,
        }
        candidate_config = {
            "model": MODEL_TIERS.get("medium", "qwen2.5-coder:14b"),
            "prompt_override": new_content,
        }

        comparison = await self._eval_runner.compare_configs(
            workspace_id=workspace_id,
            baseline_config=baseline_config,
            candidate_config=candidate_config,
            eval_case_ids=eval_case_ids,
        )

        eval_run_id = _new_id()
        self._persist_gate_result(eval_run_id, "prompt", prompt_name, comparison)

        return GateResult(
            passed=comparison.recommendation != "reject",
            regression_count=len(comparison.regressions),
            improvement_count=len(comparison.improvements),
            recommendation=comparison.recommendation,
            eval_run_id=eval_run_id,
        )

    async def gate_template_change(
        self,
        template_id: str,
        new_nodes_json: str,
        eval_case_ids: list[str],
    ) -> GateResult:
        """Gate a pipeline template change behind eval.

        Runs eval with the current template nodes as baseline and the
        proposed new nodes as candidate.

        Args:
            template_id: The pipeline_templates.id to gate.
            new_nodes_json: JSON string of the proposed new nodes array.
            eval_case_ids: Eval case IDs to run.

        Returns:
            A ``GateResult`` with pass/fail and recommendation.
        """
        logger.info("Gating template change: template=%s", template_id)

        # Load current template
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT workspace_id, nodes_json FROM pipeline_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise ValueError(f"Template not found: {template_id}")

        workspace_id = row["workspace_id"]
        current_nodes = row["nodes_json"]

        baseline_config = {
            "model": MODEL_TIERS.get("medium", "qwen2.5-coder:14b"),
            "template_nodes": current_nodes,
        }
        candidate_config = {
            "model": MODEL_TIERS.get("medium", "qwen2.5-coder:14b"),
            "template_nodes": new_nodes_json,
        }

        comparison = await self._eval_runner.compare_configs(
            workspace_id=workspace_id,
            baseline_config=baseline_config,
            candidate_config=candidate_config,
            eval_case_ids=eval_case_ids,
        )

        eval_run_id = _new_id()
        self._persist_gate_result(eval_run_id, "template", template_id, comparison)

        return GateResult(
            passed=comparison.recommendation != "reject",
            regression_count=len(comparison.regressions),
            improvement_count=len(comparison.improvements),
            recommendation=comparison.recommendation,
            eval_run_id=eval_run_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_workspace_from_cases(self, eval_case_ids: list[str]) -> str:
        """Resolve the workspace_id from the first eval case."""
        if not eval_case_ids:
            raise ValueError("eval_case_ids must not be empty")

        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT workspace_id FROM eval_cases WHERE id = ?",
                (eval_case_ids[0],),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise ValueError(f"Eval case not found: {eval_case_ids[0]}")
        return row["workspace_id"]

    def _persist_gate_result(
        self,
        eval_run_id: str,
        change_type: str,
        change_ref: str,
        comparison: ComparisonResult,
    ) -> None:
        """Persist a gate evaluation result for audit trail."""
        # Use the candidate suite's first result case_id as the eval_case_id
        # for the gate record, or create a synthetic one
        case_id = (
            comparison.candidate_results.results[0].case_id
            if comparison.candidate_results.results
            else _new_id()
        )

        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO eval_runs
                    (id, eval_case_id, job_id, status, score, details_json,
                     model_config_json, ran_at, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eval_run_id,
                    case_id,
                    None,
                    comparison.recommendation,
                    comparison.candidate_results.avg_score,
                    json.dumps({
                        "gate_type": change_type,
                        "gate_ref": change_ref,
                        "regressions": comparison.regressions,
                        "improvements": comparison.improvements,
                        "baseline_pass_rate": comparison.baseline_results.pass_rate,
                        "candidate_pass_rate": comparison.candidate_results.pass_rate,
                    }),
                    json.dumps({"type": f"gate_{change_type}", "ref": change_ref}),
                    now,
                    comparison.candidate_results.duration_ms,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Persisted gate result: id=%s type=%s ref=%s recommendation=%s",
            eval_run_id, change_type, change_ref, comparison.recommendation,
        )
