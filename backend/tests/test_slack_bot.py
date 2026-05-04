"""
Integration tests for the Slack bot (backend/integrations/slack_bot.py).

All external dependencies (slack_bolt, slack_sdk, aiohttp, DB) are mocked.
No real Slack API calls are made.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# We need to mock slack_bolt before importing the module under test, because
# the import itself tries `from slack_bolt.async_app import AsyncApp`.
# ---------------------------------------------------------------------------

_mock_async_app_cls = MagicMock(name="AsyncApp")
_mock_socket_handler_cls = MagicMock(name="AsyncSocketModeHandler")

# Pre-populate sys.modules so the conditional import succeeds
_slack_bolt_async_app = MagicMock()
_slack_bolt_async_app.AsyncApp = _mock_async_app_cls

_slack_bolt_adapter = MagicMock()
_slack_bolt_adapter.AsyncSocketModeHandler = _mock_socket_handler_cls

sys.modules.setdefault("slack_bolt", MagicMock())
sys.modules.setdefault("slack_bolt.async_app", _slack_bolt_async_app)
sys.modules.setdefault("slack_bolt.adapter", MagicMock())
sys.modules.setdefault("slack_bolt.adapter.socket_mode", MagicMock())
sys.modules.setdefault(
    "slack_bolt.adapter.socket_mode.async_handler", _slack_bolt_adapter
)
sys.modules.setdefault("slack_sdk", MagicMock())


# ---------------------------------------------------------------------------
# Helpers: fake Job / Node / JobFile objects
# ---------------------------------------------------------------------------

def _fake_job(
    *,
    job_id: str = "job-1234-abcd",
    title: str = "Test task",
    source: str = "slack",
    source_ref: str = "C123:1234567890.123456",
    status: str = "pending",
    result_summary: str | None = None,
    review_count: int = 0,
    max_reviews: int = 3,
    error: str | None = None,
    description: str | None = "Do the thing",
    requester: str | None = "U_USER",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        workspace_id="ws-default",
        title=title,
        description=description,
        source=source,
        source_ref=source_ref,
        status=status,
        priority=1,
        requester=requester,
        mode="quick",
        template_id=None,
        result_summary=result_summary,
        review_count=review_count,
        max_reviews=max_reviews,
        error=error,
        cost_cents=0.0,
        created_at="2026-04-11T00:00:00+00:00",
        updated_at="2026-04-11T00:01:00+00:00",
    )


def _fake_node(
    *,
    node_id: str = "node-0001",
    job_id: str = "job-1234-abcd",
    sequence: int = 1,
    title: str = "Step 1",
    status: str = "completed",
    output_json: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=node_id,
        job_id=job_id,
        sequence=sequence,
        title=title,
        status=status,
        output_json=output_json,
    )


def _fake_file(
    *,
    filename: str = "report.pdf",
    file_path: str = "/tmp/jobs/job-1234-abcd/output/report.pdf",
    file_type: str = "output",
) -> SimpleNamespace:
    return SimpleNamespace(
        id="file-0001",
        job_id="job-1234-abcd",
        node_id=None,
        filename=filename,
        file_path=file_path,
        file_type=file_type,
        mime_type="application/pdf",
        size_bytes=1024,
    )


# ---------------------------------------------------------------------------
# Fixture: a SlackBot instance with mocked internals
# ---------------------------------------------------------------------------

@pytest.fixture()
def bot():
    """Create a SlackBot with mocked AsyncApp and sensible defaults."""
    # Force the module-level flag to True so SlackBot.__init__ proceeds
    import backend.integrations.slack_bot as mod

    original_flag = mod._SLACK_AVAILABLE
    mod._SLACK_AVAILABLE = True

    # Create a mock AsyncApp instance that the constructor will store
    mock_app = MagicMock(name="app_instance")
    mock_app.client = AsyncMock(name="slack_client")
    mock_app.event = MagicMock(side_effect=lambda event_name: lambda fn: fn)
    mock_app.action = MagicMock(side_effect=lambda action_id: lambda fn: fn)

    # Patch AsyncApp class to return our mock instance
    with patch.object(mod, "AsyncApp", return_value=mock_app):
        bot = mod.SlackBot(
            bot_token="xoxb-test-token",
            app_token="xapp-test-token",
            allowed_team_ids=["T_ALLOWED"],
            allowed_user_ids=[],
        )

    # Expose the mock app's client for assertions
    bot._app = mock_app

    yield bot

    mod._SLACK_AVAILABLE = original_flag


@pytest.fixture()
def bot_with_user_allowlist():
    """SlackBot that has a user-level allowlist configured."""
    import backend.integrations.slack_bot as mod

    original_flag = mod._SLACK_AVAILABLE
    mod._SLACK_AVAILABLE = True

    mock_app = MagicMock(name="app_instance")
    mock_app.client = AsyncMock(name="slack_client")
    mock_app.event = MagicMock(side_effect=lambda event_name: lambda fn: fn)
    mock_app.action = MagicMock(side_effect=lambda action_id: lambda fn: fn)

    with patch.object(mod, "AsyncApp", return_value=mock_app):
        b = mod.SlackBot(
            bot_token="xoxb-test-token",
            app_token="xapp-test-token",
            allowed_team_ids=["T_ALLOWED"],
            allowed_user_ids=["U_ADMIN", "U_BOB"],
        )
    b._app = mock_app

    yield b

    mod._SLACK_AVAILABLE = original_flag


# ===================================================================
# Tests: Initialization
# ===================================================================


class TestSlackBotInit:
    """Tests for bot construction and readiness."""

    def test_init_sets_ready(self, bot):
        assert bot._ready is True
        assert bot._bot_token == "xoxb-test-token"
        assert bot._app_token == "xapp-test-token"

    def test_init_without_slack_bolt(self):
        """When _SLACK_AVAILABLE is False, the bot is created but not ready."""
        import backend.integrations.slack_bot as mod

        original = mod._SLACK_AVAILABLE
        mod._SLACK_AVAILABLE = False
        try:
            b = mod.SlackBot(
                bot_token="xoxb-test",
                app_token="xapp-test",
                allowed_team_ids=["T1"],
                allowed_user_ids=[],
            )
            assert b._ready is False
            assert b._app is None
        finally:
            mod._SLACK_AVAILABLE = original

    def test_allowed_teams_stored_as_set(self, bot):
        assert bot._allowed_team_ids == {"T_ALLOWED"}

    def test_allowed_users_empty_means_all(self, bot):
        assert bot._allowed_user_ids == set()


# ===================================================================
# Tests: Authorization
# ===================================================================


class TestAuthorization:
    """Tests for team_id and user_id validation."""

    def test_authorized_team(self, bot):
        assert bot._authorize(team_id="T_ALLOWED", user_id="U_ANY") is True

    def test_unauthorized_team(self, bot):
        assert bot._authorize(team_id="T_BAD", user_id="U_ANY") is False

    def test_none_team_rejected(self, bot):
        assert bot._authorize(team_id=None, user_id="U_ANY") is False

    def test_user_allowlist_allows_listed_user(self, bot_with_user_allowlist):
        assert bot_with_user_allowlist._authorize("T_ALLOWED", "U_ADMIN") is True

    def test_user_allowlist_rejects_unlisted_user(self, bot_with_user_allowlist):
        assert bot_with_user_allowlist._authorize("T_ALLOWED", "U_RANDOM") is False

    def test_user_allowlist_rejects_none_user(self, bot_with_user_allowlist):
        assert bot_with_user_allowlist._authorize("T_ALLOWED", None) is False

    def test_empty_user_allowlist_allows_all(self, bot):
        """When allowed_user_ids is empty, any user from an allowed team passes."""
        assert bot._authorize("T_ALLOWED", "U_ANYBODY") is True
        assert bot._authorize("T_ALLOWED", "U_SOMEONE_ELSE") is True


# ===================================================================
# Tests: Progress bar
# ===================================================================


class TestProgressBar:
    """Tests for the _progress_bar helper."""

    def test_zero_total(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(0, 0)
        assert "?" in result

    def test_full(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(5, 5)
        assert "100%" in result
        assert ">" not in result  # no arrow when complete

    def test_partial(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(3, 10)
        assert "30%" in result

    def test_half(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(5, 10)
        assert "50%" in result

    def test_over_total_clamps(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(15, 10)
        assert "100%" in result


# ===================================================================
# Tests: @mention handler
# ===================================================================


class TestHandleMention:
    """Tests for app_mention events."""

    @pytest.mark.asyncio
    async def test_mention_creates_job(self, bot):
        """A valid @mention should create a job and reply with confirmation."""
        say = AsyncMock()
        event = {
            "team": "T_ALLOWED",
            "user": "U_USER",
            "text": "<@U_BOT> build a dashboard",
            "channel": "C123",
            "thread_ts": "1234567890.123456",
            "event_ts": "1234567890.123457",
            "ts": "1234567890.123457",
        }

        fake_job = _fake_job(title="build a dashboard")

        with patch(
            "backend.integrations.slack_bot.SlackBot._create_job_from_slack",
            return_value=fake_job,
        ) as mock_create:
            await bot.handle_mention(event, say)

        mock_create.assert_called_once_with(
            text="build a dashboard",
            user_id="U_USER",
            channel="C123",
            thread_ts="1234567890.123456",
            event_id="1234567890.123457",
        )
        say.assert_called_once()
        call_kwargs = say.call_args
        assert "job-1234" in call_kwargs.kwargs.get("text", call_kwargs.args[0] if call_kwargs.args else "")

    @pytest.mark.asyncio
    async def test_mention_strips_bot_prefix(self, bot):
        """The <@BOTID> prefix should be stripped from the task text."""
        say = AsyncMock()
        event = {
            "team": "T_ALLOWED",
            "user": "U_USER",
            "text": "<@U_BOT> analyze quarterly report",
            "channel": "C123",
            "ts": "1234567890.123457",
        }

        with patch(
            "backend.integrations.slack_bot.SlackBot._create_job_from_slack",
            return_value=_fake_job(),
        ) as mock_create:
            await bot.handle_mention(event, say)

        # The text passed to _create_job_from_slack should NOT have the <@U_BOT> prefix
        assert mock_create.call_args.kwargs["text"] == "analyze quarterly report"

    @pytest.mark.asyncio
    async def test_mention_empty_text_asks_for_description(self, bot):
        """If the mention has no task text, the bot should ask for a description."""
        say = AsyncMock()
        event = {
            "team": "T_ALLOWED",
            "user": "U_USER",
            "text": "<@U_BOT>",
            "channel": "C123",
            "ts": "1234567890.123457",
        }

        await bot.handle_mention(event, say)

        say.assert_called_once()
        msg = say.call_args.kwargs.get("text", "")
        assert "task description" in msg.lower()

    @pytest.mark.asyncio
    async def test_mention_unauthorized_team_ignored(self, bot):
        """Events from unauthorized teams should be silently ignored."""
        say = AsyncMock()
        event = {
            "team": "T_EVIL",
            "user": "U_USER",
            "text": "<@U_BOT> hack the mainframe",
            "channel": "C123",
            "ts": "1234567890.123457",
        }

        await bot.handle_mention(event, say)
        say.assert_not_called()

    @pytest.mark.asyncio
    async def test_mention_duplicate_event(self, bot):
        """If _create_job_from_slack returns None (duplicate), reply accordingly."""
        say = AsyncMock()
        event = {
            "team": "T_ALLOWED",
            "user": "U_USER",
            "text": "<@U_BOT> same request again",
            "channel": "C123",
            "ts": "1234567890.123457",
        }

        with patch(
            "backend.integrations.slack_bot.SlackBot._create_job_from_slack",
            return_value=None,
        ):
            await bot.handle_mention(event, say)

        say.assert_called_once()
        msg = say.call_args.kwargs.get("text", "")
        assert "already" in msg.lower()


# ===================================================================
# Tests: DM handler
# ===================================================================


class TestHandleMessage:
    """Tests for direct message handling."""

    @pytest.mark.asyncio
    async def test_dm_creates_job(self, bot):
        """A DM (channel_type=im) should create a job."""
        say = AsyncMock()
        event = {
            "channel_type": "im",
            "team": "T_ALLOWED",
            "user": "U_USER",
            "text": "summarize my email",
            "channel": "D_DM_CHANNEL",
            "ts": "1234567890.123457",
        }

        with patch(
            "backend.integrations.slack_bot.SlackBot._create_job_from_slack",
            return_value=_fake_job(title="summarize my email"),
        ) as mock_create:
            await bot.handle_message(event, say)

        mock_create.assert_called_once()
        say.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_dm_channel_ignored(self, bot):
        """Messages in non-DM channels should be ignored by handle_message."""
        say = AsyncMock()
        event = {
            "channel_type": "channel",
            "team": "T_ALLOWED",
            "user": "U_USER",
            "text": "random chat",
            "channel": "C123",
            "ts": "1234567890.123457",
        }

        await bot.handle_message(event, say)
        say.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_messages_ignored(self, bot):
        """Messages from bots (bot_id set) should be ignored."""
        say = AsyncMock()
        event = {
            "channel_type": "im",
            "bot_id": "B_BOT",
            "team": "T_ALLOWED",
            "user": "U_USER",
            "text": "bot echo",
            "channel": "D_DM",
            "ts": "1234567890.123457",
        }

        await bot.handle_message(event, say)
        say.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_subtypes_ignored(self, bot):
        """Subtypes like message_changed or channel_join should be ignored."""
        say = AsyncMock()
        event = {
            "channel_type": "im",
            "subtype": "message_changed",
            "team": "T_ALLOWED",
            "user": "U_USER",
            "text": "edited message",
            "channel": "D_DM",
            "ts": "1234567890.123457",
        }

        await bot.handle_message(event, say)
        say.assert_not_called()


# ===================================================================
# Tests: File upload handling
# ===================================================================


class TestHandleFileShared:
    """Tests for file_shared event handling."""

    @pytest.mark.asyncio
    async def test_file_shared_missing_file_id(self, bot):
        """Events without file_id should be logged and ignored."""
        event = {"user_id": "U_USER", "channel_id": "C123"}
        # Should not raise
        await bot.handle_file_shared(event)

    @pytest.mark.asyncio
    async def test_file_shared_fetches_info_and_downloads(self, bot, tmp_path):
        """A valid file_shared event should fetch file info, download, and attach."""
        event = {
            "file_id": "F_FILE1",
            "user_id": "U_USER",
            "channel_id": "C123",
        }

        # Mock files_info API call
        bot._app.client.files_info = AsyncMock(return_value={
            "file": {
                "name": "data.csv",
                "url_private_download": "https://files.slack.com/data.csv",
                "mimetype": "text/csv",
                "shares": {
                    "public": {
                        "C123": [{"thread_ts": "1234567890.123456"}]
                    }
                },
            }
        })

        # Mock download
        file_bytes = b"col1,col2\nval1,val2\n"

        # Mock DB lookup to find the matching job
        mock_conn = MagicMock()
        mock_row = {"id": "job-1234-abcd"}
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        # Mock file storage directory
        job_dir = tmp_path / "job-1234-abcd" / "input"

        with (
            patch(
                "backend.integrations.slack_bot.SlackBot.download_file",
                new_callable=AsyncMock,
                return_value=file_bytes,
            ),
            patch("backend.integrations.slack_bot._get_conn", return_value=mock_conn),
            patch("backend.config.JOBS_DIR", tmp_path),
            patch("backend.integrations.slack_bot._new_id", return_value="file-new-id"),
        ):
            await bot.handle_file_shared(event)

        # Verify the file was written to disk
        expected_path = tmp_path / "job-1234-abcd" / "input" / "data.csv"
        assert expected_path.exists()
        assert expected_path.read_bytes() == file_bytes

        # Verify DB insert was called
        assert mock_conn.execute.call_count >= 2  # SELECT + INSERT
        mock_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_file_shared_no_matching_job(self, bot):
        """If no job matches the file's channel/thread, the file is ignored."""
        event = {
            "file_id": "F_ORPHAN",
            "user_id": "U_USER",
            "channel_id": "C_UNKNOWN",
        }

        bot._app.client.files_info = AsyncMock(return_value={
            "file": {
                "name": "orphan.txt",
                "url_private_download": "https://files.slack.com/orphan.txt",
                "mimetype": "text/plain",
                "shares": {},
            }
        })

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None

        with (
            patch(
                "backend.integrations.slack_bot.SlackBot.download_file",
                new_callable=AsyncMock,
                return_value=b"data",
            ),
            patch("backend.integrations.slack_bot._get_conn", return_value=mock_conn),
        ):
            await bot.handle_file_shared(event)

        # No commit since no job was found
        mock_conn.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_file_shared_api_error_handled(self, bot):
        """If files_info API fails, the error is caught and logged (no crash)."""
        event = {
            "file_id": "F_BROKEN",
            "user_id": "U_USER",
            "channel_id": "C123",
        }

        bot._app.client.files_info = AsyncMock(side_effect=Exception("Slack API down"))

        # Should not raise
        await bot.handle_file_shared(event)


