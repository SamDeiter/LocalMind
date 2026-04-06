"""
MigrationManager — Lightweight schema versioning for ChromaDB collections.

Tracks applied migrations in a JSON ledger file alongside the ChromaDB data.
Discovers migration files from backend/migrations/versions/, runs pending
ones in order on startup, and supports dry-run mode for previewing changes.

Each migration file must expose:
    version: str          — unique version identifier, e.g. "001"
    description: str      — human-readable summary
    up(clients) -> None   — apply the migration
    down(clients) -> None — revert the migration (best-effort)

The `clients` dict passed to up/down contains:
    {
        "memory": <chromadb.PersistentClient for memory_db>,
        "rag":    <chromadb.PersistentClient for rag_data>,
    }
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.migrations")

# Directories that hold ChromaDB persistent data
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_MEMORY_DB_DIR = _BACKEND_DIR / "memory_db"
_RAG_DATA_DIR = _BACKEND_DIR / "rag_data"

# Where we store the migration ledger
_LEDGER_PATH = _BACKEND_DIR / "migrations_applied.json"

# Where migration version files live
_VERSIONS_DIR = Path(__file__).resolve().parent / "versions"


# ── Data structures ─────────────────────────────────────────────────

@dataclass
class AppliedMigration:
    version: str
    description: str
    applied_at: float  # epoch seconds
    success: bool = True
    error: Optional[str] = None


@dataclass
class MigrationLedger:
    """Persistent record of which migrations have been applied."""
    applied: list[AppliedMigration] = field(default_factory=list)

    # -- Persistence ---------------------------------------------------

    @classmethod
    def load(cls, path: Path = _LEDGER_PATH) -> "MigrationLedger":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = [AppliedMigration(**e) for e in raw.get("applied", [])]
            return cls(applied=entries)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Corrupt migration ledger at %s — starting fresh: %s", path, exc)
            return cls()

    def save(self, path: Path = _LEDGER_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"applied": [asdict(a) for a in self.applied]}
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # -- Query ---------------------------------------------------------

    def applied_versions(self) -> set[str]:
        return {a.version for a in self.applied if a.success}


# ── Migration file discovery ────────────────────────────────────────

@dataclass
class MigrationFile:
    """A parsed migration module reference, not yet imported."""
    path: Path
    version: str  # e.g. "001"
    sort_key: str  # filename stem for ordering


def discover_migrations(versions_dir: Path = _VERSIONS_DIR) -> list[MigrationFile]:
    """Find all migration files in the versions directory, sorted by filename.

    Migration files must:
      - Live directly in `versions_dir`
      - Match the pattern NNN_*.py (leading digits used for ordering)
      - Not start with underscore or be __init__.py
    """
    if not versions_dir.is_dir():
        return []

    files: list[MigrationFile] = []
    for p in sorted(versions_dir.glob("*.py")):
        if p.name.startswith("_"):
            continue
        stem = p.stem
        # Extract the numeric prefix as the sort key
        parts = stem.split("_", 1)
        if not parts[0].isdigit():
            logger.debug("Skipping non-migration file: %s", p.name)
            continue
        files.append(MigrationFile(path=p, version=parts[0].lstrip("0") or "0", sort_key=stem))

    files.sort(key=lambda m: m.sort_key)
    return files


def _load_module(mf: MigrationFile):
    """Import a migration file as a Python module."""
    spec = importlib.util.spec_from_file_location(f"migration_{mf.sort_key}", str(mf.path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load migration: {mf.path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── ChromaDB client helpers ─────────────────────────────────────────

def _build_clients() -> dict:
    """Create fresh PersistentClient instances for each database.

    These are independent from the singletons in the tool modules so that
    migrations never interfere with the running application state.
    """
    import chromadb

    clients = {}
    if _MEMORY_DB_DIR.exists():
        clients["memory"] = chromadb.PersistentClient(path=str(_MEMORY_DB_DIR))
    else:
        _MEMORY_DB_DIR.mkdir(parents=True, exist_ok=True)
        clients["memory"] = chromadb.PersistentClient(path=str(_MEMORY_DB_DIR))

    if _RAG_DATA_DIR.exists():
        clients["rag"] = chromadb.PersistentClient(path=str(_RAG_DATA_DIR))
    else:
        _RAG_DATA_DIR.mkdir(parents=True, exist_ok=True)
        clients["rag"] = chromadb.PersistentClient(path=str(_RAG_DATA_DIR))

    return clients


# ── MigrationManager ────────────────────────────────────────────────

class MigrationManager:
    """Discovers and applies ChromaDB schema migrations in order."""

    def __init__(
        self,
        versions_dir: Path = _VERSIONS_DIR,
        ledger_path: Path = _LEDGER_PATH,
    ):
        self.versions_dir = versions_dir
        self.ledger_path = ledger_path
        self._ledger: Optional[MigrationLedger] = None

    @property
    def ledger(self) -> MigrationLedger:
        if self._ledger is None:
            self._ledger = MigrationLedger.load(self.ledger_path)
        return self._ledger

    # ── Core operations ──────────────────────────────────────────────

    def pending(self) -> list[MigrationFile]:
        """Return migration files that have not yet been applied."""
        applied = self.ledger.applied_versions()
        return [m for m in discover_migrations(self.versions_dir) if m.version not in applied]

    def run_pending(self, *, dry_run: bool = False) -> list[AppliedMigration]:
        """Apply all pending migrations in order.

        Args:
            dry_run: If True, log what *would* run without executing anything.

        Returns:
            List of AppliedMigration records for migrations that were executed
            (empty list in dry-run mode).
        """
        pending = self.pending()
        if not pending:
            logger.info("Migrations: all up to date.")
            return []

        if dry_run:
            logger.info("Migrations (dry-run): %d pending", len(pending))
            for mf in pending:
                mod = _load_module(mf)
                desc = getattr(mod, "description", mf.sort_key)
                logger.info("  [DRY-RUN] Would apply: %s — %s", mf.version, desc)
            return []

        logger.info("Migrations: %d pending, applying now...", len(pending))
        clients = _build_clients()
        results: list[AppliedMigration] = []

        for mf in pending:
            mod = _load_module(mf)
            desc = getattr(mod, "description", mf.sort_key)
            ver = getattr(mod, "version", mf.version)

            logger.info("  Applying migration %s: %s", ver, desc)
            record = AppliedMigration(
                version=ver,
                description=desc,
                applied_at=time.time(),
            )

            try:
                up_fn = getattr(mod, "up", None)
                if up_fn is None:
                    raise AttributeError(f"Migration {mf.path.name} has no up() function")
                up_fn(clients)
                record.success = True
                logger.info("  Migration %s applied successfully.", ver)
            except Exception as exc:
                record.success = False
                record.error = str(exc)
                logger.error("  Migration %s FAILED: %s", ver, exc, exc_info=True)

            results.append(record)
            self.ledger.applied.append(record)
            self.ledger.save(self.ledger_path)

            # Stop on first failure — don't skip ahead
            if not record.success:
                logger.error("Halting migrations due to failure in %s.", ver)
                break

        return results

    def rollback_last(self, *, dry_run: bool = False) -> Optional[AppliedMigration]:
        """Revert the most recently applied migration.

        Returns the AppliedMigration record or None if nothing to roll back.
        """
        successful = [a for a in self.ledger.applied if a.success]
        if not successful:
            logger.info("Migrations: nothing to roll back.")
            return None

        last = successful[-1]
        # Find the corresponding file
        all_migrations = discover_migrations(self.versions_dir)
        mf = next((m for m in all_migrations if m.version == last.version), None)
        if mf is None:
            logger.error("Cannot find migration file for version %s — manual intervention needed.", last.version)
            return None

        mod = _load_module(mf)
        desc = getattr(mod, "description", mf.sort_key)

        if dry_run:
            logger.info("  [DRY-RUN] Would roll back: %s — %s", last.version, desc)
            return None

        down_fn = getattr(mod, "down", None)
        if down_fn is None:
            logger.warning("Migration %s has no down() function — cannot roll back.", last.version)
            return None

        logger.info("  Rolling back migration %s: %s", last.version, desc)
        clients = _build_clients()

        try:
            down_fn(clients)
            # Remove from ledger
            self.ledger.applied = [a for a in self.ledger.applied if a.version != last.version]
            self.ledger.save(self.ledger_path)
            logger.info("  Migration %s rolled back successfully.", last.version)
            return last
        except Exception as exc:
            logger.error("  Rollback of %s FAILED: %s", last.version, exc, exc_info=True)
            return None

    def status(self) -> dict:
        """Return a summary of migration state."""
        all_migrations = discover_migrations(self.versions_dir)
        applied = self.ledger.applied_versions()
        pending = [m for m in all_migrations if m.version not in applied]

        return {
            "total_migrations": len(all_migrations),
            "applied": len(applied),
            "pending": len(pending),
            "pending_versions": [m.version for m in pending],
            "applied_versions": sorted(applied),
        }
