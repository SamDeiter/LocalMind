"""
Centralized audit trail -- append-only event logging with alerting hooks.

Provides:

1. **AuditLogger** -- insert and query rows in the ``job_audit_log`` table.
   Rows are never updated or deleted (immutable / append-only).

2. **SecurityAlertManager** -- threshold-based alerting that fires webhooks
   and/or Slack messages when suspicious patterns are detected.

3. Module-level convenience helpers (``audit_event``, ``get_audit_logger``,
   ``get_alert_manager``) for easy integration across the codebase.

Design constraints:
- sqlite3 for persistence (WAL + busy_timeout, matching project patterns).
- httpx.AsyncClient for non-blocking webhook delivery.
- All alerting is fire-and-forget: errors are logged but never crash the caller.
- When no webhook/Slack is configured, alerting silently no-ops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from uuid import uuid4

import httpx

from backend.config import DB_PATH, SECURITY_ALERT_WEBHOOK, SECURITY_ALERT_CHANNEL

logger = logging.getLogger("localmind.core.audit")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Return a new SQLite connection with WAL mode and row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------

class AuditLogger:
    """Append-only audit event logger backed by the ``job_audit_log`` table.

    This class is safe to use as a long-lived singleton -- each method opens
    its own connection, commits, and closes.
    """

    def __init__(self) -> None:
        self._db_path = str(DB_PATH)

    # -- Write ---------------------------------------------------------------

    def log(
        self,
        actor: str,
        action: str,
        detail: Any = None,
        source_ip: Optional[str] = None,
        job_id: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> str:
        """Insert a single audit row.  Returns the generated row ID.

        Parameters
        ----------
        actor : str
            Who performed the action (``"system"``, ``"llm"``, or a user ID).
        action : str
            Machine-readable action tag (e.g. ``"auth_failure"``,
            ``"file_upload"``, ``"tool_execution"``).
        detail : Any, optional
            Arbitrary context.  Dicts/lists are JSON-serialized; scalars are
            stored as-is.
        source_ip : str, optional
            Originating IP address if known.
        job_id : str, optional
            Related job ID (nullable for system-wide events).
        node_id : str, optional
            Related node ID.
        """
        entry_id = _new_id()
        timestamp = _now_iso()

        # Serialize structured detail
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, default=str)
        elif detail is not None:
            detail = str(detail)

        # For databases with the legacy NOT NULL constraint on job_id,
        # fall back to empty string when no job context is provided.
        effective_job_id = job_id or ""

        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO job_audit_log
                    (id, job_id, node_id, action, detail, actor, source_ip, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entry_id, effective_job_id, node_id, action, detail, actor, source_ip, timestamp),
            )
            conn.commit()
        except Exception:
            logger.exception("Failed to write audit log entry")
            raise
        finally:
            conn.close()

        return entry_id

    # -- Read ----------------------------------------------------------------

    def query(
        self,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        since: Optional[str] = None,
        job_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query audit entries with optional filters, most-recent-first.

        Parameters
        ----------
        action : str, optional
            Filter by action type.
        actor : str, optional
            Filter by actor.
        since : str, optional
            ISO timestamp -- return entries at or after this time.
        job_id : str, optional
            Filter by job ID.
        limit : int
            Maximum number of entries to return (default 50).
        """
        clauses: list[str] = []
        params: list[object] = []

        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if job_id:
            clauses.append("job_id = ?")
            params.append(job_id)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT id, job_id, node_id, action, detail, actor, source_ip, timestamp
            FROM job_audit_log
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(limit)

        conn = _get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        return [
            {
                "id": r["id"],
                "job_id": r["job_id"],
                "node_id": r["node_id"],
                "action": r["action"],
                "detail": r["detail"],
                "actor": r["actor"],
                "source_ip": r["source_ip"] if "source_ip" in r.keys() else None,
                "timestamp": r["timestamp"],
            }
            for r in rows
        ]

    # -- Aggregations --------------------------------------------------------

    def count_recent(self, action: str, minutes: int = 5) -> int:
        """Count events of a given action type in the last *minutes* minutes.

        Used by ``SecurityAlertManager`` to check alert thresholds.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM job_audit_log WHERE action = ? AND timestamp >= ?",
                (action, cutoff),
            ).fetchone()
        finally:
            conn.close()

        return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# SecurityAlertManager
# ---------------------------------------------------------------------------

class SecurityAlertManager:
    """Threshold-based security alerting with webhook and Slack delivery.

    Alert triggers:
    - ``auth_failure``: 3+ in 5 minutes
    - ``path_traversal``: any single occurrence
    - ``secret_detected``: any single occurrence
    - ``job_failure``: 3 consecutive failures for the same job (circuit breaker)
    """

    # Actions that trigger an alert on any single occurrence.
    _IMMEDIATE_ALERTS = {"path_traversal", "secret_detected"}

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        slack_channel: Optional[str] = None,
    ) -> None:
        self.webhook_url = webhook_url or SECURITY_ALERT_WEBHOOK
        self.slack_channel = slack_channel or SECURITY_ALERT_CHANNEL

    # -- Main check ----------------------------------------------------------

    async def check_and_alert(
        self,
        action: str,
        detail: Any,
        audit_logger: AuditLogger,
    ) -> None:
        """Evaluate whether *action* should trigger a security alert.

        Called after each audit log write.  Runs checks and, when thresholds
        are met, fires webhook / Slack notifications asynchronously.
        """
        try:
            alert_type: Optional[str] = None
            alert_detail: Any = detail

            if action in self._IMMEDIATE_ALERTS:
                alert_type = action

            elif action == "auth_failure":
                count = audit_logger.count_recent("auth_failure", minutes=5)
                if count >= 3:
                    alert_type = "auth_failure_burst"
                    alert_detail = {
                        "count": count,
                        "window_minutes": 5,
                        "latest": detail,
                    }

            elif action == "job_failure":
                # Circuit breaker: 3 consecutive failures for the same job.
                if isinstance(detail, str):
                    try:
                        parsed = json.loads(detail)
                    except (json.JSONDecodeError, TypeError):
                        parsed = {"raw": detail}
                elif isinstance(detail, dict):
                    parsed = detail
                else:
                    parsed = {}

                job_id = parsed.get("job_id")
                if job_id:
                    count = self._count_consecutive_job_failures(audit_logger, job_id)
                    if count >= 3:
                        alert_type = "circuit_breaker"
                        alert_detail = {
                            "job_id": job_id,
                            "consecutive_failures": count,
                        }

            if alert_type:
                # Fire-and-forget: both webhook and Slack in parallel.
                tasks = []
                if self.webhook_url:
                    tasks.append(self.send_webhook_alert(alert_type, alert_detail))
                if self.slack_channel:
                    tasks.append(self.send_slack_alert(alert_type, alert_detail))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

        except Exception:
            logger.exception("SecurityAlertManager.check_and_alert failed (non-fatal)")

    # -- Webhook delivery ----------------------------------------------------

    async def send_webhook_alert(self, alert_type: str, detail: Any) -> None:
        """POST a JSON alert to ``SECURITY_ALERT_WEBHOOK``.

        Non-blocking, errors are logged but never propagated.
        """
        if not self.webhook_url:
            return

        payload = {
            "alert_type": alert_type,
            "detail": detail if isinstance(detail, (dict, list, str)) else str(detail),
            "timestamp": _now_iso(),
            "source": "localmind",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
                if resp.status_code >= 400:
                    logger.warning(
                        "Webhook alert delivery failed: %s %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                else:
                    logger.info("Security alert sent via webhook: %s", alert_type)
        except Exception:
            logger.exception("Webhook alert delivery error (non-fatal)")

    # -- Slack delivery ------------------------------------------------------

    async def send_slack_alert(self, alert_type: str, detail: Any) -> None:
        """Post a security alert to the configured Slack channel.

        Uses the existing Slack bot integration if available; otherwise no-ops.
        """
        if not self.slack_channel:
            return

        try:
            from backend.integrations.slack_bot import get_slack_bot
            bot = get_slack_bot()
            if bot and hasattr(bot, "post_message"):
                detail_str = json.dumps(detail, default=str) if isinstance(detail, (dict, list)) else str(detail)
                text = f":rotating_light: *Security Alert: {alert_type}*\n```{detail_str}```"
                await bot.post_message(channel=self.slack_channel, text=text)
                logger.info("Security alert sent via Slack: %s", alert_type)
            else:
                logger.debug("Slack bot not available for security alert")
        except ImportError:
            logger.debug("Slack bot module not available")
        except Exception:
            logger.exception("Slack alert delivery error (non-fatal)")

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _count_consecutive_job_failures(audit_logger: AuditLogger, job_id: str) -> int:
        """Count how many of the most recent audit entries for *job_id* are failures.

        Looks at the last 10 entries for the job and counts consecutive
        ``job_failure`` actions from the most recent backward.
        """
        entries = audit_logger.query(job_id=job_id, limit=10)
        count = 0
        for entry in entries:
            if entry["action"] == "job_failure":
                count += 1
            else:
                break
        return count


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_audit_logger: Optional[AuditLogger] = None
_alert_manager: Optional[SecurityAlertManager] = None


def get_audit_logger() -> AuditLogger:
    """Return the module-level AuditLogger singleton (created on first call)."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def get_alert_manager() -> SecurityAlertManager:
    """Return the module-level SecurityAlertManager singleton."""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = SecurityAlertManager()
    return _alert_manager


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

async def audit_event(
    actor: str,
    action: str,
    detail: Any = None,
    source_ip: Optional[str] = None,
    job_id: Optional[str] = None,
    node_id: Optional[str] = None,
) -> str:
    """Log an audit event **and** check security alert thresholds in one call.

    Returns the generated audit row ID.
    """
    al = get_audit_logger()
    entry_id = al.log(
        actor=actor,
        action=action,
        detail=detail,
        source_ip=source_ip,
        job_id=job_id,
        node_id=node_id,
    )

    # Fire-and-forget alert check
    am = get_alert_manager()
    try:
        await am.check_and_alert(action=action, detail=detail, audit_logger=al)
    except Exception:
        logger.debug("Alert check failed (non-fatal)")

    return entry_id
