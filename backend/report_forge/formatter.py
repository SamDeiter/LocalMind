"""
formatter.py - Structured Report Formatter (Phase F2)
=====================================================
Raw school interview notes -> MedGemma (local, FERPA-safe) -> structured report.

Pipeline:
  1. Resolve MedGemma via the existing model_router (slot: `local_medical`).
  2. Build a few-shot chat prompt from templates.FEW_SHOT_EXAMPLES.
  3. Call Ollama via the shared resilient streaming client so retries apply.
  4. Return {content, model, template_version, tokens_used}.

Contract (consumed by audit_log.py and drive_watcher.py):
    {
        "content":          str,   # the structured markdown report
        "model":            str,   # resolved Ollama model name (e.g., "medgemma:4b")
        "template_version": str,   # from templates.TEMPLATE_VERSION
        "tokens_used":      int,   # approximate tokens in the final response
    }
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.inference.streaming_client import stream_ollama_chat
from backend.model_router import MODELS
from backend.report_forge.templates import FEW_SHOT_EXAMPLES, TEMPLATE_VERSION

logger = logging.getLogger("localmind.report_forge.formatter")

# Slot name in model_router for MedGemma (kept as a module constant so the
# resolver lookup is easy to grep for).
MEDICAL_SLOT = "local_medical"

_SYSTEM_PROMPT = (
    "You are a special education case manager's assistant. Your job is to take "
    "messy, shorthand notes from school interviews, classroom observations, and "
    "evaluations and rewrite them as clean, professional, structured reports "
    "suitable for IEP meetings, re-evaluation reports, and parent communication.\n\n"
    "Rules:\n"
    "- Preserve EVERY factual detail from the notes (scores, dates, quotes, times).\n"
    "- Never invent facts, diagnoses, scores, or names that are not in the input.\n"
    "- Use clear section headings (Markdown `#` / `##`).\n"
    "- Keep clinical language when the notes contain it; keep plain language when they don't.\n"
    "- Write in third person, past tense for observed events.\n"
    "- If a follow-up action is mentioned, surface it in a 'Next Steps' section."
)


class MedGemmaUnavailableError(RuntimeError):
    """Raised when the medical slot model cannot be resolved or reached."""


def _resolve_medical_model() -> str:
    """Look up the MedGemma model name via the model router.

    We intentionally do NOT hardcode 'medgemma:4b' — router is the source of truth.
    """
    slot = MODELS.get(MEDICAL_SLOT)
    if not slot:
        raise MedGemmaUnavailableError(
            f"Model router has no slot named '{MEDICAL_SLOT}'. "
            f"Report Forge requires MedGemma — check backend/model_router.py."
        )
    name = slot.get("name")
    if not name:
        raise MedGemmaUnavailableError(
            f"Slot '{MEDICAL_SLOT}' has no 'name' field in model_router.MODELS."
        )
    return name


def _build_messages(raw_notes: str, source_metadata: Optional[dict]) -> list[dict]:
    """Build the chat prompt: system, then alternating few-shot turns, then real input."""
    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]

    for example in FEW_SHOT_EXAMPLES:
        ex_in = example.get("input", "")
        ex_out = example.get("output", "")
        if not ex_in or not ex_out:
            continue
        messages.append({"role": "user", "content": ex_in})
        messages.append({"role": "assistant", "content": ex_out})

    # Attach lightweight source metadata so MedGemma can reference it if helpful.
    # We keep the body as raw_notes so the model sees the notes verbatim.
    if source_metadata:
        meta_lines = []
        for k in ("source", "filename", "captured_at", "student_alias", "doc_type"):
            v = source_metadata.get(k)
            if v:
                meta_lines.append(f"- {k}: {v}")
        meta_block = ""
        if meta_lines:
            meta_block = "Source metadata:\n" + "\n".join(meta_lines) + "\n\n"
        user_body = f"{meta_block}Raw notes:\n{raw_notes}"
    else:
        user_body = raw_notes

    messages.append({"role": "user", "content": user_body})
    return messages


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Good enough for audit purposes."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class ReportFormatter:
    """Turns raw school-interview notes into a structured report via MedGemma."""

    def __init__(self, model_name: Optional[str] = None):
        # Allow override for tests; default resolves from the router.
        self._model_name = model_name or _resolve_medical_model()

    @property
    def model_name(self) -> str:
        return self._model_name

    async def format_notes(
        self,
        raw_notes: str,
        source_metadata: Optional[dict] = None,
    ) -> dict:
        """Format raw notes into a structured report.

        Args:
            raw_notes: the messy input from the interview/observation/evaluation.
            source_metadata: optional dict (filename, source=drive/upload, etc.)
                             forwarded into the prompt as context.

        Returns:
            {"content": str, "model": str, "template_version": str, "tokens_used": int}

        Raises:
            MedGemmaUnavailableError: if the local model cannot be reached.
            ValueError: if raw_notes is empty.
        """
        if not raw_notes or not raw_notes.strip():
            raise ValueError("raw_notes is empty — nothing to format.")

        messages = _build_messages(raw_notes, source_metadata)

        collected: list[str] = []
        saw_done = False
        last_error: Optional[str] = None

        try:
            async for event in stream_ollama_chat(
                messages=messages,
                model=self._model_name,
            ):
                etype = event.get("type")
                if etype == "token":
                    collected.append(event.get("content", ""))
                elif etype == "done":
                    saw_done = True
                    break
                elif etype == "error":
                    last_error = event.get("error") or "unknown streaming error"
                    break
                # 'warning' and other event types are informational — ignore.
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("MedGemma streaming failed")
            raise MedGemmaUnavailableError(
                f"Failed to reach MedGemma ({self._model_name}): {exc}"
            ) from exc

        content = "".join(collected).strip()

        if not saw_done and not content:
            raise MedGemmaUnavailableError(
                f"MedGemma ({self._model_name}) returned no output"
                + (f": {last_error}" if last_error else ".")
            )
        if last_error and not content:
            raise MedGemmaUnavailableError(
                f"MedGemma ({self._model_name}) errored: {last_error}"
            )

        return {
            "content": content,
            "model": self._model_name,
            "template_version": TEMPLATE_VERSION,
            "tokens_used": _estimate_tokens(content),
        }