# ===================================================================
# Tests: Block Kit actions (approve / request changes / cancel)
# ===================================================================


class TestHandleAction:
    """Tests for Block Kit button interactions."""

    @pytest.mark.asyncio
    async def test_approve_job(self, bot):
        """Clicking Approve should update job status to executing."""
        ack = AsyncMock()
        body = {
            "user": {"id": "U_ADMIN"},
            "channel": {"id": "C123"},
            "message": {"ts": "1234567890.999"},
        }
        action = {"action_id": "approve_job", "value": "job-1234-abcd"}

        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)

        ack.assert_called_once()
        mock_queue.update_job_status.assert_called_once_with("job-1234-abcd", "executing")
        bot._app.client.chat_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_changes(self, bot):
        """Clicking Request Changes should set status to reviewing."""
        ack = AsyncMock()
        body = {
            "user": {"id": "U_ADMIN"},
            "channel": {"id": "C123"},
            "message": {"ts": "1234567890.999"},
        }
        action = {"action_id": "request_changes_job", "value": "job-1234-abcd"}

        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)

        ack.assert_called_once()
        mock_queue.update_job_status.assert_called_once_with("job-1234-abcd", "reviewing")

    @pytest.mark.asyncio
    async def test_cancel_job(self, bot):
        """Clicking Cancel should set status to cancelled."""
        ack = AsyncMock()
        body = {
            "user": {"id": "U_ADMIN"},
            "channel": {"id": "C123"},
            "message": {"ts": "1234567890.999"},
        }
        action = {"action_id": "cancel_job", "value": "job-1234-abcd"}

        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)

        ack.assert_called_once()
        mock_queue.update_job_status.assert_called_once_with("job-1234-abcd", "cancelled")

    @pytest.mark.asyncio
    async def test_action_missing_job_id(self, bot):
        """An action with no job_id in value should be silently ignored."""
        ack = AsyncMock()
        body = {"user": {"id": "U_ADMIN"}, "channel": {"id": "C123"}, "message": {"ts": "1"}}
        action = {"action_id": "approve_job", "value": ""}

        await bot.handle_action(ack, body, action)
        ack.assert_called_once()
        # No crash, no queue interaction

    @pytest.mark.asyncio
    async def test_action_unknown_job(self, bot):
        """An action for a non-existent job should be silently ignored."""
        ack = AsyncMock()
        body = {"user": {"id": "U_ADMIN"}, "channel": {"id": "C123"}, "message": {"ts": "1"}}
        action = {"action_id": "approve_job", "value": "job-ghost"}

        mock_queue = MagicMock()
        mock_queue.get_job.return_value = None

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)

        ack.assert_called_once()
        mock_queue.update_job_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_action_slack_api_error_handled(self, bot):
        """If chat_update fails after action, the error is caught gracefully."""
        ack = AsyncMock()
        body = {
            "user": {"id": "U_ADMIN"},
            "channel": {"id": "C123"},
            "message": {"ts": "1234567890.999"},
        }
        action = {"action_id": "approve_job", "value": "job-1234-abcd"}

        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()

        bot._app.client.chat_update = AsyncMock(side_effect=Exception("Slack 500"))

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            # Should not raise even though chat_update fails
            await bot.handle_action(ack, body, action)

        ack.assert_called_once()
        mock_queue.update_job_status.assert_called_once()


