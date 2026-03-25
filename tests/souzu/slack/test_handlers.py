"""Tests for souzu.slack.handlers."""

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from souzu.job_tracking import JobRegistry, JobState, PrinterState, PrintJob
from souzu.slack.client import SlackClient
from souzu.slack.handlers import can_control_job, register_job_handlers


def _make_mock_app_and_slack() -> tuple[MagicMock, MagicMock, dict[str, Any]]:
    """Create a mock Slack client with action handler capture."""
    mock_app = MagicMock()
    handlers: dict[str, Any] = {}

    def capture_action(action_id: str) -> Any:  # noqa: ANN401
        def decorator(func: Any) -> Any:  # noqa: ANN401
            handlers[action_id] = func
            return func

        return decorator

    mock_app.action = capture_action
    mock_slack = MagicMock(spec=SlackClient)
    mock_slack.app = mock_app
    return mock_app, mock_slack, handlers


def _make_body(
    thread_ts: str,
    user_id: str = "U123",
    user_name: str = "testuser",
    channel_id: str = "C456",
) -> dict[str, Any]:
    return {
        "user": {"id": user_id, "name": user_name},
        "message": {"ts": thread_ts},
        "channel": {"id": channel_id},
    }


@pytest.fixture
def job_registry_with_job() -> tuple[JobRegistry, str]:
    thread_ts = "1234567890.123456"
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING)
    state = PrinterState(current_job=job)
    registry: JobRegistry = {thread_ts: state}
    return registry, thread_ts


class TestRegisterJobHandlers:
    def test_registers_claim_action(self) -> None:
        mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, {})
        assert "claim_print" in handlers

    def test_skips_when_no_app(self) -> None:
        mock_slack = MagicMock(spec=SlackClient)
        mock_slack.app = None
        # Should not raise
        register_job_handlers(mock_slack, {})


class TestClaimHandler:
    @pytest.mark.asyncio
    async def test_first_claimant_wins(
        self, job_registry_with_job: tuple[JobRegistry, str]
    ) -> None:
        registry, thread_ts = job_registry_with_job
        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_body(thread_ts, user_id="U999", user_name="claimer")

        await handlers["claim_print"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        job = registry[thread_ts].current_job
        assert job is not None
        assert job.owner == "U999"
        mock_client.chat_postEphemeral.assert_not_awaited()
        mock_client.chat_update.assert_awaited_once()
        update_kwargs = mock_client.chat_update.call_args.kwargs
        assert update_kwargs["channel"] == "C456"
        assert update_kwargs["ts"] == thread_ts
        assert any(
            "Claimed by <@U999>" in str(block) for block in update_kwargs["blocks"]
        )
        # Verify in-thread @mention for notification
        mock_client.chat_postMessage.assert_awaited_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert post_kwargs["channel"] == "C456"
        assert post_kwargs["thread_ts"] == thread_ts
        assert "<@U999>" in post_kwargs["text"]

    @pytest.mark.asyncio
    async def test_rejects_second_claimant(
        self, job_registry_with_job: tuple[JobRegistry, str]
    ) -> None:
        registry, thread_ts = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U111"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_body(thread_ts, user_id="U222")

        await handlers["claim_print"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        job = registry[thread_ts].current_job
        assert job is not None
        assert job.owner == "U111"
        mock_client.chat_postEphemeral.assert_awaited_once()
        call_kwargs = mock_client.chat_postEphemeral.call_args.kwargs
        assert call_kwargs["user"] == "U222"
        assert "<@U111>" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_unknown_thread_ts(self) -> None:
        registry: JobRegistry = {}
        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_body("9999999999.999999")

        await handlers["claim_print"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_current_job(self) -> None:
        thread_ts = "1234567890.123456"
        state = PrinterState(current_job=None)
        registry: JobRegistry = {thread_ts: state}

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_body(thread_ts)

        await handlers["claim_print"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_not_awaited()


class TestCanControlJob:
    def test_owner_matches(self) -> None:
        job = PrintJob(duration=timedelta(hours=1), owner="U_ALICE")
        assert can_control_job("U_ALICE", job) is True

    def test_owner_mismatch(self) -> None:
        job = PrintJob(duration=timedelta(hours=1), owner="U_ALICE")
        assert can_control_job("U_BOB", job) is False

    def test_unclaimed(self) -> None:
        job = PrintJob(duration=timedelta(hours=1))
        assert can_control_job("U_ALICE", job) is False


def _make_action_body(
    thread_ts: str,
    message_ts: str = "9999.0001",
    user_id: str = "U123",
    user_name: str = "testuser",
    channel_id: str = "C456",
) -> dict[str, Any]:
    """Build a body dict as Slack sends for an action on a thread reply."""
    return {
        "user": {"id": user_id, "name": user_name},
        "message": {"ts": message_ts, "thread_ts": thread_ts},
        "channel": {"id": channel_id},
    }


class TestActionHandlers:
    @pytest.mark.asyncio
    async def test_authorized_stub_response(
        self,
        job_registry_with_job: tuple[JobRegistry, str],
    ) -> None:
        """Authorized owner gets the 'not implemented' stub message."""
        registry, thread_ts = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_awaited_once()
        assert (
            "implemented yet" in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        )

    @pytest.mark.asyncio
    async def test_unauthorized_rejection(
        self,
        job_registry_with_job: tuple[JobRegistry, str],
    ) -> None:
        """Non-owner gets 'not your print' rejection."""
        registry, thread_ts = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_STRANGER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_awaited_once()
        assert (
            "isn't your print"
            in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        )

    @pytest.mark.asyncio
    async def test_wrong_state(
        self,
        job_registry_with_job: tuple[JobRegistry, str],
    ) -> None:
        """Pause on an already-paused job sends 'not available' ephemeral."""
        registry, thread_ts = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.state = JobState.PAUSED
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_awaited_once()
        assert (
            "isn't available right now"
            in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        )

    @pytest.mark.asyncio
    async def test_unknown_job(self) -> None:
        """Action on unknown thread returns silently."""
        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, {})

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body("unknown.ts")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_current_job(self) -> None:
        """Action when current_job is None returns silently."""
        thread_ts = "1234567890.123456"
        registry: JobRegistry = {thread_ts: PrinterState(current_job=None)}

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts)

        await handlers["print_cancel"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_handler_authorized_on_paused_job(
        self,
        job_registry_with_job: tuple[JobRegistry, str],
    ) -> None:
        """Resume on a paused job by owner gets the stub response."""
        registry, thread_ts = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.state = JobState.PAUSED
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_resume"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_awaited_once()
        assert (
            "implemented yet" in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        )

    @pytest.mark.asyncio
    async def test_all_four_action_handlers_registered(self) -> None:
        """Verify all four action handlers are registered."""
        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, {})

        assert "print_pause" in handlers
        assert "print_resume" in handlers
        assert "print_cancel" in handlers
        assert "print_photo" in handlers
