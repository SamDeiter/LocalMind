"""
Identity & Tenancy module — LocalMind enterprise task worker.

Every job, memory, artifact, and audit entry is scoped to a workspace.
This module provides CRUD for organizations, workspaces, users, and memberships.

Tables are created by backend/core/schema.py (init_phase0_schema).
"""

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.config import DB_PATH

logger = logging.getLogger("localmind.core.identity")

# Role hierarchy: higher index = higher privilege
_ROLE_LEVELS: dict[str, int] = {
    "viewer": 0,
    "operator": 1,
    "admin": 2,
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class Organization:
    id: str
    name: str
    slug: str
    settings: dict
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Organization":
        return cls(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            settings=json.loads(row["settings_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "settings": self.settings,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Workspace:
    id: str
    org_id: str
    name: str
    slug: str
    deployment_mode: str
    settings: dict
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Workspace":
        return cls(
            id=row["id"],
            org_id=row["org_id"],
            name=row["name"],
            slug=row["slug"],
            deployment_mode=row["deployment_mode"],
            settings=json.loads(row["settings_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "name": self.name,
            "slug": self.slug,
            "deployment_mode": self.deployment_mode,
            "settings": self.settings,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class User:
    id: str
    org_id: str
    email: str
    display_name: str
    role: str
    slack_user_id: Optional[str]
    settings: dict
    created_at: str
    updated_at: str
    last_active_at: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(
            id=row["id"],
            org_id=row["org_id"],
            email=row["email"],
            display_name=row["display_name"],
            role=row["role"],
            slack_user_id=row["slack_user_id"],
            settings=json.loads(row["settings_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_active_at=row["last_active_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "slack_user_id": self.slack_user_id,
            "settings": self.settings,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_active_at": self.last_active_at,
        }


@dataclass
class Membership:
    user_id: str
    workspace_id: str
    role: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Membership":
        return cls(
            user_id=row["user_id"],
            workspace_id=row["workspace_id"],
            role=row["role"],
        )

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "role": self.role,
        }


# ── IdentityService ───────────────────────────────────────────────────────────

class IdentityService:
    """CRUD service for organizations, workspaces, users, and memberships.

    Each method opens its own short-lived connection, commits, and closes.
    No long-lived connections are held.
    """

    # ── Organizations ─────────────────────────────────────────────────────────

    def create_org(
        self,
        name: str,
        slug: str,
        settings: Optional[dict] = None,
    ) -> Organization:
        """Create and return a new Organization."""
        org_id = _new_id()
        now = _now()
        settings_json = json.dumps(settings or {})

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO organizations (id, name, slug, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (org_id, name, slug, settings_json, now, now),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Created org: id=%s slug=%s", org_id, slug)
        return Organization(
            id=org_id,
            name=name,
            slug=slug,
            settings=settings or {},
            created_at=now,
            updated_at=now,
        )

    def get_org(self, org_id: str) -> Optional[Organization]:
        """Return an Organization by ID, or None if not found."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM organizations WHERE id = ?", (org_id,)
            ).fetchone()
        finally:
            conn.close()

        return Organization.from_row(row) if row else None

    def get_org_by_slug(self, slug: str) -> Optional[Organization]:
        """Return an Organization by slug, or None if not found."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM organizations WHERE slug = ?", (slug,)
            ).fetchone()
        finally:
            conn.close()

        return Organization.from_row(row) if row else None

    # ── Workspaces ────────────────────────────────────────────────────────────

    def create_workspace(
        self,
        org_id: str,
        name: str,
        slug: str,
        deployment_mode: str = "hybrid",
        settings: Optional[dict] = None,
    ) -> Workspace:
        """Create and return a new Workspace scoped to an organization."""
        ws_id = _new_id()
        now = _now()
        settings_json = json.dumps(settings or {})

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO workspaces
                    (id, org_id, name, slug, deployment_mode, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ws_id, org_id, name, slug, deployment_mode, settings_json, now, now),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Created workspace: id=%s slug=%s org=%s", ws_id, slug, org_id)
        return Workspace(
            id=ws_id,
            org_id=org_id,
            name=name,
            slug=slug,
            deployment_mode=deployment_mode,
            settings=settings or {},
            created_at=now,
            updated_at=now,
        )

    def get_workspace(self, ws_id: str) -> Optional[Workspace]:
        """Return a Workspace by ID, or None if not found."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", (ws_id,)
            ).fetchone()
        finally:
            conn.close()

        return Workspace.from_row(row) if row else None

    def get_workspace_by_slug(self, slug: str) -> Optional[Workspace]:
        """Return a Workspace by slug, or None if not found."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE slug = ?", (slug,)
            ).fetchone()
        finally:
            conn.close()

        return Workspace.from_row(row) if row else None

    def get_default_workspace(self) -> Workspace:
        """Return the 'default' slug workspace.

        Raises RuntimeError if the default workspace does not exist.
        Call backend.core.schema.ensure_default_tenant() on startup to guarantee it.
        """
        ws = self.get_workspace_by_slug("default")
        if ws is None:
            raise RuntimeError(
                "Default workspace not found. "
                "Ensure ensure_default_tenant() has been called on startup."
            )
        return ws

    # ── Users ─────────────────────────────────────────────────────────────────

    def create_user(
        self,
        org_id: str,
        email: str,
        display_name: str,
        role: str = "operator",
        settings: Optional[dict] = None,
    ) -> User:
        """Create and return a new User within an organization."""
        user_id = _new_id()
        now = _now()
        settings_json = json.dumps(settings or {})

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO users
                    (id, org_id, email, display_name, role, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, org_id, email, display_name, role, settings_json, now, now),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Created user: id=%s email=%s org=%s", user_id, email, org_id)
        return User(
            id=user_id,
            org_id=org_id,
            email=email,
            display_name=display_name,
            role=role,
            slack_user_id=None,
            settings=settings or {},
            created_at=now,
            updated_at=now,
            last_active_at=None,
        )

    def get_user(self, user_id: str) -> Optional[User]:
        """Return a User by ID, or None if not found."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        finally:
            conn.close()

        return User.from_row(row) if row else None

    def get_user_by_email(self, org_id: str, email: str) -> Optional[User]:
        """Return a User by org-scoped email, or None if not found."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE org_id = ? AND email = ?",
                (org_id, email),
            ).fetchone()
        finally:
            conn.close()

        return User.from_row(row) if row else None

    def get_user_by_slack_id(self, slack_user_id: str) -> Optional[User]:
        """Return a User by Slack user ID, or None if not found."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE slack_user_id = ?", (slack_user_id,)
            ).fetchone()
        finally:
            conn.close()

        return User.from_row(row) if row else None

    def link_slack_user(self, user_id: str, slack_user_id: str) -> None:
        """Attach a Slack user ID to an existing LocalMind user."""
        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE users SET slack_user_id = ?, updated_at = ? WHERE id = ?",
                (slack_user_id, now, user_id),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Linked slack_user_id=%s to user=%s", slack_user_id, user_id)

    def touch_user_activity(self, user_id: str) -> None:
        """Update last_active_at to the current UTC time."""
        now = _now()
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE users SET last_active_at = ?, updated_at = ? WHERE id = ?",
                (now, now, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Memberships ───────────────────────────────────────────────────────────

    def add_membership(
        self,
        user_id: str,
        workspace_id: str,
        role: str = "operator",
    ) -> None:
        """Grant a user access to a workspace with the given role.

        Uses INSERT OR REPLACE so calling with an updated role is idempotent.
        """
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO memberships (user_id, workspace_id, role)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, workspace_id) DO UPDATE SET role = excluded.role
                """,
                (user_id, workspace_id, role),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Membership upserted: user=%s workspace=%s role=%s",
            user_id, workspace_id, role,
        )

    def get_memberships(self, workspace_id: str) -> list[Membership]:
        """Return all Membership records for a workspace."""
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM memberships WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
        finally:
            conn.close()

        return [Membership.from_row(r) for r in rows]

    def check_permission(
        self,
        user_id: str,
        workspace_id: str,
        required_role: str,
    ) -> bool:
        """Return True if the user's workspace role meets or exceeds required_role.

        Role hierarchy: admin > operator > viewer.
        Returns False if the user has no membership in the workspace or the
        required_role is unrecognised.
        """
        required_level = _ROLE_LEVELS.get(required_role)
        if required_level is None:
            logger.warning("check_permission: unknown required_role=%r", required_role)
            return False

        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT role FROM memberships WHERE user_id = ? AND workspace_id = ?",
                (user_id, workspace_id),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return False

        user_level = _ROLE_LEVELS.get(row["role"], -1)
        return user_level >= required_level
