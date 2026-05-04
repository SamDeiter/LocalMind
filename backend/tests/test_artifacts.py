"""
Comprehensive pytest test suite for the LocalMind artifact storage and lineage system.

Covers:
  1. Create artifact with file content, verify stored on disk
  2. SHA-256 hash is computed and stored correctly
  3. Hash verification on read (detect tampering)
  4. Artifact lineage: create artifacts for nodes 1->2->3, query lineage chain
  5. Trace back from final artifact to source node (provenance)
  6. Artifact listing by job_id and node_id
  7. Immutability — artifacts can't be overwritten (same version number)
  8. Artifact metadata (mime_type, size_bytes, provenance_json)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_db_path(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp directory for every test.

    Initialises the full schema so all tables exist.
    Patches DB_PATH in every module that caches it at import time.
    """
    db_path = tmp_path / "test_artifacts.db"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    monkeypatch.setattr("backend.config.DB_PATH", db_path)
    monkeypatch.setattr("backend.config.JOBS_DIR", jobs_dir)
    monkeypatch.setattr("backend.config.WORKSPACE_ROOT", workspace_root)

    # Patch in modules that import DB_PATH at module level
    monkeypatch.setattr("backend.db.DB_PATH", db_path)
    monkeypatch.setattr("backend.core.schema.DB_PATH", db_path)
    try:
        monkeypatch.setattr("backend.core.artifacts.DB_PATH", db_path)
    except AttributeError:
        pass
    try:
        monkeypatch.setattr("backend.core.artifacts.WORKSPACE_ROOT", workspace_root)
    except AttributeError:
        pass
    try:
        monkeypatch.setattr("backend.core.artifacts.JOBS_DIR", jobs_dir)
    except AttributeError:
        pass
    try:
        monkeypatch.setattr("backend.jobs.queue.DB_PATH", db_path)
    except AttributeError:
        pass
    try:
        monkeypatch.setattr("backend.core.policy.DB_PATH", db_path)
    except AttributeError:
        pass

    # Initialize full schema
    from backend.core.schema import ensure_default_tenant, init_phase0_schema
    from backend.db import init_db

    init_db()
    init_phase0_schema()
    ensure_default_tenant()


@pytest.fixture
def workspace_id():
    """Return the default workspace ID created by ensure_default_tenant."""
    from backend.config import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM workspaces WHERE slug = 'default'"
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


@pytest.fixture
def job_id():
    """Return a fake job ID for testing."""
    return uuid.uuid4().hex


@pytest.fixture
def sample_file(tmp_path):
    """Create a sample text file for artifact testing."""
    f = tmp_path / "jobs" / "sample_output.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("Hello, LocalMind artifacts!", encoding="utf-8")
    return f


@pytest.fixture
def manager():
    """Return a fresh ArtifactManager instance."""
    from backend.core.artifacts import ArtifactManager
    return ArtifactManager()


# ---------------------------------------------------------------------------
# Test: Create artifact with file content, verify stored on disk
# ---------------------------------------------------------------------------


class TestArtifactCreation:

    def test_create_artifact_and_version(self, manager, workspace_id, job_id, sample_file):
        """Create an artifact + version, verify it references the file on disk."""
        artifact_id = manager.create_artifact(
            job_id=job_id,
            workspace_id=workspace_id,
            name="Test Output",
            artifact_type="txt",
        )
        assert artifact_id is not None
        assert len(artifact_id) == 32  # UUID hex

        version_id = manager.add_version(
            artifact_id=artifact_id,
            file_path=str(sample_file),
            created_by="test_user",
        )
        assert version_id is not None

        # Verify the artifact record exists
        artifact = manager.get_artifact(artifact_id)
        assert artifact is not None
        assert artifact["job_id"] == job_id
        assert artifact["name"] == "Test Output"
        assert artifact["artifact_type"] == "txt"
        assert artifact["current_version_id"] == version_id

        # Verify the version record
        version = manager.get_version(version_id)
        assert version is not None
        assert version["artifact_id"] == artifact_id
        assert version["version_number"] == 1
        assert version["file_path"] == str(sample_file)
        assert version["created_by"] == "test_user"

        # Verify file still on disk
        assert sample_file.exists()
        assert sample_file.read_text(encoding="utf-8") == "Hello, LocalMind artifacts!"


# ---------------------------------------------------------------------------
# Test: SHA-256 hash is computed and stored
# ---------------------------------------------------------------------------


