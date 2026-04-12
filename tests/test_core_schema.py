"""
Tests for backend/core/schema.py
"""

import sqlite3
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import uuid
import pytest

from backend.core.schema import init_phase0_schema, ensure_default_tenant

@pytest.fixture
def schema_db(tmp_path):
    """Patch DB_PATH globally for schema.py and return the path."""
    db_file = tmp_path / "schema_test.db"
    with patch("backend.core.schema.DB_PATH", db_file):
        yield db_file

EXPECTED_TABLES = {
    "organizations", "workspaces", "users", "memberships",
    "secret_refs", "provider_configs", "oauth_credentials", "quotas",
    "approval_policies", "approvals",
    "source_events", "node_attempts", "tool_invocations", "worker_leases", "dead_letters",
    "artifacts", "artifact_versions", "artifact_previews", "artifact_diffs",
    "evidence_items", "source_snapshots", "evidence_links",
    "gpu_slots", "scheduler_state",
    "eval_cases", "eval_runs", "template_versions", "prompt_versions", "model_registry",
    "corrections",
    "jobs", "job_nodes", "job_files", "job_audit_log", "pipeline_templates",
    "recycle_bin", "api_keys",
}

def test_schema_creates_all_tables(schema_db):
    """init_phase0_schema must create all 35+ expected tables."""
    init_phase0_schema()

    conn = sqlite3.connect(str(schema_db))
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"