# ===================================================================
# Tests: handle_worker_event
# ===================================================================


class TestHandleWorkerEvent:
    """Tests for the worker event bridge (handle_worker_event)."""

    @pytest.mark.asyncio
    async def test_ignores_non_slack_jobs(self, bot):
        """Events for jobs with source != 'slack' should be silently ignored."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job(source="web")

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_done", {"job_id": "job-web"})

        bot._app.client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_missing_job_id(self, bot):
        """Events without job_id should be silently ignored."""
        await bot.handle_worker_event("job_done", {})
        bot._app.client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_unparseable_source_ref(self, bot):
        """Jobs with source_ref lacking ':' separator should be skipped."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job(source_ref="no-colon-here")

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_done", {"job_id": "job-1234-abcd"})

        bot._app.client.chat_postMessage.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_planning_posts_started(self, bot):
        """job_planning event should post a 'job started' message."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()
        mock_queue.get_nodes.return_value = [_fake_node(), _fake_node(sequence=2)]

        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "msg-ts-1"})

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_planning", {"job_id": "job-1234-abcd"})

        bot._app.client.chat_postMessage.assert_called_once()
        call_kwargs = bot._app.client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C123"
        assert call_kwargs["thread_ts"] == "1234567890.123456"
        assert "Job started" in call_kwargs["text"] or "Test task" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_job_progress_posts_progress_bar(self, bot):
        """job_progress event should post a progress update with a bar."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()

        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "msg-ts-2"})

        data = {
            "job_id": "job-1234-abcd",
            "progress": {
                "completed": 3,
                "total": 5,
                "current_node": "Generate report",
            },
        }

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_progress", data)

        bot._app.client.chat_postMessage.assert_called_once()
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "60%" in text  # 3/5 = 60%
        assert "(3/5)" in text
        assert "Generate report" in text

    @pytest.mark.asyncio
    async def test_job_progress_no_current_node(self, bot):
        """job_progress without current_node should still work."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()

        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "msg-ts-3"})

        data = {
            "job_id": "job-1234-abcd",
            "progress": {
                "completed": 2,
                "total": 4,
            },
        }

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_progress", data)

        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "50%" in text
        assert "(2/4)" in text
        # Should NOT have "Current:" line if no current_node
        assert "Current:" not in text

    @pytest.mark.asyncio
    async def test_node_completed_posts_node_result(self, bot):
        """node_completed event should post a node result to the thread."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()
        mock_queue.get_node.return_value = _fake_node(
            title="Analyze data",
            status="completed",
            output_json='{"summary": "Found 42 anomalies"}',
        )

        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "msg-ts-4"})

        data = {"job_id": "job-1234-abcd", "node_id": "node-0001"}

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("node_completed", data)

        bot._app.client.chat_postMessage.assert_called_once()
        call_kwargs = bot._app.client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C123"
        assert "Analyze data" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_job_reviewing_posts_review_gate(self, bot):
        """job_reviewing event should post Block Kit buttons for review gate."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job(status="reviewing", review_count=1)
        mock_queue.get_nodes.return_value = [
            _fake_node(sequence=1, status="completed"),
            _fake_node(sequence=2, status="running", node_id="node-0002", title="Final step"),
        ]

        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "msg-ts-5"})

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_reviewing", {"job_id": "job-1234-abcd"})

        bot._app.client.chat_postMessage.assert_called_once()
        call_kwargs = bot._app.client.chat_postMessage.call_args.kwargs
        assert "blocks" in call_kwargs
        # Verify the blocks contain action buttons
        blocks = call_kwargs["blocks"]
        action_block = next((b for b in blocks if b.get("type") == "actions"), None)
        assert action_block is not None
        button_ids = [el["action_id"] for el in action_block["elements"]]
        assert "approve_job" in button_ids
        assert "request_changes_job" in button_ids
        assert "cancel_job" in button_ids

    @pytest.mark.asyncio
    async def test_job_done_posts_completion_and_uploads(self, bot, tmp_path):
        """job_done event should post completion and upload output files."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job(
            status="done",
            result_summary="Generated 3 reports successfully",
        )

        # Create a real temp file to test upload
        output_file = tmp_path / "report.pdf"
        output_file.write_bytes(b"%PDF-fake-content")

        mock_queue.get_files.return_value = [
            _fake_file(file_path=str(output_file)),
        ]

        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "msg-ts-6"})
        bot._app.client.files_upload_v2 = AsyncMock()

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_done", {"job_id": "job-1234-abcd"})

        # Should post completion message
        bot._app.client.chat_postMessage.assert_called()
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "Complete" in text or "complete" in text.lower()
        assert "Generated 3 reports" in text

        # Should upload the output file
        bot._app.client.files_upload_v2.assert_called_once()
        upload_kwargs = bot._app.client.files_upload_v2.call_args.kwargs
        assert upload_kwargs["channel"] == "C123"
        assert upload_kwargs["title"] == "report.pdf"

    @pytest.mark.asyncio
    async def test_job_done_no_output_files(self, bot):
        """job_done with no output files should still post completion message."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job(
            status="done",
            result_summary="All done",
        )
        mock_queue.get_files.return_value = []

        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "msg-ts-7"})

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_done", {"job_id": "job-1234-abcd"})

        bot._app.client.chat_postMessage.assert_called()
        bot._app.client.files_upload_v2.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_failed_posts_error(self, bot):
        """job_failed event should post failure message with error details."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job(status="failed")

        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "msg-ts-8"})

        data = {
            "job_id": "job-1234-abcd",
            "error": "Model crashed: OOM on deepseek-r1:14b",
        }

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_failed", data)

        bot._app.client.chat_postMessage.assert_called_once()
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "Failed" in text or "failed" in text.lower()
        assert "OOM" in text

    @pytest.mark.asyncio
    async def test_job_failed_truncates_long_error(self, bot):
        """Long error messages should be truncated to 500 chars."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job(status="failed")

        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "msg-ts-9"})

        long_error = "x" * 1000
        data = {"job_id": "job-1234-abcd", "error": long_error}

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_failed", data)

        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        # The error inside the code block should be at most 500 chars
        assert "x" * 501 not in text

    @pytest.mark.asyncio
    async def test_slack_api_down_doesnt_crash_worker(self, bot):
        """If the Slack API is down during notification, it should not crash."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()

        bot._app.client.chat_postMessage = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            # These should all complete without raising
            await bot.handle_worker_event("job_planning", {"job_id": "job-1234-abcd"})
            await bot.handle_worker_event("job_progress", {
                "job_id": "job-1234-abcd",
                "progress": {"completed": 1, "total": 3},
            })
            await bot.handle_worker_event("job_failed", {
                "job_id": "job-1234-abcd",
                "error": "Some error",
            })

    @pytest.mark.asyncio
    async def test_unhandled_event_types_silently_ignored(self, bot):
        """Event types not in the dispatch map should be silently ignored."""
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _fake_job()

        bot._app.client.chat_postMessage = AsyncMock()

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_worker_event("job_executing", {"job_id": "job-1234-abcd"})
            await bot.handle_worker_event("node_retry", {"job_id": "job-1234-abcd"})
            await bot.handle_worker_event("worker_circuit_open", {"job_id": "job-1234-abcd"})

        bot._app.client.chat_postMessage.assert_not_called()


