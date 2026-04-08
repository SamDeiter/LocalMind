"""
Phase 0 Enterprise Schema — all control plane tables.

Called from server.py lifespan to ensure tables exist on startup.
Uses CREATE TABLE IF NOT EXISTS for idempotent migration.
Follows existing pattern from backend/db.py.
"""

import logging
import sqlite3

from backend.config import DB_PATH

logger = logging.getLogger("localmind.core.schema")


def init_phase0_schema():
    """Create all Phase 0 enterprise control plane tables."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")

    # ── 0.1 Identity & Tenancy ──────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            settings_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL REFERENCES organizations(id),
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            deployment_mode TEXT NOT NULL DEFAULT 'hybrid',
            settings_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL REFERENCES organizations(id),
            email TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            slack_user_id TEXT,
            settings_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_active_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_org
            ON users(org_id, email);
        CREATE INDEX IF NOT EXISTS idx_users_slack
            ON users(slack_user_id);

        CREATE TABLE IF NOT EXISTS memberships (
            user_id TEXT NOT NULL REFERENCES users(id),
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            role TEXT NOT NULL DEFAULT 'operator',
            PRIMARY KEY (user_id, workspace_id)
        );
    """)

    # ── 0.2 Secrets & Provider Configs ──────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS secret_refs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            name TEXT NOT NULL,
            provider TEXT NOT NULL,
            encrypted_value TEXT NOT NULL,
            created_by TEXT REFERENCES users(id),
            created_at TEXT NOT NULL,
            rotated_at TEXT,
            UNIQUE(workspace_id, name)
        );

        CREATE TABLE IF NOT EXISTS provider_configs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            provider TEXT NOT NULL,
            config_json TEXT NOT NULL,
            secret_ref_id TEXT REFERENCES secret_refs(id),
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, provider)
        );

        CREATE TABLE IF NOT EXISTS oauth_credentials (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            user_id TEXT NOT NULL REFERENCES users(id),
            provider TEXT NOT NULL,
            encrypted_token_json TEXT NOT NULL,
            scopes TEXT NOT NULL,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            refreshed_at TEXT,
            UNIQUE(workspace_id, user_id, provider)
        );

        CREATE TABLE IF NOT EXISTS quotas (
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            resource TEXT NOT NULL,
            limit_value INTEGER NOT NULL,
            current_value INTEGER NOT NULL DEFAULT 0,
            reset_at TEXT,
            PRIMARY KEY (workspace_id, resource)
        );
    """)

    # ── 0.3 Policy Engine ───────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS approval_policies (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            name TEXT NOT NULL,
            description TEXT,
            conditions_json TEXT NOT NULL,
            actions_json TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            policy_id TEXT REFERENCES approval_policies(id),
            job_id TEXT NOT NULL,
            node_id TEXT,
            tool_call_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at TEXT NOT NULL,
            decided_by TEXT REFERENCES users(id),
            decided_at TEXT,
            expires_at TEXT NOT NULL,
            reason TEXT
        );
    """)

    # ── 0.4 Durable Execution ───────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS source_events (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            processed_at TEXT,
            result_json TEXT,
            UNIQUE(source, source_event_id)
        );

        CREATE TABLE IF NOT EXISTS node_attempts (
            id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            input_json TEXT,
            output_json TEXT,
            error TEXT,
            error_category TEXT,
            model_used TEXT,
            tokens_in INTEGER,
            tokens_out INTEGER,
            cost_cents REAL,
            duration_ms INTEGER,
            UNIQUE(node_id, attempt_number)
        );
        CREATE INDEX IF NOT EXISTS idx_attempts_node
            ON node_attempts(node_id);

        CREATE TABLE IF NOT EXISTS tool_invocations (
            id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL REFERENCES node_attempts(id),
            tool_name TEXT NOT NULL,
            args_json TEXT NOT NULL,
            result_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            policy_result TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            duration_ms INTEGER,
            idempotency_key TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_invocations_attempt
            ON tool_invocations(attempt_id);

        CREATE TABLE IF NOT EXISTS worker_leases (
            worker_id TEXT PRIMARY KEY,
            hostname TEXT NOT NULL,
            pid INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            last_heartbeat TEXT NOT NULL,
            current_job_id TEXT,
            current_node_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            capabilities_json TEXT
        );

        CREATE TABLE IF NOT EXISTS dead_letters (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            node_id TEXT,
            attempt_id TEXT,
            error TEXT NOT NULL,
            error_category TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            resolved_by TEXT REFERENCES users(id),
            resolution TEXT
        );
    """)

    # ── 0.5 Artifacts & Versioning ──────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            name TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            current_version_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_job
            ON artifacts(job_id);

        CREATE TABLE IF NOT EXISTS artifact_versions (
            id TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL REFERENCES artifacts(id),
            version_number INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            created_by TEXT NOT NULL,
            node_attempt_id TEXT,
            parent_version_id TEXT,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(artifact_id, version_number)
        );
        CREATE INDEX IF NOT EXISTS idx_versions_artifact
            ON artifact_versions(artifact_id);
        CREATE INDEX IF NOT EXISTS idx_versions_hash
            ON artifact_versions(sha256);

        CREATE TABLE IF NOT EXISTS artifact_previews (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES artifact_versions(id),
            preview_type TEXT NOT NULL,
            file_path TEXT,
            content_text TEXT,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifact_diffs (
            id TEXT PRIMARY KEY,
            from_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
            to_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
            diff_type TEXT NOT NULL,
            diff_json TEXT,
            visual_diff_path TEXT,
            summary TEXT,
            created_at TEXT NOT NULL
        );
    """)

    # ── 0.6 Evidence & Provenance ───────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS evidence_items (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            node_attempt_id TEXT REFERENCES node_attempts(id),
            source_type TEXT NOT NULL,
            source_uri TEXT,
            source_snapshot_id TEXT,
            extracted_text TEXT NOT NULL,
            confidence REAL,
            retrieved_at TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_evidence_job
            ON evidence_items(job_id);

        CREATE TABLE IF NOT EXISTS source_snapshots (
            id TEXT PRIMARY KEY,
            uri TEXT NOT NULL,
            snapshot_type TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            file_path TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            size_bytes INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evidence_links (
            evidence_id TEXT NOT NULL REFERENCES evidence_items(id),
            artifact_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
            target_location TEXT,
            description TEXT,
            PRIMARY KEY (evidence_id, artifact_version_id)
        );
    """)

    # ── 0.8 Scheduler & GPU Resource Manager ────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gpu_slots (
            slot_id TEXT PRIMARY KEY,
            worker_id TEXT NOT NULL REFERENCES worker_leases(worker_id),
            model_id TEXT NOT NULL,
            lora_id TEXT,
            vram_mb INTEGER NOT NULL,
            acquired_at TEXT NOT NULL,
            job_id TEXT,
            node_id TEXT,
            priority INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS scheduler_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    # ── 0.10 Evaluation & Template Lifecycle ────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS eval_cases (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            name TEXT NOT NULL,
            description TEXT,
            input_json TEXT NOT NULL,
            expected_output_json TEXT NOT NULL,
            artifact_type TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS eval_runs (
            id TEXT PRIMARY KEY,
            eval_case_id TEXT NOT NULL REFERENCES eval_cases(id),
            job_id TEXT,
            status TEXT NOT NULL,
            score REAL,
            details_json TEXT,
            model_config_json TEXT,
            ran_at TEXT NOT NULL,
            duration_ms INTEGER
        );

        CREATE TABLE IF NOT EXISTS template_versions (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            nodes_json TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL DEFAULT 'draft',
            promoted_by TEXT REFERENCES users(id),
            promoted_at TEXT,
            eval_run_id TEXT REFERENCES eval_runs(id),
            created_at TEXT NOT NULL,
            UNIQUE(template_id, version_number)
        );

        CREATE TABLE IF NOT EXISTS prompt_versions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            content TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            UNIQUE(name, version_number)
        );

        CREATE TABLE IF NOT EXISTS model_registry (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_id TEXT NOT NULL,
            quantization TEXT,
            vram_mb INTEGER,
            capabilities_json TEXT DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );
    """)

    # ── 0.15 Corrections / Feedback Loop ────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS corrections (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            artifact_version_id TEXT,
            node_id TEXT,
            correction_type TEXT NOT NULL,
            target_location TEXT,
            original_value TEXT,
            corrected_value TEXT,
            user_instruction TEXT,
            submitted_by TEXT NOT NULL REFERENCES users(id),
            submitted_at TEXT NOT NULL,
            promoted_to_memory_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT,
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_corrections_job
            ON corrections(job_id);
        CREATE INDEX IF NOT EXISTS idx_corrections_status
            ON corrections(status);
    """)

    # ── Phase 1 Job Pipeline ────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            title TEXT NOT NULL,
            description TEXT,
            source TEXT NOT NULL DEFAULT 'web',
            source_ref TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 0,
            requester TEXT REFERENCES users(id),
            mode TEXT NOT NULL DEFAULT 'quick',
            template_id TEXT,
            result_summary TEXT,
            review_count INTEGER NOT NULL DEFAULT 0,
            max_reviews INTEGER NOT NULL DEFAULT 3,
            error TEXT,
            cost_cents REAL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_workspace ON jobs(workspace_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

        CREATE TABLE IF NOT EXISTS job_nodes (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            sequence INTEGER NOT NULL,
            title TEXT NOT NULL,
            instructions TEXT,
            tools_allowed TEXT,
            expected_output TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            input_json TEXT,
            output_json TEXT,
            error TEXT,
            auto_generated INTEGER NOT NULL DEFAULT 1,
            input_schema_json TEXT,
            output_schema_json TEXT,
            side_effects_json TEXT,
            retry_policy_json TEXT,
            timeout_sec INTEGER DEFAULT 300,
            rollback_strategy TEXT,
            acceptance_tests_json TEXT,
            depends_on TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_nodes_job
            ON job_nodes(job_id, sequence);

        CREATE TABLE IF NOT EXISTS job_files (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            node_id TEXT,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT NOT NULL DEFAULT 'input',
            mime_type TEXT,
            size_bytes INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_files_job ON job_files(job_id);

        CREATE TABLE IF NOT EXISTS job_audit_log (
            id TEXT PRIMARY KEY,
            job_id TEXT,
            node_id TEXT,
            action TEXT NOT NULL,
            detail TEXT,
            actor TEXT,
            source_ip TEXT,
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_job ON job_audit_log(job_id);
        CREATE INDEX IF NOT EXISTS idx_audit_time ON job_audit_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_audit_action ON job_audit_log(action);

        CREATE TABLE IF NOT EXISTS pipeline_templates (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            name TEXT NOT NULL,
            description TEXT,
            nodes_json TEXT NOT NULL,
            created_by TEXT REFERENCES users(id),
            use_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_templates_name
            ON pipeline_templates(name);
    """)

    # ── Security: Recycle Bin ───────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recycle_bin (
            id TEXT PRIMARY KEY,
            original_path TEXT NOT NULL,
            recycle_path TEXT NOT NULL,
            deleted_by TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            job_id TEXT,
            node_id TEXT,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            reason TEXT DEFAULT 'deleted',
            restored_at TEXT,
            restored_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_recycle_job
            ON recycle_bin(job_id);
        CREATE INDEX IF NOT EXISTS idx_recycle_expires
            ON recycle_bin(expires_at);
        CREATE INDEX IF NOT EXISTS idx_recycle_deleted_by
            ON recycle_bin(deleted_by);
    """)

    # ── Security: API Keys ──────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            key_hash TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            rate_limit INTEGER NOT NULL DEFAULT 60,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at TEXT
        );
    """)

    # ── Migrations for existing databases ─────────────────────────
    # Add source_ip column to job_audit_log if missing (Section 26).
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(job_audit_log)").fetchall()}
        if "source_ip" not in cols:
            conn.execute("ALTER TABLE job_audit_log ADD COLUMN source_ip TEXT")
            logger.info("Migration: added source_ip to job_audit_log")
    except Exception as e:
        logger.debug("job_audit_log migration skipped: %s", e)

    # Make job_id nullable for system-wide audit events.  SQLite cannot
    # ALTER COLUMN, but the CREATE TABLE IF NOT EXISTS above already uses
    # the nullable definition for fresh installs.  For existing DBs with
    # NOT NULL, we tolerate the constraint by inserting empty-string job_id
    # when no job context exists.

    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON job_audit_log(action)")

    # ── Swarm: Multi-Agent Coordination (Phase 5) ─────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS job_delegation (
            id TEXT PRIMARY KEY,
            parent_job_id TEXT NOT NULL,
            child_job_id TEXT NOT NULL,
            delegation_type TEXT NOT NULL DEFAULT 'sub_task',
            context_json TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE(parent_job_id, child_job_id)
        );
        CREATE INDEX IF NOT EXISTS idx_delegation_parent ON job_delegation(parent_job_id);
        CREATE INDEX IF NOT EXISTS idx_delegation_child ON job_delegation(child_job_id);

        CREATE TABLE IF NOT EXISTS swarm_shared_memory (
            id TEXT PRIMARY KEY,
            tree_root_job_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            written_by_job_id TEXT NOT NULL,
            written_by_node_id TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(tree_root_job_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_shared_mem_tree ON swarm_shared_memory(tree_root_job_id);

        CREATE TABLE IF NOT EXISTS resource_locks (
            id TEXT PRIMARY KEY,
            resource_path TEXT NOT NULL,
            lock_type TEXT NOT NULL DEFAULT 'exclusive',
            held_by_job_id TEXT NOT NULL,
            held_by_node_id TEXT,
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            released_at TEXT,
            UNIQUE(resource_path, held_by_job_id)
        );
        CREATE INDEX IF NOT EXISTS idx_locks_resource ON resource_locks(resource_path);
        CREATE INDEX IF NOT EXISTS idx_locks_job ON resource_locks(held_by_job_id);

        CREATE TABLE IF NOT EXISTS agent_messages (
            id TEXT PRIMARY KEY,
            tree_root_job_id TEXT NOT NULL,
            from_job_id TEXT NOT NULL,
            to_job_id TEXT,
            message_type TEXT NOT NULL,
            subject TEXT,
            body_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            read_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_tree ON agent_messages(tree_root_job_id);
        CREATE INDEX IF NOT EXISTS idx_messages_to ON agent_messages(to_job_id, status);
    """)

    # ── AI Time Machine: Action Versioning ───────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS action_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            timestamp REAL NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            before_state TEXT,
            after_state TEXT,
            diff TEXT,
            parent_id INTEGER REFERENCES action_versions(id),
            conversation_id TEXT,
            user_id TEXT,
            metadata TEXT,
            reverted INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_av_timestamp ON action_versions(timestamp);
        CREATE INDEX IF NOT EXISTS idx_av_entity ON action_versions(entity_type, entity_id);
        CREATE INDEX IF NOT EXISTS idx_av_conversation ON action_versions(conversation_id);
    """)

    # ── Cross-Project Hub: Registry & Patterns ────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS project_registry (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            description TEXT,
            language_breakdown TEXT,  -- JSON: {"python": 45, "javascript": 30, ...}
            file_count INTEGER DEFAULT 0,
            total_lines INTEGER DEFAULT 0,
            last_scanned_at REAL,
            active INTEGER DEFAULT 1,
            created_at REAL NOT NULL,
            metadata TEXT DEFAULT '{}'  -- JSON: extra info
        );
        CREATE INDEX IF NOT EXISTS idx_project_reg_path ON project_registry(path);
        CREATE INDEX IF NOT EXISTS idx_project_reg_active ON project_registry(active);

        CREATE TABLE IF NOT EXISTS cross_project_patterns (
            id TEXT PRIMARY KEY,
            pattern_type TEXT NOT NULL,  -- 'dependency', 'code_pattern', 'architecture', 'naming', 'issue'
            title TEXT NOT NULL,
            description TEXT,
            source_project_id TEXT REFERENCES project_registry(id),
            related_project_ids TEXT,  -- JSON array of project IDs
            confidence REAL DEFAULT 0.0,
            occurrences INTEGER DEFAULT 1,
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            metadata TEXT DEFAULT '{}'  -- JSON: extra detail
        );
        CREATE INDEX IF NOT EXISTS idx_patterns_type ON cross_project_patterns(pattern_type);
        CREATE INDEX IF NOT EXISTS idx_patterns_source ON cross_project_patterns(source_project_id);
    """)

    conn.commit()
    conn.close()
    logger.info("Phase 0 enterprise schema initialized")


def ensure_default_tenant():
    """Create default org + workspace + admin user for single-user deployments."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Check if default org exists
    row = conn.execute(
        "SELECT id FROM organizations WHERE slug = 'default'"
    ).fetchone()

    if row is not None:
        conn.close()
        return  # Already set up

    from datetime import datetime, timezone
    import uuid

    now = datetime.now(timezone.utc).isoformat()
    org_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    conn.execute(
        "INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (org_id, "LocalMind", "default", now, now),
    )
    conn.execute(
        "INSERT INTO workspaces (id, org_id, name, slug, deployment_mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ws_id, org_id, "Default", "default", "hybrid", now, now),
    )
    conn.execute(
        "INSERT INTO users (id, org_id, email, display_name, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, org_id, "admin@localhost", "Admin", "admin", now, now),
    )
    conn.execute(
        "INSERT INTO memberships (user_id, workspace_id, role) VALUES (?, ?, ?)",
        (user_id, ws_id, "admin"),
    )

    conn.commit()
    conn.close()
    logger.info(f"Default tenant created: org={org_id}, workspace={ws_id}, user={user_id}")
