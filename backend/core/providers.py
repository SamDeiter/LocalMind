"""
Secrets & Provider Configs module — LocalMind enterprise task worker.

Manages encrypted secrets (API keys, tokens), provider configurations
(Gemini, Ollama, Slack, Google OAuth), and per-workspace quotas.

Secrets are stored AES-256-GCM encrypted. The LLM NEVER sees raw credentials.
get_secret() and get_oauth_token() are server-side only — never expose to LLM
or return in API responses.

Tables are created by backend/core/schema.py (init_phase0_schema).
"""

import base64
import hashlib
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.config import DB_PATH, WORKSPACE_ROOT

logger = logging.getLogger("localmind.core.providers")

# ── Encryption availability ───────────────────────────────────────────────────

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    logger.warning(
        "cryptography library not available. Secrets will be stored as "
        "base64-encoded plaintext WITHOUT encryption. Install with: "
        "pip install cryptography"
    )

# AES-256-GCM parameters
_NONCE_BYTES = 12   # 96-bit nonce (GCM standard)
_KEY_BYTES = 32     # 256-bit key
_PBKDF2_ITERATIONS = 100_000
_SALT_BYTES = 16

# Prefix stored alongside ciphertext to distinguish encrypted vs plaintext-fallback blobs
_ENCRYPTED_PREFIX = "enc1:"   # versioned prefix for future-proofing
_PLAINTEXT_PREFIX = "b64v0:"  # fallback when cryptography is absent


# ── Connection helper ─────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ── Encryption helpers ────────────────────────────────────────────────────────

def _derive_key(master_password: str, salt: bytes) -> bytes:
    """Derive a 256-bit key from master_password using PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        master_password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=_KEY_BYTES,
    )


def encrypt_value(plaintext: str, master_key: bytes) -> str:
    """
    Encrypt plaintext using AES-256-GCM.

    Returns a base64-encoded blob: prefix + base64(salt + nonce + ciphertext + tag).
    The salt is stored alongside the ciphertext so each secret uses a unique
    derived key (master_key is the root secret, salt provides per-value KDF).

    Falls back to base64-only storage with a warning if cryptography is unavailable.
    """
    if not _CRYPTO_AVAILABLE:
        logger.warning("Storing secret as base64 plaintext — cryptography library missing")
        encoded = base64.b64encode(plaintext.encode("utf-8")).decode("ascii")
        return _PLAINTEXT_PREFIX + encoded

    salt = os.urandom(_SALT_BYTES)
    derived = _derive_key(master_key.hex(), salt)  # use hex repr as stable string
    nonce = os.urandom(_NONCE_BYTES)
    aesgcm = AESGCM(derived)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    blob = base64.b64encode(salt + nonce + ciphertext_with_tag).decode("ascii")
    return _ENCRYPTED_PREFIX + blob


def decrypt_value(encrypted: str, master_key: bytes) -> str:
    """
    Decrypt a value produced by encrypt_value().

    Handles both encrypted (enc1:) and plaintext-fallback (b64v0:) blobs.
    Raises ValueError on malformed input or decryption failure.
    """
    if encrypted.startswith(_PLAINTEXT_PREFIX):
        raw = encrypted[len(_PLAINTEXT_PREFIX):]
        return base64.b64decode(raw.encode("ascii")).decode("utf-8")

    if not encrypted.startswith(_ENCRYPTED_PREFIX):
        raise ValueError(f"Unknown encryption prefix in stored value: {encrypted[:10]!r}")

    if not _CRYPTO_AVAILABLE:
        raise RuntimeError(
            "Cannot decrypt: cryptography library not installed. "
            "Install with: pip install cryptography"
        )

    raw = encrypted[len(_ENCRYPTED_PREFIX):]
    blob = base64.b64decode(raw.encode("ascii"))

    # Layout: salt (16) | nonce (12) | ciphertext+tag (rest)
    salt = blob[:_SALT_BYTES]
    nonce = blob[_SALT_BYTES : _SALT_BYTES + _NONCE_BYTES]
    ciphertext_with_tag = blob[_SALT_BYTES + _NONCE_BYTES :]

    derived = _derive_key(master_key.hex(), salt)
    aesgcm = AESGCM(derived)
    try:
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except Exception as exc:
        raise ValueError(f"Decryption failed (wrong key or corrupted data): {exc}") from exc

    return plaintext_bytes.decode("utf-8")


def get_master_key() -> bytes:
    """
    Return the AES-256 master key used for all secret encryption.

    Resolution order:
    1. LOCALMIND_MASTER_KEY env var (hex-encoded 32-byte key).
    2. WORKSPACE_ROOT/.master_key file (auto-generated on first run).

    The key file is created with mode 0o600 (owner-readable only).
    """
    env_key = os.environ.get("LOCALMIND_MASTER_KEY", "").strip()
    if env_key:
        try:
            key_bytes = bytes.fromhex(env_key)
        except ValueError as exc:
            raise ValueError(
                "LOCALMIND_MASTER_KEY must be a hex-encoded 32-byte (256-bit) key"
            ) from exc
        if len(key_bytes) != _KEY_BYTES:
            raise ValueError(
                f"LOCALMIND_MASTER_KEY must be exactly {_KEY_BYTES} bytes "
                f"({_KEY_BYTES * 2} hex chars); got {len(key_bytes)} bytes"
            )
        return key_bytes

    key_file = WORKSPACE_ROOT / ".master_key"
    if key_file.exists():
        try:
            raw = key_file.read_text().strip()
            key_bytes = bytes.fromhex(raw)
            if len(key_bytes) != _KEY_BYTES:
                raise ValueError("Stored key has wrong length")
            return key_bytes
        except Exception as exc:
            raise RuntimeError(
                f"Failed to read master key from {key_file}: {exc}. "
                "Delete the file to generate a new key (this will invalidate all stored secrets)."
            ) from exc

    # First run: generate and persist a new key
    logger.info("Generating new master key at %s", key_file)
    key_bytes = os.urandom(_KEY_BYTES)
    try:
        key_file.write_text(key_bytes.hex())
        key_file.chmod(0o600)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to write master key to {key_file}: {exc}"
        ) from exc
    logger.warning(
        "New master key generated and stored at %s — back this up! "
        "Losing it makes all stored secrets unrecoverable.",
        key_file,
    )
    return key_bytes


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class SecretRef:
    """Metadata record for a stored secret. Never contains plaintext."""
    id: str
    workspace_id: str
    name: str
    provider: str
    # encrypted_value is held internally for server-side decrypt; excluded from to_dict()
    _encrypted_value: str = field(repr=False)
    created_by: Optional[str]
    created_at: str
    rotated_at: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SecretRef":
        return cls(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            provider=row["provider"],
            _encrypted_value=row["encrypted_value"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            rotated_at=row["rotated_at"],
        )

    def to_dict(self) -> dict:
        """Safe representation — NEVER includes encrypted_value or plaintext."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "provider": self.provider,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "rotated_at": self.rotated_at,
        }


