"""
Evidence & Provenance tracking -- LocalMind enterprise task worker.

When the system produces a claim (e.g. "Q1 revenue was $4.2M" on a slide),
this module maintains a traceable chain back to the original source:

    artifact change --> evidence_item --> source_snapshot --> original source

Three service classes:
    EvidenceCollector   -- record and query evidence items
    SourceSnapshotStore -- capture and store immutable source snapshots
    EvidenceLinker      -- link evidence to specific artifact version changes

Tables (created by backend/core/schema.py):
    evidence_items, source_snapshots, evidence_links
"""

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config import DB_PATH, WORKSPACE_ROOT

logger = logging.getLogger("localmind.core.evidence")

# Snapshot storage directory
SNAPSHOTS_DIR = WORKSPACE_ROOT / "snapshots"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Valid source types for evidence items
VALID_SOURCE_TYPES = {"web_page", "file_extract", "api_response", "user_input", "memory"}

# Valid snapshot types and their file extensions
SNAPSHOT_TYPE_EXTENSIONS: dict[str, str] = {
    "html": ".html",
    "pdf": ".pdf",
    "json": ".json",
    "text": ".txt",
}


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


# -- EvidenceCollector -------------------------------------------------------

class EvidenceCollector:
    """Record and query evidence items that back claims made in artifacts.

    Each evidence item captures a specific piece of extracted text, the source
    it came from, and an optional confidence score.  Evidence is scoped to a
    job and optionally to a specific node attempt within that job.
    """

    def record_evidence(
        self,
        job_id: str,
        node_attempt_id: Optional[str],
        source_type: str,
        extracted_text: str,
        source_uri: Optional[str] = None,
        confidence: Optional[float] = None,
        metadata_json: Optional[str] = None,
    ) -> str:
        """Record a new evidence item and return its ID.

        Args:
            job_id: The job this evidence belongs to.
            node_attempt_id: Optional reference to the node_attempts row that
                produced this evidence.
            source_type: One of web_page, file_extract, api_response,
                user_input, memory.
            extracted_text: The actual text/data extracted from the source.
            source_uri: Optional URI pointing to the original source.
            confidence: Optional confidence score in [0.0, 1.0].
            metadata_json: Optional JSON string with additional metadata.

        Returns:
            The newly created evidence item ID (hex UUID).

        Raises:
            ValueError: If source_type is not a recognised value, or if
                confidence is outside [0.0, 1.0].
        """
        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"Invalid source_type={source_type!r}. "
                f"Must be one of {sorted(VALID_SOURCE_TYPES)}"
            )
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {confidence}"
            )

        evidence_id = _new_id()
        now = _now()

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO evidence_items
                    (id, job_id, node_attempt_id, source_type, source_uri,
                     extracted_text, confidence, retrieved_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    job_id,
                    node_attempt_id,
                    source_type,
                    source_uri,
                    extracted_text,
                    confidence,
                    now,
                    metadata_json or "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Recorded evidence: id=%s job=%s type=%s uri=%s",
            evidence_id, job_id, source_type, source_uri,
        )
        return evidence_id

    def get_evidence(self, evidence_id: str) -> Optional[dict]:
        """Return an evidence item by ID, or None if not found.

        Args:
            evidence_id: The evidence item ID to look up.

        Returns:
            A dict with all evidence_items columns, or None.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM evidence_items WHERE id = ?",
                (evidence_id,),
            ).fetchone()
        finally:
            conn.close()

        return _row_to_dict(row)

    def get_evidence_for_job(self, job_id: str) -> list[dict]:
        """Return all evidence items for a given job, ordered by retrieval time.

        Args:
            job_id: The job ID to query.

        Returns:
            A list of dicts, one per evidence item.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM evidence_items WHERE job_id = ? ORDER BY retrieved_at",
                (job_id,),
            ).fetchall()
        finally:
            conn.close()

        return _rows_to_dicts(rows)

    def get_evidence_for_attempt(self, node_attempt_id: str) -> list[dict]:
        """Return all evidence items for a given node attempt.

        Args:
            node_attempt_id: The node_attempts row ID to query.

        Returns:
            A list of dicts, one per evidence item.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM evidence_items WHERE node_attempt_id = ? ORDER BY retrieved_at",
                (node_attempt_id,),
            ).fetchall()
        finally:
            conn.close()

        return _rows_to_dicts(rows)


# -- SourceSnapshotStore -----------------------------------------------------

class SourceSnapshotStore:
    """Capture and store immutable snapshots of source content.

    When evidence is gathered from a web page, file, or API response, the raw
    content is saved to disk so the original source can always be re-examined,
    even if the live source changes or disappears.  Each snapshot is
    content-addressed via SHA-256.
    """

    def save_snapshot(
        self,
        uri: str,
        snapshot_type: str,
        content_bytes: bytes,
    ) -> str:
        """Save a source snapshot to disk and record it in the database.

        Args:
            uri: The original URI of the source (URL, file path, etc.).
            snapshot_type: One of html, pdf, json, text.
            content_bytes: The raw content to snapshot.

        Returns:
            The newly created snapshot ID (hex UUID).

        Raises:
            ValueError: If snapshot_type is not recognised, or content_bytes
                is empty.
        """
        if snapshot_type not in SNAPSHOT_TYPE_EXTENSIONS:
            raise ValueError(
                f"Invalid snapshot_type={snapshot_type!r}. "
                f"Must be one of {sorted(SNAPSHOT_TYPE_EXTENSIONS)}"
            )
        if not content_bytes:
            raise ValueError("content_bytes must not be empty")

        snapshot_id = _new_id()
        ext = SNAPSHOT_TYPE_EXTENSIONS[snapshot_type]
        file_path = SNAPSHOTS_DIR / f"{snapshot_id}{ext}"
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        size_bytes = len(content_bytes)
        now = _now()

        # Write content to disk
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content_bytes)

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO source_snapshots
                    (id, uri, snapshot_type, content_hash, file_path,
                     captured_at, size_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    uri,
                    snapshot_type,
                    content_hash,
                    str(file_path),
                    now,
                    size_bytes,
                ),
            )
            conn.commit()
        except Exception:
            # Clean up the file if the DB insert fails
            if file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    logger.warning(
                        "Failed to clean up snapshot file after DB error: %s",
                        file_path,
                    )
            raise
        finally:
            conn.close()

        logger.info(
            "Saved snapshot: id=%s type=%s hash=%s size=%d uri=%s",
            snapshot_id, snapshot_type, content_hash[:12], size_bytes, uri,
        )
        return snapshot_id

    def get_snapshot(self, snapshot_id: str) -> Optional[dict]:
        """Return a snapshot record by ID, or None if not found.

        Args:
            snapshot_id: The snapshot ID to look up.

        Returns:
            A dict with all source_snapshots columns, or None.
        """
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM source_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        finally:
            conn.close()

        return _row_to_dict(row)

    def link_evidence_to_snapshot(
        self,
        evidence_id: str,
        snapshot_id: str,
    ) -> None:
        """Link an evidence item to a source snapshot.

        Updates the source_snapshot_id column on the evidence_items row.

        Args:
            evidence_id: The evidence item to update.
            snapshot_id: The snapshot to link to.

        Raises:
            ValueError: If the evidence item or snapshot does not exist.
        """
        conn = _get_conn()
        try:
            # Verify both records exist
            ev_row = conn.execute(
                "SELECT id FROM evidence_items WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            if ev_row is None:
                raise ValueError(f"Evidence item not found: {evidence_id}")

            snap_row = conn.execute(
                "SELECT id FROM source_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            if snap_row is None:
                raise ValueError(f"Snapshot not found: {snapshot_id}")

            conn.execute(
                "UPDATE evidence_items SET source_snapshot_id = ? WHERE id = ?",
                (snapshot_id, evidence_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Linked evidence %s to snapshot %s", evidence_id, snapshot_id,
        )


# -- EvidenceLinker ----------------------------------------------------------

class EvidenceLinker:
    """Link evidence items to specific artifact version changes.

    This creates the final bridge in the provenance chain:
        artifact_version --> evidence_link --> evidence_item --> source_snapshot
    """

    def link_to_artifact(
        self,
        evidence_id: str,
        artifact_version_id: str,
        target_location: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        """Link an evidence item to an artifact version.

        Args:
            evidence_id: The evidence item ID.
            artifact_version_id: The artifact_versions row ID.
            target_location: Optional location descriptor such as
                "slide:rId4:shape:Title1" or "sheet:A15".
            description: Optional human-readable description of what this
                evidence supports in the artifact.

        Raises:
            ValueError: If the evidence item or artifact version does not exist.
        """
        conn = _get_conn()
        try:
            # Verify evidence exists
            ev_row = conn.execute(
                "SELECT id FROM evidence_items WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            if ev_row is None:
                raise ValueError(f"Evidence item not found: {evidence_id}")

            # Verify artifact version exists
            av_row = conn.execute(
                "SELECT id FROM artifact_versions WHERE id = ?",
                (artifact_version_id,),
            ).fetchone()
            if av_row is None:
                raise ValueError(
                    f"Artifact version not found: {artifact_version_id}"
                )

            conn.execute(
                """
                INSERT INTO evidence_links
                    (evidence_id, artifact_version_id, target_location, description)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(evidence_id, artifact_version_id)
                    DO UPDATE SET
                        target_location = excluded.target_location,
                        description = excluded.description
                """,
                (evidence_id, artifact_version_id, target_location, description),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Linked evidence %s to artifact version %s (location=%s)",
            evidence_id, artifact_version_id, target_location,
        )

    def get_evidence_for_artifact(
        self,
        artifact_version_id: str,
    ) -> list[dict]:
        """Return all evidence items supporting changes in an artifact version.

        Each returned dict contains the evidence_items fields plus the
        evidence_links fields (target_location, description).

        Args:
            artifact_version_id: The artifact_versions row ID.

        Returns:
            A list of dicts with merged evidence_item and link data.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    ei.*,
                    el.target_location,
                    el.description AS link_description
                FROM evidence_links el
                JOIN evidence_items ei ON ei.id = el.evidence_id
                WHERE el.artifact_version_id = ?
                ORDER BY ei.retrieved_at
                """,
                (artifact_version_id,),
            ).fetchall()
        finally:
            conn.close()

        return _rows_to_dicts(rows)

    def get_provenance_chain(
        self,
        artifact_version_id: str,
    ) -> list[dict]:
        """Return the full provenance chain for an artifact version.

        Each entry in the chain contains:
            - evidence_id, source_type, extracted_text, confidence
            - target_location, link_description (from evidence_links)
            - snapshot_id, snapshot_uri, snapshot_type, content_hash,
              snapshot_file_path (from source_snapshots, if linked)

        This provides a complete audit trail from artifact change back to
        the original source.

        Args:
            artifact_version_id: The artifact_versions row ID.

        Returns:
            A list of provenance chain entries (dicts), ordered by retrieval
            time.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    ei.id              AS evidence_id,
                    ei.job_id,
                    ei.node_attempt_id,
                    ei.source_type,
                    ei.source_uri,
                    ei.extracted_text,
                    ei.confidence,
                    ei.retrieved_at,
                    ei.metadata_json,
                    el.target_location,
                    el.description     AS link_description,
                    ss.id              AS snapshot_id,
                    ss.uri             AS snapshot_uri,
                    ss.snapshot_type,
                    ss.content_hash,
                    ss.file_path       AS snapshot_file_path,
                    ss.captured_at     AS snapshot_captured_at,
                    ss.size_bytes      AS snapshot_size_bytes
                FROM evidence_links el
                JOIN evidence_items ei ON ei.id = el.evidence_id
                LEFT JOIN source_snapshots ss ON ss.id = ei.source_snapshot_id
                WHERE el.artifact_version_id = ?
                ORDER BY ei.retrieved_at
                """,
                (artifact_version_id,),
            ).fetchall()
        finally:
            conn.close()

        return _rows_to_dicts(rows)
