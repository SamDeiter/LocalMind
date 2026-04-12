"""
Comprehensive tests for the LocalMind enterprise core infrastructure modules:
  - backend/core/schema.py   (Phase 0 schema creation & default tenant)
  - backend/core/identity.py (Identity & Tenancy CRUD, RBAC)
  - backend/core/providers.py (Secrets, provider configs, OAuth, quotas)
  - backend/core/policy.py   (Policy engine, built-in policies, approvals)

Each group uses tmp_path + patch to isolate SQLite databases.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def schema_db(tmp_path):
    """Patch DB_PATH globally for schema.py and return the path."""
    db_file = tmp_path / "schema_test.db"
    with patch("backend.core.schema.DB_PATH", db_file):
        yield db_file


@pytest.fixture
def identity_db(tmp_path):
    """Patch DB_PATH for both schema.py and identity.py, init schema, return path."""
    db_file = tmp_path / "identity_test.db"
    with patch("backend.core.schema.DB_PATH", db_file), \
         patch("backend.core.identity.DB_PATH", db_file):
        from backend.core.schema import init_phase0_schema
        init_phase0_schema()
        yield db_file


@pytest.fixture
def provider_db(tmp_path):
    """Patch DB_PATH for schema + providers, init schema, provide master key."""
    db_file = tmp_path / "provider_test.db"
    master_key_file = tmp_path / ".master_key"
    with patch("backend.core.schema.DB_PATH", db_file), \
         patch("backend.core.providers.DB_PATH", db_file), \
         patch("backend.core.providers.WORKSPACE_ROOT", tmp_path):
        from backend.core.schema import init_phase0_schema
        init_phase0_schema()
        yield db_file, tmp_path


@pytest.fixture
def policy_db(tmp_path):
    """Patch DB_PATH for schema + policy, init schema, return path."""
    db_file = tmp_path / "policy_test.db"
    with patch("backend.core.schema.DB_PATH", db_file), \
         patch("backend.core.policy.DB_PATH", db_file):
        from backend.core.schema import init_phase0_schema
        init_phase0_schema()
        yield db_file


def _seed_org_ws_user(db_file):
    """Insert a minimal org + workspace + user for FK-dependent tests. Returns (org_id, ws_id, user_id)."""
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys=ON")
    now = datetime.now(timezone.utc).isoformat()
    org_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (?,?,?,?,?)",
        (org_id, "TestOrg", f"test-{uuid.uuid4().hex[:8]}", now, now),
    )
    conn.execute(
        "INSERT INTO workspaces (id, org_id, name, slug, deployment_mode, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (ws_id, org_id, "TestWS", f"ws-{uuid.uuid4().hex[:8]}", "hybrid", now, now),
    )
    conn.execute(
        "INSERT INTO users (id, org_id, email, display_name, role, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, org_id, "test@localhost", "Tester", "admin", now, now),
    )
    conn.commit()
    conn.close()
    return org_id, ws_id, user_id


# 2. identity.py tests
# ═══════════════════════════════════════════════════════════════════════════════


def test_identity_create_and_get_org(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("Acme Corp", "acme")
    assert org.name == "Acme Corp"
    assert org.slug == "acme"
    assert org.id

    fetched = svc.get_org(org.id)
    assert fetched is not None
    assert fetched.slug == "acme"


def test_identity_get_org_by_slug(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("SlugTest", "slug-test")
    fetched = svc.get_org_by_slug("slug-test")
    assert fetched is not None
    assert fetched.id == org.id


def test_identity_get_org_not_found(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    assert svc.get_org("nonexistent-id") is None


def test_identity_create_workspace(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("WsOrg", "ws-org")
    ws = svc.create_workspace(org.id, "Dev", "dev-ws", deployment_mode="strict-local")
    assert ws.name == "Dev"
    assert ws.deployment_mode == "strict-local"
    assert ws.org_id == org.id


def test_identity_get_workspace_by_slug(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("WsSlugOrg", "ws-slug-org")
    ws = svc.create_workspace(org.id, "Prod", "prod-ws")
    fetched = svc.get_workspace_by_slug("prod-ws")
    assert fetched is not None
    assert fetched.id == ws.id


def test_identity_get_default_workspace_raises(identity_db):
    """get_default_workspace raises RuntimeError if no default workspace exists."""
    from backend.core.identity import IdentityService
    svc = IdentityService()
    with pytest.raises(RuntimeError, match="Default workspace not found"):
        svc.get_default_workspace()


def test_identity_get_default_workspace_after_tenant(identity_db):
    """get_default_workspace works after ensure_default_tenant."""
    from backend.core.identity import IdentityService
    with patch("backend.core.schema.DB_PATH", identity_db):
        from backend.core.schema import ensure_default_tenant
        ensure_default_tenant()
    svc = IdentityService()
    ws = svc.get_default_workspace()
    assert ws.slug == "default"


def test_identity_create_user(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("UserOrg", "user-org")
    user = svc.create_user(org.id, "alice@example.com", "Alice", role="admin")
    assert user.email == "alice@example.com"
    assert user.role == "admin"
    assert user.display_name == "Alice"


def test_identity_get_user_by_email(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("EmailOrg", "email-org")
    user = svc.create_user(org.id, "bob@test.com", "Bob")
    fetched = svc.get_user_by_email(org.id, "bob@test.com")
    assert fetched is not None
    assert fetched.id == user.id


def test_identity_link_slack_user(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("SlackOrg", "slack-org")
    user = svc.create_user(org.id, "slack@test.com", "Slacker")
    svc.link_slack_user(user.id, "U12345")
    fetched = svc.get_user_by_slack_id("U12345")
    assert fetched is not None
    assert fetched.id == user.id


def test_identity_touch_user_activity(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("ActivityOrg", "activity-org")
    user = svc.create_user(org.id, "active@test.com", "Active")
    assert user.last_active_at is None
    svc.touch_user_activity(user.id)
    refreshed = svc.get_user(user.id)
    assert refreshed.last_active_at is not None


def test_identity_membership_add_and_list(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("MemOrg", "mem-org")
    ws = svc.create_workspace(org.id, "MemWS", "mem-ws")
    user = svc.create_user(org.id, "mem@test.com", "Member")
    svc.add_membership(user.id, ws.id, "operator")

    members = svc.get_memberships(ws.id)
    assert len(members) == 1
    assert members[0].user_id == user.id
    assert members[0].role == "operator"


def test_identity_membership_upsert_role(identity_db):
    """add_membership with updated role overwrites the old role."""
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("UpsertOrg", "upsert-org")
    ws = svc.create_workspace(org.id, "UpsertWS", "upsert-ws")
    user = svc.create_user(org.id, "upser@test.com", "Upsert")
    svc.add_membership(user.id, ws.id, "viewer")
    svc.add_membership(user.id, ws.id, "admin")  # upsert

    members = svc.get_memberships(ws.id)
    assert len(members) == 1
    assert members[0].role == "admin"


def test_identity_check_permission(identity_db):
    """check_permission enforces role hierarchy: admin >= operator >= viewer."""
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("PermOrg", "perm-org")
    ws = svc.create_workspace(org.id, "PermWS", "perm-ws")
    user = svc.create_user(org.id, "perm@test.com", "PermUser")
    svc.add_membership(user.id, ws.id, "operator")

    assert svc.check_permission(user.id, ws.id, "viewer") is True
    assert svc.check_permission(user.id, ws.id, "operator") is True
    assert svc.check_permission(user.id, ws.id, "admin") is False


def test_identity_check_permission_no_membership(identity_db):
    """check_permission returns False if user has no membership."""
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("NoPerm", "no-perm")
    ws = svc.create_workspace(org.id, "NoPermWS", "no-perm-ws")
    user = svc.create_user(org.id, "noperm@test.com", "NoPerm")
    assert svc.check_permission(user.id, ws.id, "viewer") is False


def test_identity_check_permission_unknown_role(identity_db):
    """check_permission returns False for an unrecognised required_role."""
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("UnkOrg", "unk-org")
    ws = svc.create_workspace(org.id, "UnkWS", "unk-ws")
    user = svc.create_user(org.id, "unk@test.com", "Unk")
    svc.add_membership(user.id, ws.id, "admin")
    assert svc.check_permission(user.id, ws.id, "superadmin") is False


def test_identity_org_to_dict(identity_db):
    from backend.core.identity import IdentityService
    svc = IdentityService()
    org = svc.create_org("DictOrg", "dict-org", settings={"theme": "dark"})
    d = org.to_dict()
    assert d["name"] == "DictOrg"
    assert d["settings"] == {"theme": "dark"}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. providers.py tests
# ═══════════════════════════════════════════════════════════════════════════════


def test_providers_encrypt_decrypt_roundtrip(provider_db):
    """encrypt_value -> decrypt_value roundtrip with real AES-256-GCM."""
    from backend.core.providers import encrypt_value, decrypt_value, _CRYPTO_AVAILABLE
    if not _CRYPTO_AVAILABLE:
        pytest.skip("cryptography library not installed")

    key = os.urandom(32)
    plaintext = "sk-test-secret-key-12345"
    encrypted = encrypt_value(plaintext, key)
    assert encrypted.startswith("enc1:")
    assert plaintext not in encrypted  # must not leak plaintext

    decrypted = decrypt_value(encrypted, key)
    assert decrypted == plaintext


def test_providers_encrypt_decrypt_unicode(provider_db):
    """Encryption handles unicode strings correctly."""
    from backend.core.providers import encrypt_value, decrypt_value, _CRYPTO_AVAILABLE
    if not _CRYPTO_AVAILABLE:
        pytest.skip("cryptography library not installed")

    key = os.urandom(32)
    plaintext = "secret-with-emoji-\U0001f680-and-accents-\u00e9\u00e0"
    decrypted = decrypt_value(encrypt_value(plaintext, key), key)
    assert decrypted == plaintext


def test_providers_decrypt_wrong_key(provider_db):
    """Decrypting with the wrong key must raise ValueError."""
    from backend.core.providers import encrypt_value, decrypt_value, _CRYPTO_AVAILABLE
    if not _CRYPTO_AVAILABLE:
        pytest.skip("cryptography library not installed")

    key1 = os.urandom(32)
    key2 = os.urandom(32)
    encrypted = encrypt_value("my-secret", key1)
    with pytest.raises(ValueError, match="Decryption failed"):
        decrypt_value(encrypted, key2)


def test_providers_plaintext_fallback(provider_db):
    """When _CRYPTO_AVAILABLE is False, values are stored as base64 and can round-trip."""
    from backend.core.providers import encrypt_value, decrypt_value
    with patch("backend.core.providers._CRYPTO_AVAILABLE", False):
        key = os.urandom(32)
        encrypted = encrypt_value("fallback-secret", key)
        assert encrypted.startswith("b64v0:")
        decrypted = decrypt_value(encrypted, key)
        assert decrypted == "fallback-secret"


def test_providers_decrypt_unknown_prefix(provider_db):
    """decrypt_value raises ValueError for unknown prefix."""
    from backend.core.providers import decrypt_value
    key = os.urandom(32)
    with pytest.raises(ValueError, match="Unknown encryption prefix"):
        decrypt_value("garbage:abc123", key)


def test_providers_get_master_key_from_env(provider_db):
    """get_master_key reads from LOCALMIND_MASTER_KEY env var."""
    from backend.core.providers import get_master_key
    test_key = os.urandom(32)
    with patch.dict(os.environ, {"LOCALMIND_MASTER_KEY": test_key.hex()}):
        result = get_master_key()
    assert result == test_key


def test_providers_get_master_key_bad_env(provider_db):
    """get_master_key raises ValueError for non-hex env var."""
    from backend.core.providers import get_master_key
    with patch.dict(os.environ, {"LOCALMIND_MASTER_KEY": "not-hex"}):
        with pytest.raises(ValueError, match="hex-encoded"):
            get_master_key()


def test_providers_get_master_key_wrong_length(provider_db):
    """get_master_key raises ValueError for wrong-length hex key."""
    from backend.core.providers import get_master_key
    short_key = os.urandom(16).hex()  # 16 bytes, not 32
    with patch.dict(os.environ, {"LOCALMIND_MASTER_KEY": short_key}):
        with pytest.raises(ValueError, match="exactly 32 bytes"):
            get_master_key()


def test_providers_get_master_key_generates_file(provider_db):
    """get_master_key creates .master_key file on first run when no env var set."""
    _, ws_root = provider_db
    from backend.core.providers import get_master_key
    key_file = ws_root / ".master_key"
    # Ensure no env var and no file
    with patch.dict(os.environ, {}, clear=False):
        env = os.environ.copy()
        env.pop("LOCALMIND_MASTER_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            if key_file.exists():
                key_file.unlink()
            result = get_master_key()
    assert key_file.exists()
    assert len(result) == 32
    stored = bytes.fromhex(key_file.read_text().strip())
    assert stored == result


def test_providers_store_and_get_secret(provider_db):
    """store_secret + get_secret roundtrip."""
    db_file, ws_root = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    # Force a known master key
    svc._master_key = os.urandom(32)

    ref = svc.store_secret(ws_id, "gemini-key", "gemini", "AIza-real-key", created_by=user_id)
    assert ref.name == "gemini-key"
    assert ref.provider == "gemini"

    plaintext = svc.get_secret(ref.id)
    assert plaintext == "AIza-real-key"


def test_providers_secret_to_dict_excludes_value(provider_db):
    """SecretRef.to_dict must never expose the encrypted value."""
    db_file, ws_root = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    ref = svc.store_secret(ws_id, "safe-key", "openai", "sk-secret")
    d = ref.to_dict()
    assert "encrypted_value" not in d
    assert "sk-secret" not in json.dumps(d)


def test_providers_rotate_secret(provider_db):
    """rotate_secret changes the stored value and updates rotated_at."""
    db_file, _ = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    ref = svc.store_secret(ws_id, "rotate-me", "aws", "old-key")
    svc.rotate_secret(ref.id, "new-key")
    assert svc.get_secret(ref.id) == "new-key"


def test_providers_delete_secret(provider_db):
    """delete_secret removes the secret; get_secret raises KeyError after."""
    db_file, _ = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    ref = svc.store_secret(ws_id, "del-me", "azure", "az-key")
    svc.delete_secret(ref.id)
    with pytest.raises(KeyError):
        svc.get_secret(ref.id)


def test_providers_list_secrets(provider_db):
    """list_secrets returns metadata for all workspace secrets."""
    db_file, _ = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    svc.store_secret(ws_id, "key-a", "gemini", "val-a")
    svc.store_secret(ws_id, "key-b", "openai", "val-b")
    secrets = svc.list_secrets(ws_id)
    names = {s.name for s in secrets}
    assert names == {"key-a", "key-b"}


def test_providers_set_and_get_provider_config(provider_db):
    """set_provider_config + get_provider_config roundtrip."""
    db_file, _ = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    cfg = svc.set_provider_config(ws_id, "gemini", {"model": "gemini-pro", "temperature": 0.7})
    assert cfg.provider == "gemini"
    assert cfg.config["model"] == "gemini-pro"
    assert cfg.enabled is True

    fetched = svc.get_provider_config(ws_id, "gemini")
    assert fetched is not None
    assert fetched.config["temperature"] == 0.7


def test_providers_upsert_provider_config(provider_db):
    """Calling set_provider_config twice updates config in place."""
    db_file, _ = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    cfg1 = svc.set_provider_config(ws_id, "ollama", {"url": "http://localhost:11434"})
    cfg2 = svc.set_provider_config(ws_id, "ollama", {"url": "http://gpu-box:11434"}, enabled=False)
    assert cfg2.id == cfg1.id  # same row updated
    assert cfg2.enabled is False
    assert cfg2.config["url"] == "http://gpu-box:11434"


def test_providers_is_provider_enabled(provider_db):
    """is_provider_enabled returns True/False based on the enabled flag."""
    db_file, _ = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    assert svc.is_provider_enabled(ws_id, "gemini") is False  # not configured yet
    svc.set_provider_config(ws_id, "gemini", {}, enabled=True)
    assert svc.is_provider_enabled(ws_id, "gemini") is True
    svc.set_provider_config(ws_id, "gemini", {}, enabled=False)
    assert svc.is_provider_enabled(ws_id, "gemini") is False


def test_providers_oauth_store_and_get(provider_db):
    """store_oauth_token + get_oauth_token roundtrip."""
    db_file, _ = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    token = {"access_token": "ya29.abc", "refresh_token": "1//xyz", "token_type": "Bearer"}
    cred = svc.store_oauth_token(ws_id, user_id, "google", token, scopes=["gmail.readonly"])
    assert cred.provider == "google"
    assert cred.scopes == ["gmail.readonly"]

    decrypted = svc.get_oauth_token(ws_id, user_id, "google")
    assert decrypted == token


def test_providers_oauth_upsert(provider_db):
    """Storing OAuth token twice for the same (ws, user, provider) updates in place."""
    db_file, _ = provider_db
    org_id, ws_id, user_id = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    token1 = {"access_token": "old"}
    token2 = {"access_token": "new"}
    cred1 = svc.store_oauth_token(ws_id, user_id, "slack", token1, scopes=["chat:write"])
    cred2 = svc.store_oauth_token(ws_id, user_id, "slack", token2, scopes=["chat:write", "users:read"])
    assert cred2.id == cred1.id  # same row, upserted

    decrypted = svc.get_oauth_token(ws_id, user_id, "slack")
    assert decrypted == token2


def test_providers_quota_check_no_row(provider_db):
    """check_quota returns (True, 0, -1) when no quota row exists (unlimited)."""
    db_file, _ = provider_db
    org_id, ws_id, _ = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    allowed, current, limit = svc.check_quota(ws_id, "api_calls")
    assert allowed is True
    assert current == 0
    assert limit == -1


def test_providers_quota_upsert_and_check(provider_db):
    """upsert_quota + check_quota basic flow."""
    db_file, _ = provider_db
    org_id, ws_id, _ = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    q = svc.upsert_quota(ws_id, "api_calls", 100)
    assert q.limit_value == 100
    assert q.current_value == 0

    allowed, current, limit = svc.check_quota(ws_id, "api_calls")
    assert allowed is True
    assert current == 0
    assert limit == 100


def test_providers_quota_increment_and_exhaust(provider_db):
    """increment_quota respects the limit and denies when exceeded."""
    db_file, _ = provider_db
    org_id, ws_id, _ = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    svc.upsert_quota(ws_id, "tokens", 10)
    assert svc.increment_quota(ws_id, "tokens", 5) is True
    assert svc.increment_quota(ws_id, "tokens", 5) is True
    # Now at 10/10 — next increment should fail
    assert svc.increment_quota(ws_id, "tokens", 1) is False


def test_providers_quota_reset(provider_db):
    """reset_quota sets current_value back to 0."""
    db_file, _ = provider_db
    org_id, ws_id, _ = _seed_org_ws_user(db_file)

    from backend.core.providers import ProviderService
    svc = ProviderService()
    svc._master_key = os.urandom(32)

    svc.upsert_quota(ws_id, "requests", 50)
    svc.increment_quota(ws_id, "requests", 30)
    svc.reset_quota(ws_id, "requests")

    allowed, current, limit = svc.check_quota(ws_id, "requests")
    assert current == 0
    assert allowed is True


# ═══════════════════════════════════════════════════════════════════════════════
# 4. policy.py tests
# ═══════════════════════════════════════════════════════════════════════════════


def test_policy_default_allow(policy_db):
    """With no policies, evaluate returns ALLOW."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="j1", node_id="n1",
    )
    decision = engine.evaluate("web_search", {"query": "hello"}, ctx)
    assert decision.result == PolicyResult.ALLOW


