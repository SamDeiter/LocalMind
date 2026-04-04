"""
LocalMind Database Migrations
=============================
Lightweight versioning system for ChromaDB collections.

Usage:
    from backend.migrations import MigrationManager
    manager = MigrationManager()
    manager.run_pending()          # apply all unapplied migrations
    manager.run_pending(dry_run=True)  # preview what would run
"""

from .manager import MigrationManager

__all__ = ["MigrationManager"]