@dataclass
class ProviderConfig:
    id: str
    workspace_id: str
    provider: str
    config: dict
    secret_ref_id: Optional[str]
    enabled: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ProviderConfig":
        return cls(
            id=row["id"],
            workspace_id=row["workspace_id"],
            provider=row["provider"],
            config=json.loads(row["config_json"]),
            secret_ref_id=row["secret_ref_id"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "provider": self.provider,
            "config": self.config,
            "secret_ref_id": self.secret_ref_id,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class OAuthCredential:
    id: str
    workspace_id: str
    user_id: str
    provider: str
    # encrypted_token_json held internally; excluded from to_dict()
    _encrypted_token_json: str = field(repr=False)
    scopes: list[str]
    expires_at: Optional[str]
    created_at: str
    refreshed_at: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "OAuthCredential":
        scopes_raw = row["scopes"]
        try:
            scopes = json.loads(scopes_raw)
        except (json.JSONDecodeError, TypeError):
            scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()]
        return cls(
            id=row["id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            provider=row["provider"],
            _encrypted_token_json=row["encrypted_token_json"],
            scopes=scopes,
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            refreshed_at=row["refreshed_at"],
        )

    def to_dict(self) -> dict:
        """Safe representation — NEVER includes token data."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "provider": self.provider,
            "scopes": self.scopes,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "refreshed_at": self.refreshed_at,
        }


@dataclass
class Quota:
    workspace_id: str
    resource: str
    limit_value: int
    current_value: int
    reset_at: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Quota":
        return cls(
            workspace_id=row["workspace_id"],
            resource=row["resource"],
            limit_value=row["limit_value"],
            current_value=row["current_value"],
            reset_at=row["reset_at"],
        )

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "resource": self.resource,
            "limit_value": self.limit_value,
            "current_value": self.current_value,
            "reset_at": self.reset_at,
        }


# ── ProviderService ───────────────────────────────────────────────────────────

class ProviderService:
    """
    Central service for secrets, provider configs, OAuth tokens, and quotas.

    All encryption/decryption uses the master key returned by get_master_key().
    The master key is loaded once per ProviderService instance and cached.

    SECURITY CONTRACT:
    - get_secret() and get_oauth_token() return plaintext credentials.
      They MUST only be called server-side (e.g. when constructing an API
      request to Gemini/Slack). Never pass their return values to the LLM,
      log them, or include them in HTTP responses to clients.
    - list_secrets() returns metadata only — no encrypted or plaintext values.
    """

    def __init__(self) -> None:
        self._master_key: Optional[bytes] = None

    @property
    def master_key(self) -> bytes:
        """Lazy-load and cache the master key."""
        if self._master_key is None:
            self._master_key = get_master_key()
        return self._master_key

    # ── Internal helpers ──────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())

    # ── Secrets ───────────────────────────────────────────────────

    def store_secret(
        self,
        workspace_id: str,
        name: str,
        provider: str,
        plaintext_value: str,
        created_by: Optional[str] = None,
    ) -> SecretRef:
        """
        Encrypt plaintext_value and store it as a named secret for the workspace.

        Raises sqlite3.IntegrityError if (workspace_id, name) already exists.
        Use rotate_secret() to update an existing secret's value.
        """
        encrypted = encrypt_value(plaintext_value, self.master_key)
        now = self._now()
        secret_id = self._new_id()

        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO secret_refs
                    (id, workspace_id, name, provider, encrypted_value, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (secret_id, workspace_id, name, provider, encrypted, created_by, now),
            )

        logger.info(
            "Stored secret id=%s name=%r provider=%s workspace=%s",
            secret_id, name, provider, workspace_id,
        )
        return SecretRef(
            id=secret_id,
            workspace_id=workspace_id,
            name=name,
            provider=provider,
            _encrypted_value=encrypted,
            created_by=created_by,
            created_at=now,
            rotated_at=None,
        )

    def get_secret(self, secret_id: str) -> str:
        """
        Return the decrypted plaintext for a secret.

        SERVER-SIDE ONLY — never return this value to the LLM or in an API response.
        Raises KeyError if the secret does not exist.
        """
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT encrypted_value FROM secret_refs WHERE id = ?",
                (secret_id,),
            ).fetchone()

        if row is None:
            raise KeyError(f"Secret not found: {secret_id!r}")

        return decrypt_value(row["encrypted_value"], self.master_key)

    def rotate_secret(self, secret_id: str, new_plaintext: str) -> None:
        """
        Replace a secret's encrypted value with a freshly encrypted new_plaintext.
        Updates rotated_at timestamp.
        Raises KeyError if the secret does not exist.
        """
        # Verify it exists first
        with _get_conn() as conn:
            exists = conn.execute(
                "SELECT id FROM secret_refs WHERE id = ?", (secret_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"Secret not found: {secret_id!r}")

        encrypted = encrypt_value(new_plaintext, self.master_key)
        now = self._now()

        with _get_conn() as conn:
            conn.execute(
                "UPDATE secret_refs SET encrypted_value = ?, rotated_at = ? WHERE id = ?",
                (encrypted, now, secret_id),
            )

        logger.info("Rotated secret id=%s at %s", secret_id, now)

    def delete_secret(self, secret_id: str) -> None:
        """
        Delete a secret by ID.
        Note: provider_configs referencing this secret_ref_id will have their
        secret_ref_id set to NULL (foreign key with no CASCADE, caller should
        update or remove associated provider configs first if needed).
        """
        with _get_conn() as conn:
            conn.execute("DELETE FROM secret_refs WHERE id = ?", (secret_id,))

        logger.info("Deleted secret id=%s", secret_id)

    def list_secrets(self, workspace_id: str) -> list[SecretRef]:
        """
        Return metadata for all secrets in a workspace.
        NEVER exposes encrypted_value or decrypted plaintext.
        """
        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, name, provider, encrypted_value,
                       created_by, created_at, rotated_at
                FROM secret_refs
                WHERE workspace_id = ?
                ORDER BY created_at DESC
                """,
                (workspace_id,),
            ).fetchall()

        return [SecretRef.from_row(r) for r in rows]

    # ── Provider Configs ──────────────────────────────────────────

    def set_provider_config(
        self,
        workspace_id: str,
        provider: str,
        config: dict,
        secret_ref_id: Optional[str] = None,
        enabled: bool = True,
    ) -> ProviderConfig:
        """
        Upsert a provider configuration for the workspace.
        Uses INSERT OR REPLACE so calling this again with new values updates in-place.
        """
        now = self._now()
        config_id = self._new_id()
        config_json = json.dumps(config)

        with _get_conn() as conn:
            # Check if a row already exists (to preserve id and created_at)
            existing = conn.execute(
                "SELECT id, created_at FROM provider_configs WHERE workspace_id = ? AND provider = ?",
                (workspace_id, provider),
            ).fetchone()

            if existing:
                config_id = existing["id"]
                created_at = existing["created_at"]
                conn.execute(
                    """
                    UPDATE provider_configs
                    SET config_json = ?, secret_ref_id = ?, enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (config_json, secret_ref_id, int(enabled), now, config_id),
                )
            else:
                created_at = now
                conn.execute(
                    """
                    INSERT INTO provider_configs
                        (id, workspace_id, provider, config_json, secret_ref_id,
                         enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (config_id, workspace_id, provider, config_json,
                     secret_ref_id, int(enabled), created_at, now),
                )

        logger.info(
            "Set provider config provider=%s workspace=%s enabled=%s",
            provider, workspace_id, enabled,
        )
        return ProviderConfig(
            id=config_id,
            workspace_id=workspace_id,
            provider=provider,
            config=config,
            secret_ref_id=secret_ref_id,
            enabled=enabled,
            created_at=created_at,
            updated_at=now,
        )

    def get_provider_config(
        self, workspace_id: str, provider: str
    ) -> Optional[ProviderConfig]:
        """Return the ProviderConfig for (workspace, provider), or None if not configured."""
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, workspace_id, provider, config_json, secret_ref_id,
                       enabled, created_at, updated_at
                FROM provider_configs
                WHERE workspace_id = ? AND provider = ?
                """,
                (workspace_id, provider),
            ).fetchone()

        if row is None:
            return None
        return ProviderConfig.from_row(row)

    def is_provider_enabled(self, workspace_id: str, provider: str) -> bool:
        """Return True if the provider is configured and enabled for the workspace."""
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT enabled FROM provider_configs WHERE workspace_id = ? AND provider = ?",
                (workspace_id, provider),
            ).fetchone()

        if row is None:
            return False
        return bool(row["enabled"])

    # ── OAuth Credentials ─────────────────────────────────────────

    def store_oauth_token(
        self,
        workspace_id: str,
        user_id: str,
        provider: str,
        token_json: dict,
        scopes: list[str],
        expires_at: Optional[str] = None,
    ) -> OAuthCredential:
        """
        Encrypt and store an OAuth token dict for a user+provider combination.
        Upserts: if a credential already exists for (workspace, user, provider),
        it is refreshed in-place.

        token_json should contain the full token payload (access_token,
        refresh_token, token_type, etc.) — it is encrypted before storage.
        """
        plaintext = json.dumps(token_json)
        encrypted = encrypt_value(plaintext, self.master_key)
        scopes_json = json.dumps(scopes)
        now = self._now()

        with _get_conn() as conn:
            existing = conn.execute(
                """
                SELECT id, created_at FROM oauth_credentials
                WHERE workspace_id = ? AND user_id = ? AND provider = ?
                """,
                (workspace_id, user_id, provider),
            ).fetchone()

            if existing:
                cred_id = existing["id"]
                created_at = existing["created_at"]
                conn.execute(
                    """
                    UPDATE oauth_credentials
                    SET encrypted_token_json = ?, scopes = ?, expires_at = ?, refreshed_at = ?
                    WHERE id = ?
                    """,
                    (encrypted, scopes_json, expires_at, now, cred_id),
                )
            else:
                cred_id = self._new_id()
                created_at = now
                conn.execute(
                    """
                    INSERT INTO oauth_credentials
                        (id, workspace_id, user_id, provider, encrypted_token_json,
                         scopes, expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (cred_id, workspace_id, user_id, provider, encrypted,
                     scopes_json, expires_at, created_at),
                )

        logger.info(
            "Stored OAuth token provider=%s user=%s workspace=%s",
            provider, user_id, workspace_id,
        )
        return OAuthCredential(
            id=cred_id,
            workspace_id=workspace_id,
            user_id=user_id,
            provider=provider,
            _encrypted_token_json=encrypted,
            scopes=scopes,
            expires_at=expires_at,
            created_at=created_at,
            refreshed_at=now if existing else None,
        )

    def get_oauth_token(
        self, workspace_id: str, user_id: str, provider: str
    ) -> Optional[dict]:
        """
        Return the decrypted token dict for a user+provider, or None if not stored.

        SERVER-SIDE ONLY — never pass this to the LLM or include in API responses.
        """
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT encrypted_token_json FROM oauth_credentials
                WHERE workspace_id = ? AND user_id = ? AND provider = ?
                """,
                (workspace_id, user_id, provider),
            ).fetchone()

        if row is None:
            return None

        plaintext = decrypt_value(row["encrypted_token_json"], self.master_key)
        return json.loads(plaintext)

    # ── Quotas ────────────────────────────────────────────────────

    def check_quota(
        self, workspace_id: str, resource: str
    ) -> tuple[bool, int, int]:
        """
        Check whether the workspace is within quota for the given resource.

        Returns (allowed, current_value, limit_value).
        - allowed=True  → current_value < limit_value (usage is within quota)
        - allowed=False → current_value >= limit_value (quota exhausted)

        If no quota row exists, returns (True, 0, -1) — unlimited by default.
        -1 limit_value means "no quota configured / unlimited".
        """
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT limit_value, current_value FROM quotas WHERE workspace_id = ? AND resource = ?",
                (workspace_id, resource),
            ).fetchone()

        if row is None:
            return (True, 0, -1)

        limit = row["limit_value"]
        current = row["current_value"]
        allowed = current < limit
        return (allowed, current, limit)

    def increment_quota(
        self, workspace_id: str, resource: str, amount: int = 1
    ) -> bool:
        """
        Attempt to increment current_value by amount.

        Returns True if the increment was applied (still within quota).
        Returns False if the increment would meet or exceed the limit — no change is made.

        If no quota row exists, increments are always accepted (returns True)
        but no row is created. To enforce a quota, first create a row via
        set_provider_config or directly via the quotas table.
        """
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT limit_value, current_value FROM quotas WHERE workspace_id = ? AND resource = ?",
                (workspace_id, resource),
            ).fetchone()

            if row is None:
                # No quota configured — allow freely
                return True

            limit = row["limit_value"]
            current = row["current_value"]

            if current + amount > limit:
                logger.warning(
                    "Quota exceeded for workspace=%s resource=%s current=%d limit=%d requested=%d",
                    workspace_id, resource, current, limit, amount,
                )
                return False

            conn.execute(
                "UPDATE quotas SET current_value = current_value + ? WHERE workspace_id = ? AND resource = ?",
                (amount, workspace_id, resource),
            )

        return True

    def reset_quota(self, workspace_id: str, resource: str) -> None:
        """
        Reset current_value to 0 and update reset_at for the given resource.
        No-op if the quota row does not exist.
        """
        now = self._now()
        with _get_conn() as conn:
            conn.execute(
                "UPDATE quotas SET current_value = 0, reset_at = ? WHERE workspace_id = ? AND resource = ?",
                (now, workspace_id, resource),
            )

        logger.info("Reset quota workspace=%s resource=%s at %s", workspace_id, resource, now)

    def upsert_quota(
        self,
        workspace_id: str,
        resource: str,
        limit_value: int,
        reset_at: Optional[str] = None,
    ) -> Quota:
        """
        Create or update a quota row for (workspace, resource).
        Preserves current_value if the row already exists.
        """
        now = self._now()
        with _get_conn() as conn:
            existing = conn.execute(
                "SELECT current_value FROM quotas WHERE workspace_id = ? AND resource = ?",
                (workspace_id, resource),
            ).fetchone()

            if existing:
                current = existing["current_value"]
                conn.execute(
                    "UPDATE quotas SET limit_value = ?, reset_at = ? WHERE workspace_id = ? AND resource = ?",
                    (limit_value, reset_at, workspace_id, resource),
                )
            else:
                current = 0
                conn.execute(
                    "INSERT INTO quotas (workspace_id, resource, limit_value, current_value, reset_at) VALUES (?, ?, ?, ?, ?)",
                    (workspace_id, resource, limit_value, current, reset_at),
                )

        return Quota(
            workspace_id=workspace_id,
            resource=resource,
            limit_value=limit_value,
            current_value=current,
            reset_at=reset_at,
        )


# ── Module-level singleton (optional convenience) ─────────────────────────────

_service: Optional[ProviderService] = None


def get_provider_service() -> ProviderService:
    """Return the module-level ProviderService singleton, creating it on first call."""
    global _service
    if _service is None:
        _service = ProviderService()
    return _service