def test_policy_rate_limit(policy_db):
    """Built-in: deny after 100 tool calls in a node."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult, _MAX_TOOL_CALLS_PER_NODE
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="j1", node_id="n1",
        tool_call_count=_MAX_TOOL_CALLS_PER_NODE,  # at limit
    )
    decision = engine.evaluate("any_tool", {}, ctx)
    assert decision.result == PolicyResult.DENY
    assert "Rate limit" in decision.reason


def test_policy_bulk_delete_limit(policy_db):
    """Built-in: deny >10 file deletes in a single node."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult, _MAX_DELETES_PER_NODE
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="j1", node_id="n1",
        _delete_count=_MAX_DELETES_PER_NODE,  # at limit
    )
    decision = engine.evaluate("delete_file", {}, ctx)
    assert decision.result == PolicyResult.DENY
    assert "delete limit" in decision.reason.lower()


def test_policy_strict_local_blocks_egress(policy_db):
    """Built-in: strict-local mode blocks egress tools."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="strict-local",
        job_id="j1", node_id="n1",
    )
    for tool in ("web_search", "browser", "gmail", "gemini"):
        decision = engine.evaluate(tool, {}, ctx)
        assert decision.result == PolicyResult.DENY, f"{tool} should be denied in strict-local"
        assert "egress" in decision.reason.lower()


def test_policy_hybrid_allows_egress(policy_db):
    """Hybrid mode does not block egress tools (built-in)."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="j1", node_id="n1",
    )
    decision = engine.evaluate("web_search", {"query": "test"}, ctx)
    assert decision.result == PolicyResult.ALLOW


