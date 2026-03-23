"""
Meta-Critic: the Critique-Backtrack-Refine reconstruction loop.

Based on the Four-Phase Meta-Cognitive Architecture:
  Problem Definition -> Decomposition -> Reconstruction -> Final Answer

This module implements Phase 3 (Reconstruction) where the AI reviews
its own proposals through iterative self-correction before they're saved.

Flow:
  1. Critique  - Ask the AI to evaluate its own proposal
  2. Score     - Extract a confidence score (0.0-1.0) and concerns
  3. Backtrack - If confidence < threshold, reject with reason
  4. Refine    - If borderline (0.4-0.7), ask AI to fix issues and re-score
  5. Approve   - If confidence >= threshold, pass to proposal pipeline
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("autonomy.meta_critic")

WORKSPACE = Path.home() / "LocalMind_Workspace"
CALIBRATION_PATH = WORKSPACE / "calibration_log.jsonl"


@dataclass
class CritiqueResult:
    """Result of a meta-cognitive critique pass."""
    approved: bool
    confidence: float
    concerns: list = field(default_factory=list)
    refinement: Optional[dict] = None
    phase: str = "critique"  # critique | refine | backtrack
    reason: str = ""
    passes: int = 1


class MetaCritic:
    """Reviews proposals through a Critique-Backtrack-Refine loop."""

    # Threshold boundaries
    REJECT_BELOW = 0.35     # Auto-reject if confidence < this
    REFINE_BELOW = 0.65     # Try to refine if confidence < this
    APPROVE_ABOVE = 0.65    # Auto-approve if confidence >= this
    MAX_REFINE_PASSES = 2   # Max number of refine attempts

    def __init__(self, ollama_url: str, model: str, emit_activity=None):
        self.ollama_url = ollama_url
        self.model = model
        self._emit = emit_activity or (lambda *a, **kw: None)

    async def review(self, proposal: dict, file_list: list = None) -> CritiqueResult:
        """
        Full Critique-Backtrack-Refine loop on a proposal.
        Returns CritiqueResult with approval status.
        """
        title = proposal.get("title", "Untitled")
        self._emit("thinking",
                    f"Critiquing proposal: '{title}'",
                    thinking_type="critique_start",
                    proposal_title=title)

        # Phase 1: Critique
        critique = await self._critique(proposal, file_list)

        if critique.confidence < self.REJECT_BELOW:
            # Phase 3a: Backtrack - too low, reject immediately
            critique.phase = "backtrack"
            critique.approved = False
            critique.reason = (
                f"Confidence too low ({critique.confidence:.0%}): "
                f"{'; '.join(critique.concerns[:2])}"
            )

            self._emit("thinking",
                        f"Backtrack: Rejected '{title}' ({critique.confidence:.0%} confidence)",
                        thinking_type="backtrack",
                        concerns=critique.concerns,
                        confidence=critique.confidence)

            self._log_calibration(proposal, critique)
            return critique

        if critique.confidence < self.REFINE_BELOW:
            # Phase 2: Refine - borderline, try to improve
            self._emit("thinking",
                        f"Refining '{title}' ({critique.confidence:.0%} -> needs improvement)",
                        thinking_type="refine_start",
                        concerns=critique.concerns)

            for pass_num in range(self.MAX_REFINE_PASSES):
                refined = await self._refine(proposal, critique.concerns, file_list)
                if refined is None:
                    break

                # Re-critique the refined version
                proposal = refined
                critique = await self._critique(proposal, file_list)
                critique.passes += 1 + pass_num
                critique.refinement = refined

                if critique.confidence >= self.APPROVE_ABOVE:
                    break  # Good enough now

                self._emit("thinking",
                            f"Refine pass {pass_num + 2}: confidence {critique.confidence:.0%}",
                            thinking_type="refine_pass",
                            confidence=critique.confidence)

            # After refining, check if we improved enough
            if critique.confidence < self.REJECT_BELOW:
                critique.phase = "backtrack"
                critique.approved = False
                critique.reason = (
                    f"Still too low after {critique.passes} passes "
                    f"({critique.confidence:.0%})"
                )
                self._emit("thinking",
                            f"Backtrack after refine: '{title}' still {critique.confidence:.0%}",
                            thinking_type="backtrack_after_refine")
                self._log_calibration(proposal, critique)
                return critique

        # Phase 4: Approve
        critique.phase = "approve"
        critique.approved = True
        pass_label = "passes" if critique.passes > 1 else "pass"
        critique.reason = (
            f"Approved ({critique.confidence:.0%} confidence, "
            f"{critique.passes} {pass_label})"
        )

        self._emit("thinking",
                    f"Approved '{title}' ({critique.confidence:.0%} confidence "
                    f"after {critique.passes} {pass_label})",
                    thinking_type="critique_approved",
                    confidence=critique.confidence,
                    passes=critique.passes)

        self._log_calibration(proposal, critique)
        return critique

    async def _critique(self, proposal: dict, file_list: list = None) -> CritiqueResult:
        """Ask the AI to evaluate a proposal and return a confidence score."""
        title = proposal.get("title", "?")
        description = proposal.get("description", "?")
        files = proposal.get("files_affected", [])
        category = proposal.get("category", "?")

        available_files = ""
        if file_list:
            sample = file_list[:40]
            available_files = (
                "\nAVAILABLE FILES IN PROJECT:\n"
                + "\n".join(f"  - {f}" for f in sample)
            )

        prompt = (
            "You are a code review critic. Evaluate this AI-generated proposal.\n\n"
            f"PROPOSAL TITLE: {title}\n"
            f"CATEGORY: {category}\n"
            f"DESCRIPTION: {description}\n"
            f"FILES TO EDIT: {', '.join(files)}\n"
            f"{available_files}\n\n"
            "Evaluate on these criteria:\n"
            "1. FEASIBILITY: Can this edit actually be made with search-and-replace?\n"
            "2. SPECIFICITY: Is the description specific enough to generate exact code?\n"
            "3. FILE ACCURACY: Do the target files exist and are they the right ones?\n"
            "4. SCOPE: Is this a small, focused change (not trying to do too much)?\n"
            "5. VALUE: Will this actually improve the codebase meaningfully?\n\n"
            "Output a JSON object with:\n"
            '- confidence: float 0.0-1.0 (your confidence this will succeed)\n'
            '- concerns: list of strings (specific issues found, empty if none)\n'
            '- verdict: "approve" | "refine" | "reject"\n'
            "Only output JSON, nothing else."
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 200, "num_ctx": 2048},
                    },
                )

                if resp.status_code == 200:
                    text = resp.json().get("response", "").strip()
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()

                    data = json.loads(text)
                    return CritiqueResult(
                        approved=False,  # Not yet - review() decides
                        confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
                        concerns=data.get("concerns", []),
                    )

        except (httpx.HTTPError, json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Critique failed for '{title}': {e}")

        # If critique itself fails, return a neutral score
        return CritiqueResult(
            approved=False,
            confidence=0.5,
            concerns=["Critique model failed to respond - using default score"],
        )

    async def _refine(self, proposal: dict, concerns: list, file_list: list = None) -> Optional[dict]:
        """Ask the AI to fix the concerns in a proposal."""
        title = proposal.get("title", "?")
        concern_text = "\n".join(f"  - {c}" for c in concerns)

        prompt = (
            "You previously generated a code proposal that has some issues.\n\n"
            f"ORIGINAL PROPOSAL:\n{json.dumps(proposal, indent=2)}\n\n"
            f"CONCERNS:\n{concern_text}\n\n"
            "Fix these concerns and output an IMPROVED version of the proposal.\n"
            "Make the description more specific, fix file targets if wrong, "
            "reduce scope if too broad.\n\n"
            "Output only the improved JSON proposal with keys: "
            "title, category, description, files_affected, effort, priority.\n"
            "Only output JSON, nothing else."
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 400, "num_ctx": 2048},
                    },
                )

                if resp.status_code == 200:
                    text = resp.json().get("response", "").strip()
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()

                    refined = json.loads(text)
                    # Ensure required keys exist
                    if "title" in refined and "description" in refined:
                        return refined

        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Refine failed for '{title}': {e}")

        return None

    def _log_calibration(self, proposal: dict, critique: CritiqueResult):
        """Log predicted confidence for later calibration analysis."""
        entry = {
            "timestamp": time.time(),
            "title": proposal.get("title", "?"),
            "category": proposal.get("category", "?"),
            "predicted_confidence": critique.confidence,
            "verdict": critique.phase,
            "passes": critique.passes,
            "concerns": critique.concerns[:3],
            # actual_outcome will be filled in later by self_improver
            "actual_outcome": None,
        }
        try:
            CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CALIBRATION_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.warning(f"Failed to log calibration: {e}")