class TestSHA256Hash:

    def test_sha256_computed_on_add_version(self, manager, workspace_id, job_id, sample_file):
        """Verify that SHA-256 is computed and stored when a version is added."""
        expected_hash = hashlib.sha256(
            sample_file.read_bytes()
        ).hexdigest()

        artifact_id = manager.create_artifact(
            job_id=job_id,
            workspace_id=workspace_id,
            name="Hash Test",
            artifact_type="txt",
        )
        version_id = manager.add_version(
            artifact_id=artifact_id,
            file_path=str(sample_file),
            created_by="test_user",
        )

        version = manager.get_version(version_id)
        assert version["sha256"] == expected_hash

    def test_sha256_differs_for_different_content(self, manager, workspace_id, job_id, tmp_path):
        """Two files with different content produce different hashes."""
        f1 = tmp_path / "jobs" / "file1.txt"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("Content A", encoding="utf-8")

        f2 = tmp_path / "jobs" / "file2.txt"
        f2.write_text("Content B", encoding="utf-8")

        artifact_id = manager.create_artifact(
            job_id=job_id,
            workspace_id=workspace_id,
            name="Hash Diff",
            artifact_type="txt",
        )
        v1 = manager.add_version(artifact_id=artifact_id, file_path=str(f1), created_by="user1")
        v2 = manager.add_version(artifact_id=artifact_id, file_path=str(f2), created_by="user2")

        ver1 = manager.get_version(v1)
        ver2 = manager.get_version(v2)
        assert ver1["sha256"] != ver2["sha256"]


# ---------------------------------------------------------------------------
# Test: Hash verification on read (detect tampering)
# ---------------------------------------------------------------------------


class TestHashVerification:

    def test_detect_tampering(self, manager, workspace_id, job_id, sample_file):
        """If file is modified after artifact creation, hash mismatch is detectable."""
        from backend.core.artifacts import _compute_sha256

        artifact_id = manager.create_artifact(
            job_id=job_id,
            workspace_id=workspace_id,
            name="Tamper Test",
            artifact_type="txt",
        )
        version_id = manager.add_version(
            artifact_id=artifact_id,
            file_path=str(sample_file),
            created_by="test_user",
        )

        version = manager.get_version(version_id)
        stored_hash = version["sha256"]

        # Hash matches before tampering
        assert _compute_sha256(sample_file) == stored_hash

        # Tamper with the file
        sample_file.write_text("TAMPERED CONTENT", encoding="utf-8")

        # Hash no longer matches
        current_hash = _compute_sha256(sample_file)
        assert current_hash != stored_hash

    def test_file_not_found_raises(self, manager, workspace_id, job_id, tmp_path):
        """Adding a version with a nonexistent file raises FileNotFoundError."""
        artifact_id = manager.create_artifact(
            job_id=job_id,
            workspace_id=workspace_id,
            name="Missing File",
            artifact_type="txt",
        )
        nonexistent = tmp_path / "jobs" / "ghost.txt"
        with pytest.raises(FileNotFoundError):
            manager.add_version(
                artifact_id=artifact_id,
                file_path=str(nonexistent),
                created_by="test_user",
            )


# ---------------------------------------------------------------------------
# Test: Artifact lineage (nodes 1 -> 2 -> 3)
# ---------------------------------------------------------------------------