def test_policy_ssrf_localhost(policy_db):
    """Built-in: SSRF protection blocks localhost URLs."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="j1", node_id="n1",
    )
    decision = engine.evaluate("browser", {"url": "http://localhost:8080/admin"}, ctx)
    assert decision.result == PolicyResult.DENY
    assert "SSRF" in decision.reason


def test_policy_ssrf_127_0_0_1(policy_db):
    """SSRF blocks 127.0.0.1."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="j1", node_id="n1",
    )
    decision = engine.evaluate("browser", {"url": "http://127.0.0.1:3000"}, ctx)
    assert decision.result == PolicyResult.DENY


def test_policy_ssrf_file_scheme(policy_db):
    """SSRF blocks file:// URIs."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="j1", node_id="n1",
    )
    decision = engine.evaluate("browser", {"url": "file:///etc/passwd"}, ctx)
    assert decision.result == PolicyResult.DENY


def test_policy_ssrf_link_local(policy_db):
    """SSRF blocks 169.254.x.x (link-local / cloud metadata)."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="j1", node_id="n1",
    )
    decision = engine.evaluate("browser", {"url": "http://169.254.169.254/latest/meta-data/"}, ctx)
    assert decision.result == PolicyResult.DENY


def test_policy_file_write_outside_job_dir(policy_db):
    """Built-in: file writes outside the job directory are denied."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="job-123", node_id="n1",
    )
    decision = engine.evaluate("write_file", {"path": "/etc/crontab"}, ctx)
    assert decision.result == PolicyResult.DENY
    assert "outside job directory" in decision.reason.lower()


def test_policy_file_write_inside_job_dir_allowed(policy_db):
    """File writes inside the job directory are allowed."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    ctx = PolicyContext(
        workspace_id="ws1", deployment_mode="hybrid",
        job_id="job-123", node_id="n1",
    )
    decision = engine.evaluate("write_file", {"path": "/data/jobs/job-123/output.txt"}, ctx)
    assert decision.result == PolicyResult.ALLOW