# ===================================================================
# Tests: Lifecycle (start / stop)
# ===================================================================


class TestLifecycle:
    """Tests for bot start() and stop()."""

    @pytest.mark.asyncio
    async def test_start_creates_handler_and_starts(self, bot):
        """start() should create a SocketModeHandler and call start_async()."""
        import backend.integrations.slack_bot as mod

        mock_handler_instance = AsyncMock()
        mock_handler_cls = MagicMock(return_value=mock_handler_instance)

        with patch.object(mod, "AsyncSocketModeHandler", mock_handler_cls):
            await bot.start()

        mock_handler_cls.assert_called_once_with(
            app=bot._app,
            app_token="xapp-test-token",
        )
        mock_handler_instance.start_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_noop_when_not_ready(self):
        """start() should be a no-op when the bot is not ready."""
        import backend.integrations.slack_bot as mod

        original = mod._SLACK_AVAILABLE
        mod._SLACK_AVAILABLE = False
        try:
            b = mod.SlackBot(
                bot_token="xoxb-test",
                app_token="xapp-test",
                allowed_team_ids=["T1"],
                allowed_user_ids=[],
            )
            # Should not raise
            await b.start()
            assert b._handler is None
        finally:
            mod._SLACK_AVAILABLE = original

    @pytest.mark.asyncio
    async def test_stop_closes_handler(self, bot):
        """stop() should call close_async() on the handler."""
        mock_handler = AsyncMock()
        bot._handler = mock_handler

        await bot.stop()

        mock_handler.close_async.assert_called_once()
        assert bot._handler is None
        assert len(bot._last_update) == 0

    @pytest.mark.asyncio
    async def test_stop_handles_close_error(self, bot):
        """stop() should handle errors from close_async() gracefully."""
        mock_handler = AsyncMock()
        mock_handler.close_async.side_effect = Exception("Connection lost")
        bot._handler = mock_handler

        # Should not raise
        await bot.stop()
        assert bot._handler is None

    @pytest.mark.asyncio
    async def test_stop_when_no_handler(self, bot):
        """stop() should be safe to call when handler is None."""
        bot._handler = None
        await bot.stop()  # Should not raise


