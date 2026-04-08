"""
Error Handling & Degradation Strategy — LocalMind enterprise task worker.

Every error that surfaces in the job pipeline is classified into an ErrorCategory,
mapped to a default ErrorResponse (retry schedule, backoff, fallback strategy, and
user notification template), and then resolved to a concrete next action by the
DegradationCascade at runtime.

Design constraints:
- stdlib only (enum, dataclasses, logging).
- httpx is an optional runtime dependency — we detect its exceptions by class-name
  string comparison to avoid a hard import.
- All public interfaces are fully type-annotated.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("localmind.core.error_strategy")


# ---------------------------------------------------------------------------
# ErrorCategory
# ---------------------------------------------------------------------------

class ErrorCategory(Enum):
    """Canonical classification of every error that can occur in the pipeline."""

    TRANSIENT = "transient"
    """Network timeout, Ollama busy, temporary disk full — safe to retry."""

    PERMANENT = "permanent"
    """Invalid input, unsupported format, corrupted file — retrying won't help."""

    INJECTION_DETECTED = "injection"
    """Prompt-guard flagged the model output — halt and quarantine immediately."""

    TIMEOUT = "timeout"
    """A pipeline node exceeded its configured timeout_sec limit."""

    RESOURCE = "resource"
    """OOM, VRAM exhausted, disk full — need to free resources before retrying."""

    CONTRACT = "contract"
    """Node output does not satisfy output_schema_json — schema contract broken."""

    UPSTREAM = "upstream"
    """The previous node's output is unusable; retry the upstream node instead."""

    MODEL = "model"
    """Ollama crashed, LoRA is corrupt, or the requested model was not found."""


# ---------------------------------------------------------------------------
# ErrorResponse
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorResponse:
    """
    Policy record that governs how the pipeline handles one ErrorCategory.

    Attributes
    ----------
    category:
        The ErrorCategory this response applies to.
    should_retry:
        Whether the pipeline should attempt a retry at all.
    max_retries:
        Maximum number of retry attempts before escalating to the fallback strategy.
    backoff_ms:
        Millisecond delays before each successive retry attempt.  The list may be
        shorter than max_retries; the last value is repeated if exhausted.
    fallback_strategy:
        Action taken once all retries are exhausted.  One of:
          "fallback_model"    — switch to a smaller / backup model
          "simplify_plan"     — ask the planner to emit a simpler sub-graph
          "partial_delivery"  — deliver whatever was completed so far
          "dead_letter"       — route the job to the dead-letter queue
          "halt_quarantine"   — stop processing and quarantine the payload
          None                — no automatic fallback (permanent / upstream errors)
    user_notification:
        Template string sent to the requester.  Use {job_id}, {node_id}, {error}
        as placeholders.
    """

    category: ErrorCategory
    should_retry: bool
    max_retries: int
    backoff_ms: list[int]
    fallback_strategy: Optional[str]
    user_notification: str


# ---------------------------------------------------------------------------
# Default policy table
# ---------------------------------------------------------------------------