def test_policy_create_and_evaluate_deny(policy_db):
    """Create a workspace deny policy and verify evaluate uses it."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()

    # Seed workspace
    org_id, ws_id, user_id = _seed_org_ws_user(policy_db)

    # Create a policy that denies 'dangerous_tool'
    policy_id = engine.create_policy(
        workspace_id=ws_id,
        name="block-dangerous",
        description="Block dangerous_tool",
        conditions={"tool": "dangerous_tool"},
        actions={"type": "deny", "reason": "This tool is banned."},
        priority=10,
    )
    assert policy_id

    ctx = PolicyContext(
        workspace_id=ws_id, deployment_mode="hybrid",
        job_id="j1", node_id="n1",
    )
    decision = engine.evaluate("dangerous_tool", {}, ctx)
    assert decision.result == PolicyResult.DENY
    assert decision.policy_id == policy_id
    assert "banned" in decision.reason.lower()


def test_policy_create_and_evaluate_require_approval(policy_db):
    """Create a workspace require_approval policy and verify approval is created."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    org_id, ws_id, user_id = _seed_org_ws_user(policy_db)

    engine.create_policy(
        workspace_id=ws_id,
        name="approve-deploys",
        description="Require approval for deploy tool",
        conditions={"tool": "deploy"},
        actions={"type": "require_approval", "reason": "Needs manager sign-off.", "expires_minutes": 5},
        priority=5,
    )

    ctx = PolicyContext(
        workspace_id=ws_id, deployment_mode="hybrid",
        job_id="j1", node_id="n1",
    )
    decision = engine.evaluate("deploy", {}, ctx)
    assert decision.result == PolicyResult.REQUIRE_APPROVAL
    assert decision.approval_id is not None


