"""
Comprehensive pytest tests for the Slack Bot integration module.

Covers: initialization, authorization, event handlers, message processing,
job notifications, rate limiting, deduplication, file handling, Block Kit
actions, lifecycle, and the singleton factory.

All external dependencies (slack_sdk, aiohttp, sqlite3, backend.jobs)
are mocked — no real services needed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BOT_TOKEN = "xoxb-test-token"
APP_TOKEN = "xapp-test-token"
TEAM_ID = "T_ALLOWED"
USER_ID = "U_ALLOWED"


@pytest.fixture
def mock_config():
    """Patch config values used at import time by slack_bot."""
    with patch.dict(
        "os.environ",
        {
            "SLACK_BOT_TOKEN": BOT_TOKEN,
            "SLACK_APP_TOKEN": APP_TOKEN,
            "SLACK_ENABLED": "true",
            "SLACK_ALLOWED_TEAM_IDS": TEAM_ID,
            "SLACK_ALLOWED_USER_IDS": USER_ID,
        },
    ):
        yield


@pytest.fixture
def mock_async_app():
    """Return a MagicMock that stands in for slack_bolt AsyncApp."""
    app = MagicMock()
    app.client = MagicMock()
    # Make all Slack API client methods async
    app.client.chat_postMessage = AsyncMock(return_value={"ts": "1234567890.000100"})
    app.client.chat_update = AsyncMock(return_value={"ok": True})
    app.client.files_info = AsyncMock(return_value={"file": {}})
    app.client.files_upload_v2 = AsyncMock(return_value={"ok": True})
    # event / action decorators should be callable
    app.event = MagicMock(side_effect=lambda evt: lambda fn: fn)
    app.action = MagicMock(side_effect=lambda act: lambda fn: fn)
    return app


@pytest.fixture
def bot(mock_async_app):
    """Create a SlackBot with mocked internals — no real Slack connection."""
    with patch(
        "backend.integrations.slack_bot._SLACK_AVAILABLE", True
    ), patch(
        "backend.integrations.slack_bot.AsyncApp", return_value=mock_async_app
    ):
        from backend.integrations.slack_bot import SlackBot

        b = SlackBot(
            bot_token=BOT_TOKEN,
            app_token=APP_TOKEN,
            allowed_team_ids=[TEAM_ID],
            allowed_user_ids=[USER_ID],
        )
        # Replace the internal app reference with our mock
        b._app = mock_async_app
        return b


def _make_job(**overrides):
    """Build a minimal job-like object (SimpleNamespace)."""
    defaults = dict(
        id="job-1234-abcd-5678",
        title="Test Job",
        description="Do something",
        status="executing",
        result_summary="All good.",
        review_count=0,
        max_reviews=3,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_node(**overrides):
    """Build a minimal node-like object."""
    defaults = dict(
        id="node-0001",
        job_id="job-1234-abcd-5678",
        sequence=1,
        title="Step 1",
        status="completed",
        output_json='{"summary": "done"}',
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# =========================================================================
# TestSlackBotInit
# =========================================================================


class TestSlackBotInit:
    """Tests for SlackBot.__init__ and readiness checks."""

    def test_init_sets_ready_when_slack_available(self, bot):
        assert bot._ready is True

    def test_init_stores_allowed_teams(self, bot):
        assert TEAM_ID in bot._allowed_team_ids

    def test_init_stores_allowed_users(self, bot):
        assert USER_ID in bot._allowed_user_ids

    def test_init_rate_limit_default(self, bot):
        assert bot._rate_limit_sec == 3.0
        assert bot._last_update == {}

    def test_init_not_ready_when_slack_unavailable(self):
        with patch("backend.integrations.slack_bot._SLACK_AVAILABLE", False):
            from backend.integrations.slack_bot import SlackBot

            b = SlackBot(
                bot_token=BOT_TOKEN,
                app_token=APP_TOKEN,
                allowed_team_ids=[TEAM_ID],
                allowed_user_ids=[],
            )
            assert b._ready is False
            assert b._app is None

    def test_register_handlers_skipped_when_not_ready(self):
        with patch("backend.integrations.slack_bot._SLACK_AVAILABLE", False):
            from backend.integrations.slack_bot import SlackBot

            b = SlackBot(
                bot_token=BOT_TOKEN,
                app_token=APP_TOKEN,
                allowed_team_ids=[TEAM_ID],
                allowed_user_ids=[],
            )
            # _register_handlers should have been a no-op; _app is None
            assert b._handler is None


# =========================================================================
# TestAuthorization
# =========================================================================


class TestAuthorization:
    """Tests for SlackBot._authorize."""

    def test_authorize_valid_team_and_user(self, bot):
        assert bot._authorize(TEAM_ID, USER_ID) is True

    def test_authorize_rejects_unknown_team(self, bot):
        assert bot._authorize("T_UNKNOWN", USER_ID) is False

    def test_authorize_rejects_none_team(self, bot):
        assert bot._authorize(None, USER_ID) is False

    def test_authorize_rejects_unknown_user(self, bot):
        assert bot._authorize(TEAM_ID, "U_UNKNOWN") is False

    def test_authorize_rejects_none_user_when_allowlist_set(self, bot):
        assert bot._authorize(TEAM_ID, None) is False

    def test_authorize_allows_any_user_when_allowlist_empty(self, bot):
        bot._allowed_user_ids = set()
        assert bot._authorize(TEAM_ID, "U_ANY") is True

    def test_authorize_allows_none_user_when_allowlist_empty(self, bot):
        bot._allowed_user_ids = set()
        assert bot._authorize(TEAM_ID, None) is True


# =========================================================================
# TestEventHandlers
# =========================================================================


class TestEventHandlers:
    """Tests for handle_mention and handle_message."""

    @pytest.mark.asyncio
    async def test_mention_creates_job(self, bot):
        say = AsyncMock()
        event = {
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "<@U_BOT> summarize the report",
            "channel": "C123",
            "ts": "1111.0001",
            "event_ts": "1111.0001",
        }
        with patch.object(bot, "_create_job_from_slack", return_value=_make_job()):
            await bot.handle_mention(event, say)
        say.assert_awaited_once()
        assert "Created job" in say.call_args.kwargs.get("text", say.call_args[1].get("text", ""))

    @pytest.mark.asyncio
    async def test_mention_empty_text_asks_for_description(self, bot):
        say = AsyncMock()
        event = {
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "<@U_BOT>",
            "channel": "C123",
            "ts": "1111.0002",
        }
        await bot.handle_mention(event, say)
        say.assert_awaited_once()
        assert "task description" in say.call_args.kwargs.get("text", say.call_args[1].get("text", "")).lower()

    @pytest.mark.asyncio
    async def test_mention_duplicate_event(self, bot):
        say = AsyncMock()
        event = {
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "<@U_BOT> do it again",
            "channel": "C123",
            "ts": "1111.0003",
            "event_ts": "1111.0003",
        }
        with patch.object(bot, "_create_job_from_slack", return_value=None):
            await bot.handle_mention(event, say)
        assert "already received" in say.call_args.kwargs.get("text", say.call_args[1].get("text", "")).lower()

    @pytest.mark.asyncio
    async def test_mention_unauthorized_team_skips(self, bot):
        say = AsyncMock()
        event = {
            "team": "T_BAD",
            "user": USER_ID,
            "text": "<@U_BOT> hack",
            "channel": "C123",
            "ts": "1111.0004",
        }
        await bot.handle_mention(event, say)
        say.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_dm_creates_job(self, bot):
        say = AsyncMock()
        event = {
            "channel_type": "im",
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "please do the thing",
            "channel": "D999",
            "ts": "2222.0001",
            "event_ts": "2222.0001",
        }
        with patch.object(bot, "_create_job_from_slack", return_value=_make_job()):
            await bot.handle_message(event, say)
        say.assert_awaited_once()
        assert "On it!" in say.call_args.kwargs.get("text", say.call_args[1].get("text", ""))

    @pytest.mark.asyncio
    async def test_message_ignores_non_dm(self, bot):
        say = AsyncMock()
        event = {
            "channel_type": "channel",
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "hello",
            "channel": "C123",
            "ts": "2222.0002",
        }
        await bot.handle_message(event, say)
        say.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_ignores_bot_messages(self, bot):
        say = AsyncMock()
        event = {
            "channel_type": "im",
            "bot_id": "B_SOME_BOT",
            "team": TEAM_ID,
            "text": "bot echo",
            "channel": "D999",
            "ts": "2222.0003",
        }
        await bot.handle_message(event, say)
        say.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_ignores_subtypes(self, bot):
        say = AsyncMock()
        event = {
            "channel_type": "im",
            "subtype": "message_changed",
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "edited",
            "channel": "D999",
            "ts": "2222.0004",
        }
        await bot.handle_message(event, say)
        say.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_empty_text_skipped(self, bot):
        say = AsyncMock()
        event = {
            "channel_type": "im",
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "   ",
            "channel": "D999",
            "ts": "2222.0005",
            "event_ts": "2222.0005",
        }
        await bot.handle_message(event, say)
        say.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_duplicate_event(self, bot):
        say = AsyncMock()
        event = {
            "channel_type": "im",
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "dup request",
            "channel": "D999",
            "ts": "2222.0006",
            "event_ts": "2222.0006",
        }
        with patch.object(bot, "_create_job_from_slack", return_value=None):
            await bot.handle_message(event, say)
        assert "duplicate" in say.call_args.kwargs.get("text", say.call_args[1].get("text", "")).lower()


# =========================================================================
# TestMessageProcessing (_create_job_from_slack)
# =========================================================================


class TestMessageProcessing:
    """Tests for _create_job_from_slack (job creation from Slack events)."""

    def test_creates_job_successfully(self, bot):
        mock_queue = MagicMock()
        mock_queue.create_job.return_value = _make_job()
        with patch(
            "backend.integrations.slack_bot._is_duplicate_event", return_value=False
        ), patch(
            "backend.integrations.slack_bot._record_event"
        ), patch(
            "backend.jobs.queue.JobQueue", return_value=mock_queue
        ):
            job = bot._create_job_from_slack(
                text="do something",
                user_id=USER_ID,
                channel="C123",
                thread_ts="1111.0001",
                event_id="evt-1",
            )
        assert job is not None
        assert job.id == "job-1234-abcd-5678"
        mock_queue.create_job.assert_called_once()

    def test_returns_none_for_duplicate(self, bot):
        with patch(
            "backend.integrations.slack_bot._is_duplicate_event", return_value=True
        ):
            result = bot._create_job_from_slack(
                text="dup",
                user_id=USER_ID,
                channel="C123",
                thread_ts="1111.0002",
                event_id="evt-dup",
            )
        assert result is None

    def test_records_event_after_creation(self, bot):
        mock_queue = MagicMock()
        mock_queue.create_job.return_value = _make_job()
        with patch(
            "backend.integrations.slack_bot._is_duplicate_event", return_value=False
        ), patch(
            "backend.integrations.slack_bot._record_event"
        ) as mock_record, patch(
            "backend.jobs.queue.JobQueue", return_value=mock_queue
        ):
            bot._create_job_from_slack(
                text="record me",
                user_id=USER_ID,
                channel="C123",
                thread_ts=None,
                event_id="evt-rec",
            )
        mock_record.assert_called_once()
        args = mock_record.call_args
        assert args[1]["source"] == "slack" or args[0][0] == "slack"


# =========================================================================
# TestJobNotifications
# =========================================================================


class TestJobNotifications:
    """Tests for Slack notification methods (post_job_started, post_job_complete, etc.)."""

    @pytest.mark.asyncio
    async def test_post_job_started(self, bot):
        job = _make_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = [_make_node(), _make_node()]
            ts = await bot.post_job_started("C123", "thread-ts", job)
        assert ts == "1234567890.000100"
        bot._app.client.chat_postMessage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_job_started_returns_none_on_failure(self, bot):
        bot._app.client.chat_postMessage = AsyncMock(side_effect=Exception("API error"))
        job = _make_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = []
            ts = await bot.post_job_started("C123", "thread-ts", job)
        assert ts is None

    @pytest.mark.asyncio
    async def test_post_node_complete_completed(self, bot):
        node = _make_node(status="completed")
        await bot.post_node_complete("C123", "thread-ts", node)
        bot._app.client.chat_postMessage.assert_awaited_once()
        call_kwargs = bot._app.client.chat_postMessage.call_args.kwargs
        assert "completed" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_post_node_complete_failed(self, bot):
        node = _make_node(status="failed", output_json=None)
        await bot.post_node_complete("C123", "thread-ts", node)
        bot._app.client.chat_postMessage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_node_complete_with_dict_output(self, bot):
        node = _make_node(output_json='{"result": "some result text"}')
        await bot.post_node_complete("C123", "thread-ts", node)
        bot._app.client.chat_postMessage.assert_awaited_once()
        blocks = bot._app.client.chat_postMessage.call_args.kwargs["blocks"]
        # Should have 2 blocks: status context + output preview context
        assert len(blocks) == 2

    @pytest.mark.asyncio
    async def test_post_node_complete_with_malformed_json(self, bot):
        node = _make_node(output_json="not valid json{{{")
        await bot.post_node_complete("C123", "thread-ts", node)
        bot._app.client.chat_postMessage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_review_gate(self, bot):
        job = _make_job()
        node = _make_node()
        await bot.post_review_gate("C123", "thread-ts", job, node)
        bot._app.client.chat_postMessage.assert_awaited_once()
        blocks = bot._app.client.chat_postMessage.call_args.kwargs["blocks"]
        # Should have a section and an actions block
        block_types = [b["type"] for b in blocks]
        assert "section" in block_types
        assert "actions" in block_types

    @pytest.mark.asyncio
    async def test_post_review_gate_failure_logged(self, bot):
        bot._app.client.chat_postMessage = AsyncMock(side_effect=Exception("fail"))
        job = _make_job()
        node = _make_node()
        # Should not raise, just log
        await bot.post_review_gate("C123", "thread-ts", job, node)

    @pytest.mark.asyncio
    async def test_post_job_complete_no_files(self, bot):
        job = _make_job(result_summary="Finished successfully.")
        await bot.post_job_complete("C123", "thread-ts", job)
        bot._app.client.chat_postMessage.assert_awaited_once()
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "Finished successfully." in text

    @pytest.mark.asyncio
    async def test_post_job_complete_with_files(self, bot, tmp_path):
        job = _make_job()
        output_file = tmp_path / "result.txt"
        output_file.write_text("output data")
        f = SimpleNamespace(file_path=str(output_file), filename="result.txt")
        bot.upload_file = AsyncMock()
        await bot.post_job_complete("C123", "thread-ts", job, output_files=[f])
        bot.upload_file.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_job_complete_missing_file_skipped(self, bot):
        job = _make_job()
        f = SimpleNamespace(file_path="/nonexistent/file.txt", filename="file.txt")
        bot.upload_file = AsyncMock()
        await bot.post_job_complete("C123", "thread-ts", job, output_files=[f])
        bot.upload_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_post_job_complete_no_summary(self, bot):
        job = _make_job(result_summary=None)
        await bot.post_job_complete("C123", "thread-ts", job)
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "No summary available" in text

    @pytest.mark.asyncio
    async def test_post_job_failed(self, bot):
        job = _make_job()
        await bot.post_job_failed("C123", "thread-ts", job, "Something broke")
        bot._app.client.chat_postMessage.assert_awaited_once()
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "Something broke" in text
        assert "Failed" in text

    @pytest.mark.asyncio
    async def test_post_job_failed_empty_error(self, bot):
        job = _make_job()
        await bot.post_job_failed("C123", "thread-ts", job, "")
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "Unknown error" in text

    @pytest.mark.asyncio
    async def test_post_job_failed_long_error_truncated(self, bot):
        job = _make_job()
        long_error = "x" * 1000
        await bot.post_job_failed("C123", "thread-ts", job, long_error)
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        # error_preview is error[:500]
        assert len(long_error) > 500
        assert "x" * 500 in text


# =========================================================================
# TestRateLimiting
# =========================================================================


class TestRateLimiting:
    """Tests for update_progress rate-limiting."""

    @pytest.mark.asyncio
    async def test_update_progress_posts_first_call(self, bot):
        job = _make_job()
        node = _make_node()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = [
                _make_node(status="completed"),
                _make_node(status="pending"),
            ]
            await bot.update_progress("C123", "msg-ts-001", job, node)
        bot._app.client.chat_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_progress_rate_limited(self, bot):
        job = _make_job()
        node = _make_node()
        # Simulate a recent update
        bot._last_update["msg-ts-001"] = time.monotonic()

        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = []
            await bot.update_progress("C123", "msg-ts-001", job, node)
        # Should NOT have called chat_update because rate limit hasn't elapsed
        bot._app.client.chat_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_progress_allowed_after_cooldown(self, bot):
        job = _make_job()
        node = _make_node()
        # Simulate an old update (well past the 3s rate limit)
        bot._last_update["msg-ts-001"] = time.monotonic() - 10.0

        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = [
                _make_node(status="completed"),
            ]
            await bot.update_progress("C123", "msg-ts-001", job, node)
        bot._app.client.chat_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_progress_different_messages_independent(self, bot):
        job = _make_job()
        node = _make_node()
        bot._last_update["msg-A"] = time.monotonic()  # recently updated

        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = []
            # msg-B has never been updated, so it should go through
            await bot.update_progress("C123", "msg-B", job, node)
        bot._app.client.chat_update.assert_awaited_once()


# =========================================================================
# TestDeduplication
# =========================================================================


class TestDeduplication:
    """Tests for _is_duplicate_event and _record_event DB helpers."""

    def test_is_duplicate_returns_false_for_new_event(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch("backend.integrations.slack_bot._get_conn", return_value=mock_conn):
            from backend.integrations.slack_bot import _is_duplicate_event

            assert _is_duplicate_event("slack", "slack:evt-new") is False
        mock_conn.close.assert_called_once()

    def test_is_duplicate_returns_true_for_existing_event(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"id": "some-id"}
        with patch("backend.integrations.slack_bot._get_conn", return_value=mock_conn):
            from backend.integrations.slack_bot import _is_duplicate_event

            assert _is_duplicate_event("slack", "slack:evt-old") is True
        mock_conn.close.assert_called_once()

    def test_record_event_inserts_and_commits(self):
        mock_conn = MagicMock()
        with patch(
            "backend.integrations.slack_bot._get_conn", return_value=mock_conn
        ), patch("backend.integrations.slack_bot._new_id", return_value="uuid-1"), patch(
            "backend.integrations.slack_bot._now", return_value="2026-01-01T00:00:00Z"
        ):
            from backend.integrations.slack_bot import _record_event

            _record_event("slack", "slack:evt-1", "default", '{"job_id": "j1"}')
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_record_event_closes_conn_on_error(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("db error")
        with patch(
            "backend.integrations.slack_bot._get_conn", return_value=mock_conn
        ), patch("backend.integrations.slack_bot._new_id", return_value="uuid-1"), patch(
            "backend.integrations.slack_bot._now", return_value="2026-01-01T00:00:00Z"
        ):
            from backend.integrations.slack_bot import _record_event

            with pytest.raises(Exception, match="db error"):
                _record_event("slack", "slack:evt-err", "default")
        mock_conn.close.assert_called_once()


# =========================================================================
# TestBlockKitActions
# =========================================================================


class TestBlockKitActions:
    """Tests for handle_action (approve, request_changes, cancel)."""

    @pytest.mark.asyncio
    async def test_approve_job(self, bot):
        ack = AsyncMock()
        body = {
            "user": {"id": USER_ID},
            "channel": {"id": "C123"},
            "message": {"ts": "9999.0001"},
        }
        action = {"action_id": "approve_job", "value": "job-1234"}
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _make_job(id="job-1234")
        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)
        ack.assert_awaited_once()
        mock_queue.update_job_status.assert_called_once_with("job-1234", "executing")
        bot._app.client.chat_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_request_changes_job(self, bot):
        ack = AsyncMock()
        body = {
            "user": {"id": USER_ID},
            "channel": {"id": "C123"},
            "message": {"ts": "9999.0002"},
        }
        action = {"action_id": "request_changes_job", "value": "job-5678"}
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _make_job(id="job-5678")
        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)
        mock_queue.update_job_status.assert_called_once_with("job-5678", "reviewing")

    @pytest.mark.asyncio
    async def test_cancel_job(self, bot):
        ack = AsyncMock()
        body = {
            "user": {"id": USER_ID},
            "channel": {"id": "C123"},
            "message": {"ts": "9999.0003"},
        }
        action = {"action_id": "cancel_job", "value": "job-9999"}
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _make_job(id="job-9999")
        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)
        mock_queue.update_job_status.assert_called_once_with("job-9999", "cancelled")

    @pytest.mark.asyncio
    async def test_action_missing_job_id(self, bot):
        ack = AsyncMock()
        body = {"user": {"id": USER_ID}, "channel": {"id": "C123"}, "message": {"ts": "x"}}
        action = {"action_id": "approve_job", "value": ""}
        await bot.handle_action(ack, body, action)
        ack.assert_awaited_once()
        # Should return early without calling any queue methods
        bot._app.client.chat_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_action_unknown_job(self, bot):
        ack = AsyncMock()
        body = {"user": {"id": USER_ID}, "channel": {"id": "C123"}, "message": {"ts": "x"}}
        action = {"action_id": "approve_job", "value": "nonexistent"}
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = None
        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)
        bot._app.client.chat_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_action_unknown_action_id(self, bot):
        ack = AsyncMock()
        body = {
            "user": {"id": USER_ID},
            "channel": {"id": "C123"},
            "message": {"ts": "9999.0004"},
        }
        action = {"action_id": "unknown_action", "value": "job-1234"}
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _make_job(id="job-1234")
        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)
        # update_job_status should NOT be called for an unknown action
        mock_queue.update_job_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_action_chat_update_failure_logged(self, bot):
        ack = AsyncMock()
        body = {
            "user": {"id": USER_ID},
            "channel": {"id": "C123"},
            "message": {"ts": "9999.0005"},
        }
        action = {"action_id": "approve_job", "value": "job-1234"}
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _make_job(id="job-1234")
        bot._app.client.chat_update = AsyncMock(side_effect=Exception("Slack down"))
        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            # Should not raise
            await bot.handle_action(ack, body, action)
        mock_queue.update_job_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_action_no_channel_skips_update(self, bot):
        ack = AsyncMock()
        body = {
            "user": {"id": USER_ID},
            "channel": {},
            "message": {"ts": "9999.0006"},
        }
        action = {"action_id": "cancel_job", "value": "job-1234"}
        mock_queue = MagicMock()
        mock_queue.get_job.return_value = _make_job(id="job-1234")
        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            await bot.handle_action(ack, body, action)
        # channel is "" so chat_update should be skipped
        bot._app.client.chat_update.assert_not_awaited()


# =========================================================================
# TestFileHandling
# =========================================================================


class TestFileHandling:
    """Tests for handle_file_shared and download/upload utilities."""

    @pytest.mark.asyncio
    async def test_file_shared_missing_file_id(self, bot):
        event = {"file_id": "", "user_id": USER_ID, "channel_id": "C123"}
        await bot.handle_file_shared(event)
        bot._app.client.files_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_file_shared_api_failure(self, bot):
        bot._app.client.files_info = AsyncMock(side_effect=Exception("API down"))
        event = {"file_id": "F001", "user_id": USER_ID, "channel_id": "C123"}
        # Should not raise
        await bot.handle_file_shared(event)

    @pytest.mark.asyncio
    async def test_file_shared_no_download_url(self, bot):
        bot._app.client.files_info = AsyncMock(
            return_value={"file": {"name": "doc.pdf"}}
        )
        event = {"file_id": "F002", "user_id": USER_ID, "channel_id": "C123"}
        await bot.handle_file_shared(event)
        # Should return early because no download URL

    @pytest.mark.asyncio
    async def test_upload_file_calls_api(self, bot, tmp_path):
        f = tmp_path / "out.txt"
        f.write_text("data")
        await bot.upload_file("C123", "thread-ts", f, "out.txt")
        bot._app.client.files_upload_v2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upload_file_raises_on_failure(self, bot, tmp_path):
        bot._app.client.files_upload_v2 = AsyncMock(side_effect=Exception("upload err"))
        f = tmp_path / "bad.txt"
        f.write_text("data")
        with pytest.raises(Exception, match="upload err"):
            await bot.upload_file("C123", "thread-ts", f, "bad.txt")

    @pytest.mark.asyncio
    async def test_download_file_success(self, bot):
        import sys

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"file-content")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp = MagicMock()
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
        with patch.dict(sys.modules, {"aiohttp": mock_aiohttp}):
            data = await bot.download_file("https://files.slack.com/f1", BOT_TOKEN)
        assert data == b"file-content"

    @pytest.mark.asyncio
    async def test_download_file_non_200_raises(self, bot):
        import sys

        mock_resp = AsyncMock()
        mock_resp.status = 403
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_aiohttp = MagicMock()
        mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
        with patch.dict(sys.modules, {"aiohttp": mock_aiohttp}):
            with pytest.raises(RuntimeError, match="HTTP 403"):
                await bot.download_file("https://files.slack.com/f2", BOT_TOKEN)


# =========================================================================
# TestLifecycle
# =========================================================================


class TestLifecycle:
    """Tests for start() and stop() lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_when_not_ready_is_noop(self):
        with patch("backend.integrations.slack_bot._SLACK_AVAILABLE", False):
            from backend.integrations.slack_bot import SlackBot

            b = SlackBot(
                bot_token=BOT_TOKEN,
                app_token=APP_TOKEN,
                allowed_team_ids=[TEAM_ID],
                allowed_user_ids=[],
            )
            await b.start()
            assert b._handler is None

    @pytest.mark.asyncio
    async def test_start_creates_handler(self, bot):
        mock_handler = AsyncMock()
        mock_handler.start_async = AsyncMock()
        with patch(
            "backend.integrations.slack_bot.AsyncSocketModeHandler",
            return_value=mock_handler,
        ):
            await bot.start()
        mock_handler.start_async.assert_awaited_once()
        assert bot._handler is mock_handler

    @pytest.mark.asyncio
    async def test_stop_closes_handler(self, bot):
        mock_handler = AsyncMock()
        mock_handler.close_async = AsyncMock()
        bot._handler = mock_handler
        bot._last_update["msg-1"] = 1234.0

        await bot.stop()

        mock_handler.close_async.assert_awaited_once()
        assert bot._handler is None
        assert bot._last_update == {}

    @pytest.mark.asyncio
    async def test_stop_when_no_handler_is_safe(self, bot):
        bot._handler = None
        # Should not raise
        await bot.stop()

    @pytest.mark.asyncio
    async def test_stop_handles_close_exception(self, bot):
        mock_handler = AsyncMock()
        mock_handler.close_async = AsyncMock(side_effect=Exception("close err"))
        bot._handler = mock_handler
        # Should not raise
        await bot.stop()
        assert bot._handler is None