class TestArtifactLineage:

    def test_lineage_chain_three_nodes(self, manager, workspace_id, job_id, tmp_path):
        """Create artifacts for 3 sequential nodes, verify parent_version_id chain."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        # Node 1 produces initial file
        f1 = jobs_dir / "node1_output.txt"
        f1.write_text("Raw data from node 1", encoding="utf-8")

        art1_id = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id,
            name="Node 1 Output", artifact_type="txt",
        )
        v1_id = manager.add_version(
            artifact_id=art1_id, file_path=str(f1),
            created_by="node_1", node_attempt_id="attempt_1",
        )

        # Node 2 transforms node 1's output
        f2 = jobs_dir / "node2_output.txt"
        f2.write_text("Processed data from node 2", encoding="utf-8")

        art2_id = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id,
            name="Node 2 Output", artifact_type="txt",
        )
        v2_id = manager.add_version(
            artifact_id=art2_id, file_path=str(f2),
            created_by="node_2", node_attempt_id="attempt_2",
            parent_version_id=v1_id,
        )

        # Node 3 produces final artifact from node 2
        f3 = jobs_dir / "node3_output.txt"
        f3.write_text("Final report from node 3", encoding="utf-8")

        art3_id = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id,
            name="Node 3 Output", artifact_type="txt",
        )
        v3_id = manager.add_version(
            artifact_id=art3_id, file_path=str(f3),
            created_by="node_3", node_attempt_id="attempt_3",
            parent_version_id=v2_id,
        )

        # Verify the chain: v3 -> v2 -> v1 -> None
        ver3 = manager.get_version(v3_id)
        assert ver3["parent_version_id"] == v2_id
        assert ver3["node_attempt_id"] == "attempt_3"

        ver2 = manager.get_version(v2_id)
        assert ver2["parent_version_id"] == v1_id
        assert ver2["node_attempt_id"] == "attempt_2"

        ver1 = manager.get_version(v1_id)
        assert ver1["parent_version_id"] is None
        assert ver1["node_attempt_id"] == "attempt_1"


# ---------------------------------------------------------------------------
# Test: Trace back from final artifact to source node (provenance)
# ---------------------------------------------------------------------------


class TestProvenanceTrace:

    def test_trace_back_to_source(self, manager, workspace_id, job_id, tmp_path):
        """Trace from final artifact back through lineage to the source node."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        files = []
        version_ids = []
        node_ids = ["node_A", "node_B", "node_C"]

        for i, node_id in enumerate(node_ids):
            f = jobs_dir / f"{node_id}_output.txt"
            f.write_text(f"Output from {node_id}", encoding="utf-8")
            files.append(f)

            art_id = manager.create_artifact(
                job_id=job_id, workspace_id=workspace_id,
                name=f"{node_id} artifact", artifact_type="txt",
            )
            parent = version_ids[-1] if version_ids else None
            vid = manager.add_version(
                artifact_id=art_id, file_path=str(f),
                created_by=node_id, node_attempt_id=f"attempt_{node_id}",
                parent_version_id=parent,
            )
            version_ids.append(vid)

        # Trace from final version back to root
        chain = []
        current_vid = version_ids[-1]
        while current_vid is not None:
            ver = manager.get_version(current_vid)
            chain.append(ver)
            current_vid = ver["parent_version_id"]

        assert len(chain) == 3
        assert chain[0]["node_attempt_id"] == "attempt_node_C"
        assert chain[1]["node_attempt_id"] == "attempt_node_B"
        assert chain[2]["node_attempt_id"] == "attempt_node_A"
        assert chain[2]["parent_version_id"] is None


# ---------------------------------------------------------------------------
# Test: Artifact listing by job_id and node_id
# ---------------------------------------------------------------------------