ERROR_RESPONSES: dict[ErrorCategory, ErrorResponse] = {

    ErrorCategory.TRANSIENT: ErrorResponse(
        category=ErrorCategory.TRANSIENT,
        should_retry=True,
        max_retries=3,
        backoff_ms=[1_000, 5_000, 15_000],
        fallback_strategy="dead_letter",
        user_notification=(
            "Job {job_id} hit a transient error on node {node_id}.  "
            "Retrying automatically — no action needed."
        ),
    ),

    ErrorCategory.PERMANENT: ErrorResponse(
        category=ErrorCategory.PERMANENT,
        should_retry=False,
        max_retries=0,
        backoff_ms=[],
        fallback_strategy=None,
        user_notification=(
            "Job {job_id} failed permanently on node {node_id}: {error}.  "
            "Please review the input and resubmit."
        ),
    ),

    ErrorCategory.INJECTION_DETECTED: ErrorResponse(
        category=ErrorCategory.INJECTION_DETECTED,
        should_retry=False,
        max_retries=0,
        backoff_ms=[],
        fallback_strategy="halt_quarantine",
        user_notification=(
            "Job {job_id} was halted on node {node_id}: potential prompt injection "
            "detected.  The payload has been quarantined for review."
        ),
    ),

    ErrorCategory.TIMEOUT: ErrorResponse(
        category=ErrorCategory.TIMEOUT,
        should_retry=True,
        max_retries=1,
        backoff_ms=[0],           # retry immediately with a 2× timeout budget
        fallback_strategy="simplify_plan",
        user_notification=(
            "Job {job_id} timed out on node {node_id}.  "
            "Retrying with an extended timeout; will simplify the plan if it times out again."
        ),
    ),

    ErrorCategory.RESOURCE: ErrorResponse(
        category=ErrorCategory.RESOURCE,
        should_retry=True,
        max_retries=2,
        backoff_ms=[10_000, 30_000],
        fallback_strategy="fallback_model",
        user_notification=(
            "Job {job_id} hit a resource constraint on node {node_id} ({error}).  "
            "Waiting for capacity to free up before retrying."
        ),
    ),

    ErrorCategory.CONTRACT: ErrorResponse(
        category=ErrorCategory.CONTRACT,
        should_retry=True,
        max_retries=1,
        backoff_ms=[0],
        fallback_strategy="partial_delivery",
        user_notification=(
            "Job {job_id} produced an output that does not match the expected schema "
            "on node {node_id}.  Retrying with stricter formatting instructions."
        ),
    ),

    ErrorCategory.UPSTREAM: ErrorResponse(
        category=ErrorCategory.UPSTREAM,
        should_retry=False,
        max_retries=0,
        backoff_ms=[],
        fallback_strategy=None,
        user_notification=(
            "Job {job_id} cannot continue: node {node_id} received unusable output "
            "from a previous step.  The upstream node will be retried."
        ),
    ),

    ErrorCategory.MODEL: ErrorResponse(
        category=ErrorCategory.MODEL,
        should_retry=True,
        max_retries=2,
        backoff_ms=[5_000, 15_000],
        fallback_strategy="fallback_model",
        user_notification=(
            "Job {job_id} encountered a model error on node {node_id}: {error}.  "
            "Running a health check and switching to a fallback model if needed."
        ),
    ),
}


# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------

def classify_error(exc: Exception) -> ErrorCategory:
    """
    Map a raw exception to the closest ErrorCategory.

    httpx exceptions are matched by class-name string comparison so that httpx
    remains an optional dependency — the classifier never imports it directly.

    Parameters
    ----------
    exc:
        The exception raised somewhere in the pipeline.

    Returns
    -------
    ErrorCategory
        The category that best describes the exception.  Defaults to TRANSIENT
        when no more-specific match is found (safe: will be retried).
    """
    exc_type = type(exc)
    exc_type_name: str = exc_type.__name__
    exc_module: str = getattr(exc_type, "__module__", "") or ""

    # ── Timeout ──────────────────────────────────────────────────────────────
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        logger.debug("classify_error: TIMEOUT — %s", exc_type_name)
        return ErrorCategory.TIMEOUT

    # httpx.ReadTimeout / httpx.ConnectTimeout / httpx.TimeoutException
    if "httpx" in exc_module and "Timeout" in exc_type_name:
        logger.debug("classify_error: TIMEOUT (httpx) — %s", exc_type_name)
        return ErrorCategory.TIMEOUT

    # ── Transient (network / connectivity) ───────────────────────────────────
    if isinstance(exc, ConnectionError):
        logger.debug("classify_error: TRANSIENT — %s", exc_type_name)
        return ErrorCategory.TRANSIENT

    # httpx.ConnectError, httpx.RemoteProtocolError, httpx.NetworkError …
    if "httpx" in exc_module and exc_type_name in (
        "ConnectError",
        "RemoteProtocolError",
        "NetworkError",
        "HTTPStatusError",
        "RequestError",
    ):
        logger.debug("classify_error: TRANSIENT (httpx) — %s", exc_type_name)
        return ErrorCategory.TRANSIENT

    # ── Resource ─────────────────────────────────────────────────────────────
    if isinstance(exc, MemoryError):
        logger.debug("classify_error: RESOURCE — %s", exc_type_name)
        return ErrorCategory.RESOURCE

    # ── Permanent ────────────────────────────────────────────────────────────
    if isinstance(exc, (FileNotFoundError, ValueError, TypeError, UnicodeDecodeError)):
        logger.debug("classify_error: PERMANENT — %s", exc_type_name)
        return ErrorCategory.PERMANENT

    # ── Default: safe TRANSIENT so the pipeline can retry ────────────────────
    logger.debug(
        "classify_error: TRANSIENT (default) — unrecognised exception %s.%s",
        exc_module,
        exc_type_name,
    )
    return ErrorCategory.TRANSIENT


