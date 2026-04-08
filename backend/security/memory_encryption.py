"""
Memory Privacy & Encryption — backend/security/memory_encryption.py

Field-level AES-256-GCM encryption for memory content, per-user memory
isolation, lifecycle management (retention/archival/purge), and key
rotation utilities.

Design goals:
  - Encrypt sensitive memory content at rest while preserving the ability
    to search on unencrypted metadata (category, subcategory).
  - Per-user key derivation via HKDF so a compromised memory row for one
    user does not expose another user's data.
  - Graceful degradation: if the ``cryptography`` library is not installed
    the module falls back to base64-only encoding (NOT secure -- documented
    as dev-only mode).
  - Atomic per-memory key rotation: each row is independently re-encrypted
    so a crash mid-rotation leaves every row in a valid state (either old
    key or new key, never corrupted).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from backend.config import DB_PATH

logger = logging.getLogger("localmind.security.memory_encryption")

# ---------------------------------------------------------------------------
# Optional dependency: cryptography
# ---------------------------------------------------------------------------

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    _HAS_CRYPTO = True
except ImportError:  # pragma: no cover
    _HAS_CRYPTO = False
    logger.warning(
        "cryptography library not installed -- memory encryption will use "
        "base64-only encoding (NOT secure, dev-only mode)"
    )

# ---------------------------------------------------------------------------
# Config constants (env-driven, sensible defaults)
# ---------------------------------------------------------------------------

MEMORY_ENCRYPTION_ENABLED: bool = (
    os.getenv("MEMORY_ENCRYPTION_ENABLED", "false").lower() == "true"
)
MEMORY_ISOLATION_ENABLED: bool = (
    os.getenv("MEMORY_ISOLATION_ENABLED", "false").lower() == "true"
)
MEMORY_RETENTION_DAYS: int = int(os.getenv("MEMORY_RETENTION_DAYS", "365"))
MEMORY_MAX_PER_USER: int = int(os.getenv("MEMORY_MAX_PER_USER", "10000"))
MEMORY_ENCRYPTION_KEY: str = os.getenv("MEMORY_ENCRYPTION_KEY", "")

# Sentinel value stored on first encryption to verify key correctness later.
_SENTINEL_PLAINTEXT = "localmind-key-verification-sentinel-v1"
_SENTINEL_USER_ID = "__system__sentinel__"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Open a WAL-mode connection to the main LocalMind database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_memory_columns() -> None:
    """Add ``owner_id``, ``encrypted``, and ``archived_at`` columns to the
    ``memories`` table if they do not already exist.

    Uses ALTER TABLE wrapped in try/except so it is safe to call
    repeatedly (idempotent migration).
    """
    conn = _get_conn()
    migrations = [
        ("owner_id", "ALTER TABLE memories ADD COLUMN owner_id TEXT DEFAULT ''"),
        ("encrypted", "ALTER TABLE memories ADD COLUMN encrypted INTEGER DEFAULT 0"),
        ("archived_at", "ALTER TABLE memories ADD COLUMN archived_at REAL DEFAULT NULL"),
    ]
    for col_name, ddl in migrations:
        try:
            conn.execute(ddl)
            logger.info("Added column '%s' to memories table", col_name)
        except sqlite3.OperationalError:
            # Column already exists -- expected on subsequent startups.
            pass

    # Index for owner-scoped queries.
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_owner ON memories(owner_id)"
        )
    except sqlite3.OperationalError:
        pass

    # Index for lifecycle archival queries.
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived_at)"
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# MemoryEncryption
# ---------------------------------------------------------------------------

class MemoryEncryption:
    """Field-level AES-256-GCM encryption with per-user key derivation.

    Wire format (base64-encoded):
        nonce (12 bytes) || tag (16 bytes) || ciphertext (variable)

    When ``cryptography`` is not installed the class falls back to plain
    base64 encoding so the rest of the system can still function in
    development without installing native dependencies.  A warning is
    logged on every encrypt/decrypt call in fallback mode.
    """

    def __init__(self, master_key: bytes) -> None:
        """Initialise with the 32-byte master key.

        Parameters
        ----------
        master_key:
            Raw 32-byte key, typically decoded from the hex string stored
            in the ``MEMORY_ENCRYPTION_KEY`` environment variable.
        """
        if len(master_key) != 32:
            raise ValueError(
                f"master_key must be exactly 32 bytes, got {len(master_key)}"
            )
        self._master_key = master_key

    # -- key derivation ----------------------------------------------------

    def _derive_user_key(self, user_id: str) -> bytes:
        """Derive a per-user 256-bit key via HKDF-SHA256.

        Parameters
        ----------
        user_id:
            Used as the HKDF salt so each user's data is encrypted under
            a distinct derived key.

        Returns
        -------
        bytes
            32-byte derived key.
        """
        if not _HAS_CRYPTO:
            # Fallback: deterministic but NOT cryptographically isolated.
            import hashlib

            return hashlib.sha256(self._master_key + user_id.encode()).digest()

        hkdf = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=user_id.encode("utf-8"),
            info=b"localmind-memory-encryption-v1",
        )
        return hkdf.derive(self._master_key)

    # -- encrypt / decrypt ------------------------------------------------

    def encrypt(self, user_id: str, plaintext: str) -> str:
        """Encrypt *plaintext* for *user_id*.

        Returns
        -------
        str
            Base64-encoded blob: ``nonce || tag || ciphertext``.
        """
        if not _HAS_CRYPTO:
            logger.warning(
                "encrypt() using base64-only fallback (NOT secure)"
            )
            return base64.b64encode(plaintext.encode("utf-8")).decode("ascii")

        key = self._derive_user_key(user_id)
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)  # 96-bit nonce for AES-GCM
        # AESGCM.encrypt returns ciphertext || tag (tag is last 16 bytes).
        ct_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        # Reorder to nonce || tag || ciphertext for deterministic parsing.
        tag = ct_with_tag[-16:]
        ciphertext = ct_with_tag[:-16]
        blob = nonce + tag + ciphertext
        return base64.b64encode(blob).decode("ascii")

    def decrypt(self, user_id: str, ciphertext_blob: str) -> str:
        """Decrypt a blob previously produced by :meth:`encrypt`.

        Parameters
        ----------
        user_id:
            Must match the user_id used at encryption time.
        ciphertext_blob:
            Base64-encoded string returned by :meth:`encrypt`.

        Returns
        -------
        str
            The original plaintext.

        Raises
        ------
        Exception
            ``cryptography.exceptions.InvalidTag`` if the key or data is
            wrong, or ``ValueError`` if the blob is malformed.
        """
        if not _HAS_CRYPTO:
            logger.warning(
                "decrypt() using base64-only fallback (NOT secure)"
            )
            return base64.b64decode(ciphertext_blob).decode("utf-8")

        raw = base64.b64decode(ciphertext_blob)
        if len(raw) < 28:  # 12 nonce + 16 tag minimum
            raise ValueError(
                f"Ciphertext blob too short ({len(raw)} bytes); expected >= 28"
            )
        nonce = raw[:12]
        tag = raw[12:28]
        ciphertext = raw[28:]

        key = self._derive_user_key(user_id)
        aesgcm = AESGCM(key)
        # Reconstruct the format AESGCM expects: ciphertext || tag
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext + tag, None)
        return plaintext_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# MemoryIsolation
# ---------------------------------------------------------------------------

class MemoryIsolation:
    """Per-user memory scoping.

    All queries filter on ``owner_id`` so one user can never read, modify,
    or delete another user's memories.  Optionally encrypts content via
    :class:`MemoryEncryption` when ``encrypted=True``.
    """

    def __init__(self, encryption: Optional[MemoryEncryption] = None) -> None:
        self._enc = encryption

    # -- read -------------------------------------------------------------

    def get_user_memories(
        self,
        user_id: str,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return memories owned by *user_id*.

        Parameters
        ----------
        user_id:
            Owner filter.
        category:
            Optional category filter (``episodic``, ``semantic``, ``procedural``).
        limit:
            Maximum rows returned.

        Returns
        -------
        list[dict]
            Each dict mirrors the ``memories`` table columns.  Encrypted
            content is transparently decrypted if an encryption instance
            was provided.
        """
        conn = _get_conn()
        try:
            sql = "SELECT * FROM memories WHERE owner_id = ? AND archived_at IS NULL"
            params: list[object] = [user_id]
            if category:
                sql += " AND category = ?"
                params.append(category)
            sql += " ORDER BY accessed_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        results: list[dict] = []
        for row in rows:
            d = dict(row)
            if d.get("encrypted") and self._enc:
                try:
                    d["content"] = self._enc.decrypt(user_id, d["content"])
                except Exception as exc:
                    logger.warning(
                        "Failed to decrypt memory #%s for user %s: %s",
                        d.get("id"), user_id, exc,
                    )
                    d["content"] = "[decryption failed]"
            results.append(d)
        return results

    # -- write ------------------------------------------------------------

    def save_user_memory(
        self,
        user_id: str,
        content: str,
        category: str,
        subcategory: str = "",
        encrypted: bool = True,
    ) -> int:
        """Save a memory scoped to *user_id*.

        Parameters
        ----------
        user_id:
            Owner of the memory.
        content:
            Plaintext content to store (will be encrypted if requested).
        category:
            Memory category (``episodic``, ``semantic``, ``procedural``).
        subcategory:
            Optional sub-category for finer classification.
        encrypted:
            If ``True`` and an encryption instance is available, the
            content is encrypted before writing.

        Returns
        -------
        int
            The ``id`` (rowid) of the newly inserted memory.
        """
        # Enforce per-user limit.
        conn = _get_conn()
        try:
            count_row = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE owner_id = ?", (user_id,)
            ).fetchone()
            current_count = count_row[0] if count_row else 0
            if current_count >= MEMORY_MAX_PER_USER:
                raise ValueError(
                    f"User {user_id!r} has reached the memory limit "
                    f"({MEMORY_MAX_PER_USER})"
                )
        finally:
            conn.close()

        is_encrypted = 0
        stored_content = content
        if encrypted and self._enc:
            stored_content = self._enc.encrypt(user_id, content)
            is_encrypted = 1

        now = time.time()
        conn = _get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO memories
                   (content, category, subcategory, source, metadata,
                    created_at, accessed_at, access_count, relevance_score,
                    owner_id, encrypted)
                   VALUES (?, ?, ?, ?, '{}', ?, ?, 0, 1.0, ?, ?)""",
                (
                    stored_content,
                    category,
                    subcategory,
                    "memory_isolation",
                    now,
                    now,
                    user_id,
                    is_encrypted,
                ),
            )
            conn.commit()
            memory_id = cursor.lastrowid
        finally:
            conn.close()

        logger.debug(
            "Saved memory #%s for user %s [%s/%s] encrypted=%s",
            memory_id, user_id, category, subcategory, bool(is_encrypted),
        )
        return memory_id  # type: ignore[return-value]

    # -- delete -----------------------------------------------------------

    def delete_user_memory(self, user_id: str, memory_id: int) -> bool:
        """Soft-delete a memory by moving it to the recycle bin (``archived_at``).

        Only deletes if the memory is owned by *user_id*.

        Parameters
        ----------
        user_id:
            Must match the memory's ``owner_id``.
        memory_id:
            Row ID of the memory to delete.

        Returns
        -------
        bool
            ``True`` if the memory was found and archived, ``False`` otherwise.
        """
        now = time.time()
        conn = _get_conn()
        try:
            cursor = conn.execute(
                """UPDATE memories
                      SET archived_at = ?
                    WHERE id = ? AND owner_id = ? AND archived_at IS NULL""",
                (now, memory_id, user_id),
            )
            conn.commit()
            changed = cursor.rowcount > 0
        finally:
            conn.close()

        if changed:
            logger.info(
                "Archived memory #%s (owner=%s) to recycle bin", memory_id, user_id
            )
        return changed

    # -- search -----------------------------------------------------------

    def search_user_memories(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> list[dict]:
        """Full-text search scoped to *user_id*.

        When memories are encrypted, only ``category`` and ``subcategory``
        are searchable via FTS5 (the encrypted content blob is opaque).
        Unencrypted memories are fully searchable.

        Parameters
        ----------
        user_id:
            Owner scope.
        query:
            FTS5 search query string.
        limit:
            Maximum results.

        Returns
        -------
        list[dict]
        """
        conn = _get_conn()
        try:
            # FTS5 join, filtered to user's non-archived memories.
            sql = """
                SELECT m.*, rank AS fts_rank
                  FROM memories m
                  JOIN memories_fts fts ON m.id = fts.rowid
                 WHERE memories_fts MATCH ?
                   AND m.owner_id = ?
                   AND m.archived_at IS NULL
                 ORDER BY fts_rank
                 LIMIT ?
            """
            try:
                rows = conn.execute(sql, (query, user_id, limit)).fetchall()
            except sqlite3.OperationalError:
                # FTS query syntax error -- fall back to LIKE.
                rows = conn.execute(
                    """SELECT * FROM memories
                        WHERE owner_id = ?
                          AND archived_at IS NULL
                          AND (content LIKE ? OR category LIKE ? OR subcategory LIKE ?)
                        ORDER BY accessed_at DESC
                        LIMIT ?""",
                    (user_id, f"%{query}%", f"%{query}%", f"%{query}%", limit),
                ).fetchall()
        finally:
            conn.close()

        results: list[dict] = []
        for row in rows:
            d = dict(row)
            if d.get("encrypted") and self._enc:
                try:
                    d["content"] = self._enc.decrypt(user_id, d["content"])
                except Exception:
                    d["content"] = "[decryption failed]"
            results.append(d)
        return results


# ---------------------------------------------------------------------------
# MemoryLifecycle
# ---------------------------------------------------------------------------

class MemoryLifecycle:
    """Retention, archival, and statistics for the memories table."""

    def archive_expired(self, retention_days: int = MEMORY_RETENTION_DAYS) -> int:
        """Archive memories older than *retention_days* with no recent access.

        A memory is considered "expired" when:
        - ``created_at`` is older than *retention_days* ago, AND
        - ``accessed_at`` is also older than *retention_days* ago (no
          recent reads).

        Archived memories have ``archived_at`` set (soft-delete / recycle
        bin) but remain in the table for potential restore.

        Parameters
        ----------
        retention_days:
            Number of days before a memory is eligible for archival.

        Returns
        -------
        int
            Count of newly archived memories.
        """
        cutoff = time.time() - (retention_days * 86400)
        now = time.time()
        conn = _get_conn()
        try:
            cursor = conn.execute(
                """UPDATE memories
                      SET archived_at = ?
                    WHERE archived_at IS NULL
                      AND created_at < ?
                      AND accessed_at < ?""",
                (now, cutoff, cutoff),
            )
            conn.commit()
            count = cursor.rowcount
        finally:
            conn.close()

        if count:
            logger.info(
                "Archived %d expired memories (retention=%d days)", count, retention_days
            )
        return count

    def purge_archived(self, retention_days: int = MEMORY_RETENTION_DAYS) -> int:
        """Permanently delete memories archived longer than ``2 * retention_days``.

        Parameters
        ----------
        retention_days:
            Base retention period.  Purge threshold is ``2 * retention_days``.

        Returns
        -------
        int
            Count of permanently deleted memories.
        """
        purge_cutoff = time.time() - (2 * retention_days * 86400)
        conn = _get_conn()
        try:
            cursor = conn.execute(
                """DELETE FROM memories
                    WHERE archived_at IS NOT NULL
                      AND archived_at < ?""",
                (purge_cutoff,),
            )
            conn.commit()
            count = cursor.rowcount
        finally:
            conn.close()

        if count:
            logger.info(
                "Purged %d memories archived > %d days ago",
                count, 2 * retention_days,
            )
        return count

    def get_memory_stats(self, user_id: Optional[str] = None) -> dict:
        """Aggregate statistics about stored memories.

        Parameters
        ----------
        user_id:
            If provided, scopes stats to a single user.

        Returns
        -------
        dict
            Keys: ``total``, ``by_category`` (dict), ``encrypted``,
            ``plaintext``, ``archived``, ``oldest`` (ISO timestamp or None),
            ``newest`` (ISO timestamp or None).
        """
        conn = _get_conn()
        try:
            where = ""
            params: list[object] = []
            if user_id:
                where = "WHERE owner_id = ?"
                params = [user_id]

            # Total count
            total = conn.execute(
                f"SELECT COUNT(*) FROM memories {where}", params
            ).fetchone()[0]

            # By category
            cat_rows = conn.execute(
                f"SELECT category, COUNT(*) AS cnt FROM memories {where} GROUP BY category",
                params,
            ).fetchall()
            by_category = {r["category"]: r["cnt"] for r in cat_rows}

            # Encrypted vs plaintext
            encrypted = conn.execute(
                f"SELECT COUNT(*) FROM memories {where}"
                + (" AND" if where else " WHERE")
                + " encrypted = 1",
                params,
            ).fetchone()[0]
            plaintext = total - encrypted

            # Archived count
            archived = conn.execute(
                f"SELECT COUNT(*) FROM memories {where}"
                + (" AND" if where else " WHERE")
                + " archived_at IS NOT NULL",
                params,
            ).fetchone()[0]

            # Oldest / newest
            oldest_row = conn.execute(
                f"SELECT MIN(created_at) FROM memories {where}", params
            ).fetchone()
            newest_row = conn.execute(
                f"SELECT MAX(created_at) FROM memories {where}", params
            ).fetchone()

            oldest_ts = oldest_row[0] if oldest_row and oldest_row[0] else None
            newest_ts = newest_row[0] if newest_row and newest_row[0] else None

            def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
                if ts is None:
                    return None
                return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        finally:
            conn.close()

        return {
            "total": total,
            "by_category": by_category,
            "encrypted": encrypted,
            "plaintext": plaintext,
            "archived": archived,
            "oldest": _ts_to_iso(oldest_ts),
            "newest": _ts_to_iso(newest_ts),
        }


# ---------------------------------------------------------------------------
# KeyManager
# ---------------------------------------------------------------------------

class KeyManager:
    """Master-key generation, rotation, and verification utilities."""

    @staticmethod
    def generate_master_key() -> str:
        """Generate a cryptographically random 32-byte key.

        Returns
        -------
        str
            64-character hex string suitable for the ``MEMORY_ENCRYPTION_KEY``
            environment variable.
        """
        return secrets.token_hex(32)

    @staticmethod
    def rotate_master_key(old_key: bytes, new_key: bytes) -> int:
        """Re-encrypt every encrypted memory from *old_key* to *new_key*.

        The operation is **atomic per-memory**: each row is read, decrypted
        with the old key, re-encrypted with the new key, and written back
        in a single UPDATE.  If the process crashes mid-rotation, every
        memory is in a consistent state -- encrypted under either the old
        or the new key, never corrupted.

        Parameters
        ----------
        old_key:
            The current 32-byte master key.
        new_key:
            The replacement 32-byte master key.

        Returns
        -------
        int
            Number of memories successfully re-encrypted.
        """
        old_enc = MemoryEncryption(old_key)
        new_enc = MemoryEncryption(new_key)

        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT id, owner_id, content FROM memories WHERE encrypted = 1"
            ).fetchall()

            count = 0
            for row in rows:
                mem_id = row["id"]
                owner_id = row["owner_id"]
                old_blob = row["content"]

                try:
                    plaintext = old_enc.decrypt(owner_id, old_blob)
                except Exception as exc:
                    logger.error(
                        "Key rotation: failed to decrypt memory #%s "
                        "(owner=%s): %s -- skipping",
                        mem_id, owner_id, exc,
                    )
                    continue

                new_blob = new_enc.encrypt(owner_id, plaintext)

                # Atomic per-row update.
                conn.execute(
                    "UPDATE memories SET content = ? WHERE id = ?",
                    (new_blob, mem_id),
                )
                conn.commit()  # Commit after each row for crash safety.
                count += 1

            logger.info(
                "Key rotation complete: %d / %d memories re-encrypted",
                count, len(rows),
            )
        finally:
            conn.close()

        return count

    @staticmethod
    def verify_key(master_key: bytes) -> bool:
        """Verify that *master_key* can decrypt the stored sentinel value.

        On first call (no sentinel stored yet) the sentinel is created
        automatically.

        Parameters
        ----------
        master_key:
            The 32-byte master key to test.

        Returns
        -------
        bool
            ``True`` if the key correctly decrypts the sentinel, ``False``
            if decryption fails (wrong key).
        """
        enc = MemoryEncryption(master_key)

        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT content FROM memories WHERE owner_id = ? AND encrypted = 1 LIMIT 1",
                (_SENTINEL_USER_ID,),
            ).fetchone()

            if row is None:
                # First run -- store the sentinel.
                blob = enc.encrypt(_SENTINEL_USER_ID, _SENTINEL_PLAINTEXT)
                now = time.time()
                conn.execute(
                    """INSERT INTO memories
                       (content, category, subcategory, source, metadata,
                        created_at, accessed_at, access_count, relevance_score,
                        owner_id, encrypted)
                       VALUES (?, 'system', 'sentinel', 'key_manager', '{}',
                               ?, ?, 0, 0.0, ?, 1)""",
                    (blob, now, now, _SENTINEL_USER_ID),
                )
                conn.commit()
                logger.info("Stored encryption key sentinel")
                return True

            # Sentinel exists -- try to decrypt it.
            try:
                plaintext = enc.decrypt(_SENTINEL_USER_ID, row["content"])
                return plaintext == _SENTINEL_PLAINTEXT
            except Exception:
                return False
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-level convenience: initialise encryption from env if enabled
# ---------------------------------------------------------------------------

def get_memory_encryption() -> Optional[MemoryEncryption]:
    """Return a :class:`MemoryEncryption` instance configured from the
    ``MEMORY_ENCRYPTION_KEY`` env var, or ``None`` if encryption is
    disabled or the key is missing.
    """
    if not MEMORY_ENCRYPTION_ENABLED:
        return None
    if not MEMORY_ENCRYPTION_KEY:
        logger.warning(
            "MEMORY_ENCRYPTION_ENABLED=true but MEMORY_ENCRYPTION_KEY is empty"
        )
        return None
    try:
        key_bytes = bytes.fromhex(MEMORY_ENCRYPTION_KEY)
    except ValueError:
        logger.error("MEMORY_ENCRYPTION_KEY is not valid hex")
        return None
    if len(key_bytes) != 32:
        logger.error(
            "MEMORY_ENCRYPTION_KEY must be 64 hex chars (32 bytes), got %d bytes",
            len(key_bytes),
        )
        return None
    return MemoryEncryption(key_bytes)


def get_memory_isolation() -> Optional[MemoryIsolation]:
    """Return a :class:`MemoryIsolation` instance if isolation is enabled,
    otherwise ``None``.
    """
    if not MEMORY_ISOLATION_ENABLED:
        return None
    enc = get_memory_encryption()
    return MemoryIsolation(encryption=enc)