def test_policy_create_dry_run_policy(policy_db):
    """Create a dry_run workspace policy."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    org_id, ws_id, user_id = _seed_org_ws_user(policy_db)

    engine.create_policy(
        workspace_id=ws_id,
        name="dry-run-writes",
        description="Dry-run all file writes",
        conditions={"tool": ["write_file", "save_file"]},
        actions={"type": "dry_run", "reason": "Dry run mode active."},
        priority=1,
    )

    ctx = PolicyContext(
        workspace_id=ws_id, deployment_mode="hybrid",
        job_id="j1", node_id="n1",
    )
    decision = engine.evaluate("write_file", {"path": "/data/jobs/j1/out.txt"}, ctx)
    assert decision.result == PolicyResult.DRY_RUN


def test_policy_list_and_delete(policy_db):
    """list_policies returns all workspace policies; delete_policy removes one."""
    from backend.core.policy import PolicyEngine
    engine = PolicyEngine()
    org_id, ws_id, _ = _seed_org_ws_user(policy_db)

    pid1 = engine.create_policy(ws_id, "p1", "desc1", {}, {"type": "allow"}, priority=1)
    pid2 = engine.create_policy(ws_id, "p2", "desc2", {}, {"type": "allow"}, priority=2)

    policies = engine.list_policies(ws_id)
    ids = {p["id"] for p in policies}
    assert pid1 in ids and pid2 in ids

    engine.delete_policy(pid1)
    policies = engine.list_policies(ws_id)
    ids = {p["id"] for p in policies}
    assert pid1 not in ids
    assert pid2 in ids


def test_policy_update_enabled(policy_db):
    """update_policy toggles the enabled flag; disabled policies are skipped."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    org_id, ws_id, _ = _seed_org_ws_user(policy_db)

    pid = engine.create_policy(
        ws_id, "toggle-me", "Toggle test",
        conditions={"tool": "risky_tool"},
        actions={"type": "deny", "reason": "blocked"},
        priority=10,
    )

    ctx = PolicyContext(workspace_id=ws_id, deployment_mode="hybrid", job_id="j1", node_id="n1")

    # Should deny when enabled
    decision = engine.evaluate("risky_tool", {}, ctx)
    assert decision.result == PolicyResult.DENY

    # Disable and re-check
    engine.update_policy(pid, enabled=False)
    decision = engine.evaluate("risky_tool", {}, ctx)
    assert decision.result == PolicyResult.ALLOW


