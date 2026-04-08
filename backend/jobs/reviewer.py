"""
3-Tier QA Reviewer — LocalMind enterprise task worker.

The "caboose" of the train — validates job output through 3 tiers of quality
assurance after the node executor finishes all cars.

Review pipeline:
  Tier 1: Deterministic format/schema checks (fast, no LLM)
  Tier 2: LLM critique via Gemini (completeness, accuracy, formatting, adherence)
  Tier 3: Human review flag (escalation decision based on Tier 1+2 outcomes)

A failed review returns structured feedback that the job runner can use to
re-plan or re-execute the job.
"""

from __future__ import annotations

import json
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.gemini_client import generate, is_available
from backend.jobs.models import Job, JobFile, Node, NodeStatus

logger = logging.getLogger("localmind.jobs.reviewer")

# ---------------------------------------------------------------------------
# Tier 2 scoring threshold — below this the review is considered failed.
# ---------------------------------------------------------------------------
_TIER2_PASS_THRESHOLD: float = 0.7

# Human review is flagged when Tier 2 score falls below this (stricter).
_TIER2_HUMAN_FLAG_THRESHOLD: float = 0.7

# Max characters of node output included in the Tier 2 prompt.
_OUTPUT_SUMMARY_MAX_CHARS: int = 800


# ---------------------------------------------------------------------------
# ReviewIssue
# ---------------------------------------------------------------------------


@dataclass
class ReviewIssue:
    """A single issue found during any review tier."""

    tier: int  # 1, 2, or 3
    severity: str  # "critical" | "warning" | "info"
    category: str  # e.g. "completeness", "format", "schema", "adherence"
    description: str
    node_id: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
            "node_id": self.node_id,
            "suggestion": self.suggestion,
        }


# ---------------------------------------------------------------------------
# ReviewResult
# ---------------------------------------------------------------------------


@dataclass
class ReviewResult:
    """Aggregated result from all three review tiers."""

    passed: bool
    score: float  # 0.0 – 1.0
    feedback: str
    issues: list[ReviewIssue] = field(default_factory=list)
    needs_human_review: bool = False
    tier1_passed: bool = True
    tier2_passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "feedback": self.feedback,
            "issues": [i.to_dict() for i in self.issues],
            "needs_human_review": self.needs_human_review,
            "tier1_passed": self.tier1_passed,
            "tier2_passed": self.tier2_passed,
        }


# ---------------------------------------------------------------------------
# JobReviewer
# ---------------------------------------------------------------------------


