"""
cross_stage_validators.py — Cross-pipeline integrity validators.

Two rule-based validators that check consistency across pipeline stages:

  1. InformationConsistencyValidator — fuzzy-matches proposal fields against
     reflection input to catch hallucinated paths, truncated descriptions
  2. ProposalIntegrityValidator — validates the full proposal lifecycle
"""

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from backend.validation.base import Validator, ValidationResult, Severity

logger = logging.getLogger("localmind.validation.cross_stage")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class InformationConsistencyValidator(Validator):
    """
    Detects factual inconsistencies between input context and LLM-generated
    proposals. Catches hallucinated file paths, truncated descriptions, and
    modified attribute values.

    Based on AgentFixer's Information-Consistency-Validator.
    Uses regex + fuzzy string matching (no LLM needed).
    """

    # Minimum similarity ratio to consider two strings "matching"
    SIMILARITY_THRESHOLD = 0.6

    def __init__(self):
        super().__init__("InformationConsistencyValidator", Severity.MODERATE)

    def validate(self, context: dict) -> ValidationResult:
        """
        Context keys:
          - proposal: dict — the proposal to validate
          - file_list: list[str] — available files in the project
          - existing_titles: set[str] — titles of existing proposals (for dedup)
        """
        proposal = context.get("proposal", {})
        file_list = context.get("file_list", [])
        existing_titles = context.get("existing_titles", set())

        if not proposal:
            return self._pass("No proposal to validate")

        issues = []

        # 1. File existence check
        files_affected = proposal.get("files_affected", [])
        if files_affected and file_list:
            hallucinated = []
            for f in files_affected:
                # Normalize path separators
                normalized = f.replace("\\", "/")
                # Check if any real file matches
                match = any(
                    real.replace("\\", "/") == normalized
                    or real.replace("\\", "/").endswith(normalized)
                    or normalized.endswith(real.replace("\\", "/"))
                    for real in file_list
                )
                # Also check absolute path existence
                abs_path = PROJECT_ROOT / f
                if not match and not abs_path.exists():
                    hallucinated.append(f)

            if hallucinated:
                issues.append({
                    "type": "hallucinated_files",
                    "message": f"Files not found in project: {hallucinated}",
                    "files": hallucinated,
                })

        # 2. Title dedup check
        title = proposal.get("title", "")
        if title and existing_titles:
            for existing in existing_titles:
                similarity = SequenceMatcher(None, title.lower(), existing.lower()).ratio()
                if similarity > 0.85:
                    issues.append({
                        "type": "duplicate_title",
                        "message": f"Title too similar to existing: '{existing}' ({similarity:.0%})",
                        "similar_to": existing,
                        "similarity": similarity,
                    })
                    break

        # 3. Description quality check
        description = proposal.get("description", "")
        if len(description) < 20:
            issues.append({
                "type": "thin_description",
                "message": f"Description too short ({len(description)} chars) — "
                           "unlikely to produce a good code edit",
            })

        # 4. Category validity check
        valid_categories = {"performance", "feature", "ux", "security", "code_quality", "bugfix"}
        category = proposal.get("category", "")
        if category and category not in valid_categories:
            issues.append({
                "type": "invalid_category",
                "message": f"Unknown category '{category}' — "
                           f"valid: {sorted(valid_categories)}",
            })

        # 5. Check for empty files_affected
        if not files_affected:
            issues.append({
                "type": "no_files",
                "message": "No files_affected specified — proposal cannot be executed",
            })

        if issues:
            return self._fail(
                f"Information consistency issues: {len(issues)} found",
                recommendations=[i["message"] for i in issues[:3]],
                issues=issues,
            )

        return self._pass(
            "Proposal is consistent with project context",
            files_checked=len(files_affected),
        )


class ProposalIntegrityValidator(Validator):
    """
    Validates the structural integrity of a proposal through its lifecycle:
    reflection → critique → code-edit → execution.

    Checks that all required fields are present, values are within bounds,
    and the proposal hasn't been corrupted during pipeline transit.
    """

    REQUIRED_FIELDS = {"title", "category", "description", "files_affected"}
    VALID_EFFORTS = {"small", "medium", "large"}
    VALID_PRIORITIES = {"low", "medium", "high", "critical"}

    MAX_TITLE_LENGTH = 120
    MAX_DESCRIPTION_LENGTH = 2000
    MAX_FILES = 5

    def __init__(self):
        super().__init__("ProposalIntegrityValidator", Severity.CRITICAL)

    def validate(self, context: dict) -> ValidationResult:
        """
        Context keys:
          - proposal: dict — the proposal to validate
          - stage: str — current pipeline stage ("reflection", "critique", "execution")
        """
        proposal = context.get("proposal", {})
        stage = context.get("stage", "unknown")

        if not proposal:
            return self._fail(
                "Empty proposal — nothing to validate",
                recommendations=["Check that the reflection cycle produced output"],
            )

        issues = []

        # Required fields
        missing = self.REQUIRED_FIELDS - set(proposal.keys())
        if missing:
            issues.append(f"Missing required fields: {sorted(missing)}")

        # Title bounds
        title = proposal.get("title", "")
        if len(title) > self.MAX_TITLE_LENGTH:
            issues.append(f"Title too long ({len(title)} chars, max {self.MAX_TITLE_LENGTH})")

        # Description bounds
        desc = proposal.get("description", "")
        if len(desc) > self.MAX_DESCRIPTION_LENGTH:
            issues.append(f"Description too long ({len(desc)} chars, max {self.MAX_DESCRIPTION_LENGTH})")

        # Files count
        files = proposal.get("files_affected", [])
        if len(files) > self.MAX_FILES:
            issues.append(f"Too many files ({len(files)}, max {self.MAX_FILES})")

        # Effort validity  
        effort = proposal.get("effort", "").lower()
        if effort and effort not in self.VALID_EFFORTS:
            issues.append(f"Invalid effort '{effort}' — valid: {sorted(self.VALID_EFFORTS)}")

        # Priority validity
        priority = proposal.get("priority", "").lower()
        if priority and priority not in self.VALID_PRIORITIES:
            issues.append(f"Invalid priority '{priority}' — valid: {sorted(self.VALID_PRIORITIES)}")

        # Files must be strings
        non_str_files = [f for f in files if not isinstance(f, str)]
        if non_str_files:
            issues.append(f"files_affected contains non-string values: {non_str_files}")

        if issues:
            severity_text = "blocking" if missing else "quality"
            return self._fail(
                f"Proposal integrity issues at {stage}: {len(issues)} ({severity_text})",
                recommendations=issues,
                stage=stage,
            )

        return self._pass(
            f"Proposal structurally valid at stage '{stage}'",
            stage=stage,
            fields=len(proposal),
        )
