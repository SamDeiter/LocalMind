"""
proposals.py — Proposal management for LocalMind Autonomy Engine
================================================================
Handles CRUD, deduplication, approval, and failed-title tracking.

Extracted from autonomy.py to keep files lean and editable.
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.autonomy.proposals")

PROPOSALS_DIR = Path.home() / "LocalMind_Workspace" / "proposals"

MAX_RETRIES = 2  # Max times a proposal can be retried before permanent blacklist


class ProposalManager:
    """Manages proposal lifecycle: create, dedup, approve, deny, retry."""

    def __init__(self):
        self._failed_titles: set[str] = set()

    def is_duplicate(self, new_title: str) -> bool:
        """Check if a similar proposal already exists (Jaccard + failed-title check)."""
        if not PROPOSALS_DIR.exists():
            # Still check against failed titles
            return self._is_failed_title(new_title)

        new_words = set(new_title.lower().split())
        for f in PROPOSALS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                existing_title = data.get("title", "")
                existing_words = set(existing_title.lower().split())
                if not new_words or not existing_words:
                    continue
                overlap = len(new_words & existing_words)
                total = len(new_words | existing_words)
                similarity = overlap / total if total > 0 else 0
                if similarity > 0.70:
                    logger.info(f"Duplicate detected: \"{new_title}\" ≈ \"{existing_title}\" ({similarity:.0%})")
                    return True
            except Exception:
                continue

        return self._is_failed_title(new_title)

    def _is_failed_title(self, new_title: str) -> bool:
        """Check against failed titles in memory (lower threshold)."""
        new_words = set(new_title.lower().split())
        for failed_title in self._failed_titles:
            failed_words = set(failed_title.lower().split())
            if not new_words or not failed_words:
                continue
            overlap = len(new_words & failed_words)
            total = len(new_words | failed_words)
            similarity = overlap / total if total > 0 else 0
            if similarity > 0.50:
                logger.info(f"Blocked (similar to failed): \"{new_title}\" ≈ \"{failed_title}\" ({similarity:.0%})")
                return True
        return False

    def save(self, proposal: dict, mode: str = "supervised",
             auto_approve_risks: set = None, log_fn=None, emit_activity=None) -> Optional[dict]:
        """Save a proposal to disk. Returns the full proposal or None if duplicate."""
        PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

        title = proposal.get("title", "Untitled")
        if self.is_duplicate(title):
            if log_fn:
                log_fn("proposal_deduplicated", {"title": title})
            if emit_activity:
                emit_activity("info", f"Skipped duplicate proposal: {title}")
            return None

        full_proposal = {
            "id": str(uuid.uuid4())[:8],
            "title": proposal.get("title", "Untitled"),
            "category": proposal.get("category", "feature"),
            "description": proposal.get("description", ""),
            "files_affected": proposal.get("files_affected", []),
            "effort": proposal.get("effort", "medium"),
            "priority": proposal.get("priority", "medium"),
            "status": "proposed",
            "source": "autonomy_reflection",
            "created_at": time.time(),
            "created_at_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Normalize files_affected to list
        if isinstance(full_proposal["files_affected"], str):
            full_proposal["files_affected"] = [
                f.strip() for f in full_proposal["files_affected"].split(",") if f.strip()
            ]

        filepath = PROPOSALS_DIR / f"{full_proposal['id']}_{full_proposal['category']}.json"

        # In autonomous mode, auto-approve low/medium risk proposals
        risk = full_proposal.get("priority", "medium").lower()
        if auto_approve_risks and mode == "autonomous" and risk in auto_approve_risks:
            full_proposal["status"] = "approved"
            full_proposal["auto_approved"] = True
            full_proposal["status_changed_at"] = time.time()
            if log_fn:
                log_fn("proposal_auto_approved", {
                    "id": full_proposal["id"], "title": full_proposal["title"], "risk": risk
                })
            if emit_activity:
                emit_activity("auto_approved",
                              f"Auto-approved: {full_proposal['title']} (risk: {risk})",
                              proposal_id=full_proposal["id"])
            logger.info(f"🤖 Auto-approved proposal: {full_proposal['title']} (risk: {risk})")

        filepath.write_text(json.dumps(full_proposal, indent=2), encoding="utf-8")
        return full_proposal

    def list_proposals(self, status_filter: str = "all") -> list[dict]:
        """List all proposals, optionally filtered by status."""
        if not PROPOSALS_DIR.exists():
            return []

        proposals = []
        for f in sorted(PROPOSALS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if status_filter == "all" or data.get("status") == status_filter:
                    proposals.append(data)
            except Exception:
                continue
        return proposals

    def approve(self, proposal_id: str) -> Optional[dict]:
        """Mark a proposal as approved."""
        return self._update_status(proposal_id, "approved")

    def deny(self, proposal_id: str) -> Optional[dict]:
        """Mark a proposal as denied."""
        return self._update_status(proposal_id, "denied")

    def retry(self, proposal_id: str, emit_activity=None) -> Optional[dict]:
        """Reset a failed proposal to approved status for re-execution."""
        if not PROPOSALS_DIR.exists():
            return None

        for f in PROPOSALS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("id") == proposal_id:
                    retry_count = data.get("retry_count", 0)
                    if retry_count >= MAX_RETRIES:
                        logger.warning(f"Proposal '{data.get('title')}' hit max retries ({MAX_RETRIES})")
                        if emit_activity:
                            emit_activity("error", f"Cannot retry: '{data.get('title')}' failed {retry_count} times (max: {MAX_RETRIES})")
                        return None
                    data["status"] = "approved"
                    data["error"] = None
                    data["status_changed_at"] = time.time()
                    data["status_changed_at_human"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    if emit_activity:
                        emit_activity("proposal_retried",
                                      f"Retrying: {data.get('title')}",
                                      proposal_id=proposal_id)
                    return data
            except Exception:
                continue
        return None

    def mark_failed(self, proposal: dict, error: str, filepath=None):
        """Mark a proposal as failed and track its title."""
        proposal["status"] = "failed"
        proposal["retry_count"] = proposal.get("retry_count", 0) + 1
        proposal["error"] = error
        self._failed_titles.add(proposal.get("title", ""))
        if filepath:
            filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

    def get_anti_repeat_titles(self) -> list[str]:
        """Get titles to include in anti-repeat prompt (recent + failed)."""
        recent = [p.get("title", "") for p in self.list_proposals()][-5:]
        return list(set(recent) | self._failed_titles)

    def _update_status(self, proposal_id: str, new_status: str) -> Optional[dict]:
        """Update a proposal's status by ID."""
        if not PROPOSALS_DIR.exists():
            return None

        for f in PROPOSALS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("id") == proposal_id:
                    data["status"] = new_status
                    data["status_changed_at"] = time.time()
                    data["status_changed_at_human"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    return data
            except Exception:
                continue
        return None