# =========================================================================
# TestProgressBar
# =========================================================================


class TestProgressBar:
    """Tests for the _progress_bar helper."""

    def test_zero_total(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(0, 0)
        assert "?" in result

    def test_half_done(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(5, 10, width=10)
        assert "50%" in result

    def test_fully_complete(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(10, 10, width=10)
        assert "100%" in result
        assert ">" not in result  # arrow not present when full

    def test_zero_completed(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(0, 10, width=10)
        assert "0%" in result

    def test_over_100_capped(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(15, 10, width=10)
        assert "100%" in result


# =========================================================================
# TestGetSlackBot (singleton factory)
# =========================================================================


class TestGetSlackBot:
    """Tests for the module-level get_slack_bot() factory."""

    def test_returns_none_when_disabled(self):
        with patch("backend.integrations.slack_bot.SLACK_ENABLED", False), patch(
            "backend.integrations.slack_bot._instance", None
        ):
            from backend.integrations.slack_bot import get_slack_bot

            assert get_slack_bot() is None

    def test_returns_none_when_slack_unavailable(self):
        with patch("backend.integrations.slack_bot.SLACK_ENABLED", True), patch(
            "backend.integrations.slack_bot._SLACK_AVAILABLE", False
        ), patch("backend.integrations.slack_bot._instance", None):
            from backend.integrations.slack_bot import get_slack_bot

            assert get_slack_bot() is None

    def test_returns_none_when_bot_token_empty(self):
        with patch("backend.integrations.slack_bot.SLACK_ENABLED", True), patch(
            "backend.integrations.slack_bot._SLACK_AVAILABLE", True
        ), patch("backend.integrations.slack_bot.SLACK_BOT_TOKEN", ""), patch(
            "backend.integrations.slack_bot._instance", None
        ):
            from backend.integrations.slack_bot import get_slack_bot

            assert get_slack_bot() is None

    def test_returns_none_when_app_token_empty(self):
        with patch("backend.integrations.slack_bot.SLACK_ENABLED", True), patch(
            "backend.integrations.slack_bot._SLACK_AVAILABLE", True
        ), patch("backend.integrations.slack_bot.SLACK_BOT_TOKEN", BOT_TOKEN), patch(
            "backend.integrations.slack_bot.SLACK_APP_TOKEN", ""
        ), patch("backend.integrations.slack_bot._instance", None):
            from backend.integrations.slack_bot import get_slack_bot

            assert get_slack_bot() is None

    def test_returns_none_when_no_allowed_teams(self):
        with patch("backend.integrations.slack_bot.SLACK_ENABLED", True), patch(
            "backend.integrations.slack_bot._SLACK_AVAILABLE", True
        ), patch("backend.integrations.slack_bot.SLACK_BOT_TOKEN", BOT_TOKEN), patch(
            "backend.integrations.slack_bot.SLACK_APP_TOKEN", APP_TOKEN
        ), patch("backend.integrations.slack_bot.SLACK_ALLOWED_TEAM_IDS", []), patch(
            "backend.integrations.slack_bot._instance", None
        ):
            from backend.integrations.slack_bot import get_slack_bot

            assert get_slack_bot() is None

    def test_returns_cached_instance(self):
        sentinel = object()
        with patch("backend.integrations.slack_bot._instance", sentinel):
            from backend.integrations.slack_bot import get_slack_bot

            assert get_slack_bot() is sentinel

    def test_creates_new_instance_when_fully_configured(self):
        mock_app = MagicMock()
        mock_app.event = MagicMock(side_effect=lambda evt: lambda fn: fn)
        mock_app.action = MagicMock(side_effect=lambda act: lambda fn: fn)
        mock_app.client = AsyncMock()

        with patch("backend.integrations.slack_bot._instance", None), \
             patch("backend.integrations.slack_bot.SLACK_ENABLED", True), \
             patch("backend.integrations.slack_bot._SLACK_AVAILABLE", True), \
             patch("backend.integrations.slack_bot.SLACK_BOT_TOKEN", BOT_TOKEN), \
             patch("backend.integrations.slack_bot.SLACK_APP_TOKEN", APP_TOKEN), \
             patch("backend.integrations.slack_bot.SLACK_ALLOWED_TEAM_IDS", [TEAM_ID]), \
             patch("backend.integrations.slack_bot.SLACK_ALLOWED_USER_IDS", [USER_ID]), \
             patch("backend.integrations.slack_bot.AsyncApp", return_value=mock_app):
            from backend.integrations.slack_bot import get_slack_bot

            result = get_slack_bot()
            assert result is not None
            assert result._ready is True

    def test_singleton_returns_same_instance_on_second_call(self):
        mock_app = MagicMock()
        mock_app.event = MagicMock(side_effect=lambda evt: lambda fn: fn)
        mock_app.action = MagicMock(side_effect=lambda act: lambda fn: fn)
        mock_app.client = AsyncMock()

        with patch("backend.integrations.slack_bot._instance", None), \
             patch("backend.integrations.slack_bot.SLACK_ENABLED", True), \
             patch("backend.integrations.slack_bot._SLACK_AVAILABLE", True), \
             patch("backend.integrations.slack_bot.SLACK_BOT_TOKEN", BOT_TOKEN), \
             patch("backend.integrations.slack_bot.SLACK_APP_TOKEN", APP_TOKEN), \
             patch("backend.integrations.slack_bot.SLACK_ALLOWED_TEAM_IDS", [TEAM_ID]), \
             patch("backend.integrations.slack_bot.SLACK_ALLOWED_USER_IDS", []), \
             patch("backend.integrations.slack_bot.AsyncApp", return_value=mock_app):
            import backend.integrations.slack_bot as mod

            mod._instance = None
            first = mod.get_slack_bot()
            second = mod.get_slack_bot()
            assert first is second
            assert first is not None


# =========================================================================
# TestDeduplicationWithDB (real SQLite via tmp_path)
# =========================================================================

# Minimal schema sufficient for dedup + job lookup tests.
_DEDUP_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    processed_at TEXT,
    result_json TEXT,
    UNIQUE(source, source_event_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    source TEXT NOT NULL DEFAULT 'web',
    source_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    requester TEXT,
    mode TEXT NOT NULL DEFAULT 'quick',
    template_id TEXT,
    result_summary TEXT,
    review_count INTEGER NOT NULL DEFAULT 0,
    max_reviews INTEGER NOT NULL DEFAULT 3,
    error TEXT,
    cost_cents REAL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_nodes (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    title TEXT NOT NULL,
    instructions TEXT,
    tools_allowed TEXT,
    expected_output TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    input_json TEXT,
    output_json TEXT,
    error TEXT,
    auto_generated INTEGER NOT NULL DEFAULT 1,
    input_schema_json TEXT,
    output_schema_json TEXT,
    side_effects_json TEXT,
    retry_policy_json TEXT,
    timeout_sec INTEGER DEFAULT 300,
    rollback_strategy TEXT,
    acceptance_tests_json TEXT,
    depends_on TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_files (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    node_id TEXT,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL DEFAULT 'input',
    mime_type TEXT,
    size_bytes INTEGER
);

CREATE TABLE IF NOT EXISTS job_audit_log (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    node_id TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    actor TEXT,
    timestamp TEXT NOT NULL
);
"""


def _init_test_db(db_path: Path) -> None:
    """Create required tables for Slack bot DB-backed tests."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DEDUP_SCHEMA)
    conn.commit()
    conn.close()


@pytest.fixture
def slack_db(tmp_path):
    """Temporary SQLite DB with all tables needed by Slack bot and job queue."""
    db_file = tmp_path / "test_slack.db"
    _init_test_db(db_file)
    with patch("backend.integrations.slack_bot.DB_PATH", db_file), \
         patch("backend.config.DB_PATH", db_file), \
         patch("backend.jobs.queue.DB_PATH", db_file):
        yield db_file


@pytest.fixture
def db_bot(slack_db):
    """SlackBot wired to a real tmp SQLite DB for integration-style tests."""
    mock_app = MagicMock()
    mock_app.client = MagicMock()
    mock_app.client.chat_postMessage = AsyncMock(return_value={"ts": "1234567890.000100"})
    mock_app.client.chat_update = AsyncMock(return_value={"ok": True})
    mock_app.client.files_info = AsyncMock(return_value={"file": {}})
    mock_app.client.files_upload_v2 = AsyncMock(return_value={"ok": True})
    mock_app.event = MagicMock(side_effect=lambda evt: lambda fn: fn)
    mock_app.action = MagicMock(side_effect=lambda act: lambda fn: fn)

    with patch("backend.integrations.slack_bot._SLACK_AVAILABLE", True), \
         patch("backend.integrations.slack_bot.AsyncApp", return_value=mock_app):
        from backend.integrations.slack_bot import SlackBot

        b = SlackBot(
            bot_token=BOT_TOKEN,
            app_token=APP_TOKEN,
            allowed_team_ids=[TEAM_ID],
            allowed_user_ids=[USER_ID],
        )
        b._app = mock_app
        return b


class TestDeduplicationWithDB:
    """Hit the real source_events table via tmp_path-backed SQLite."""

    def test_new_event_not_duplicate(self, slack_db):
        from backend.integrations.slack_bot import _is_duplicate_event

        assert _is_duplicate_event("slack", "evt-fresh") is False

    def test_recorded_event_is_duplicate(self, slack_db):
        from backend.integrations.slack_bot import _is_duplicate_event, _record_event

        _record_event("slack", "evt-abc", "default")
        assert _is_duplicate_event("slack", "evt-abc") is True

    def test_different_source_not_duplicate(self, slack_db):
        from backend.integrations.slack_bot import _is_duplicate_event, _record_event

        _record_event("slack", "evt-xyz", "default")
        assert _is_duplicate_event("web", "evt-xyz") is False

    def test_record_idempotent_insert_or_ignore(self, slack_db):
        from backend.integrations.slack_bot import _record_event

        _record_event("slack", "evt-idem", "default")
        _record_event("slack", "evt-idem", "default")  # no error

    def test_record_stores_result_json(self, slack_db):
        import sqlite3
        from backend.integrations.slack_bot import _record_event

        _record_event("slack", "evt-rj", "default", result_json='{"job_id": "j1"}')
        conn = sqlite3.connect(str(slack_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT result_json FROM source_events WHERE source_event_id = ?",
            ("evt-rj",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert "j1" in row["result_json"]


# =========================================================================
# TestCreateJobFromSlackWithDB
# =========================================================================


class TestCreateJobFromSlackWithDB:
    """Integration tests for _create_job_from_slack with real DB."""

    def test_creates_job_and_records_event(self, db_bot, slack_db):
        import sqlite3

        mock_queue = MagicMock()
        mock_queue.create_job.return_value = _make_job(id="job-db1")
        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            result = db_bot._create_job_from_slack(
                text="real db test",
                user_id=USER_ID,
                channel="C1",
                thread_ts="t1",
                event_id="evt-db1",
            )

        assert result is not None
        assert result.id == "job-db1"

        conn = sqlite3.connect(str(slack_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM source_events WHERE source_event_id = ?",
            ("slack:evt-db1",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert '"job_id"' in row["result_json"]

    def test_returns_none_for_duplicate_via_db(self, db_bot, slack_db):
        from backend.integrations.slack_bot import _record_event

        _record_event("slack", "slack:evt-dbdup", "default")
        result = db_bot._create_job_from_slack(
            text="dup via db",
            user_id=USER_ID,
            channel="C1",
            thread_ts="t1",
            event_id="evt-dbdup",
        )
        assert result is None

    def test_title_truncated_to_120_chars(self, db_bot, slack_db):
        long_text = "a" * 250
        mock_queue = MagicMock()
        mock_queue.create_job.return_value = _make_job()
        with patch("backend.jobs.queue.JobQueue", return_value=mock_queue):
            db_bot._create_job_from_slack(
                text=long_text,
                user_id=USER_ID,
                channel="C1",
                thread_ts="t1",
                event_id="evt-trunc",
            )
        call_kwargs = mock_queue.create_job.call_args.kwargs
        assert len(call_kwargs["title"]) <= 120


# =========================================================================
# TestHandleFileSharedWithDB
# =========================================================================


class TestHandleFileSharedWithDB:
    """Integration: file_shared attaches to an existing job via real DB."""

    @pytest.mark.asyncio
    async def test_file_attached_to_existing_job(self, db_bot, slack_db, tmp_path):
        import sqlite3

        # Insert a job row that the file lookup will match
        conn = sqlite3.connect(str(slack_db))
        now = "2025-06-01T00:00:00Z"
        job_id = "job-filetest"
        conn.execute(
            """INSERT INTO jobs (id, workspace_id, title, source, source_ref,
               status, priority, mode, review_count, max_reviews, cost_cents,
               created_at, updated_at)
            VALUES (?, 'default', 'file test', 'slack', '7777.8888',
                    'executing', 0, 'quick', 0, 3, 0, ?, ?)""",
            (job_id, now, now),
        )
        conn.commit()
        conn.close()

        db_bot._app.client.files_info = AsyncMock(
            return_value={
                "file": {
                    "name": "upload.csv",
                    "url_private_download": "https://files.slack.com/upload.csv",
                    "mimetype": "text/csv",
                    "shares": {
                        "public": {
                            "C_FILE": [{"thread_ts": "7777.8888"}],
                        }
                    },
                }
            }
        )

        jobs_dir = tmp_path / "jobs"
        with patch("backend.config.JOBS_DIR", jobs_dir):
            db_bot.download_file = AsyncMock(return_value=b"header\nrow1\n")
            event = {"file_id": "F_DB", "user_id": USER_ID, "channel_id": "C_FILE"}
            await db_bot.handle_file_shared(event)

        # File saved on disk
        saved = jobs_dir / job_id / "input" / "upload.csv"
        assert saved.exists()
        assert saved.read_bytes() == b"header\nrow1\n"

        # Record in job_files table
        conn = sqlite3.connect(str(slack_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM job_files WHERE job_id = ?", (job_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["filename"] == "upload.csv"
        assert row["mime_type"] == "text/csv"
        assert row["size_bytes"] == len(b"header\nrow1\n")

    @pytest.mark.asyncio
    async def test_file_no_matching_job_ignored(self, db_bot, slack_db):
        db_bot._app.client.files_info = AsyncMock(
            return_value={
                "file": {
                    "name": "orphan.txt",
                    "url_private_download": "https://files.slack.com/orphan.txt",
                    "shares": {"public": {"C1": [{"thread_ts": "no-match-ts"}]}},
                }
            }
        )
        db_bot.download_file = AsyncMock(return_value=b"data")
        event = {"file_id": "F_ORPHAN", "user_id": USER_ID, "channel_id": "C1"}
        await db_bot.handle_file_shared(event)
        # No error, file simply ignored

    @pytest.mark.asyncio
    async def test_download_failure_logged_not_raised(self, db_bot, slack_db):
        db_bot._app.client.files_info = AsyncMock(
            return_value={
                "file": {
                    "name": "fail.bin",
                    "url_private_download": "https://files.slack.com/fail.bin",
                    "shares": {"public": {}},
                }
            }
        )
        db_bot.download_file = AsyncMock(side_effect=RuntimeError("net error"))
        event = {"file_id": "F_FAIL", "user_id": USER_ID, "channel_id": "C1"}
        # Should not raise
        await db_bot.handle_file_shared(event)


# =========================================================================
# TestMentionBotPrefixStripping
# =========================================================================


class TestMentionBotPrefixStripping:
    """Verify that handle_mention strips the <@BOT_ID> prefix correctly."""

    @pytest.mark.asyncio
    async def test_strips_bot_mention(self, bot):
        say = AsyncMock()
        captured = {}

        def fake_create(text, user_id, channel, thread_ts, event_id):
            captured["text"] = text
            return _make_job(title=text[:120])

        bot._create_job_from_slack = MagicMock(side_effect=fake_create)
        event = {
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "<@U_BOT123> build a dashboard",
            "channel": "C1",
            "ts": "strip-1",
            "event_ts": "strip-1",
        }
        await bot.handle_mention(event, say)
        assert captured["text"] == "build a dashboard"

    @pytest.mark.asyncio
    async def test_no_closing_bracket_keeps_full_text(self, bot):
        """When the mention has no closing '>', the text is kept as-is."""
        say = AsyncMock()
        captured = {}

        def fake_create(text, user_id, channel, thread_ts, event_id):
            captured["text"] = text
            return _make_job(title=text[:120])

        bot._create_job_from_slack = MagicMock(side_effect=fake_create)
        event = {
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "<@U_BOT no closing bracket",
            "channel": "C1",
            "ts": "strip-2",
            "event_ts": "strip-2",
        }
        await bot.handle_mention(event, say)
        # The find(">") returns -1, so stripping does NOT happen
        # But the text still starts with <@, so it goes into create_job as-is
        assert captured["text"] == "<@U_BOT no closing bracket"

    @pytest.mark.asyncio
    async def test_mention_with_only_spaces_after_bot_id(self, bot):
        say = AsyncMock()
        event = {
            "team": TEAM_ID,
            "user": USER_ID,
            "text": "<@U_BOT>    ",
            "channel": "C1",
            "ts": "strip-3",
            "event_ts": "strip-3",
        }
        await bot.handle_mention(event, say)
        # Empty text after stripping should prompt user
        call_text = say.call_args.kwargs.get("text", say.call_args[1].get("text", ""))
        assert "task description" in call_text.lower()


# =========================================================================
# TestProgressBarEdgeCases
# =========================================================================


class TestProgressBarEdgeCases:
    """Additional edge cases for the _progress_bar helper."""

    def test_negative_total_returns_question_marks(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(5, -3)
        assert "?" in result

    def test_custom_width(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(5, 10, width=20)
        bar_inner = result.split("[")[1].split("]")[0]
        assert len(bar_inner) == 20

    def test_one_of_one(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(1, 1)
        assert "100%" in result

    def test_arrow_present_when_partial(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(3, 10, width=10)
        bar_inner = result.split("[")[1].split("]")[0]
        assert ">" in bar_inner

    def test_default_width_is_15(self):
        from backend.integrations.slack_bot import _progress_bar

        result = _progress_bar(5, 10)
        bar_inner = result.split("[")[1].split("]")[0]
        assert len(bar_inner) == 15


# =========================================================================
# TestUpdateProgressContent
# =========================================================================


class TestUpdateProgressContent:
    """Verify update_progress message content."""

    @pytest.mark.asyncio
    async def test_includes_progress_bar_and_node_title(self, bot):
        bot._app.client.chat_update = AsyncMock()
        bot._last_update.clear()
        job = _make_job(title="My Job")
        node = _make_node(title="Current Step")

        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = [
                _make_node(status="completed"),
                _make_node(status="pending"),
                _make_node(status="pending"),
            ]
            await bot.update_progress("C1", "msg-content", job, node)

        call_kwargs = bot._app.client.chat_update.call_args.kwargs
        text = call_kwargs["text"]
        assert "My Job" in text
        assert "Current Step" in text
        assert "1/3" in text

    @pytest.mark.asyncio
    async def test_handles_exception_in_get_nodes(self, bot):
        """If get_nodes raises, update_progress still sends a message."""
        bot._app.client.chat_update = AsyncMock()
        bot._last_update.clear()
        job = _make_job()
        node = _make_node()

        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.side_effect = Exception("db error")
            await bot.update_progress("C1", "msg-err", job, node)

        bot._app.client.chat_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_none_current_node_shows_ellipsis(self, bot):
        bot._app.client.chat_update = AsyncMock()
        bot._last_update.clear()
        job = _make_job()

        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = []
            await bot.update_progress("C1", "msg-none", job, None)

        call_kwargs = bot._app.client.chat_update.call_args.kwargs
        assert "..." in call_kwargs["text"]


# =========================================================================
# TestReviewGateContent
# =========================================================================


class TestReviewGateContent:
    """Verify review gate message structure and content."""

    @pytest.mark.asyncio
    async def test_review_count_shown(self, bot):
        bot._app.client.chat_postMessage = AsyncMock()
        job = _make_job(review_count=1, max_reviews=3)
        node = _make_node()
        await bot.post_review_gate("C1", "t1", job, node)

        blocks = bot._app.client.chat_postMessage.call_args.kwargs["blocks"]
        section = [b for b in blocks if b["type"] == "section"][0]
        section_text = section["text"]["text"]
        assert "Review 2/3" in section_text

    @pytest.mark.asyncio
    async def test_button_styles(self, bot):
        bot._app.client.chat_postMessage = AsyncMock()
        job = _make_job()
        node = _make_node()
        await bot.post_review_gate("C1", "t1", job, node)

        blocks = bot._app.client.chat_postMessage.call_args.kwargs["blocks"]
        actions = [b for b in blocks if b["type"] == "actions"][0]
        elements = actions["elements"]

        # Approve button should be "primary" style
        approve_btn = [e for e in elements if e["action_id"] == "approve_job"][0]
        assert approve_btn.get("style") == "primary"

        # Cancel button should be "danger" style
        cancel_btn = [e for e in elements if e["action_id"] == "cancel_job"][0]
        assert cancel_btn.get("style") == "danger"

        # Request changes has no explicit style
        rc_btn = [e for e in elements if e["action_id"] == "request_changes_job"][0]
        assert "style" not in rc_btn


# =========================================================================
# TestPostJobStartedNodeCount
# =========================================================================


class TestPostJobStartedNodeCount:
    """Verify singular/plural node count in job started message."""

    @pytest.mark.asyncio
    async def test_singular_node(self, bot):
        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "x"})
        job = _make_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = [_make_node()]
            await bot.post_job_started("C1", "t1", job)
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "1 node)" in text  # singular, no 's'

    @pytest.mark.asyncio
    async def test_plural_nodes(self, bot):
        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "x"})
        job = _make_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = [_make_node(), _make_node()]
            await bot.post_job_started("C1", "t1", job)
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "2 nodes)" in text  # plural

    @pytest.mark.asyncio
    async def test_zero_nodes(self, bot):
        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "x"})
        job = _make_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_nodes.return_value = []
            await bot.post_job_started("C1", "t1", job)
        text = bot._app.client.chat_postMessage.call_args.kwargs["text"]
        assert "0 nodes)" in text


# =========================================================================
# TestHandleWorkerEvent
# =========================================================================


class TestHandleWorkerEvent:
    """Tests for SlackBot.handle_worker_event — the bridge between
    worker lifecycle events and Slack notification methods."""

    def _slack_job(self, **overrides):
        """Build a job with source='slack' and a valid source_ref."""
        defaults = dict(
            id="job-abcd-1234",
            title="Slack Task",
            description="Do the thing",
            status="executing",
            result_summary="All good.",
            review_count=0,
            max_reviews=3,
            source="slack",
            source_ref="C_CHAN:1111.0001",
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    # 1. job_planning → post_job_started
    @pytest.mark.asyncio
    async def test_job_planning_calls_post_job_started(self, bot):
        job = self._slack_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            with patch.object(bot, "post_job_started", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_planning", {"job_id": job.id})
        mock_notify.assert_awaited_once_with("C_CHAN", "1111.0001", job)

    # 2. node_completed → post_node_complete
    @pytest.mark.asyncio
    async def test_node_completed_calls_post_node_complete(self, bot):
        job = self._slack_job()
        node = _make_node(id="node-99", job_id=job.id)
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            MockQ.return_value.get_node.return_value = node
            with patch.object(bot, "post_node_complete", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("node_completed", {"job_id": job.id, "node_id": "node-99"})
        mock_notify.assert_awaited_once_with("C_CHAN", "1111.0001", node)

    # 3. job_done → post_job_complete with output files
    @pytest.mark.asyncio
    async def test_job_done_calls_post_job_complete(self, bot):
        job = self._slack_job()
        refreshed_job = self._slack_job(result_summary="Final summary.")
        fake_files = [SimpleNamespace(file_path="/tmp/out.txt", filename="out.txt")]
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            inst = MockQ.return_value
            inst.get_job.side_effect = [job, refreshed_job]
            inst.get_files.return_value = fake_files
            with patch.object(bot, "post_job_complete", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_done", {"job_id": job.id})
        mock_notify.assert_awaited_once_with(
            "C_CHAN", "1111.0001", refreshed_job, output_files=fake_files,
        )

    # 4. job_failed → post_job_failed
    @pytest.mark.asyncio
    async def test_job_failed_calls_post_job_failed(self, bot):
        job = self._slack_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            with patch.object(bot, "post_job_failed", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_failed", {"job_id": job.id, "error": "Boom"})
        mock_notify.assert_awaited_once_with("C_CHAN", "1111.0001", job, "Boom")

    # 5. job_reviewing → post_review_gate
    @pytest.mark.asyncio
    async def test_job_reviewing_calls_post_review_gate(self, bot):
        job = self._slack_job()
        node = _make_node(status="completed", sequence=2, title="Step 2")
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            inst = MockQ.return_value
            inst.get_job.return_value = job
            inst.get_nodes.return_value = [
                _make_node(status="completed", sequence=1),
                node,
            ]
            with patch.object(bot, "post_review_gate", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_reviewing", {"job_id": job.id})
        mock_notify.assert_awaited_once_with("C_CHAN", "1111.0001", job, node)

    # 6. job_progress → posts progress message via chat_postMessage
    @pytest.mark.asyncio
    async def test_job_progress_posts_message(self, bot):
        job = self._slack_job()
        bot._app.client.chat_postMessage = AsyncMock(return_value={"ts": "x"})
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            await bot.handle_worker_event("job_progress", {
                "job_id": job.id,
                "progress": {"completed": 3, "total": 5, "current_node": "Step 3"},
            })
        bot._app.client.chat_postMessage.assert_awaited_once()
        call_kwargs = bot._app.client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C_CHAN"
        assert call_kwargs["thread_ts"] == "1111.0001"
        assert "Step 3" in call_kwargs["text"]
        assert "3/5" in call_kwargs["text"]

    # 7. Non-Slack job → returns immediately, no notifications
    @pytest.mark.asyncio
    async def test_non_slack_job_skipped(self, bot):
        job = SimpleNamespace(
            id="job-other", source="web", source_ref="C_CHAN:1111.0001",
        )
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            with patch.object(bot, "post_job_started", new_callable=AsyncMock) as mock_started:
                await bot.handle_worker_event("job_planning", {"job_id": job.id})
        mock_started.assert_not_awaited()

    # 8. Missing job_id → returns gracefully
    @pytest.mark.asyncio
    async def test_missing_job_id_returns_gracefully(self, bot):
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            await bot.handle_worker_event("job_planning", {})
        MockQ.return_value.get_job.assert_not_called()

    # 9. Job not found → returns gracefully
    @pytest.mark.asyncio
    async def test_job_not_found_returns_gracefully(self, bot):
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = None
            with patch.object(bot, "post_job_started", new_callable=AsyncMock) as mock_started:
                await bot.handle_worker_event("job_planning", {"job_id": "nonexistent"})
        mock_started.assert_not_awaited()

    # 10. Unknown event type → silently ignored
    @pytest.mark.asyncio
    async def test_unknown_event_type_ignored(self, bot):
        job = self._slack_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            # Should not raise
            await bot.handle_worker_event("some_unknown_event", {"job_id": job.id})

    # 11. source_ref format parsing — correctly splits channel:thread_ts
    @pytest.mark.asyncio
    async def test_source_ref_parsing(self, bot):
        job = self._slack_job(source_ref="C_GENERAL:9999.0042")
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            with patch.object(bot, "post_job_started", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_planning", {"job_id": job.id})
        mock_notify.assert_awaited_once_with("C_GENERAL", "9999.0042", job)

    @pytest.mark.asyncio
    async def test_source_ref_without_colon_skips(self, bot):
        """Legacy source_ref with no colon should skip notification."""
        job = self._slack_job(source_ref="C_CHAN_ONLY")
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            with patch.object(bot, "post_job_started", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_planning", {"job_id": job.id})
        mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_source_ref_empty_channel_skips(self, bot):
        """source_ref with empty channel (e.g. ':1111.0001') should skip."""
        job = self._slack_job(source_ref=":1111.0001")
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            with patch.object(bot, "post_job_started", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_planning", {"job_id": job.id})
        mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_source_ref_empty_thread_ts_skips(self, bot):
        """source_ref with empty thread_ts (e.g. 'C_CHAN:') should skip."""
        job = self._slack_job(source_ref="C_CHAN:")
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            with patch.object(bot, "post_job_started", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_planning", {"job_id": job.id})
        mock_notify.assert_not_awaited()

    # 12. Slack API error during notification → caught, no crash
    @pytest.mark.asyncio
    async def test_slack_api_error_during_progress_no_crash(self, bot):
        job = self._slack_job()
        bot._app.client.chat_postMessage = AsyncMock(side_effect=Exception("Slack API down"))
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            # Should not raise even though chat_postMessage throws
            await bot.handle_worker_event("job_progress", {
                "job_id": job.id,
                "progress": {"completed": 1, "total": 2, "current_node": "Step 1"},
            })

    @pytest.mark.asyncio
    async def test_job_failed_default_error_message(self, bot):
        """job_failed with no error key in data should use 'Unknown error'."""
        job = self._slack_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            with patch.object(bot, "post_job_failed", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_failed", {"job_id": job.id})
        mock_notify.assert_awaited_once_with("C_CHAN", "1111.0001", job, "Unknown error")

    @pytest.mark.asyncio
    async def test_node_completed_without_node_id_skips(self, bot):
        """node_completed without node_id should not call post_node_complete."""
        job = self._slack_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            with patch.object(bot, "post_node_complete", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("node_completed", {"job_id": job.id})
        mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_node_completed_node_not_found_skips(self, bot):
        """node_completed with unknown node_id should not call post_node_complete."""
        job = self._slack_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            MockQ.return_value.get_job.return_value = job
            MockQ.return_value.get_node.return_value = None
            with patch.object(bot, "post_node_complete", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("node_completed", {"job_id": job.id, "node_id": "gone"})
        mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_reviewing_no_completed_nodes_skips(self, bot):
        """job_reviewing with no completed/running nodes should not call post_review_gate."""
        job = self._slack_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            inst = MockQ.return_value
            inst.get_job.return_value = job
            inst.get_nodes.return_value = [
                _make_node(status="pending", sequence=1),
            ]
            with patch.object(bot, "post_review_gate", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_reviewing", {"job_id": job.id})
        mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_done_no_output_files(self, bot):
        """job_done with no output files passes output_files=None."""
        job = self._slack_job()
        with patch("backend.jobs.queue.JobQueue") as MockQ:
            inst = MockQ.return_value
            inst.get_job.side_effect = [job, job]
            inst.get_files.return_value = []
            with patch.object(bot, "post_job_complete", new_callable=AsyncMock) as mock_notify:
                await bot.handle_worker_event("job_done", {"job_id": job.id})
        mock_notify.assert_awaited_once_with(
            "C_CHAN", "1111.0001", job, output_files=None,
        )


# =========================================================================
# TestActivityMultiplex
# =========================================================================


class TestActivityMultiplex:
    """Tests for the _activity_multiplex function in server.py that
    fans out worker events to SSE subscribers and the Slack bot."""

    @staticmethod
    async def _run_multiplex(emit_fn, get_bot_fn, event_type, data):
        """Replicate the _activity_multiplex logic from server.py."""
        await emit_fn(event_type, data)
        bot = get_bot_fn()
        if bot:
            try:
                await bot.handle_worker_event(event_type, data)
            except Exception:
                pass  # non-fatal, matches server.py behavior

    @pytest.mark.asyncio
    async def test_calls_both_emit_activity_and_slack_bot(self):
        """Both emit_activity and bot.handle_worker_event are called."""
        mock_emit = AsyncMock()
        mock_bot = MagicMock()
        mock_bot.handle_worker_event = AsyncMock()

        await self._run_multiplex(
            mock_emit, lambda: mock_bot, "job_done", {"job_id": "j1"},
        )

        mock_emit.assert_awaited_once_with("job_done", {"job_id": "j1"})
        mock_bot.handle_worker_event.assert_awaited_once_with("job_done", {"job_id": "j1"})

    @pytest.mark.asyncio
    async def test_slack_failure_does_not_prevent_sse(self):
        """If Slack bot raises, SSE emission still succeeds."""
        mock_emit = AsyncMock()
        mock_bot = MagicMock()
        mock_bot.handle_worker_event = AsyncMock(side_effect=Exception("Slack boom"))

        # Should not raise
        await self._run_multiplex(
            mock_emit, lambda: mock_bot, "job_failed", {"job_id": "j2", "error": "x"},
        )

        mock_emit.assert_awaited_once_with("job_failed", {"job_id": "j2", "error": "x"})

    @pytest.mark.asyncio
    async def test_works_when_no_slack_bot(self):
        """When get_slack_bot() returns None, only SSE is emitted."""
        mock_emit = AsyncMock()

        await self._run_multiplex(
            mock_emit, lambda: None, "job_planning", {"job_id": "j3"},
        )

        mock_emit.assert_awaited_once_with("job_planning", {"job_id": "j3"})