# ---------------------------------------------------------------------------
# DegradationCascade
# ---------------------------------------------------------------------------

# Maps each category to the ordered sequence of actions the pipeline takes.
# Index 0 = first retry action, …, last entry = terminal action.
_CASCADE_ACTIONS: dict[ErrorCategory, list[str]] = {
    ErrorCategory.TRANSIENT:         ["retry", "retry", "retry", "dead_letter"],
    ErrorCategory.PERMANENT:         ["dead_letter"],
    ErrorCategory.INJECTION_DETECTED:["dead_letter"],
    ErrorCategory.TIMEOUT:           ["retry", "simplify"],
    ErrorCategory.RESOURCE:          ["retry", "retry", "fallback_model"],
    ErrorCategory.CONTRACT:          ["retry", "partial_delivery"],
    ErrorCategory.UPSTREAM:          ["dead_letter"],
    ErrorCategory.MODEL:             ["retry", "retry", "fallback_model"],
}


class DegradationCascade:
    """
    Resolves the concrete next action for a given category and attempt number.

    The cascade is stateless: it purely maps (category, attempt) → action string.
    The job runner is responsible for tracking attempt counts and persisting state
    to node_attempts / dead_letters.

    Usage
    -----
    ::

        cascade = DegradationCascade()
        response = cascade.get_response(ErrorCategory.TRANSIENT)
        action = cascade.next_action(ErrorCategory.TRANSIENT, attempt=0)  # "retry"
        action = cascade.next_action(ErrorCategory.TRANSIENT, attempt=3)  # "dead_letter"
    """

    def get_response(self, category: ErrorCategory) -> ErrorResponse:
        """
        Return the default ErrorResponse for the given category.

        Parameters
        ----------
        category:
            The ErrorCategory to look up.

        Returns
        -------
        ErrorResponse
            The policy record from ERROR_RESPONSES.

        Raises
        ------
        KeyError
            If the category has no entry in ERROR_RESPONSES (should never happen
            for well-formed enum values).
        """
        response = ERROR_RESPONSES[category]
        logger.debug(
            "get_response: category=%s retry=%s max_retries=%d fallback=%s",
            category.value,
            response.should_retry,
            response.max_retries,
            response.fallback_strategy,
        )
        return response

    def next_action(self, category: ErrorCategory, attempt: int) -> str:
        """
        Determine the next pipeline action for a given error category and attempt number.

        Parameters
        ----------
        category:
            The ErrorCategory that was raised.
        attempt:
            Zero-based attempt index (0 = first failure, 1 = after first retry, …).

        Returns
        -------
        str
            One of:
            - ``"retry"``           — schedule another attempt (respecting backoff_ms)
            - ``"fallback_model"``  — switch to an alternative Ollama model
            - ``"simplify"``        — ask the planner to emit a simpler node sub-graph
            - ``"partial_delivery"``— deliver completed artefacts and mark job partial
            - ``"dead_letter"``     — move to dead-letter queue and notify operator
        """
        cascade = _CASCADE_ACTIONS.get(category, ["dead_letter"])

        if attempt < len(cascade):
            action = cascade[attempt]
        else:
            # Past the end of the defined cascade — always dead-letter.
            action = "dead_letter"

        logger.info(
            "next_action: category=%s attempt=%d -> %s",
            category.value,
            attempt,
            action,
        )
        return action

    def backoff_for(self, category: ErrorCategory, attempt: int) -> int:
        """
        Return the backoff delay in milliseconds before the given retry attempt.

        If the attempt index is past the end of the configured backoff_ms list,
        the last configured value is repeated (exponential-cap pattern).

        Parameters
        ----------
        category:
            The ErrorCategory driving the backoff lookup.
        attempt:
            Zero-based attempt index.

        Returns
        -------
        int
            Milliseconds to wait before dispatching the next attempt.
            Returns 0 when no backoff is configured or retries are not applicable.
        """
        response = ERROR_RESPONSES[category]
        if not response.backoff_ms:
            return 0
        idx = min(attempt, len(response.backoff_ms) - 1)
        delay: int = response.backoff_ms[idx]
        logger.debug(
            "backoff_for: category=%s attempt=%d -> %d ms",
            category.value,
            attempt,
            delay,
        )
        return delay