# ===================================================================
# Tests: get_slack_bot() singleton factory
# ===================================================================


class TestGetSlackBot:
    """Tests for the module-level singleton factory."""

    def test_returns_none_when_disabled(self):
        import backend.integrations.slack_bot as mod

        # Reset singleton
        original_instance = mod._instance
        mod._instance = None

        try:
            with (
                patch.object(mod, "SLACK_ENABLED", False),
            ):
                result = mod.get_slack_bot()
            assert result is None
        finally:
            mod._instance = original_instance

    def test_returns_none_when_missing_bot_token(self):
        import backend.integrations.slack_bot as mod

        original_instance = mod._instance
        mod._instance = None

        try:
            with (
                patch.object(mod, "SLACK_ENABLED", True),
                patch.object(mod, "_SLACK_AVAILABLE", True),
                patch.object(mod, "SLACK_BOT_TOKEN", ""),
            ):
                result = mod.get_slack_bot()
            assert result is None
        finally:
            mod._instance = original_instance

    def test_returns_none_when_missing_app_token(self):
        import backend.integrations.slack_bot as mod

        original_instance = mod._instance
        mod._instance = None

        try:
            with (
                patch.object(mod, "SLACK_ENABLED", True),
                patch.object(mod, "_SLACK_AVAILABLE", True),
                patch.object(mod, "SLACK_BOT_TOKEN", "xoxb-real"),
                patch.object(mod, "SLACK_APP_TOKEN", ""),
            ):
                result = mod.get_slack_bot()
            assert result is None
        finally:
            mod._instance = original_instance

    def test_returns_none_when_no_allowed_teams(self):
        import backend.integrations.slack_bot as mod

        original_instance = mod._instance
        mod._instance = None

        try:
            with (
                patch.object(mod, "SLACK_ENABLED", True),
                patch.object(mod, "_SLACK_AVAILABLE", True),
                patch.object(mod, "SLACK_BOT_TOKEN", "xoxb-real"),
                patch.object(mod, "SLACK_APP_TOKEN", "xapp-real"),
                patch.object(mod, "SLACK_ALLOWED_TEAM_IDS", []),
            ):
                result = mod.get_slack_bot()
            assert result is None
        finally:
            mod._instance = original_instance

    def test_returns_cached_instance(self):
        import backend.integrations.slack_bot as mod

        sentinel = object()
        original_instance = mod._instance
        mod._instance = sentinel

        try:
            result = mod.get_slack_bot()
            assert result is sentinel
        finally:
            mod._instance = original_instance


