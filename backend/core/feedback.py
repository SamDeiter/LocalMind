"""
User Feedback & Correction system -- LocalMind enterprise task worker.

Captures user corrections to job outputs, promotes generalized learnings
into the memory system, and extracts corrections from re-uploaded files.

Four service classes:
    CorrectionManager  -- submit, review, and list corrections
    MemoryPromoter     -- promote corrections into typed memories
    FileDiffCorrector  -- extract corrections from re-uploaded file diffs
    CorrectionStats    -- analytics on correction frequency and types

Tables (created by backend/core/schema.py):
    corrections
"""

import difflib
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config import DB_PATH

logger = logging.getLogger("localmind.core.feedback")

# Valid correction types
VALID_CORRECTION_TYPES = {
    "factual",
    "formatting",
    "structural",
    "instruction_misunderstand",
}

# Valid review actions
VALID_REVIEW_ACTIONS = {"promote", "dismiss", "duplicate"}

# Valid correction statuses
VALID_STATUSES = {"pending", "promoted", "dismissed", "duplicate"}

# Valid memory types for promotion
VALID_MEMORY_TYPES = {
    "org_style_rule",
    "template_correction",
    "tool_quirk",
    "source_trust",
    "user_preference",
    "general",
}

# Valid scope types for memories
VALID_SCOPE_TYPES = {"workspace", "org", "user", "global"}


# -- Helpers -----------------------------------------------------------------

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


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    """Convert a sqlite3.Row to a plain dict, or return None."""
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    """Convert a list of sqlite3.Row objects to a list of dicts."""
    return [dict(r) for r in rows]


def _ensure_corrections_schema(conn: sqlite3.Connection) -> None:
    """Ensure the corrections table has the review columns.

    The schema.py table may not include reviewed_by/reviewed_at if it was
    created before this module was added.  This adds them idempotently.
    """
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(corrections)").fetchall()
    }
    if "reviewed_by" not in existing:
        conn.execute(
            "ALTER TABLE corrections ADD COLUMN reviewed_by TEXT"
        )
    if "reviewed_at" not in existing:
        conn.execute(
            "ALTER TABLE corrections ADD COLUMN reviewed_at TEXT"
        )


