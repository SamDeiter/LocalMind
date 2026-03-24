"""
proposals.py — Proposal management for LocalMind Autonomy Engine
================================================================
Handles CRUD, deduplication, approval, and failed-title tracking.

Extracted from autonomy.py to keep files lean and editable.
"""

import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.autonomy.proposals")

PROPOSALS_DIR = Path.home() / "LocalMind_Workspace" / "proposals"
ARCHIVE_DIR = Path.home() / "LocalMind_Workspace" / "proposals_archive"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

MAX_RETRIES = 5  # Max times a proposal can be retried before permanent blacklist

# Directories to skip when validating file existence
_SKIP_DIRS = {"venv", "node_modules", "__pycache__", ".git", "memory_db", "browser_recordings"}


def validate_files_exist(files_affected: list[str]) -> list[str]:
    """Validate that proposed files actually exist in the project.

    Returns the subset of files that exist on disk.
    Filters out hallucinated paths the AI invented.
    """
    if not files_affected:
        return []

    valid = []
    for rel_path in files_affected:
        # Reject paths traversing into blocked dirs
        parts = Path(rel_path).parts
        if any(part in _SKIP_DIRS for part in parts):
            logger.info(f"Rejected file in blocked dir: {rel_path}")
            continue

        target = (PROJECT_ROOT / rel_path).resolve()

        # Security: ensure path doesn't escape project root
        if not str(target).startswith(str(PROJECT_ROOT)):
            logger.warning(f"Rejected path escaping project: {rel_path}")
            continue

        if target.exists() and target.is_file():
            valid.append(rel_path)
        else:
            logger.info(f"Rejected hallucinated file: {rel_path}")

    return valid

# Synonym groups for dedup normalization
_SYNONYM_GROUPS = [
    {"improve", "enhance", "refine", "upgrade", "optimize", "boost"},
    {"add", "implement", "introduce", "create", "include"},
    {"error", "exception", "fault", "failure"},
    {"handling", "management", "processing"},
    {"refactor", "restructure", "reorganize", "rework", "clean"},
    {"fix", "resolve", "repair", "patch", "correct"},
]

# Build lookup: word -> canonical form (first word in its group)
_SYNONYM_MAP: dict[str, str] = {}
for group in _SYNONYM_GROUPS:
    canonical = sorted(group)[0]  # alphabetically first as canonical
    for word in group:
        _SYNONYM_MAP[word] = canonical


def _normalize_title(title: str) -> set[str]:
    """Normalize a title into a set of canonical words for dedup comparison."""
    words = set(title.lower().split())
    # Remove common filler words
    filler = {"in", "the", "a", "an", "for", "of", "to", "and", "with", "on", "is", "by"}
    words -= filler
    # Map synonyms to canonical forms
    return {_SYNONYM_MAP.get(w, w) for w in words}