def test_policy_approval_lifecycle(policy_db):
    """Full approval lifecycle: create -> check -> decide -> check again."""
    from backend.core.policy import PolicyEngine
    engine = PolicyEngine()
    _, _, user_id = _seed_org_ws_user(policy_db)

    approval_id = engine.create_approval(
        policy_id=None,
        job_id="j1",
        node_id="n1",
        tool_call={"name": "deploy", "args": {}},
        expires_minutes=30,
    )

    assert engine.check_approval(approval_id) == "pending"

    engine.decide_approval(approval_id, decided_by=user_id, approved=True, reason="Looks good")
    assert engine.check_approval(approval_id) == "approved"


def test_policy_approval_deny(policy_db):
    """Approval can be denied."""
    from backend.core.policy import PolicyEngine
    engine = PolicyEngine()
    _, _, user_id = _seed_org_ws_user(policy_db)

    approval_id = engine.create_approval(
        policy_id=None, job_id="j1", node_id="n1",
        tool_call={"name": "nuke", "args": {}}, expires_minutes=30,
    )
    engine.decide_approval(approval_id, decided_by=user_id, approved=False, reason="Too risky")
    assert engine.check_approval(approval_id) == "denied"


def test_policy_approval_cannot_decide_twice(policy_db):
    """Deciding on a non-pending approval raises ValueError."""
    from backend.core.policy import PolicyEngine
    engine = PolicyEngine()
    _, _, user_id = _seed_org_ws_user(policy_db)

    approval_id = engine.create_approval(
        policy_id=None, job_id="j1", node_id="n1",
        tool_call={"name": "x", "args": {}}, expires_minutes=30,
    )
    engine.decide_approval(approval_id, user_id, True)
    with pytest.raises(ValueError, match="current status is 'approved'"):
        engine.decide_approval(approval_id, user_id, False)