# ===================================================================
# Tests: Job creation helper
# ===================================================================


class TestCreateJobFromSlack:
    """Tests for _create_job_from_slack."""

    def test_creates_job_with_correct_params(self, bot):
        """_create_job_from_slack should create a job with source='slack'."""
        fake_job = _fake_job()
        mock_queue = MagicMock()
        mock_queue.create_job.return_value = fake_job

        with (
            patch("backend.integrations.slack_bot._is_duplicate_event", return_value=False),
            patch("backend.integrations.slack_bot._record_event"),
            patch("backend.integrations.slack_bot._get_default_workspace_id", return_value="ws-default"),
            patch("backend.jobs.queue.JobQueue", return_value=mock_queue),
        ):
            result = bot._create_job_from_slack(
                text="Build a report",
                user_id="U_USER",
                channel="C123",
                thread_ts="1234567890.123456",
                event_id="evt-001",
            )

        assert result is fake_job
        mock_queue.create_job.assert_called_once()
        call_kwargs = mock_queue.create_job.call_args.kwargs
        assert call_kwargs["source"] == "slack"
        assert call_kwargs["source_ref"] == "C123:1234567890.123456"
        assert call_kwargs["requester"] == "U_USER"
        assert call_kwargs["title"] == "Build a report"

    def test_returns_none_for_duplicate(self, bot):
        """Duplicate events should return None without creating a job."""
        with (
            patch("backend.integrations.slack_bot._is_duplicate_event", return_value=True),
            patch("backend.integrations.slack_bot._get_default_workspace_id", return_value="ws-default"),
        ):
            result = bot._create_job_from_slack(
                text="duplicate request",
                user_id="U_USER",
                channel="C123",
                thread_ts="1234567890.123456",
                event_id="evt-dup",
            )

        assert result is None

    def test_source_ref_without_thread_ts(self, bot):
        """When thread_ts is None, source_ref should just be the channel."""
        fake_job = _fake_job()
        mock_queue = MagicMock()
        mock_queue.create_job.return_value = fake_job

        with (
            patch("backend.integrations.slack_bot._is_duplicate_event", return_value=False),
            patch("backend.integrations.slack_bot._record_event"),
            patch("backend.integrations.slack_bot._get_default_workspace_id", return_value="ws-default"),
            patch("backend.jobs.queue.JobQueue", return_value=mock_queue),
        ):
            result = bot._create_job_from_slack(
                text="No thread",
                user_id="U_USER",
                channel="C123",
                thread_ts=None,
                event_id="evt-002",
            )

        call_kwargs = mock_queue.create_job.call_args.kwargs
        assert call_kwargs["source_ref"] == "C123"