class JobReviewer:
    """3-tier QA reviewer for completed job output."""

    def __init__(self, tool_registry: Any = None) -> None:
        # tool_registry reserved for future tool-aware checks (e.g. re-run a
        # validation tool).  Stored but not used in the current implementation.
        self._tool_registry = tool_registry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def review(
        self,
        job: Job,
        nodes: list[Node],
        output_files: list[JobFile] | None = None,
    ) -> ReviewResult:
        """Run 3-tier QA review on job output.

        Tier 1 (deterministic) runs first; if it finds critical issues Tier 2
        is still executed so feedback is as rich as possible.  Tier 3 is a
        pure decision function that inspects Tier 1 + 2 outcomes.

        Returns a ReviewResult with pass/fail, score, feedback, and all issues.
        """
        output_files = output_files or []

        logger.info(
            "Starting review for job %s (%d nodes, %d files)",
            job.id,
            len(nodes),
            len(output_files),
        )

        # ── Tier 1 ──────────────────────────────────────────────────────
        tier1_issues = await self._tier1_format_checks(job, nodes, output_files)
        tier1_criticals = [i for i in tier1_issues if i.severity == "critical"]
        tier1_passed = len(tier1_criticals) == 0

        if not tier1_passed:
            logger.warning(
                "Tier 1 found %d critical issue(s) for job %s",
                len(tier1_criticals),
                job.id,
            )

        # ── Tier 2 ──────────────────────────────────────────────────────
        # Build node_outputs map for the LLM prompt.
        node_outputs: dict[str, Any] = {}
        for node in nodes:
            if node.output_json:
                try:
                    node_outputs[node.id] = json.loads(node.output_json)
                except (json.JSONDecodeError, TypeError):
                    node_outputs[node.id] = node.output_json
            else:
                node_outputs[node.id] = None

        tier2_issues, tier2_score = await self._tier2_llm_critique(
            job, nodes, node_outputs
        )
        tier2_passed = tier2_score >= _TIER2_PASS_THRESHOLD

        if not tier2_passed:
            logger.warning(
                "Tier 2 score %.2f below threshold %.2f for job %s",
                tier2_score,
                _TIER2_PASS_THRESHOLD,
                job.id,
            )

        # ── Tier 3 ──────────────────────────────────────────────────────
        needs_human_review = self._tier3_human_flag(
            job, tier1_issues, tier2_issues, tier2_score
        )

        # ── Aggregate ───────────────────────────────────────────────────
        all_issues = tier1_issues + tier2_issues

        # Overall pass: Tier 1 no criticals AND Tier 2 meets threshold.
        overall_passed = tier1_passed and tier2_passed

        # Composite score: Tier 1 contributes a binary 0 or 1 (weight 0.3),
        # Tier 2 contributes the LLM score (weight 0.7).
        t1_score = 1.0 if tier1_passed else max(
            0.0, 1.0 - 0.25 * len(tier1_criticals)
        )
        composite_score = round(0.3 * t1_score + 0.7 * tier2_score, 4)

        feedback = self._build_feedback(
            overall_passed, composite_score, tier1_issues, tier2_issues, needs_human_review
        )

        logger.info(
            "Review complete for job %s: passed=%s score=%.3f human_flag=%s",
            job.id,
            overall_passed,
            composite_score,
            needs_human_review,
        )

        return ReviewResult(
            passed=overall_passed,
            score=composite_score,
            feedback=feedback,
            issues=all_issues,
            needs_human_review=needs_human_review,
            tier1_passed=tier1_passed,
            tier2_passed=tier2_passed,
        )

    # ------------------------------------------------------------------
    # Tier 1 — Deterministic format/schema checks
    # ------------------------------------------------------------------

    async def _tier1_format_checks(
        self,
        job: Job,
        nodes: list[Node],
        output_files: list[JobFile],
    ) -> list[ReviewIssue]:
        """Run deterministic checks against nodes and output files.

        Checks:
        - All required nodes completed successfully (not failed / cancelled)
        - Output files exist on disk and are non-empty
        - PPTX files are valid ZIP archives
        - XLSX files are valid ZIP archives
        - JSON outputs parse successfully
        - Output matches node.expected_output type hints
        - No empty/null outputs on required (non-skipped) nodes
        """
        issues: list[ReviewIssue] = []

        # Map file ids to files by node for quick lookup
        files_by_node: dict[str | None, list[JobFile]] = {}
        for jf in output_files:
            files_by_node.setdefault(jf.node_id, []).append(jf)

        for node in nodes:
            node_issues = self._check_node(node, files_by_node)
            issues.extend(node_issues)

        # Check all output files (regardless of node assignment)
        for jf in output_files:
            file_issues = self._check_output_file(jf)
            issues.extend(file_issues)

        return issues

    def _check_node(
        self,
        node: Node,
        files_by_node: dict[str | None, list[JobFile]],
    ) -> list[ReviewIssue]:
        """Check a single node for completion and output validity."""
        issues: list[ReviewIssue] = []
        nid = node.id
        title = node.title or f"Node {node.sequence}"

        # ── Node must have completed (not failed / cancelled / still pending) ──
        terminal_ok = {NodeStatus.COMPLETED.value, NodeStatus.SKIPPED.value}
        if node.status not in terminal_ok:
            severity = "critical" if node.status == NodeStatus.FAILED.value else "warning"
            issues.append(ReviewIssue(
                tier=1,
                severity=severity,
                category="completeness",
                description=(
                    f"Node '{title}' did not complete successfully "
                    f"(status={node.status!r})"
                ),
                node_id=nid,
                suggestion="Re-run this node or check its error log.",
            ))
            # If the node failed/is incomplete don't bother checking its output.
            return issues

        # Skipped nodes don't need output checks.
        if node.status == NodeStatus.SKIPPED.value:
            return issues

        # ── Output must not be null/empty for completed required nodes ──
        if not node.output_json or node.output_json.strip() in ("", "null", "{}"):
            issues.append(ReviewIssue(
                tier=1,
                severity="critical",
                category="completeness",
                description=f"Node '{title}' completed but produced no output.",
                node_id=nid,
                suggestion="Ensure the node writes a non-empty output_json.",
            ))

        # ── JSON output must parse ──
        if node.output_json:
            try:
                json.loads(node.output_json)
            except (json.JSONDecodeError, TypeError) as exc:
                issues.append(ReviewIssue(
                    tier=1,
                    severity="critical",
                    category="format",
                    description=(
                        f"Node '{title}' output_json is not valid JSON: {exc}"
                    ),
                    node_id=nid,
                    suggestion="Fix the node to produce well-formed JSON output.",
                ))

        # ── expected_output type checks ──
        if node.expected_output:
            exp = node.expected_output.lower()
            node_files = files_by_node.get(nid, [])

            # If expected_output mentions a file type, require at least one file
            file_keywords = {"file", "pptx", "xlsx", "csv", "pdf", "docx", "zip"}
            if any(kw in exp for kw in file_keywords) and not node_files:
                issues.append(ReviewIssue(
                    tier=1,
                    severity="warning",
                    category="format",
                    description=(
                        f"Node '{title}' expected a file output "
                        f"({node.expected_output!r}) but no files were recorded."
                    ),
                    node_id=nid,
                    suggestion="Ensure the node registers produced files in job_files.",
                ))

            # If expected_output mentions JSON, validate the output structure
            if "json" in exp and node.output_json:
                try:
                    parsed = json.loads(node.output_json)
                    if not isinstance(parsed, (dict, list)):
                        issues.append(ReviewIssue(
                            tier=1,
                            severity="warning",
                            category="format",
                            description=(
                                f"Node '{title}' expected JSON output but produced "
                                f"a scalar value ({type(parsed).__name__})."
                            ),
                            node_id=nid,
                            suggestion="Return a JSON object or array, not a bare scalar.",
                        ))
                except (json.JSONDecodeError, TypeError):
                    pass  # Already reported above

        # ── output_schema_json validation ──
        if node.output_schema_json and node.output_json:
            schema_issues = self._validate_against_schema(
                node.output_json, node.output_schema_json, node, title
            )
            issues.extend(schema_issues)

        return issues

    def _validate_against_schema(
        self,
        output_json: str,
        schema_json: str,
        node: Node,
        title: str,
    ) -> list[ReviewIssue]:
        """Best-effort JSON Schema validation without external deps."""
        issues: list[ReviewIssue] = []
        try:
            output = json.loads(output_json)
            schema = json.loads(schema_json)
        except (json.JSONDecodeError, TypeError):
            return issues

        if not isinstance(schema, dict):
            return issues

        required_fields: list[str] = schema.get("required", [])
        if required_fields and isinstance(output, dict):
            missing = [f for f in required_fields if f not in output]
            if missing:
                issues.append(ReviewIssue(
                    tier=1,
                    severity="warning",
                    category="schema",
                    description=(
                        f"Node '{title}' output is missing required schema "
                        f"field(s): {missing}"
                    ),
                    node_id=node.id,
                    suggestion=(
                        f"Ensure the node produces these fields: "
                        f"{', '.join(missing)}"
                    ),
                ))

        return issues

    def _check_output_file(self, jf: JobFile) -> list[ReviewIssue]:
        """Check a single output file for existence, size, and format validity."""
        issues: list[ReviewIssue] = []
        path = Path(jf.file_path)

        # ── File must exist ──
        if not path.exists():
            issues.append(ReviewIssue(
                tier=1,
                severity="critical",
                category="format",
                description=(
                    f"Output file '{jf.filename}' is registered but does not "
                    f"exist on disk at {jf.file_path!r}."
                ),
                node_id=jf.node_id,
                suggestion="Verify the node writes the file before completing.",
            ))
            return issues

        # ── File must be non-empty ──
        try:
            size = path.stat().st_size
        except OSError as exc:
            issues.append(ReviewIssue(
                tier=1,
                severity="warning",
                category="format",
                description=f"Could not stat file '{jf.filename}': {exc}",
                node_id=jf.node_id,
            ))
            return issues

        if size == 0:
            issues.append(ReviewIssue(
                tier=1,
                severity="critical",
                category="format",
                description=f"Output file '{jf.filename}' is empty (0 bytes).",
                node_id=jf.node_id,
                suggestion="Ensure the node writes actual content to the file.",
            ))

        # ── ZIP-based format validation (PPTX, XLSX, DOCX) ──
        ext = path.suffix.lower()
        if ext in {".pptx", ".xlsx", ".docx", ".zip"}:
            zip_issues = self._validate_zipfile(jf, path, ext)
            issues.extend(zip_issues)

        # ── JSON file validation ──
        if ext == ".json":
            json_issues = self._validate_json_file(jf, path)
            issues.extend(json_issues)

        return issues

    def _validate_zipfile(
        self, jf: JobFile, path: Path, ext: str
    ) -> list[ReviewIssue]:
        """Validate that a ZIP-based office format is a valid ZIP archive."""
        issues: list[ReviewIssue] = []
        try:
            with zipfile.ZipFile(path, "r") as zf:
                bad = zf.testzip()
                if bad is not None:
                    issues.append(ReviewIssue(
                        tier=1,
                        severity="critical",
                        category="format",
                        description=(
                            f"File '{jf.filename}' failed ZIP integrity check "
                            f"(first bad file: {bad!r})."
                        ),
                        node_id=jf.node_id,
                        suggestion=(
                            f"Re-generate the {ext.lstrip('.')} file — "
                            "it may have been truncated or corrupted."
                        ),
                    ))
        except zipfile.BadZipFile as exc:
            issues.append(ReviewIssue(
                tier=1,
                severity="critical",
                category="format",
                description=(
                    f"File '{jf.filename}' is not a valid {ext.lstrip('.')} "
                    f"(ZIP) file: {exc}"
                ),
                node_id=jf.node_id,
                suggestion=(
                    f"Re-generate the {ext.lstrip('.')} file from scratch."
                ),
            ))
        except OSError as exc:
            issues.append(ReviewIssue(
                tier=1,
                severity="warning",
                category="format",
                description=f"Could not open '{jf.filename}' for ZIP validation: {exc}",
                node_id=jf.node_id,
            ))
        return issues

    def _validate_json_file(self, jf: JobFile, path: Path) -> list[ReviewIssue]:
        """Validate that a .json file contains valid JSON."""
        issues: list[ReviewIssue] = []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            issues.append(ReviewIssue(
                tier=1,
                severity="critical",
                category="format",
                description=f"File '{jf.filename}' contains invalid JSON: {exc}",
                node_id=jf.node_id,
                suggestion="Ensure the node writes valid JSON to this file.",
            ))
        except OSError as exc:
            issues.append(ReviewIssue(
                tier=1,
                severity="warning",
                category="format",
                description=f"Could not read '{jf.filename}' for JSON validation: {exc}",
                node_id=jf.node_id,
            ))
        return issues

    # ------------------------------------------------------------------
    # Tier 2 — LLM critique via Gemini
    # ------------------------------------------------------------------

    async def _tier2_llm_critique(
        self,
        job: Job,
        nodes: list[Node],
        node_outputs: dict[str, Any],
    ) -> tuple[list[ReviewIssue], float]:
        """Call Gemini to critique job output quality.

        Returns (issues, score) where score is 0.0–1.0.
        Falls back to ([], 1.0) if Gemini is unavailable.
        """
        if not is_available():
            logger.info(
                "Gemini unavailable; skipping Tier 2 critique for job %s", job.id
            )
            return [], 1.0

        prompt = self._build_tier2_prompt(job, nodes, node_outputs)

        try:
            raw = await generate(
                prompt=prompt,
                model="gemini-2.0-flash",
                system_instruction=(
                    "You are a strict quality reviewer for an automated task worker. "
                    "Respond ONLY with valid JSON matching the requested schema."
                ),
                scrub=True,
            )
        except Exception as exc:
            logger.warning(
                "Tier 2 Gemini call failed for job %s: %s — passing through", job.id, exc
            )
            return [], 1.0

        return self._parse_tier2_response(raw, nodes)

    def _build_tier2_prompt(
        self,
        job: Job,
        nodes: list[Node],
        node_outputs: dict[str, Any],
    ) -> str:
        """Construct the Tier 2 LLM prompt."""
        lines: list[str] = []

        lines.append("You are a quality reviewer for an automated task worker.")
        lines.append("")
        lines.append(f"TASK: {job.description or job.title}")
        lines.append("")
        lines.append("NODE OUTPUTS:")

        for node in nodes:
            raw_output = node_outputs.get(node.id)
            if raw_output is None:
                output_summary = "(no output)"
            elif isinstance(raw_output, (dict, list)):
                summary = json.dumps(raw_output, ensure_ascii=False)
                output_summary = (
                    summary[:_OUTPUT_SUMMARY_MAX_CHARS] + "..."
                    if len(summary) > _OUTPUT_SUMMARY_MAX_CHARS
                    else summary
                )
            else:
                text = str(raw_output)
                output_summary = (
                    text[:_OUTPUT_SUMMARY_MAX_CHARS] + "..."
                    if len(text) > _OUTPUT_SUMMARY_MAX_CHARS
                    else text
                )

            lines.append(
                f"Node {node.sequence}: {node.title or '(untitled)'}\n"
                f"  Status: {node.status}\n"
                f"  Expected: {node.expected_output or '(not specified)'}\n"
                f"  Produced: {output_summary}"
            )

        lines.append("")
        lines.append("REVIEW RUBRIC:")
        lines.append("1. Completeness: Did the system produce everything requested?")
        lines.append("2. Accuracy: Are facts, numbers, and references correct?")
        lines.append("3. Formatting: Does output match the expected format and style?")
        lines.append(
            "4. Instruction adherence: Did the system follow the original instructions?"
        )
        lines.append("")
        lines.append(
            'Respond with JSON (no markdown fences):\n'
            '{\n'
            '  "score": 0.0-1.0,\n'
            '  "passed": true/false,\n'
            '  "issues": [\n'
            '    {\n'
            '      "severity": "critical|warning|info",\n'
            '      "category": "completeness|accuracy|formatting|adherence",\n'
            '      "description": "...",\n'
            '      "node_sequence": N,\n'
            '      "suggestion": "..."\n'
            '    }\n'
            '  ],\n'
            '  "summary": "one-line assessment"\n'
            '}'
        )

        return "\n".join(lines)

    def _parse_tier2_response(
        self,
        raw: str,
        nodes: list[Node],
    ) -> tuple[list[ReviewIssue], float]:
        """Parse Gemini's JSON response into (issues, score).

        Tolerant: strips markdown fences, handles partial JSON.
        Returns ([], 1.0) if parsing completely fails.
        """
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            # Try to salvage a score via regex
            score_match = re.search(r'"score"\s*:\s*([0-9.]+)', cleaned)
            score = float(score_match.group(1)) if score_match else 1.0
            score = max(0.0, min(1.0, score))
            logger.warning("Could not parse Tier 2 JSON response; using score=%.2f", score)
            return [], score

        score = float(data.get("score", 1.0))
        score = max(0.0, min(1.0, score))

        # Build a seq -> node_id lookup
        seq_to_id: dict[int, str] = {n.sequence: n.id for n in nodes}

        issues: list[ReviewIssue] = []
        for raw_issue in data.get("issues", []):
            if not isinstance(raw_issue, dict):
                continue

            seq = raw_issue.get("node_sequence")
            node_id = seq_to_id.get(int(seq)) if seq is not None else None

            severity = raw_issue.get("severity", "info")
            if severity not in {"critical", "warning", "info"}:
                severity = "info"

            category = raw_issue.get("category", "completeness")
            if category not in {"completeness", "accuracy", "formatting", "adherence"}:
                category = "completeness"

            description = str(raw_issue.get("description", "")).strip()
            suggestion = raw_issue.get("suggestion")
            if suggestion:
                suggestion = str(suggestion).strip() or None

            if not description:
                continue

            issues.append(ReviewIssue(
                tier=2,
                severity=severity,
                category=category,
                description=description,
                node_id=node_id,
                suggestion=suggestion,
            ))

        return issues, score

    # ------------------------------------------------------------------
    # Tier 3 — Human review flag
    # ------------------------------------------------------------------

    def _tier3_human_flag(
        self,
        job: Job,
        tier1_issues: list[ReviewIssue],
        tier2_issues: list[ReviewIssue],
        tier2_score: float,
    ) -> bool:
        """Determine whether the job output needs human review.

        Returns True if any of:
        - Any Tier 1 critical issue exists
        - Tier 2 score < _TIER2_HUMAN_FLAG_THRESHOLD (0.7)
        - Job is high-priority (priority >= 8 on a 1-10 scale)
        - This is the last review attempt (review_count >= max_reviews - 1)
        """
        # Tier 1 critical issues escalate immediately
        if any(i.severity == "critical" for i in tier1_issues):
            logger.info(
                "Human flag: Tier 1 critical issue(s) on job %s", job.id
            )
            return True

        # Tier 2 score below threshold
        if tier2_score < _TIER2_HUMAN_FLAG_THRESHOLD:
            logger.info(
                "Human flag: Tier 2 score %.2f below threshold for job %s",
                tier2_score,
                job.id,
            )
            return True

        # High-priority jobs get human eyes regardless
        if job.priority >= 8:
            logger.info(
                "Human flag: high-priority job %s (priority=%d)", job.id, job.priority
            )
            return True

        # Last review attempt — escalate rather than loop forever
        if job.review_count >= job.max_reviews - 1:
            logger.info(
                "Human flag: last review attempt for job %s "
                "(review_count=%d, max_reviews=%d)",
                job.id,
                job.review_count,
                job.max_reviews,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Feedback builder
    # ------------------------------------------------------------------

    def _build_feedback(
        self,
        passed: bool,
        score: float,
        tier1_issues: list[ReviewIssue],
        tier2_issues: list[ReviewIssue],
        needs_human_review: bool,
    ) -> str:
        """Compose a human-readable feedback string for the job runner."""
        lines: list[str] = []

        status_word = "PASSED" if passed else "FAILED"
        lines.append(f"Review {status_word} (score={score:.2f})")

        if needs_human_review:
            lines.append("-> Flagged for human review.")

        criticals = [i for i in tier1_issues if i.severity == "critical"]
        warnings = [i for i in tier1_issues if i.severity == "warning"]

        if criticals:
            lines.append(f"Tier 1 critical ({len(criticals)}):")
            for issue in criticals[:5]:  # cap to avoid absurdly long feedback
                lines.append(f"  - [{issue.category}] {issue.description}")
            if len(criticals) > 5:
                lines.append(f"  ... and {len(criticals) - 5} more.")

        if warnings:
            lines.append(f"Tier 1 warnings ({len(warnings)}):")
            for issue in warnings[:3]:
                lines.append(f"  - [{issue.category}] {issue.description}")

        if tier2_issues:
            t2_crits = [i for i in tier2_issues if i.severity == "critical"]
            t2_warns = [i for i in tier2_issues if i.severity == "warning"]
            if t2_crits:
                lines.append(f"Tier 2 critical ({len(t2_crits)}):")
                for issue in t2_crits[:5]:
                    lines.append(f"  - [{issue.category}] {issue.description}")
            if t2_warns:
                lines.append(f"Tier 2 warnings ({len(t2_warns)}):")
                for issue in t2_warns[:3]:
                    lines.append(f"  - [{issue.category}] {issue.description}")

        if passed and not tier1_issues and not tier2_issues:
            lines.append("All checks passed with no issues.")

        return "\n".join(lines)
