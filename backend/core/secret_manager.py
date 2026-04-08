"""
API Key & Secret Lifecycle Manager — LocalMind enterprise task worker.

Provides four components:

1. **SecretScrubber** — regex-based scrubbing of known secret patterns from
   any outbound text.  Applied before every string exits the system.

2. **SecretLifecycleManager** — full CRUD + rotation, revocation, and
   hygiene checks for encrypted secrets stored in the ``secret_refs`` table.
   Builds on the encryption primitives in ``backend.core.providers``.

3. **StartupSecretScanner** — scans config files and environment variables
   for plaintext secrets that should be stored in the encrypted vault.

4. **SecretEncryption** — standalone AES-256-GCM encrypt/decrypt helper
   with graceful fallback to base64 when the ``cryptography`` package is
   absent.

All DB access follows the project convention: WAL mode, busy_timeout,
foreign_keys ON.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from backend.config import DB_PATH, WORKSPACE_ROOT

logger = logging.getLogger("localmind.core.secret_manager")

# ── Configuration ────────────────────────────────────────────────────────────

SECRET_ROTATION_REMINDER_DAYS = int(
    os.getenv("SECRET_ROTATION_REMINDER_DAYS", "90")
)
SECRET_GRACE_PERIOD_DAYS = int(os.getenv("SECRET_GRACE_PERIOD_DAYS", "7"))
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

# ── Crypto availability ─────────────────────────────────────────────────────

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    logger.warning(
        "cryptography library not installed. SecretEncryption will fall back "
        "to base64 encoding (NOT secure). Install with: pip install cryptography"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SecretEncryption — AES-256-GCM helper
# ═══════════════════════════════════════════════════════════════════════════════


class SecretEncryption:
    """AES-256-GCM encrypt / decrypt helper.

    Wire format (base64-encoded):  ``nonce (12 bytes) || tag (16 bytes) || ciphertext``

    Falls back to plain base64 encoding when the ``cryptography`` library is
    not installed.  A warning is logged on every fallback operation.
    """

    _NONCE_BYTES = 12  # 96-bit nonce (GCM standard)
    _TAG_BYTES = 16    # 128-bit authentication tag

    # ── public API ───────────────────────────────────────────────

    @staticmethod
    def encrypt(plaintext: str, key: bytes) -> str:
        """Encrypt *plaintext* with AES-256-GCM using *key* (32 bytes).

        Returns ``base64(nonce || tag || ciphertext)``.

        If ``cryptography`` is not installed the plaintext is base64-encoded
        with an ``insecure:`` prefix so that :meth:`decrypt` can distinguish
        the two formats.
        """
        if not _CRYPTO_AVAILABLE:
            logger.warning(
                "SecretEncryption.encrypt: using insecure base64 fallback"
            )
            encoded = base64.b64encode(plaintext.encode("utf-8")).decode("ascii")
            return f"insecure:{encoded}"

        if len(key) != 32:
            raise ValueError(
                f"AES-256-GCM requires a 32-byte key; got {len(key)} bytes"
            )

        nonce = os.urandom(SecretEncryption._NONCE_BYTES)
        aesgcm = AESGCM(key)
        # AESGCM.encrypt returns ciphertext || tag
        ct_and_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

        # Split off the tag (last 16 bytes) so our wire format is
        # nonce || tag || ciphertext — matching the spec exactly.
        ciphertext = ct_and_tag[: -SecretEncryption._TAG_BYTES]
        tag = ct_and_tag[-SecretEncryption._TAG_BYTES :]

        blob = nonce + tag + ciphertext
        return base64.b64encode(blob).decode("ascii")

    @staticmethod
    def decrypt(blob: str, key: bytes) -> str:
        """Decrypt a blob produced by :meth:`encrypt`.

        Extracts nonce, tag, and ciphertext, then decrypts with AES-256-GCM.
        Handles both the encrypted format and the insecure base64 fallback.

        Raises:
            ValueError: on decryption failure or malformed input.
            RuntimeError: if the blob is encrypted but ``cryptography`` is
                not installed.
        """
        # Handle insecure fallback blobs
        if blob.startswith("insecure:"):
            raw = blob[len("insecure:"):]
            return base64.b64decode(raw.encode("ascii")).decode("utf-8")

        if not _CRYPTO_AVAILABLE:
            raise RuntimeError(
                "Cannot decrypt: cryptography library not installed. "
                "Install with: pip install cryptography"
            )

        if len(key) != 32:
            raise ValueError(
                f"AES-256-GCM requires a 32-byte key; got {len(key)} bytes"
            )

        raw = base64.b64decode(blob.encode("ascii"))

        min_len = SecretEncryption._NONCE_BYTES + SecretEncryption._TAG_BYTES + 1
        if len(raw) < min_len:
            raise ValueError(
                f"Encrypted blob too short ({len(raw)} bytes); "
                f"expected at least {min_len}"
            )

        nonce = raw[: SecretEncryption._NONCE_BYTES]
        tag = raw[
            SecretEncryption._NONCE_BYTES
            : SecretEncryption._NONCE_BYTES + SecretEncryption._TAG_BYTES
        ]
        ciphertext = raw[
            SecretEncryption._NONCE_BYTES + SecretEncryption._TAG_BYTES :
        ]

        # AESGCM.decrypt expects ciphertext || tag
        ct_and_tag = ciphertext + tag

        aesgcm = AESGCM(key)
        try:
            plaintext_bytes = aesgcm.decrypt(nonce, ct_and_tag, None)
        except Exception as exc:
            raise ValueError(
                f"Decryption failed (wrong key or corrupted data): {exc}"
            ) from exc

        return plaintext_bytes.decode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: encryption key resolution
# ═══════════════════════════════════════════════════════════════════════════════


def _resolve_encryption_key() -> bytes:
    """Return the 32-byte AES key, deriving it from the ENCRYPTION_KEY env var.

    Falls back to the master key from ``backend.core.providers`` if
    ENCRYPTION_KEY is empty.
    """
    if ENCRYPTION_KEY:
        try:
            key_bytes = bytes.fromhex(ENCRYPTION_KEY)
        except ValueError as exc:
            raise ValueError(
                "ENCRYPTION_KEY must be a hex-encoded 32-byte (64 hex char) key"
            ) from exc
        if len(key_bytes) != 32:
            raise ValueError(
                f"ENCRYPTION_KEY must be exactly 32 bytes (64 hex chars); "
                f"got {len(key_bytes)} bytes"
            )
        return key_bytes

    # Fallback to the provider master key
    from backend.core.providers import get_master_key

    return get_master_key()


# ═══════════════════════════════════════════════════════════════════════════════
# DB helper
# ═══════════════════════════════════════════════════════════════════════════════


def _get_conn() -> sqlite3.Connection:
    """Return a SQLite connection with WAL, busy_timeout, and foreign keys."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_lifecycle_columns() -> None:
    """Add lifecycle columns to secret_refs if they do not yet exist.

    This is idempotent and safe to call on every import / startup.
    Columns added:
        - status TEXT DEFAULT 'active'
        - last_used_at TEXT
        - revoked_at TEXT
        - revoke_reason TEXT
        - successor_id TEXT
    """
    conn = _get_conn()
    try:
        # Inspect existing columns
        cursor = conn.execute("PRAGMA table_info(secret_refs)")
        existing = {row["name"] for row in cursor.fetchall()}

        migrations: list[str] = []
        if "status" not in existing:
            migrations.append(
                "ALTER TABLE secret_refs ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )
        if "last_used_at" not in existing:
            migrations.append(
                "ALTER TABLE secret_refs ADD COLUMN last_used_at TEXT"
            )
        if "revoked_at" not in existing:
            migrations.append(
                "ALTER TABLE secret_refs ADD COLUMN revoked_at TEXT"
            )
        if "revoke_reason" not in existing:
            migrations.append(
                "ALTER TABLE secret_refs ADD COLUMN revoke_reason TEXT"
            )
        if "successor_id" not in existing:
            migrations.append(
                "ALTER TABLE secret_refs ADD COLUMN successor_id TEXT"
            )

        for stmt in migrations:
            conn.execute(stmt)
            logger.info("Migrated secret_refs: %s", stmt)

        if migrations:
            conn.commit()
    finally:
        conn.close()


# Run migration at import time (idempotent)
try:
    _ensure_lifecycle_columns()
except Exception:
    # DB may not exist yet (e.g., during tests before schema init).
    # The columns will be created once schema.py runs and this module
    # is re-imported or SecretLifecycleManager is first used.
    logger.debug(
        "secret_manager: deferred lifecycle column migration (DB not ready)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Utility helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SecretScrubber
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ScrubMatch:
    """A single secret pattern match found during scrubbing."""

    pattern_name: str
    position: int
    length: int


@dataclass(frozen=True)
class ScrubResult:
    """Result of scrubbing a string for secrets."""

    scrubbed_text: str
    matches: list[ScrubMatch]
    had_secrets: bool


class SecretScrubber:
    """Regex-based scrubber that redacts known secret patterns.

    Applied to **every** string before it exits the system (API responses,
    logs destined for the LLM, SSE events, etc.).

    Usage::

        result = SecretScrubber.scrub(some_text)
        safe_text = result.scrubbed_text
    """

    PATTERNS: list[tuple[str, re.Pattern[str]]] = [
        ("openai_key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
        ("google_api_key", re.compile(r"AIza[a-zA-Z0-9_-]{35}")),
        ("aws_access_key", re.compile(r"AKIA[A-Z0-9]{16}")),
        ("github_pat", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
        ("github_oauth", re.compile(r"gho_[a-zA-Z0-9]{36}")),
        ("slack_bot_token", re.compile(r"xoxb-[a-zA-Z0-9-]+")),
        ("slack_user_token", re.compile(r"xoxp-[a-zA-Z0-9-]+")),
        ("slack_app_token", re.compile(r"xapp-[a-zA-Z0-9-]+")),
        ("google_oauth_token", re.compile(r"ya29\.[a-zA-Z0-9_-]+")),
        (
            "jwt",
            re.compile(r"eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}"),
        ),
        (
            "password_assignment",
            re.compile(r"(?i)password\s*[=:]\s*\S+"),
        ),
        (
            "secret_assignment",
            re.compile(r"(?i)secret\s*[=:]\s*\S+"),
        ),
    ]

    _REDACTED = "[REDACTED]"

    @classmethod
    def scrub(cls, text: str) -> ScrubResult:
        """Replace all known secret patterns with ``[REDACTED]``.

        Returns a :class:`ScrubResult` containing the scrubbed text, a list
        of :class:`ScrubMatch` objects describing what was found, and a
        boolean indicating whether any secrets were detected.
        """
        if not text:
            return ScrubResult(scrubbed_text=text, matches=[], had_secrets=False)

        matches: list[ScrubMatch] = []

        # Collect all match spans first so we can replace in a single pass
        # from right to left (preserving earlier positions).
        all_spans: list[tuple[int, int, str]] = []  # (start, end, pattern_name)

        for pattern_name, compiled in cls.PATTERNS:
            for m in compiled.finditer(text):
                all_spans.append((m.start(), m.end(), pattern_name))

        if not all_spans:
            return ScrubResult(scrubbed_text=text, matches=[], had_secrets=False)

        # Sort by start position, then by longest match first to handle
        # overlapping patterns.
        all_spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))

        # De-duplicate overlapping spans — keep the longest (first in sorted order).
        merged: list[tuple[int, int, str]] = []
        for start, end, name in all_spans:
            if merged and start < merged[-1][1]:
                # Overlaps with the previous span; skip if fully contained,
                # extend if partially overlapping.
                prev_start, prev_end, prev_name = merged[-1]
                if end > prev_end:
                    merged[-1] = (prev_start, end, prev_name)
                continue
            merged.append((start, end, name))

        # Build matches list (positions are relative to the ORIGINAL text).
        for start, end, name in merged:
            matches.append(
                ScrubMatch(
                    pattern_name=name,
                    position=start,
                    length=end - start,
                )
            )

        # Replace from right to left to preserve positions.
        scrubbed = text
        for start, end, _name in reversed(merged):
            scrubbed = scrubbed[:start] + cls._REDACTED + scrubbed[end:]

        return ScrubResult(
            scrubbed_text=scrubbed,
            matches=matches,
            had_secrets=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SecretLifecycleManager
# ═══════════════════════════════════════════════════════════════════════════════


class SecretLifecycleManager:
    """Manage the full lifecycle of encrypted secrets.

    Operations: create, read metadata, decrypt, rotate (with successor
    tracking), revoke, list, hygiene checks, and purge.

    Encryption uses :class:`SecretEncryption` with the key resolved by
    :func:`_resolve_encryption_key`.
    """

    def __init__(self, key: Optional[bytes] = None) -> None:
        self._key: Optional[bytes] = key

    @property
    def key(self) -> bytes:
        """Lazy-load and cache the encryption key."""
        if self._key is None:
            self._key = _resolve_encryption_key()
        return self._key

    # ── create ───────────────────────────────────────────────────

    def create_secret(
        self,
        workspace_id: str,
        name: str,
        provider: str,
        plaintext_value: str,
        created_by: Optional[str] = None,
    ) -> str:
        """Encrypt *plaintext_value* and insert a new secret.

        Returns the new secret's ID.

        Raises:
            sqlite3.IntegrityError: if ``(workspace_id, name)`` already exists.
        """
        secret_id = _new_id()
        encrypted = SecretEncryption.encrypt(plaintext_value, self.key)
        now = _now_iso()

        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO secret_refs
                    (id, workspace_id, name, provider, encrypted_value,
                     created_by, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (secret_id, workspace_id, name, provider, encrypted,
                 created_by, now),
            )

        logger.info(
            "Created secret id=%s name=%r provider=%s workspace=%s",
            secret_id, name, provider, workspace_id,
        )
        return secret_id

    # ── read metadata ────────────────────────────────────────────

    def get_secret_metadata(self, secret_id: str) -> dict:
        """Return metadata for a secret — everything EXCEPT the encrypted value.

        Raises:
            KeyError: if the secret does not exist.
        """
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, workspace_id, name, provider, created_by,
                       created_at, rotated_at, status, last_used_at,
                       revoked_at, revoke_reason, successor_id
                FROM secret_refs
                WHERE id = ?
                """,
                (secret_id,),
            ).fetchone()

        if row is None:
            raise KeyError(f"Secret not found: {secret_id!r}")

        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "name": row["name"],
            "provider": row["provider"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "rotated_at": row["rotated_at"],
            "status": row["status"],
            "last_used_at": row["last_used_at"],
            "revoked_at": row["revoked_at"],
            "revoke_reason": row["revoke_reason"],
            "successor_id": row["successor_id"],
        }

    # ── decrypt ──────────────────────────────────────────────────

    def decrypt_secret(self, secret_id: str) -> str:
        """Decrypt and return the plaintext value.  Updates ``last_used_at``.

        SERVER-SIDE ONLY — never expose this to the LLM or HTTP responses.

        Raises:
            KeyError: if the secret does not exist.
            ValueError: if the secret has been revoked.
        """
        now = _now_iso()

        with _get_conn() as conn:
            row = conn.execute(
                "SELECT encrypted_value, status FROM secret_refs WHERE id = ?",
                (secret_id,),
            ).fetchone()

            if row is None:
                raise KeyError(f"Secret not found: {secret_id!r}")

            if row["status"] == "revoked":
                raise ValueError(
                    f"Cannot decrypt revoked secret: {secret_id!r}"
                )

            # Update last_used_at
            conn.execute(
                "UPDATE secret_refs SET last_used_at = ? WHERE id = ?",
                (now, secret_id),
            )

        plaintext = SecretEncryption.decrypt(row["encrypted_value"], self.key)
        logger.debug("Decrypted secret id=%s", secret_id)
        return plaintext

    # ── rotate ───────────────────────────────────────────────────

    def rotate_secret(
        self, secret_id: str, new_plaintext_value: str
    ) -> str:
        """Rotate a secret by creating a new one and deprecating the old.

        The old secret's status is set to ``deprecated`` and its
        ``successor_id`` is set to the new secret's ID.

        Returns the new secret's ID.

        Raises:
            KeyError: if the old secret does not exist.
            ValueError: if the old secret is already revoked.
        """
        with _get_conn() as conn:
            old = conn.execute(
                """
                SELECT id, workspace_id, name, provider, created_by, status
                FROM secret_refs
                WHERE id = ?
                """,
                (secret_id,),
            ).fetchone()

        if old is None:
            raise KeyError(f"Secret not found: {secret_id!r}")

        if old["status"] == "revoked":
            raise ValueError(
                f"Cannot rotate a revoked secret: {secret_id!r}"
            )

        # Create the successor with a unique name to avoid UNIQUE constraint.
        # Convention: append the rotation timestamp.
        now = _now_iso()
        new_name = f"{old['name']}"  # keep the same name

        # We need to remove the old unique constraint entry first by
        # changing the old secret's name to include a deprecation suffix.
        new_id = _new_id()
        encrypted = SecretEncryption.encrypt(new_plaintext_value, self.key)

        with _get_conn() as conn:
            # Deprecate the old secret (rename to free up the unique slot)
            deprecated_name = f"{old['name']}::deprecated::{now}"
            conn.execute(
                """
                UPDATE secret_refs
                SET status = 'deprecated',
                    name = ?,
                    rotated_at = ?,
                    successor_id = ?
                WHERE id = ?
                """,
                (deprecated_name, now, new_id, secret_id),
            )

            # Insert the new secret with the original name
            conn.execute(
                """
                INSERT INTO secret_refs
                    (id, workspace_id, name, provider, encrypted_value,
                     created_by, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (new_id, old["workspace_id"], new_name, old["provider"],
                 encrypted, old["created_by"], now),
            )

        logger.info(
            "Rotated secret old=%s -> new=%s name=%r",
            secret_id, new_id, old["name"],
        )
        return new_id

    # ── revoke ───────────────────────────────────────────────────

    def revoke_secret(self, secret_id: str, reason: str) -> None:
        """Immediately revoke a secret.  It can no longer be decrypted.

        Raises:
            KeyError: if the secret does not exist.
        """
        now = _now_iso()

        with _get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM secret_refs WHERE id = ?",
                (secret_id,),
            ).fetchone()

            if row is None:
                raise KeyError(f"Secret not found: {secret_id!r}")

            conn.execute(
                """
                UPDATE secret_refs
                SET status = 'revoked',
                    revoked_at = ?,
                    revoke_reason = ?
                WHERE id = ?
                """,
                (now, reason, secret_id),
            )

        logger.warning(
            "Revoked secret id=%s reason=%r", secret_id, reason
        )

    # ── list ─────────────────────────────────────────────────────

    def list_secrets(self, workspace_id: str) -> list[dict]:
        """Return metadata for all secrets in a workspace.  Never includes values.

        Results are ordered by ``created_at`` descending.
        """
        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, name, provider, created_by,
                       created_at, rotated_at, status, last_used_at,
                       revoked_at, revoke_reason, successor_id
                FROM secret_refs
                WHERE workspace_id = ?
                ORDER BY created_at DESC
                """,
                (workspace_id,),
            ).fetchall()

        return [
            {
                "id": r["id"],
                "workspace_id": r["workspace_id"],
                "name": r["name"],
                "provider": r["provider"],
                "created_by": r["created_by"],
                "created_at": r["created_at"],
                "rotated_at": r["rotated_at"],
                "status": r["status"],
                "last_used_at": r["last_used_at"],
                "revoked_at": r["revoked_at"],
                "revoke_reason": r["revoke_reason"],
                "successor_id": r["successor_id"],
            }
            for r in rows
        ]

    # ── hygiene: rotation reminders ──────────────────────────────

    def check_rotation_reminders(
        self, reminder_days: int = SECRET_ROTATION_REMINDER_DAYS
    ) -> list[dict]:
        """Return active secrets that have not been rotated in *reminder_days*.

        For secrets that have never been rotated, ``created_at`` is used
        as the reference date.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=reminder_days)
        ).isoformat()

        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, name, provider, created_at,
                       rotated_at, last_used_at
                FROM secret_refs
                WHERE status = 'active'
                  AND COALESCE(rotated_at, created_at) < ?
                ORDER BY COALESCE(rotated_at, created_at) ASC
                """,
                (cutoff,),
            ).fetchall()

        results = []
        for r in rows:
            last_rotation = r["rotated_at"] or r["created_at"]
            results.append({
                "id": r["id"],
                "workspace_id": r["workspace_id"],
                "name": r["name"],
                "provider": r["provider"],
                "last_rotated_at": last_rotation,
                "last_used_at": r["last_used_at"],
                "days_since_rotation": (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(last_rotation)
                ).days,
            })

        if results:
            logger.info(
                "Rotation reminder: %d secret(s) not rotated in %d+ days",
                len(results), reminder_days,
            )

        return results

    # ── hygiene: unused secrets ──────────────────────────────────

    def check_unused_secrets(self, days: int = 30) -> list[dict]:
        """Return active secrets not used in *days* days.

        Secrets that have **never** been used (``last_used_at IS NULL``) are
        included if they were created more than *days* ago.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, name, provider, created_at,
                       last_used_at
                FROM secret_refs
                WHERE status = 'active'
                  AND (
                      (last_used_at IS NOT NULL AND last_used_at < ?)
                      OR
                      (last_used_at IS NULL AND created_at < ?)
                  )
                ORDER BY COALESCE(last_used_at, created_at) ASC
                """,
                (cutoff, cutoff),
            ).fetchall()

        results = []
        for r in rows:
            ref_date = r["last_used_at"] or r["created_at"]
            results.append({
                "id": r["id"],
                "workspace_id": r["workspace_id"],
                "name": r["name"],
                "provider": r["provider"],
                "last_used_at": r["last_used_at"],
                "created_at": r["created_at"],
                "days_unused": (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(ref_date)
                ).days,
            })

        if results:
            logger.info(
                "Unused secrets: %d secret(s) not used in %d+ days",
                len(results), days,
            )

        return results

    # ── purge revoked ────────────────────────────────────────────

    def purge_revoked(
        self, grace_period_days: int = SECRET_GRACE_PERIOD_DAYS
    ) -> int:
        """Delete revoked secrets whose grace period has elapsed.

        Returns the number of secrets purged.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=grace_period_days)
        ).isoformat()

        with _get_conn() as conn:
            cursor = conn.execute(
                """
                DELETE FROM secret_refs
                WHERE status = 'revoked'
                  AND revoked_at IS NOT NULL
                  AND revoked_at < ?
                """,
                (cutoff,),
            )
            count = cursor.rowcount

        if count:
            logger.info(
                "Purged %d revoked secret(s) past %d-day grace period",
                count, grace_period_days,
            )

        return count