def test_policy_approval_expiry(policy_db):
    """Expired approvals are lazily marked as expired on check."""
    from backend.core.policy import PolicyEngine
    engine = PolicyEngine()

    # Create an approval that expires immediately (0 minutes)
    approval_id = engine.create_approval(
        policy_id=None, job_id="j1", node_id="n1",
        tool_call={"name": "x", "args": {}}, expires_minutes=0,
    )
    # check_approval should lazily expire it
    status = engine.check_approval(approval_id)
    assert status == "expired"


def test_policy_expire_stale_approvals(policy_db):
    """expire_stale_approvals batch-expires past-due approvals."""
    from backend.core.policy import PolicyEngine
    engine = PolicyEngine()

    # Create two approvals with 0-minute expiry (already expired)
    a1 = engine.create_approval(None, "j1", "n1", {"name": "x", "args": {}}, expires_minutes=0)
    a2 = engine.create_approval(None, "j2", "n2", {"name": "y", "args": {}}, expires_minutes=0)
    # Create one with long expiry
    a3 = engine.create_approval(None, "j3", "n3", {"name": "z", "args": {}}, expires_minutes=9999)

    count = engine.expire_stale_approvals()
    assert count >= 2

    assert engine.check_approval(a1) == "expired"
    assert engine.check_approval(a2) == "expired"
    assert engine.check_approval(a3) == "pending"