# ===================================================================
# Tests: Rate limiting on progress updates
# ===================================================================


class TestRateLimiting:
    """Tests for progress update rate limiting."""

    @pytest.mark.asyncio
    async def test_rate_limit_skips_rapid_updates(self, bot):
        """Updates within the rate limit window should be skipped."""
        import time

        # Simulate that we just updated this message
        bot._last_update["msg-ts-1"] = time.monotonic()

        mock_queue_instance = MagicMock()
        mock_queue_instance.get_nodes.return_value = [
            _fake_node(status="completed"),
            _fake_node(sequence=2, status="pending"),
        ]

        job = _fake_job()
        node = _fake_node()

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue_instance):
            await bot.update_progress("C123", "msg-ts-1", job, node)

        # Should NOT have called chat_update due to rate limit
        bot._app.client.chat_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_allows_after_window(self, bot):
        """Updates after the rate limit window should proceed."""
        import time

        # Simulate that the last update was long ago
        bot._last_update["msg-ts-1"] = time.monotonic() - 10.0

        mock_queue_instance = MagicMock()
        mock_queue_instance.get_nodes.return_value = [
            _fake_node(status="completed"),
            _fake_node(sequence=2, status="pending"),
        ]

        bot._app.client.chat_update = AsyncMock()

        job = _fake_job()
        node = _fake_node(title="Current step")

        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue_instance):
            await bot.update_progress("C123", "msg-ts-1", job, node)

        bot._app.client.chat_update.assert_called_once()


