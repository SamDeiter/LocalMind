"""
Slack Bot integration for LocalMind — Socket Mode.

Bridges Slack events (mentions, DMs, file uploads, Block Kit actions) into the
LocalMind job pipeline and delivers lifecycle notifications back into Slack
threads.

Requires ``slack-bolt[async]`` and ``aiohttp``.  If not installed the module
still imports cleanly — a warning is logged and ``SlackBot.start()`` becomes a
no-op so the rest of the application is unaffected.

Configuration (via env / backend.config):
    SLACK_BOT_TOKEN          — xoxb-... Bot User OAuth Token
    SLACK_APP_TOKEN          — xapp-... App-Level Token (Socket Mode)
    SLACK_ENABLED            — "true" to activate
    SLACK_ALLOWED_TEAM_IDS   — comma-separated whitelist (required)
    SLACK_ALLOWED_USER_IDS   — comma-separated whitelist (optional)
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import (
    DB_PATH,
    SLACK_ALLOWED_TEAM_IDS,
    SLACK_ALLOWED_USER_IDS,
    SLACK_APP_TOKEN,
    SLACK_BOT_TOKEN,
    SLACK_ENABLED,
)

logger = logging.getLogger("localmind.integrations.slack")

# ---------------------------------------------------------------------------
# Graceful import — slack-bolt may not be installed
# ---------------------------------------------------------------------------

_SLACK_AVAILABLE = False

try:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    _SLACK_AVAILABLE = True
except ImportError:
    logger.warning(
        "slack-bolt is not installed — Slack integration disabled. "
        "Install with: pip install slack-bolt[async] aiohttp"
    )
    AsyncApp = None  # type: ignore[assignment,misc]
    AsyncSocketModeHandler = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# DB helpers (mirrors pattern in backend/jobs/queue.py)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Deduplication via source_events table
# ---------------------------------------------------------------------------


def _is_duplicate_event(source: str, source_event_id: str) -> bool:
    """Return True if this (source, source_event_id) pair was already processed."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM source_events WHERE source = ? AND source_event_id = ?",
            (source, source_event_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _record_event(
    source: str,
    source_event_id: str,
    workspace_id: str,
    result_json: str | None = None,
) -> None:
    """Insert a record into source_events to mark this event as processed."""
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO source_events
                (id, source, source_event_id, workspace_id, processed_at, result_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_new_id(), source, source_event_id, workspace_id, _now(), result_json),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Progress bar helper
# ---------------------------------------------------------------------------


def _progress_bar(completed: int, total: int, width: int = 15) -> str:
    """Render a text progress bar like: [========>......] 53%"""
    if total <= 0:
        return "[" + "?" * width + "] ?%"
    ratio = min(completed / total, 1.0)
    filled = int(width * ratio)
    arrow = ">" if filled < width else ""
    bar = "=" * max(filled - 1, 0) + arrow + "." * (width - filled)
    pct = int(ratio * 100)
    return f"[{bar}] {pct}%"


# ---------------------------------------------------------------------------
# SlackBot
# ---------------------------------------------------------------------------

# Default workspace slug used when creating jobs from Slack.
_DEFAULT_WORKSPACE = "default"


class SlackBot:
    """Slack Bot using Socket Mode for bi-directional communication.

    Parameters
    ----------
    bot_token : str
        ``xoxb-...`` Bot User OAuth Token.
    app_token : str
        ``xapp-...`` App-Level Token with ``connections:write`` scope.
    allowed_team_ids : list[str]
        Workspace (team) IDs that are authorized to interact.
    allowed_user_ids : list[str]
        Optional user-level allowlist.  Empty list means *all users* in
        allowed teams are accepted.
    """

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        allowed_team_ids: list[str],
        allowed_user_ids: list[str],
    ) -> None:
        if not _SLACK_AVAILABLE:
            logger.error("Cannot create SlackBot — slack-bolt is not installed")
            self._app: Any = None
            self._handler: Any = None
            self._ready = False
            return

        self._bot_token = bot_token
        self._app_token = app_token
        self._allowed_team_ids = set(allowed_team_ids)
        self._allowed_user_ids = set(allowed_user_ids)

        self._app = AsyncApp(token=bot_token)
        self._handler: Any = None
        self._ready = True

        # Rate-limit tracker: message_ts -> last_update_epoch
        self._last_update: dict[str, float] = {}
        self._rate_limit_sec = 3.0

        # Register event handlers
        self._register_handlers()

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def _authorize(self, team_id: str | None, user_id: str | None) -> bool:
        """Check if an event's team and user are authorized.

        Returns True when authorized, False otherwise (with a warning log).
        """
        if not team_id or team_id not in self._allowed_team_ids:
            logger.warning(
                "Rejected event from unauthorized team_id=%s (allowed: %s)",
                team_id,
                self._allowed_team_ids,
            )
            return False

        if self._allowed_user_ids and (
            not user_id or user_id not in self._allowed_user_ids
        ):
            logger.warning(
                "Rejected event from unauthorized user_id=%s in team=%s (allowed: %s)",
                user_id,
                team_id,
                self._allowed_user_ids,
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Job creation helper
    # ------------------------------------------------------------------

    def _create_job_from_slack(
        self,
        text: str,
        user_id: str,
        channel: str,
        thread_ts: str | None,
        event_id: str,
    ) -> Any:
        """Create a job in the pipeline from a Slack message.

        Returns the created Job dataclass or None if deduplicated.
        """
        from backend.jobs.queue import JobQueue

        source_event_id = f"slack:{event_id}"

        if _is_duplicate_event("slack", source_event_id):
            logger.info("Duplicate Slack event %s — skipping job creation", event_id)
            return None

        queue = JobQueue()
        job = queue.create_job(
            workspace_id=_DEFAULT_WORKSPACE,
            title=text[:120].strip() or "Slack request",
            description=text,
            source="slack",
            requester=user_id,
            source_ref=thread_ts or channel,
        )

        _record_event(
            source="slack",
            source_event_id=source_event_id,
            workspace_id=_DEFAULT_WORKSPACE,
            result_json=f'{{"job_id": "{job.id}"}}',
        )

        logger.info(
            "Created job %s from Slack event %s (user=%s, channel=%s)",
            job.id,
            event_id,
            user_id,
            channel,
        )
        return job

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        """Wire up all Slack event and action handlers."""
        if not self._ready:
            return

        app = self._app

        @app.event("app_mention")
        async def _handle_mention(event: dict, say: Any) -> None:
            await self.handle_mention(event, say)

        @app.event("message")
        async def _handle_message(event: dict, say: Any) -> None:
            await self.handle_message(event, say)

        @app.event("file_shared")
        async def _handle_file_shared(event: dict) -> None:
            await self.handle_file_shared(event)

        @app.action("approve_job")
        async def _handle_approve(ack: Any, body: dict, action: dict) -> None:
            await self.handle_action(ack, body, action)

        @app.action("request_changes_job")
        async def _handle_request_changes(ack: Any, body: dict, action: dict) -> None:
            await self.handle_action(ack, body, action)

        @app.action("cancel_job")
        async def _handle_cancel(ack: Any, body: dict, action: dict) -> None:
            await self.handle_action(ack, body, action)

    async def handle_mention(self, event: dict, say: Any) -> None:
        """Handle @mention events — create a job from the mention text.

        The thread_ts is stored as source_ref so future notifications
        land in the same Slack thread.
        """
        team_id = event.get("team")
        user_id = event.get("user")
        if not self._authorize(team_id, user_id):
            return

        text = event.get("text", "").strip()
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        event_ts = event.get("event_ts") or event.get("ts", "")

        # Strip the bot mention prefix (e.g. "<@U12345> do something")
        # Remove leading <@...> pattern
        if text.startswith("<@"):
            closing = text.find(">")
            if closing != -1:
                text = text[closing + 1 :].strip()

        if not text:
            await say(
                text="Please include a task description after mentioning me.",
                thread_ts=thread_ts,
            )
            return

        job = self._create_job_from_slack(
            text=text,
            user_id=user_id or "unknown",
            channel=channel,
            thread_ts=thread_ts,
            event_id=event_ts,
        )

        if job is None:
            await say(
                text="I already received that request — checking on it now.",
                thread_ts=thread_ts,
            )
            return

        await say(
            text=f"Got it! Created job `{job.id[:8]}...` — _{job.title}_",
            thread_ts=thread_ts,
        )

    async def handle_message(self, event: dict, say: Any) -> None:
        """Handle direct messages — create a job from the DM text.

        Only processes DMs (channel_type == 'im').  Ignores bot messages,
        message_changed subtypes, and other non-user messages.
        """
        # Only handle DMs
        channel_type = event.get("channel_type", "")
        if channel_type != "im":
            return

        # Ignore bot messages and subtypes (edits, joins, etc.)
        if event.get("bot_id") or event.get("subtype"):
            return

        team_id = event.get("team")
        user_id = event.get("user")
        if not self._authorize(team_id, user_id):
            return

        text = event.get("text", "").strip()
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        event_ts = event.get("event_ts") or event.get("ts", "")

        if not text:
            return

        job = self._create_job_from_slack(
            text=text,
            user_id=user_id or "unknown",
            channel=channel,
            thread_ts=thread_ts,
            event_id=event_ts,
        )

        if job is None:
            await say(
                text="Duplicate request detected — I'm already on it.",
                thread_ts=thread_ts,
            )
            return

        await say(
            text=f"On it! Job `{job.id[:8]}...` created — _{job.title}_",
            thread_ts=thread_ts,
        )

    async def handle_file_shared(self, event: dict) -> None:
        """Handle file_shared events — download and attach to the latest job.

        If a file is shared in a thread that maps to an existing job
        (via source_ref), it is downloaded and stored as a job input file.
        """
        file_id = event.get("file_id", "")
        user_id = event.get("user_id") or event.get("user", "")
        channel_id = event.get("channel_id", "")

        if not file_id:
            logger.warning("file_shared event missing file_id")
            return

        # Fetch file info from Slack API
        try:
            result = await self._app.client.files_info(file=file_id)
            file_info = result.get("file", {})
        except Exception:
            logger.exception("Failed to fetch file info for %s", file_id)
            return

        file_name = file_info.get("name", f"slack_file_{file_id}")
        file_url = file_info.get("url_private_download") or file_info.get("url_private", "")

        if not file_url:
            logger.warning("No download URL for file %s", file_id)
            return

        # Download file content
        try:
            file_bytes = await self.download_file(file_url, self._bot_token)
        except Exception:
            logger.exception("Failed to download file %s from Slack", file_id)
            return

        # Find the most recent job for this channel/thread
        thread_ts = (
            file_info.get("shares", {})
            .get("public", {})
            .get(channel_id, [{}])[0]
            .get("thread_ts", "")
        ) or ""

        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT id FROM jobs
                WHERE source = 'slack' AND (source_ref = ? OR source_ref = ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (thread_ts, channel_id),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            logger.info(
                "No matching job for file %s in channel %s — ignoring",
                file_id,
                channel_id,
            )
            return

        job_id = row["id"]

        # Store file on disk
        from backend.config import JOBS_DIR

        job_dir = JOBS_DIR / job_id / "input"
        job_dir.mkdir(parents=True, exist_ok=True)
        file_path = job_dir / file_name
        file_path.write_bytes(file_bytes)

        # Record in job_files table
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO job_files (id, job_id, node_id, filename, file_path, file_type, mime_type, size_bytes)
                VALUES (?, ?, NULL, ?, ?, 'input', ?, ?)
                """,
                (
                    _new_id(),
                    job_id,
                    file_name,
                    str(file_path),
                    file_info.get("mimetype", "application/octet-stream"),
                    len(file_bytes),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Attached file %s (%d bytes) to job %s",
            file_name,
            len(file_bytes),
            job_id,
        )

    # ------------------------------------------------------------------
    # Block Kit interactions
    # ------------------------------------------------------------------

    async def handle_action(self, ack: Any, body: dict, action: dict) -> None:
        """Handle Block Kit button clicks for approve / request changes / cancel.

        Action IDs:
            approve_job          — mark the review gate as approved
            request_changes_job  — request changes (sends back to reviewer)
            cancel_job           — cancel the job
        """
        await ack()

        action_id = action.get("action_id", "")
        # action value stores the job_id
        job_id = action.get("value", "")
        user_id = body.get("user", {}).get("id", "unknown")
        channel = body.get("channel", {}).get("id", "")
        message_ts = body.get("message", {}).get("ts", "")

        if not job_id:
            logger.warning("Block Kit action %s has no job_id value", action_id)
            return

        from backend.jobs.queue import JobQueue

        queue = JobQueue()
        job = queue.get_job(job_id)
        if not job:
            logger.warning("Block Kit action for unknown job %s", job_id)
            return

        if action_id == "approve_job":
            queue.update_job_status(job_id, "executing")
            response_text = f"Approved by <@{user_id}>. Resuming execution..."
            logger.info("Job %s approved by %s via Slack", job_id, user_id)

        elif action_id == "request_changes_job":
            queue.update_job_status(job_id, "reviewing")
            response_text = f"Changes requested by <@{user_id}>. Sending back for review..."
            logger.info("Job %s changes requested by %s via Slack", job_id, user_id)

        elif action_id == "cancel_job":
            queue.update_job_status(job_id, "cancelled")
            response_text = f"Cancelled by <@{user_id}>."
            logger.info("Job %s cancelled by %s via Slack", job_id, user_id)

        else:
            logger.warning("Unknown action_id: %s", action_id)
            response_text = f"Unknown action: {action_id}"

        # Update the original message to reflect the action taken
        if channel and message_ts:
            try:
                await self._app.client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    text=response_text,
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": response_text,
                            },
                        }
                    ],
                )
            except Exception:
                logger.exception(
                    "Failed to update message after action %s on job %s",
                    action_id,
                    job_id,
                )

    # ------------------------------------------------------------------
    # Job lifecycle notifications
    # ------------------------------------------------------------------

    async def post_job_started(
        self,
        channel: str,
        thread_ts: str,
        job: Any,
    ) -> Optional[str]:
        """Post a "job started" notification into the Slack thread.

        Returns the message_ts of the posted message (for later updates),
        or None if the post fails.
        """
        node_count = 0
        try:
            from backend.jobs.queue import JobQueue

            queue = JobQueue()
            nodes = queue.get_nodes(job.id)
            node_count = len(nodes)
        except Exception:
            logger.debug("Could not count nodes for job %s", job.id)

        text = f"\U0001f682 Job started: *{job.title}* ({node_count} node{'s' if node_count != 1 else ''})"

        try:
            result = await self._app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=text,
            )
            return result.get("ts")
        except Exception:
            logger.exception("Failed to post job_started for %s", job.id)
            return None

    async def update_progress(
        self,
        channel: str,
        message_ts: str,
        job: Any,
        current_node: Any,
    ) -> None:
        """Update an existing message with a progress bar.

        Respects a rate limit of at most 1 update per 3 seconds per message
        to stay within Slack API limits.
        """
        now = time.monotonic()
        last = self._last_update.get(message_ts, 0.0)
        if now - last < self._rate_limit_sec:
            return
        self._last_update[message_ts] = now

        # Calculate progress
        completed = 0
        total = 0
        try:
            from backend.jobs.queue import JobQueue

            queue = JobQueue()
            nodes = queue.get_nodes(job.id)
            total = len(nodes)
            completed = sum(
                1 for n in nodes if n.status in ("completed", "failed", "skipped")
            )
        except Exception:
            logger.debug("Could not calculate progress for job %s", job.id)

        bar = _progress_bar(completed, total)
        current_label = current_node.title if current_node else "..."
        text = (
            f"\U0001f682 *{job.title}*\n"
            f"`{bar}` ({completed}/{total})\n"
            f"Current: _{current_label}_"
        )

        try:
            await self._app.client.chat_update(
                channel=channel,
                ts=message_ts,
                text=text,
            )
        except Exception:
            logger.exception(
                "Failed to update progress for job %s (msg %s)", job.id, message_ts
            )

    async def post_node_complete(
        self,
        channel: str,
        thread_ts: str,
        node: Any,
    ) -> None:
        """Post a collapsed node result into the thread."""
        status_emoji = "\u2705" if node.status == "completed" else "\u274c"
        output_preview = ""
        if node.output_json:
            try:
                import json

                output = json.loads(node.output_json)
                if isinstance(output, dict):
                    summary = output.get("summary", output.get("result", ""))
                else:
                    summary = str(output)
                output_preview = summary[:200]
            except Exception:
                output_preview = str(node.output_json)[:200]

        blocks: list[dict[str, Any]] = [
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"{status_emoji} *Node {node.sequence}: {node.title}* — {node.status}"
                        ),
                    }
                ],
            },
        ]

        if output_preview:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"```{output_preview}```",
                        }
                    ],
                }
            )

        try:
            await self._app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"Node {node.sequence}: {node.title} — {node.status}",
                blocks=blocks,
            )
        except Exception:
            logger.exception(
                "Failed to post node_complete for node %s (job %s)",
                node.id,
                node.job_id,
            )

    async def post_review_gate(
        self,
        channel: str,
        thread_ts: str,
        job: Any,
        node: Any,
    ) -> None:
        """Post a Block Kit review gate with Approve / Request Changes / Cancel buttons."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"\U0001f6d1 *Review Gate* — Job `{job.id[:8]}...`\n"
                        f"*{job.title}*\n"
                        f"Node: _{node.title}_ (#{node.sequence})\n"
                        f"Review {job.review_count + 1}/{job.max_reviews}"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "\u2705 Approve",
                        },
                        "style": "primary",
                        "action_id": "approve_job",
                        "value": job.id,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "\U0001f4dd Request Changes",
                        },
                        "action_id": "request_changes_job",
                        "value": job.id,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "\u274c Cancel",
                        },
                        "style": "danger",
                        "action_id": "cancel_job",
                        "value": job.id,
                    },
                ],
            },
        ]

        try:
            await self._app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"Review gate for job {job.id[:8]}... — Approve, Request Changes, or Cancel",
                blocks=blocks,
            )
        except Exception:
            logger.exception("Failed to post review gate for job %s", job.id)

    async def post_job_complete(
        self,
        channel: str,
        thread_ts: str,
        job: Any,
        output_files: list[Any] | None = None,
    ) -> None:
        """Post job completion summary and upload output files.

        Output files are uploaded via ``files.uploadV2`` into the thread.
        """
        summary = job.result_summary or "No summary available."
        text = (
            f"\u2705 *Job Complete: {job.title}*\n"
            f"_{summary}_"
        )

        try:
            await self._app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=text,
            )
        except Exception:
            logger.exception("Failed to post job_complete for %s", job.id)

        # Upload output files if any
        if output_files:
            for f in output_files:
                file_path = Path(f.file_path) if isinstance(f.file_path, str) else f.file_path
                if file_path.exists():
                    try:
                        await self.upload_file(
                            channel=channel,
                            thread_ts=thread_ts,
                            file_path=file_path,
                            title=f.filename,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to upload output file %s for job %s",
                            f.filename,
                            job.id,
                        )

    async def post_job_failed(
        self,
        channel: str,
        thread_ts: str,
        job: Any,
        error: str,
    ) -> None:
        """Post a job failure notification with error details and next steps."""
        error_preview = error[:500] if error else "Unknown error"
        text = (
            f"\u274c *Job Failed: {job.title}*\n"
            f"```{error_preview}```\n"
            f"*Next steps:*\n"
            f"\u2022 Check the job details: `GET /api/jobs/{job.id}`\n"
            f"\u2022 Review the audit log for detailed error context\n"
            f"\u2022 Retry by mentioning me with the same request"
        )

        try:
            await self._app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=text,
            )
        except Exception:
            logger.exception("Failed to post job_failed for %s", job.id)

    # ------------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------------

    async def download_file(self, file_url: str, token: str) -> bytes:
        """Download a file from Slack using the bot token for auth.

        Parameters
        ----------
        file_url : str
            The ``url_private_download`` from the Slack file object.
        token : str
            Bot token used in the Authorization header.

        Returns
        -------
        bytes
            Raw file content.

        Raises
        ------
        RuntimeError
            If the download fails or returns a non-200 status.
        """
        import aiohttp

        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url, headers=headers) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"Slack file download failed: HTTP {resp.status} for {file_url}"
                    )
                return await resp.read()

    async def upload_file(
        self,
        channel: str,
        thread_ts: str,
        file_path: Path,
        title: str,
    ) -> None:
        """Upload a file to a Slack channel/thread via files.uploadV2.

        Parameters
        ----------
        channel : str
            Slack channel ID.
        thread_ts : str
            Thread timestamp to post the file into.
        file_path : Path
            Local path to the file.
        title : str
            Display title for the uploaded file.
        """
        try:
            await self._app.client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                file=str(file_path),
                title=title,
                filename=file_path.name,
            )
        except Exception:
            logger.exception(
                "Failed to upload file %s to channel %s", file_path.name, channel
            )
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Socket Mode handler in the background.

        If slack-bolt is not installed or the bot was not properly
        initialized, this is a no-op with a warning log.
        """
        if not self._ready:
            logger.warning("SlackBot.start() called but bot is not ready — skipping")
            return

        self._handler = AsyncSocketModeHandler(
            app=self._app,
            app_token=self._app_token,
        )
        logger.info("Starting Slack Socket Mode handler...")
        # start_async() returns immediately; runs the websocket in the background
        await self._handler.start_async()
        logger.info("Slack Socket Mode handler started")

    async def stop(self) -> None:
        """Gracefully shut down the Socket Mode handler."""
        if self._handler is not None:
            logger.info("Stopping Slack Socket Mode handler...")
            try:
                await self._handler.close_async()
            except Exception:
                logger.exception("Error during Slack handler shutdown")
            self._handler = None
            logger.info("Slack Socket Mode handler stopped")

        # Clean up rate-limit tracker
        self._last_update.clear()


# ---------------------------------------------------------------------------
# Module-level convenience: singleton factory
# ---------------------------------------------------------------------------

_instance: SlackBot | None = None


def get_slack_bot() -> SlackBot | None:
    """Return the module-level SlackBot singleton, or None if disabled.

    Call this from server.py lifespan to wire up the integration::

        from backend.integrations.slack_bot import get_slack_bot

        bot = get_slack_bot()
        if bot:
            await bot.start()
    """
    global _instance

    if _instance is not None:
        return _instance

    if not SLACK_ENABLED:
        logger.info("Slack integration disabled (SLACK_ENABLED != true)")
        return None

    if not _SLACK_AVAILABLE:
        logger.warning("Slack integration enabled but slack-bolt not installed")
        return None

    if not SLACK_BOT_TOKEN:
        logger.error("SLACK_ENABLED=true but SLACK_BOT_TOKEN is empty")
        return None

    if not SLACK_APP_TOKEN:
        logger.error("SLACK_ENABLED=true but SLACK_APP_TOKEN is empty")
        return None

    if not SLACK_ALLOWED_TEAM_IDS:
        logger.error("SLACK_ENABLED=true but SLACK_ALLOWED_TEAM_IDS is empty")
        return None

    _instance = SlackBot(
        bot_token=SLACK_BOT_TOKEN,
        app_token=SLACK_APP_TOKEN,
        allowed_team_ids=SLACK_ALLOWED_TEAM_IDS,
        allowed_user_ids=SLACK_ALLOWED_USER_IDS,
    )
    return _instance