def _extract_text_from_file(file_path: Path) -> str:
    """Extract text content from a file for diffing.

    Supports plain text files.  Binary formats (docx, pptx, xlsx, pdf) are
    read as raw bytes decoded with errors replaced -- a future enhancement
    can plug in format-specific extractors.

    Args:
        file_path: Path to the file to extract text from.

    Returns:
        The extracted text content.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file cannot be read.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Fall back to lossy decoding for binary-ish files
        return file_path.read_bytes().decode("utf-8", errors="replace")


# -- CorrectionManager -------------------------------------------------------

class CorrectionManager:
    """Capture and manage user corrections to job outputs.

    Corrections track what was wrong with a generated artifact and what
    the user expected instead.  Each correction can be reviewed (promoted
    to a memory, dismissed, or marked duplicate) by a reviewer.
    """

    def submit_correction(
        self,
        job_id: str,
        submitted_by: str,
        correction_type: str,
        target_location: Optional[str] = None,
        original_value: Optional[str] = None,
        corrected_value: Optional[str] = None,
        user_instruction: Optional[str] = None,
        artifact_version_id: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> str:
        """Submit a new correction against a job output.

        Args:
            job_id: The job whose output is being corrected.
            submitted_by: The user ID submitting the correction.
            correction_type: One of factual, formatting, structural,
                instruction_misunderstand.
            target_location: Optional location descriptor such as
                "slide:rId4:shape:Title1", "cell:B15", or "paragraph:3".
            original_value: The original (incorrect) value, if applicable.
            corrected_value: The corrected value the user expects.
            user_instruction: Free-text instruction from the user describing
                what should change.
            artifact_version_id: Optional artifact version this correction
                applies to.
            node_id: Optional pipeline node ID this correction relates to.

        Returns:
            The newly created correction ID (hex UUID).

        Raises:
            ValueError: If correction_type is not a recognised value.
        """
        if correction_type not in VALID_CORRECTION_TYPES:
            raise ValueError(
                f"Invalid correction_type={correction_type!r}. "
                f"Must be one of {sorted(VALID_CORRECTION_TYPES)}"
            )

        correction_id = _new_id()
        now = _now()

        conn = _get_conn()
        try:
            _ensure_corrections_schema(conn)
            conn.execute(
                """
                INSERT INTO corrections
                    (id, job_id, artifact_version_id, node_id,
                     correction_type, target_location, original_value,
                     corrected_value, user_instruction, submitted_by,
                     submitted_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    correction_id,
                    job_id,
                    artifact_version_id,
                    node_id,
                    correction_type,
                    target_location,
                    original_value,
                    corrected_value,
                    user_instruction,
                    submitted_by,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Submitted correction: id=%s job=%s type=%s by=%s",
            correction_id, job_id, correction_type, submitted_by,
        )
        return correction_id

    def get_correction(self, correction_id: str) -> Optional[dict]:
        """Return a correction by ID, or None if not found.

        Args:
            correction_id: The correction ID to look up.

        Returns:
            A dict with all corrections columns, or None.
        """
        conn = _get_conn()
        try:
            _ensure_corrections_schema(conn)
            row = conn.execute(
                "SELECT * FROM corrections WHERE id = ?",
                (correction_id,),
            ).fetchone()
        finally:
            conn.close()

        return _row_to_dict(row)

    def list_corrections(
        self,
        job_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """List corrections, optionally filtered by job and/or status.

        Args:
            job_id: If provided, only return corrections for this job.
            status: If provided, only return corrections with this status.

        Returns:
            A list of correction dicts, ordered by submission time (newest
            first).

        Raises:
            ValueError: If status is not a recognised value.
        """
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status={status!r}. "
                f"Must be one of {sorted(VALID_STATUSES)}"
            )

        clauses: list[str] = []
        params: list[str] = []

        if job_id is not None:
            clauses.append("job_id = ?")
            params.append(job_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM corrections {where} ORDER BY submitted_at DESC"

        conn = _get_conn()
        try:
            _ensure_corrections_schema(conn)
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        return _rows_to_dicts(rows)

    def review_correction(
        self,
        correction_id: str,
        reviewer_id: str,
        action: str,
    ) -> None:
        """Review a correction and update its status.

        Args:
            correction_id: The correction to review.
            reviewer_id: The user ID performing the review.
            action: One of 'promote', 'dismiss', 'duplicate'.

        Raises:
            ValueError: If action is not recognised or the correction does
                not exist.
        """
        if action not in VALID_REVIEW_ACTIONS:
            raise ValueError(
                f"Invalid action={action!r}. "
                f"Must be one of {sorted(VALID_REVIEW_ACTIONS)}"
            )

        # Map action to status
        status_map = {
            "promote": "promoted",
            "dismiss": "dismissed",
            "duplicate": "duplicate",
        }
        new_status = status_map[action]
        now = _now()

        conn = _get_conn()
        try:
            _ensure_corrections_schema(conn)

            row = conn.execute(
                "SELECT id, status FROM corrections WHERE id = ?",
                (correction_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Correction not found: {correction_id}")

            conn.execute(
                """
                UPDATE corrections
                SET status = ?, reviewed_by = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (new_status, reviewer_id, now, correction_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Reviewed correction: id=%s action=%s by=%s",
            correction_id, action, reviewer_id,
        )

    def get_pending_corrections(self) -> list[dict]:
        """Return all corrections awaiting review (status='pending').

        Returns:
            A list of correction dicts, ordered by submission time (oldest
            first so reviewers process them in order).
        """
        conn = _get_conn()
        try:
            _ensure_corrections_schema(conn)
            rows = conn.execute(
                "SELECT * FROM corrections WHERE status = 'pending' "
                "ORDER BY submitted_at ASC",
            ).fetchall()
        finally:
            conn.close()

        return _rows_to_dicts(rows)


# -- MemoryPromoter ----------------------------------------------------------

class MemoryPromoter:
    """Promote corrections into typed, reusable memories.

    When a correction reveals a systematic issue (e.g. "always use Oxford
    comma"), a reviewer can promote it to a memory that the agent will
    recall on future jobs.  This class handles the promotion and
    contradiction checking.
    """

    def promote_to_memory(
        self,
        correction_id: str,
        promoted_by: str,
        memory_content: str,
        memory_type: str = "general",
        scope_type: str = "workspace",
        scope_id: str = "",
    ) -> int:
        """Promote a correction into a typed memory.

        Generalizes the specific correction into a reusable memory and
        updates the correction status to 'promoted'.

        Args:
            correction_id: The correction to promote.
            promoted_by: The user ID performing the promotion.
            memory_content: The generalized memory text (e.g. "Always use
                Oxford commas in slide titles").
            memory_type: One of org_style_rule, template_correction,
                tool_quirk, source_trust, user_preference, general.
            scope_type: One of workspace, org, user, global.
            scope_id: ID of the scope entity (workspace ID, org ID, etc.).
                Empty string for global scope.

        Returns:
            The new memory row ID (integer).

        Raises:
            ValueError: If memory_type or scope_type is invalid, or the
                correction does not exist.
        """
        if memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(
                f"Invalid memory_type={memory_type!r}. "
                f"Must be one of {sorted(VALID_MEMORY_TYPES)}"
            )
        if scope_type not in VALID_SCOPE_TYPES:
            raise ValueError(
                f"Invalid scope_type={scope_type!r}. "
                f"Must be one of {sorted(VALID_SCOPE_TYPES)}"
            )

        now = _now()

        conn = _get_conn()
        try:
            _ensure_corrections_schema(conn)

            # Verify correction exists
            row = conn.execute(
                "SELECT id FROM corrections WHERE id = ?",
                (correction_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Correction not found: {correction_id}")

            # Insert into the memories table
            import time
            now_ts = time.time()

            cursor = conn.execute(
                """
                INSERT INTO memories
                    (content, category, subcategory, source, metadata,
                     created_at, accessed_at, access_count, relevance_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1.0)
                """,
                (
                    memory_content,
                    memory_type,
                    scope_type,
                    f"correction:{correction_id}",
                    f'{{"promoted_by": "{promoted_by}", "scope_id": "{scope_id}"}}',
                    now_ts,
                    now_ts,
                ),
            )
            memory_id = cursor.lastrowid

            # Update the correction status and link to the memory
            conn.execute(
                """
                UPDATE corrections
                SET status = 'promoted',
                    promoted_to_memory_id = ?,
                    reviewed_by = ?,
                    reviewed_at = ?
                WHERE id = ?
                """,
                (memory_id, promoted_by, now, correction_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Promoted correction %s to memory %d (type=%s scope=%s:%s) by=%s",
            correction_id, memory_id, memory_type, scope_type, scope_id,
            promoted_by,
        )
        return memory_id

    def check_contradictions(
        self,
        new_memory_content: str,
        scope_type: str,
        scope_id: str,
    ) -> list[dict]:
        """Search existing memories for potential contradictions.

        Uses FTS5 to find memories in the same scope whose content
        overlaps with the proposed new memory.  Returns them for human
        review -- the caller decides whether they truly conflict.

        Args:
            new_memory_content: The text of the proposed new memory.
            scope_type: The scope type to search within.
            scope_id: The scope entity ID.

        Returns:
            A list of dicts with id, content, category, subcategory,
            and relevance_score for each potentially conflicting memory.
        """
        # Build an FTS5 query from the memory content.
        # Extract significant words (drop very short ones and common stopwords).
        stopwords = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "can", "shall",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "and",
            "but", "or", "not", "no", "if", "it", "its", "this", "that",
        }
        words = [
            w for w in new_memory_content.split()
            if len(w) > 2 and w.lower().strip(".,!?;:'\"") not in stopwords
        ]
        if not words:
            return []

        # Use OR-joined FTS query to find related memories
        fts_query = " OR ".join(
            w.strip(".,!?;:'\"") for w in words[:20]  # cap at 20 terms
        )

        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT m.id, m.content, m.category, m.subcategory,
                       m.relevance_score, m.metadata
                FROM memories m
                JOIN memories_fts fts ON m.id = fts.rowid
                WHERE memories_fts MATCH ?
                  AND m.subcategory = ?
                ORDER BY rank
                LIMIT 20
                """,
                (fts_query, scope_type),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # FTS table may not exist yet if memory store hasn't been
            # initialised -- this is non-fatal.
            logger.warning(
                "FTS5 contradiction check failed (table may not exist): %s",
                exc,
            )
            return []
        finally:
            conn.close()

        results = _rows_to_dicts(rows)

        # Further filter by scope_id if provided
        if scope_id:
            filtered = []
            for r in results:
                metadata = r.get("metadata", "{}")
                if scope_id in metadata:
                    filtered.append(r)
            results = filtered if filtered else results

        logger.info(
            "Contradiction check: %d potential conflicts for scope=%s:%s",
            len(results), scope_type, scope_id,
        )
        return results


# -- FileDiffCorrector -------------------------------------------------------

class FileDiffCorrector:
    """Extract corrections from re-uploaded (corrected) files.

    When a user downloads an artifact, edits it, and re-uploads, this class
    diffs the two versions and creates individual corrections for each
    changed region.
    """

    def __init__(self) -> None:
        self._correction_manager = CorrectionManager()

    def extract_corrections_from_diff(
        self,
        original_version_id: str,
        corrected_file_path: str,
        submitted_by: str,
    ) -> list[str]:
        """Diff two file versions and create corrections for each change.

        Retrieves the original file via its artifact_version_id, extracts
        text from both files, and creates one correction per changed region.

        Args:
            original_version_id: The artifact_versions row ID of the
                original output.
            corrected_file_path: Path to the user's corrected file on disk.
            submitted_by: The user ID who re-uploaded the file.

        Returns:
            A list of correction IDs (hex UUIDs), one per changed region.

        Raises:
            ValueError: If the original version is not found.
            FileNotFoundError: If either file does not exist on disk.
        """
        corrected_path = Path(corrected_file_path)
        if not corrected_path.exists():
            raise FileNotFoundError(
                f"Corrected file not found: {corrected_file_path}"
            )

        # Look up the original artifact version
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM artifact_versions WHERE id = ?",
                (original_version_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise ValueError(
                f"Artifact version not found: {original_version_id}"
            )

        version = dict(row)
        original_path = Path(version["file_path"])
        job_id = self._get_job_id_for_artifact(version["artifact_id"])

        # Extract text from both files
        original_text = _extract_text_from_file(original_path)
        corrected_text = _extract_text_from_file(corrected_path)

        # Diff and create corrections
        correction_ids: list[str] = []
        changes = self._compute_changes(original_text, corrected_text)

        for change in changes:
            cid = self._correction_manager.submit_correction(
                job_id=job_id,
                submitted_by=submitted_by,
                correction_type="structural",
                target_location=change["location"],
                original_value=change["original"],
                corrected_value=change["corrected"],
                user_instruction=f"File re-upload diff (lines {change['location']})",
                artifact_version_id=original_version_id,
            )
            correction_ids.append(cid)

        logger.info(
            "Extracted %d corrections from diff: version=%s corrected=%s by=%s",
            len(correction_ids), original_version_id, corrected_file_path,
            submitted_by,
        )
        return correction_ids

    def _get_job_id_for_artifact(self, artifact_id: str) -> str:
        """Look up the job_id for an artifact.

        Args:
            artifact_id: The artifact row ID.

        Returns:
            The job_id string.

        Raises:
            ValueError: If the artifact is not found.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT job_id FROM artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise ValueError(f"Artifact not found: {artifact_id}")
        return row["job_id"]

    @staticmethod
    def _compute_changes(
        original: str,
        corrected: str,
    ) -> list[dict]:
        """Compute changed regions between two text strings.

        Uses unified diff to identify changed hunks and returns a list of
        change descriptors.

        Args:
            original: The original text content.
            corrected: The corrected text content.

        Returns:
            A list of dicts with keys: location, original, corrected.
        """
        original_lines = original.splitlines(keepends=True)
        corrected_lines = corrected.splitlines(keepends=True)

        changes: list[dict] = []
        matcher = difflib.SequenceMatcher(
            None, original_lines, corrected_lines
        )

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue

            orig_chunk = "".join(original_lines[i1:i2]).strip()
            corr_chunk = "".join(corrected_lines[j1:j2]).strip()

            # Use 1-based line numbers for human readability
            if tag == "replace":
                location = f"lines:{i1 + 1}-{i2}"
            elif tag == "delete":
                location = f"lines:{i1 + 1}-{i2}:deleted"
            elif tag == "insert":
                location = f"after_line:{i1}:inserted"
            else:
                location = f"lines:{i1 + 1}-{i2}"

            changes.append({
                "location": location,
                "original": orig_chunk,
                "corrected": corr_chunk,
            })

        return changes


# -- CorrectionStats ---------------------------------------------------------

class CorrectionStats:
    """Analytics on correction frequency and patterns.

    Helps identify systematic issues with templates, tools, or prompts
    by aggregating correction data.
    """

    def get_stats(self, workspace_id: Optional[str] = None) -> dict:
        """Return aggregate correction statistics.

        Args:
            workspace_id: If provided, scope stats to corrections on jobs
                within this workspace.  If None, return global stats.

        Returns:
            A dict with keys:
                total: total number of corrections
                by_type: dict mapping correction_type -> count
                by_status: dict mapping status -> count
                promotion_rate: fraction of corrections that were promoted
                    (0.0 if no corrections have been reviewed)
        """
        conn = _get_conn()
        try:
            _ensure_corrections_schema(conn)

            if workspace_id:
                base_query = (
                    "SELECT c.* FROM corrections c "
                    "JOIN jobs j ON c.job_id = j.id "
                    "WHERE j.workspace_id = ?"
                )
                base_params: tuple = (workspace_id,)
            else:
                base_query = "SELECT c.* FROM corrections c WHERE 1=1"
                base_params = ()

            # Total count
            total_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM ({base_query})",
                base_params,
            ).fetchone()
            total = total_row["cnt"] if total_row else 0

            # By type
            type_rows = conn.execute(
                f"SELECT correction_type, COUNT(*) as cnt "
                f"FROM ({base_query}) GROUP BY correction_type",
                base_params,
            ).fetchall()
            by_type = {row["correction_type"]: row["cnt"] for row in type_rows}

            # By status
            status_rows = conn.execute(
                f"SELECT status, COUNT(*) as cnt "
                f"FROM ({base_query}) GROUP BY status",
                base_params,
            ).fetchall()
            by_status = {row["status"]: row["cnt"] for row in status_rows}

            # Promotion rate
            reviewed = by_status.get("promoted", 0) + by_status.get(
                "dismissed", 0
            ) + by_status.get("duplicate", 0)
            promotion_rate = (
                by_status.get("promoted", 0) / reviewed
                if reviewed > 0
                else 0.0
            )

        finally:
            conn.close()

        return {
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
            "promotion_rate": round(promotion_rate, 4),
        }

    def get_common_correction_types(
        self,
        template_id: Optional[str] = None,
    ) -> list[dict]:
        """Return the most frequent correction types.

        Helps identify systematic issues -- if 80% of corrections on a
        template are 'formatting', the template prompts likely need work.

        Args:
            template_id: If provided, scope to corrections on jobs that
                used this template.  If None, return global frequencies.

        Returns:
            A list of dicts with keys: correction_type, count, percentage,
            ordered by count descending.
        """
        conn = _get_conn()
        try:
            _ensure_corrections_schema(conn)

            if template_id:
                rows = conn.execute(
                    """
                    SELECT c.correction_type, COUNT(*) as cnt
                    FROM corrections c
                    JOIN jobs j ON c.job_id = j.id
                    WHERE j.template_id = ?
                    GROUP BY c.correction_type
                    ORDER BY cnt DESC
                    """,
                    (template_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT correction_type, COUNT(*) as cnt
                    FROM corrections
                    GROUP BY correction_type
                    ORDER BY cnt DESC
                    """,
                ).fetchall()

            total = sum(row["cnt"] for row in rows)
            results = []
            for row in rows:
                results.append({
                    "correction_type": row["correction_type"],
                    "count": row["cnt"],
                    "percentage": round(
                        row["cnt"] / total * 100, 2
                    ) if total > 0 else 0.0,
                })

        finally:
            conn.close()

        return results