def test_schema_idempotent(schema_db):
    """Calling init_phase0_schema twice must not raise or corrupt data."""
    init_phase0_schema()
    init_phase0_schema()  # second call

    conn = sqlite3.connect(str(schema_db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert EXPECTED_TABLES.issubset(tables)

def test_schema_organizations_columns(schema_db):
    """Verify organizations table has the expected columns."""
    init_phase0_schema()

    conn = sqlite3.connect(str(schema_db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(organizations)").fetchall()}
    conn.close()
    assert cols == {"id", "name", "slug", "settings_json", "created_at", "updated_at"}

def test_schema_jobs_table_columns(schema_db):
    """Verify the jobs table has all expected columns."""
    init_phase0_schema()

    conn = sqlite3.connect(str(schema_db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()
    expected = {
        "id", "workspace_id", "title", "description", "source", "source_ref",
        "status", "priority", "requester", "mode", "template_id",
        "result_summary", "review_count", "max_reviews", "error",
        "cost_cents", "created_at", "updated_at",
    }
    assert expected.issubset(cols), f"Missing job columns: {expected - cols}"

def test_schema_indices_exist(schema_db):
    """Verify key indices are created."""
    init_phase0_schema()

    conn = sqlite3.connect(str(schema_db))
    indices = {r[1] for r in conn.execute("SELECT * FROM sqlite_master WHERE type='index'").fetchall() if r[1]}
    conn.close()
    for expected_idx in ("idx_users_email_org", "idx_users_slack", "idx_jobs_status", "idx_attempts_node"):
        assert expected_idx in indices, f"Expected index '{expected_idx}' not found"

def test_schema_foreign_keys_enforced(schema_db):
    """Inserting a workspace with a bad org_id should fail when FK is on."""
    init_phase0_schema()

    conn = sqlite3.connect(str(schema_db))
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO workspaces (id, org_id, name, slug, deployment_mode, created_at, updated_at) "
            "VALUES ('ws1','bad-org','x','x','hybrid','2024-01-01','2024-01-01')"
        )
    conn.close()

def test_ensure_default_tenant(schema_db):
    """ensure_default_tenant creates org, workspace, user, and membership."""
    init_phase0_schema()
    ensure_default_tenant()

    conn = sqlite3.connect(str(schema_db))
    conn.row_factory = sqlite3.Row
    org = conn.execute("SELECT * FROM organizations WHERE slug='default'").fetchone()
    assert org is not None
    assert org["name"] == "LocalMind"

    ws = conn.execute("SELECT * FROM workspaces WHERE slug='default'").fetchone()
    assert ws is not None
    assert ws["org_id"] == org["id"]

    user = conn.execute("SELECT * FROM users WHERE email='admin@localhost'").fetchone()
    assert user is not None
    assert user["role"] == "admin"

    mem = conn.execute("SELECT * FROM memberships WHERE user_id=?", (user["id"],)).fetchone()
    assert mem is not None
    assert mem["role"] == "admin"
    conn.close()

def test_ensure_default_tenant_idempotent(schema_db):
    """Calling ensure_default_tenant twice must not create duplicate rows."""
    init_phase0_schema()
    ensure_default_tenant()
    ensure_default_tenant()

    conn = sqlite3.connect(str(schema_db))
    count = conn.execute("SELECT COUNT(*) FROM organizations WHERE slug='default'").fetchone()[0]
    conn.close()
    assert count == 1

def test_ensure_default_tenant_detailed_verification(schema_db):
    """Verify exact data inserted by ensure_default_tenant by mocking UUID and datetime."""
    init_phase0_schema()

    fixed_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    fixed_now_iso = fixed_now.isoformat()
    mock_uuids = [
        uuid.UUID("00000000-0000-0000-0000-000000000001"), # org_id
        uuid.UUID("00000000-0000-0000-0000-000000000002"), # ws_id
        uuid.UUID("00000000-0000-0000-0000-000000000003"), # user_id
    ]

    # Patch at the source since they are imported inside the function
    with patch("datetime.datetime") as mock_dt, \
         patch("uuid.uuid4", side_effect=mock_uuids):
        mock_dt.now.return_value = fixed_now
        ensure_default_tenant()

    conn = sqlite3.connect(str(schema_db))
    conn.row_factory = sqlite3.Row

    org = conn.execute("SELECT * FROM organizations WHERE slug='default'").fetchone()
    assert org["id"] == str(mock_uuids[0])
    assert org["name"] == "LocalMind"
    assert org["created_at"] == fixed_now_iso

    ws = conn.execute("SELECT * FROM workspaces WHERE slug='default'").fetchone()
    assert ws["id"] == str(mock_uuids[1])
    assert ws["org_id"] == org["id"]
    assert ws["name"] == "Default"

    user = conn.execute("SELECT * FROM users WHERE email='admin@localhost'").fetchone()
    assert user["id"] == str(mock_uuids[2])
    assert user["org_id"] == org["id"]
    assert user["role"] == "admin"

    mem = conn.execute("SELECT * FROM memberships WHERE user_id=? AND workspace_id=?", (user["id"], ws["id"])).fetchone()
    assert mem is not None
    assert mem["role"] == "admin"

    conn.close()

def test_ensure_default_tenant_pragma_and_row_factory(schema_db):
    """Verify PRAGMA foreign_keys=ON and row_factory=sqlite3.Row are used."""
    init_phase0_schema()

    # Mock sqlite3.connect to capture connection usage
    original_connect = sqlite3.connect
    mock_conn = MagicMock(wraps=original_connect(str(schema_db)))

    with patch("sqlite3.connect", return_value=mock_conn):
        ensure_default_tenant()

    # Verify row_factory was set
    assert mock_conn.row_factory == sqlite3.Row

    # Verify PRAGMA foreign_keys=ON was called
    # MagicMock wraps might not show all calls if they were on the real object
    # but since we wrapped it, they should show up in mock_conn.execute.call_args_list
    pragma_calls = [call for call in mock_conn.execute.call_args_list if "PRAGMA foreign_keys=ON" in str(call)]
    assert len(pragma_calls) > 0

def test_ensure_default_tenant_fails_if_tables_missing(schema_db):
    """ensure_default_tenant should fail if organizations table does not exist."""
    # Do NOT call init_phase0_schema()
    with pytest.raises(sqlite3.OperationalError):
        ensure_default_tenant()

def test_schema_api_keys_table(schema_db):
    """Verify the api_keys table schema."""
    init_phase0_schema()

    conn = sqlite3.connect(str(schema_db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
    conn.close()
    expected = {"id", "user_id", "key_hash", "name", "role", "rate_limit", "created_at", "last_used_at", "revoked_at"}
    assert expected == cols