class ProposalManager:
    """Manages proposal lifecycle: create, dedup, approve, deny, retry."""

    def __init__(self):
        self._failed_titles: set[str] = set()
        # Load failed titles from existing proposals on startup
        self._load_failed_titles()

    def _load_failed_titles(self):
        """Load failed proposal titles from disk on startup."""
        if not PROPOSALS_DIR.exists():
            return
        for f in PROPOSALS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("status") == "failed":
                    self._failed_titles.add(data.get("title", ""))
            except Exception:
                continue

    def is_duplicate(self, new_title: str) -> bool:
        """Check if a similar proposal already exists.

        Uses normalized word-set overlap with synonym mapping
        to catch rephrasings like 'Improve Error Handling' ≈ 'Enhance Exception Management'.
        """
        new_words = _normalize_title(new_title)
        if not new_words:
            return False

        # Check against proposals on disk
        if PROPOSALS_DIR.exists():
            for f in PROPOSALS_DIR.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    existing_title = data.get("title", "")
                    existing_words = _normalize_title(existing_title)
                    if not existing_words:
                        continue
                    overlap = len(new_words & existing_words)
                    total = len(new_words | existing_words)
                    similarity = overlap / total if total > 0 else 0
                    if similarity > 0.55:
                        logger.info(f"Duplicate detected: \"{new_title}\" ≈ \"{existing_title}\" ({similarity:.0%})")
                        return True
                except Exception:
                    continue

        # Check against failed titles in memory
        return self._is_failed_title(new_title)

    def _is_failed_title(self, new_title: str) -> bool:
        """Check against failed titles in memory."""
        new_words = _normalize_title(new_title)
        for failed_title in self._failed_titles:
            failed_words = _normalize_title(failed_title)
            if not new_words or not failed_words:
                continue
            overlap = len(new_words & failed_words)
            total = len(new_words | failed_words)
            similarity = overlap / total if total > 0 else 0
            if similarity > 0.45:
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
            "retry_count": 0,
        }

        # Normalize files_affected to list
        if isinstance(full_proposal["files_affected"], str):
            full_proposal["files_affected"] = [
                f.strip() for f in full_proposal["files_affected"].split(",") if f.strip()
            ]

        # Guardrail 1: Validate files exist on disk — reject hallucinated paths
        original_files = full_proposal["files_affected"]
        valid_files = validate_files_exist(original_files)
        if original_files and not valid_files:
            rejected = [f for f in original_files if f not in valid_files]
            logger.info(f"Rejected proposal (all files hallucinated): '{title}' — {rejected}")
            if log_fn:
                log_fn("proposal_rejected_bad_files", {"title": title, "hallucinated": rejected})
            if emit_activity:
                emit_activity("info", f"Rejected (invalid files): {title} — {rejected}")
            return None
        full_proposal["files_affected"] = valid_files

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
                    data["retry_count"] = retry_count + 1  # Increment on retry, not on failure
                    data["status_changed_at"] = time.time()
                    data["status_changed_at_human"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    if emit_activity:
                        emit_activity("proposal_retried",
                                      f"Retrying ({retry_count + 1}/{MAX_RETRIES}): {data.get('title')}",
                                      proposal_id=proposal_id)
                    return data
            except Exception:
                continue
        return None

    def mark_failed(self, proposal: dict, error: str, filepath=None):
        """Mark a proposal as failed and track its title.

        Note: retry_count is NOT incremented here — it is only incremented
        when the user explicitly retries via retry(). This prevents the count
        from inflating on the first execution attempt.
        """
        proposal["status"] = "failed"
        proposal["error"] = error
        self._failed_titles.add(proposal.get("title", ""))
        if filepath:
            filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

    def cleanup_stale(self) -> dict:
        """Archive old denied/completed proposals and remove exhausted failed ones.

        Returns a summary of what was cleaned up.
        """
        if not PROPOSALS_DIR.exists():
            return {"archived": 0, "deleted": 0}

        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        archived = 0
        deleted = 0

        for f in list(PROPOSALS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                age_hours = (now - data.get("created_at", now)) / 3600
                status = data.get("status", "")

                # Archive completed/denied proposals older than 24h
                if status in ("completed", "denied") and age_hours > 24:
                    dest = ARCHIVE_DIR / f.name
                    shutil.move(str(f), str(dest))
                    archived += 1

                # Delete exhausted failed proposals (max retries hit) older than 48h
                elif status == "failed" and age_hours > 48:
                    retry_count = data.get("retry_count", 0)
                    if retry_count >= MAX_RETRIES:
                        dest = ARCHIVE_DIR / f.name
                        shutil.move(str(f), str(dest))
                        deleted += 1

            except Exception:
                continue

        if archived or deleted:
            logger.info(f"🧹 Proposal cleanup: archived {archived}, removed {deleted} exhausted")

        return {"archived": archived, "deleted": deleted}

    def get_anti_repeat_titles(self) -> list[str]:
        """Get titles to include in anti-repeat prompt (recent + failed)."""
        recent = [p.get("title", "") for p in self.list_proposals()][-10:]
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