# ===================================================================
# Tests: File upload helper
# ===================================================================


class TestUploadFile:
    """Tests for the upload_file helper."""

    @pytest.mark.asyncio
    async def test_upload_file_calls_api(self, bot, tmp_path):
        """upload_file should call files_upload_v2 with correct args."""
        test_file = tmp_path / "output.txt"
        test_file.write_text("hello world")

        bot._app.client.files_upload_v2 = AsyncMock()

        await bot.upload_file(
            channel="C123",
            thread_ts="1234567890.123456",
            file_path=test_file,
            title="Output File",
        )

        bot._app.client.files_upload_v2.assert_called_once_with(
            channel="C123",
            thread_ts="1234567890.123456",
            file=str(test_file),
            title="Output File",
            filename="output.txt",
        )

    @pytest.mark.asyncio
    async def test_upload_file_propagates_error(self, bot, tmp_path):
        """upload_file should re-raise errors from the Slack API."""
        test_file = tmp_path / "broken.txt"
        test_file.write_text("data")

        bot._app.client.files_upload_v2 = AsyncMock(
            side_effect=Exception("Upload failed")
        )

        with pytest.raises(Exception, match="Upload failed"):
            await bot.upload_file(
                channel="C123",
                thread_ts="1234567890.123456",
                file_path=test_file,
                title="Broken",
            )