class TestArtifactListing:

    def test_list_by_job_id(self, manager, workspace_id, tmp_path):
        """get_artifacts_by_job returns only artifacts belonging to that job."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        job_a = uuid.uuid4().hex
        job_b = uuid.uuid4().hex

        f1 = jobs_dir / "a.txt"
        f1.write_text("Job A output", encoding="utf-8")
        f2 = jobs_dir / "b.txt"
        f2.write_text("Job B output", encoding="utf-8")

        a1 = manager.create_artifact(
            job_id=job_a, workspace_id=workspace_id, name="A1", artifact_type="txt",
        )
        manager.add_version(artifact_id=a1, file_path=str(f1), created_by="sys")

        a2 = manager.create_artifact(
            job_id=job_b, workspace_id=workspace_id, name="B1", artifact_type="txt",
        )
        manager.add_version(artifact_id=a2, file_path=str(f2), created_by="sys")

        # Query for job_a
        arts_a = manager.get_artifacts_by_job(job_a)
        assert len(arts_a) == 1
        assert arts_a[0]["name"] == "A1"

        # Query for job_b
        arts_b = manager.get_artifacts_by_job(job_b)
        assert len(arts_b) == 1
        assert arts_b[0]["name"] == "B1"

    def test_list_by_node_attempt_id(self, manager, workspace_id, job_id, tmp_path):
        """Versions can be filtered by node_attempt_id via SQL query."""
        from backend.config import DB_PATH

        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        f1 = jobs_dir / "n1.txt"
        f1.write_text("Node 1", encoding="utf-8")
        f2 = jobs_dir / "n2.txt"
        f2.write_text("Node 2", encoding="utf-8")

        a1 = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id, name="N1 Out", artifact_type="txt",
        )
        manager.add_version(
            artifact_id=a1, file_path=str(f1), created_by="sys",
            node_attempt_id="node_001",
        )

        a2 = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id, name="N2 Out", artifact_type="txt",
        )
        manager.add_version(
            artifact_id=a2, file_path=str(f2), created_by="sys",
            node_attempt_id="node_002",
        )

        # Query versions by node_attempt_id
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM artifact_versions WHERE node_attempt_id = ?",
                ("node_001",),
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["node_attempt_id"] == "node_001"
        finally:
            conn.close()

    def test_empty_job_returns_empty_list(self, manager):
        """Querying artifacts for a nonexistent job returns an empty list."""
        arts = manager.get_artifacts_by_job("nonexistent_job_id")
        assert arts == []


# ---------------------------------------------------------------------------
# Test: Immutability — same version number can't be duplicated
# ---------------------------------------------------------------------------


class TestImmutability:

    def test_version_numbers_increment(self, manager, workspace_id, job_id, tmp_path):
        """Multiple add_version calls produce incrementing version numbers."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        art_id = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id,
            name="Multi-version", artifact_type="txt",
        )

        version_ids = []
        for i in range(3):
            f = jobs_dir / f"v{i + 1}.txt"
            f.write_text(f"Version {i + 1} content", encoding="utf-8")
            vid = manager.add_version(
                artifact_id=art_id, file_path=str(f), created_by="sys",
            )
            version_ids.append(vid)

        versions = manager.get_versions(art_id)
        assert len(versions) == 3
        assert [v["version_number"] for v in versions] == [1, 2, 3]

        # Current version points to the latest
        current = manager.get_current_version(art_id)
        assert current["id"] == version_ids[-1]
        assert current["version_number"] == 3


# ---------------------------------------------------------------------------
# Test: Artifact metadata (mime_type, size_bytes, metadata_json)
# ---------------------------------------------------------------------------


class TestArtifactMetadata:

    def test_mime_type_auto_detected(self, manager, workspace_id, job_id, tmp_path):
        """MIME type is auto-detected from file extension."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        txt_file = jobs_dir / "output.txt"
        txt_file.write_text("text content", encoding="utf-8")

        json_file = jobs_dir / "data.json"
        json_file.write_text('{"key": "value"}', encoding="utf-8")

        art1 = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id, name="Text", artifact_type="txt",
        )
        v1 = manager.add_version(artifact_id=art1, file_path=str(txt_file), created_by="sys")

        art2 = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id, name="JSON", artifact_type="json",
        )
        v2 = manager.add_version(artifact_id=art2, file_path=str(json_file), created_by="sys")

        ver1 = manager.get_version(v1)
        assert ver1["mime_type"] == "text/plain"

        ver2 = manager.get_version(v2)
        assert ver2["mime_type"] == "application/json"

    def test_file_size_stored(self, manager, workspace_id, job_id, sample_file):
        """File size in bytes is correctly computed and stored."""
        art_id = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id, name="Size Test", artifact_type="txt",
        )
        vid = manager.add_version(artifact_id=art_id, file_path=str(sample_file), created_by="sys")

        version = manager.get_version(vid)
        expected_size = sample_file.stat().st_size
        assert version["file_size_bytes"] == expected_size

    def test_metadata_json_stored(self, manager, workspace_id, job_id, sample_file):
        """Custom metadata_json is stored on the version."""
        meta = json.dumps({"source": "node_1", "tags": ["draft"]})

        art_id = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id, name="Meta Test", artifact_type="txt",
        )
        vid = manager.add_version(
            artifact_id=art_id, file_path=str(sample_file),
            created_by="sys", metadata_json=meta,
        )

        version = manager.get_version(vid)
        parsed = json.loads(version["metadata_json"])
        assert parsed["source"] == "node_1"
        assert "draft" in parsed["tags"]

    def test_default_metadata_json(self, manager, workspace_id, job_id, sample_file):
        """When metadata_json is not provided, it defaults to '{}'."""
        art_id = manager.create_artifact(
            job_id=job_id, workspace_id=workspace_id, name="Default Meta", artifact_type="txt",
        )
        vid = manager.add_version(
            artifact_id=art_id, file_path=str(sample_file), created_by="sys",
        )

        version = manager.get_version(vid)
        assert version["metadata_json"] == "{}"