def test_policy_check_approval_not_found(policy_db):
    """check_approval raises ValueError for unknown approval_id."""
    from backend.core.policy import PolicyEngine
    engine = PolicyEngine()
    with pytest.raises(ValueError, match="not found"):
        engine.check_approval("nonexistent-approval-id")


def test_policy_priority_ordering(policy_db):
    """Higher-priority policies are evaluated first."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    org_id, ws_id, _ = _seed_org_ws_user(policy_db)

    # Low-priority: allow
    engine.create_policy(
        ws_id, "allow-tool", "Allow by default",
        conditions={"tool": "multi_tool"},
        actions={"type": "allow", "reason": "allowed"},
        priority=1,
    )
    # High-priority: deny
    engine.create_policy(
        ws_id, "deny-tool", "Deny first",
        conditions={"tool": "multi_tool"},
        actions={"type": "deny", "reason": "denied by high-priority"},
        priority=100,
    )

    ctx = PolicyContext(workspace_id=ws_id, deployment_mode="hybrid", job_id="j1", node_id="n1")
    decision = engine.evaluate("multi_tool", {}, ctx)
    assert decision.result == PolicyResult.DENY
    assert "high-priority" in decision.reason


def test_policy_condition_deployment_mode(policy_db):
    """Workspace policy with deployment_mode condition fires only when matched."""
    from backend.core.policy import PolicyEngine, PolicyContext, PolicyResult
    engine = PolicyEngine()
    org_id, ws_id, _ = _seed_org_ws_user(policy_db)

    engine.create_policy(
        ws_id, "cloud-only-deny", "Deny in cloud mode",
        conditions={"tool": "local_tool", "deployment_mode": "cloud"},
        actions={"type": "deny", "reason": "not allowed in cloud"},
        priority=10,
    )

    # hybrid mode: policy should NOT fire
    ctx_hybrid = PolicyContext(workspace_id=ws_id, deployment_mode="hybrid", job_id="j1", node_id="n1")
    assert engine.evaluate("local_tool", {}, ctx_hybrid).result == PolicyResult.ALLOW

    # cloud mode: policy SHOULD fire
    ctx_cloud = PolicyContext(workspace_id=ws_id, deployment_mode="cloud", job_id="j1", node_id="n1")
    assert engine.evaluate("local_tool", {}, ctx_cloud).result == PolicyResult.DENY