# ═══════════════════════════════════════════════════════════════════════════════
# 3. StartupSecretScanner
# ═══════════════════════════════════════════════════════════════════════════════


class StartupSecretScanner:
    """Scan config files and environment variables for plaintext secrets.

    Meant to run at application startup to warn operators about secrets
    that should be moved into the encrypted vault.
    """

    # Files to scan (relative to project root).
    _CONFIG_FILES: list[str] = [
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "backend/config.py",
    ]

    @classmethod
    def scan_config_files(cls) -> list[dict]:
        """Scan known config files for plaintext secrets.

        Returns a list of warning dicts, each containing:
        - ``file``: path to the file
        - ``line``: line number (1-based)
        - ``pattern``: name of the matched pattern
        - ``preview``: first 40 chars of the line (scrubbed)
        """
        warnings: list[dict] = []
        project_root = Path(__file__).resolve().parent.parent.parent

        for rel_path in cls._CONFIG_FILES:
            filepath = project_root / rel_path
            if not filepath.is_file():
                continue

            try:
                lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                logger.debug(
                    "StartupSecretScanner: cannot read %s: %s", filepath, exc
                )
                continue

            for line_num, line in enumerate(lines, start=1):
                # Skip comment lines
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue

                result = SecretScrubber.scrub(line)
                if result.had_secrets:
                    for match in result.matches:
                        # Provide a safe preview (already scrubbed)
                        preview = result.scrubbed_text[:80]
                        warnings.append({
                            "file": str(filepath),
                            "line": line_num,
                            "pattern": match.pattern_name,
                            "preview": preview,
                        })

        if warnings:
            logger.warning(
                "StartupSecretScanner: found %d plaintext secret(s) in config files",
                len(warnings),
            )

        return warnings

    @classmethod
    def scan_environment(cls) -> list[dict]:
        """Scan environment variables for values matching secret patterns.

        Returns a list of warning dicts, each containing:
        - ``variable``: the env var name
        - ``pattern``: name of the matched pattern
        """
        warnings: list[dict] = []

        # Only scan env var *values*, not names.
        for var_name, var_value in os.environ.items():
            if not var_value:
                continue

            result = SecretScrubber.scrub(var_value)
            if result.had_secrets:
                for match in result.matches:
                    warnings.append({
                        "variable": var_name,
                        "pattern": match.pattern_name,
                    })

        if warnings:
            logger.warning(
                "StartupSecretScanner: found %d env var(s) matching secret patterns",
                len(warnings),
            )

        return warnings


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level convenience singleton
# ═══════════════════════════════════════════════════════════════════════════════

_lifecycle_manager: Optional[SecretLifecycleManager] = None


def get_secret_lifecycle_manager() -> SecretLifecycleManager:
    """Return the module-level :class:`SecretLifecycleManager` singleton."""
    global _lifecycle_manager
    if _lifecycle_manager is None:
        _lifecycle_manager = SecretLifecycleManager()
    return _lifecycle_manager
