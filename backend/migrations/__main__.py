"""
CLI entry point for the migration system.

Usage:
    python -m backend.migrations status          — show migration status
    python -m backend.migrations run             — apply pending migrations
    python -m backend.migrations run --dry-run   — preview without applying
    python -m backend.migrations rollback        — revert the last migration
    python -m backend.migrations create "desc"   — create a new migration file
"""

from __future__ import annotations

import sys


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        sys.exit(0)

    command = args[0]

    if command == "create":
        if len(args) < 2:
            print("Usage: python -m backend.migrations create <description>")
            sys.exit(1)
        from backend.migrations.create import create_migration
        desc = " ".join(args[1:])
        path = create_migration(desc)
        print(f"Created: {path}")

    elif command == "status":
        from backend.migrations.manager import MigrationManager
        mgr = MigrationManager()
        info = mgr.status()
        print(f"Total migrations:   {info['total_migrations']}")
        print(f"Applied:            {info['applied']}")
        print(f"Pending:            {info['pending']}")
        if info["pending_versions"]:
            print(f"Pending versions:   {', '.join(info['pending_versions'])}")
        if info["applied_versions"]:
            print(f"Applied versions:   {', '.join(info['applied_versions'])}")

    elif command == "run":
        from backend.migrations.manager import MigrationManager
        dry = "--dry-run" in args
        mgr = MigrationManager()
        results = mgr.run_pending(dry_run=dry)
        if not dry:
            ok = sum(1 for r in results if r.success)
            fail = sum(1 for r in results if not r.success)
            print(f"Applied: {ok}  Failed: {fail}")

    elif command == "rollback":
        from backend.migrations.manager import MigrationManager
        dry = "--dry-run" in args
        mgr = MigrationManager()
        result = mgr.rollback_last(dry_run=dry)
        if result:
            print(f"Rolled back: {result.version} — {result.description}")
        else:
            print("Nothing to roll back.")

    else:
        print(f"Unknown command: {command}")
        print("Run 'python -m backend.migrations help' for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
