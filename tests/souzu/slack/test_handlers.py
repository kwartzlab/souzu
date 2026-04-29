"""Tests for souzu.slack.handlers."""

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from souzu.bambu.mqtt import BambuMqttConnection
from souzu.job_tracking import JobRegistry, JobState, PrinterState, PrintJob
from souzu.slack.client import SlackClient
from souzu.slack.handlers import (
    can_control_job,
    register_admin_check_handler,
    register_job_handlers,
)


def _make_mock_app_and_slack() -> tuple[MagicMock, MagicMock, dict[str, Any]]:
    """Create a mock Slack client with action handler capture.

    Defaults ``is_user_in_group`` to ``False`` so admin gating doesn't
    accidentally let unauthorized users through via the auto-AsyncMock truthy
    default.
    """
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
    mock_slack.is_user_in_group = AsyncMock(return_value=False)
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
def job_registry_with_job() -> tuple[JobRegistry, str, AsyncMock]:
    thread_ts = "1234567890.123456"
    job = PrintJob(
        duration=timedelta(hours=1),
        state=JobState.RUNNING,
        slack_channel="C456",
        slack_thread_ts=thread_ts,
    )
    mock_conn = AsyncMock(spec=BambuMqttConnection)
    state = PrinterState(current_job=job, connection=mock_conn)
    registry: JobRegistry = {thread_ts: state}
    return registry, thread_ts, mock_conn


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
        self, job_registry_with_job: tuple[JobRegistry, str, AsyncMock]
    ) -> None:
        registry, thread_ts, _mock_conn = job_registry_with_job
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
        # Parent message updated with claim info
        parent_update = mock_client.chat_update.call_args_list[0]
        assert parent_update.kwargs["channel"] == "C456"
        assert parent_update.kwargs["ts"] == thread_ts
        assert any(
            "Claimed by <@U999>" in str(block)
            for block in parent_update.kwargs["blocks"]
        )

        # Actions message posted and in-thread notification
        post_calls = mock_client.chat_postMessage.call_args_list
        assert len(post_calls) == 2
        # First call: actions message with control buttons
        actions_kwargs = post_calls[0].kwargs
        assert actions_kwargs["channel"] == "C456"
        assert actions_kwargs["thread_ts"] == thread_ts
        assert actions_kwargs["blocks"][0]["type"] == "actions"
        # Verify actions_ts was stored on the job
        assert job.actions_ts is not None
        # Second call: in-thread @mention for notification
        notify_kwargs = post_calls[1].kwargs
        assert notify_kwargs["channel"] == "C456"
        assert notify_kwargs["thread_ts"] == thread_ts
        assert "<@U999>" in notify_kwargs["text"]

    @pytest.mark.asyncio
    async def test_rejects_second_claimant(
        self, job_registry_with_job: tuple[JobRegistry, str, AsyncMock]
    ) -> None:
        registry, thread_ts, _mock_conn = job_registry_with_job
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
    @pytest.mark.asyncio
    async def test_owner_matches(self) -> None:
        job = PrintJob(duration=timedelta(hours=1), owner="U_ALICE")
        mock_slack = MagicMock(spec=SlackClient)
        mock_slack.is_user_in_group = AsyncMock(return_value=False)
        assert await can_control_job("U_ALICE", job, mock_slack, "admins") is True
        # Owner short-circuit: admin lookup not consulted.
        mock_slack.is_user_in_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_owner_mismatch_non_admin(self) -> None:
        job = PrintJob(duration=timedelta(hours=1), owner="U_ALICE")
        mock_slack = MagicMock(spec=SlackClient)
        mock_slack.is_user_in_group = AsyncMock(return_value=False)
        assert await can_control_job("U_BOB", job, mock_slack, "admins") is False
        mock_slack.is_user_in_group.assert_awaited_once_with("U_BOB", "admins")

    @pytest.mark.asyncio
    async def test_owner_mismatch_admin(self) -> None:
        job = PrintJob(duration=timedelta(hours=1), owner="U_ALICE")
        mock_slack = MagicMock(spec=SlackClient)
        mock_slack.is_user_in_group = AsyncMock(return_value=True)
        assert await can_control_job("U_BOB", job, mock_slack, "admins") is True

    @pytest.mark.asyncio
    async def test_unclaimed_non_admin(self) -> None:
        job = PrintJob(duration=timedelta(hours=1))
        mock_slack = MagicMock(spec=SlackClient)
        mock_slack.is_user_in_group = AsyncMock(return_value=False)
        assert await can_control_job("U_ALICE", job, mock_slack, "admins") is False

    @pytest.mark.asyncio
    async def test_unclaimed_admin(self) -> None:
        job = PrintJob(duration=timedelta(hours=1))
        mock_slack = MagicMock(spec=SlackClient)
        mock_slack.is_user_in_group = AsyncMock(return_value=True)
        assert await can_control_job("U_ALICE", job, mock_slack, "admins") is True


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
    async def test_pause_sends_command_and_posts_audit(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Owner pauses running job: MQTT command sent, audit message posted."""
        registry, thread_ts, mock_conn = job_registry_with_job
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
        mock_conn.pause.assert_awaited_once()
        mock_client.chat_postMessage.assert_awaited_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert "Pause requested by <@U_OWNER>" in post_kwargs["text"]
        assert post_kwargs["thread_ts"] == thread_ts
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_sends_command(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Owner resumes paused job."""
        registry, thread_ts, mock_conn = job_registry_with_job
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

        mock_conn.resume.assert_awaited_once()
        mock_client.chat_postMessage.assert_awaited_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert "Resume" in post_kwargs["text"]

    @pytest.mark.asyncio
    async def test_cancel_sends_stop_command(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Owner cancels running job: stop() called, audit says Cancel."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_cancel"](ack=mock_ack, body=body, client=mock_client)

        mock_conn.stop.assert_awaited_once()
        mock_client.chat_postMessage.assert_awaited_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert "Cancel" in post_kwargs["text"]

    @pytest.mark.asyncio
    async def test_printer_offline_sends_ephemeral(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Connection is None: ephemeral offline message."""
        registry, thread_ts, _mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"
        state.connection = None

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postEphemeral.assert_awaited_once()
        assert "offline" in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        mock_client.chat_postMessage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mqtt_error_sends_ephemeral(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Command raises RuntimeError: ephemeral error message."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"
        mock_conn.pause.side_effect = RuntimeError("Not connected")

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postEphemeral.assert_awaited_once()
        assert (
            "Failed to send command"
            in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        )
        mock_client.chat_postMessage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_photo_captures_and_dms_clicker(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
        mocker: MockerFixture,
    ) -> None:
        """Photo action captures a frame and uploads it to the clicker's DM."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        mock_camera = AsyncMock()
        mock_camera.capture_frame.return_value = b"\xff\xd8fake_jpeg\xff\xd9"
        mocker.patch.object(PrinterState, "camera_client", return_value=mock_camera)

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        mock_client.conversations_open.return_value = {"channel": {"id": "D_OWNER"}}
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_photo"](ack=mock_ack, body=body, client=mock_client)

        mock_camera.capture_frame.assert_awaited_once()
        mock_client.conversations_open.assert_awaited_once_with(users="U_OWNER")
        mock_client.files_upload_v2.assert_awaited_once()
        upload_kwargs = mock_client.files_upload_v2.call_args.kwargs
        assert upload_kwargs["channel"] == "D_OWNER"
        assert "thread_ts" not in upload_kwargs
        assert upload_kwargs["filename"] == "snapshot.jpg"
        assert upload_kwargs["content"] == b"\xff\xd8fake_jpeg\xff\xd9"
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_photo_camera_unavailable(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
        mocker: MockerFixture,
    ) -> None:
        """Photo with no camera client sends ephemeral error."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        mocker.patch.object(PrinterState, "camera_client", return_value=None)

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_photo"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postEphemeral.assert_awaited_once()
        assert "unavailable" in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        mock_client.files_upload_v2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_photo_capture_error(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
        mocker: MockerFixture,
    ) -> None:
        """Photo capture failure sends ephemeral error."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        mock_camera = AsyncMock()
        mock_camera.capture_frame.side_effect = TimeoutError("timed out")
        mocker.patch.object(PrinterState, "camera_client", return_value=mock_camera)

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_photo"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postEphemeral.assert_awaited_once()
        assert "unavailable" in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        mock_client.files_upload_v2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unauthorized_rejection(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Non-owner gets 'not your print' rejection."""
        registry, thread_ts, _mock_conn = job_registry_with_job
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
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Pause on an already-paused job sends 'not available' ephemeral."""
        registry, thread_ts, _mock_conn = job_registry_with_job
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
    async def test_all_four_action_handlers_registered(self) -> None:
        """Verify all four action handlers are registered."""
        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, {})

        assert "print_pause" in handlers
        assert "print_resume" in handlers
        assert "print_cancel" in handlers
        assert "print_photo" in handlers

    @pytest.mark.asyncio
    async def test_admin_can_pause_other_users_print(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Admin (non-owner) can pause a print claimed by someone else."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        mock_slack.is_user_in_group = AsyncMock(return_value=True)
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_ADMIN")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_conn.pause.assert_awaited_once()
        mock_client.chat_postMessage.assert_awaited_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert "Pause requested by <@U_ADMIN>" in post_kwargs["text"]
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_can_cancel_other_users_print(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Admin (non-owner) can cancel a print claimed by someone else."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        mock_slack.is_user_in_group = AsyncMock(return_value=True)
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_ADMIN")

        await handlers["print_cancel"](ack=mock_ack, body=body, client=mock_client)

        mock_conn.stop.assert_awaited_once()
        mock_client.chat_postMessage.assert_awaited_once()
        assert "Cancel" in mock_client.chat_postMessage.call_args.kwargs["text"]
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_can_photo_other_users_print(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
        mocker: MockerFixture,
    ) -> None:
        """Admin (non-owner) can request a photo of a print claimed by someone else."""
        registry, thread_ts, _mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        mock_camera = AsyncMock()
        mock_camera.capture_frame.return_value = b"\xff\xd8jpeg\xff\xd9"
        mocker.patch.object(PrinterState, "camera_client", return_value=mock_camera)

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        mock_slack.is_user_in_group = AsyncMock(return_value=True)
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        mock_client.conversations_open.return_value = {"channel": {"id": "D_ADMIN"}}
        body = _make_action_body(thread_ts, user_id="U_ADMIN")

        await handlers["print_photo"](ack=mock_ack, body=body, client=mock_client)

        mock_camera.capture_frame.assert_awaited_once()
        mock_client.conversations_open.assert_awaited_once_with(users="U_ADMIN")
        mock_client.files_upload_v2.assert_awaited_once()
        upload_kwargs = mock_client.files_upload_v2.call_args.kwargs
        assert upload_kwargs["channel"] == "D_ADMIN"
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_can_control_unclaimed_print(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Admin can pause an unclaimed print (intervene on a runaway)."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        assert state.current_job.owner is None

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        mock_slack.is_user_in_group = AsyncMock(return_value=True)
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_ADMIN")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_conn.pause.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_admin_rejected_on_unclaimed_print(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Non-admin clicking on an unclaimed print's buttons is rejected."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        assert state.current_job.owner is None

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_RANDO")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_conn.pause.assert_not_awaited()
        mock_client.chat_postEphemeral.assert_awaited_once()


class TestRegisterAdminCheckHandler:
    def test_registers_check_admin_action(self) -> None:
        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_admin_check_handler(mock_slack)
        assert "check_admin" in handlers

    def test_skips_when_no_app(self) -> None:
        mock_slack = MagicMock(spec=SlackClient)
        mock_slack.app = None
        # Should not raise
        register_admin_check_handler(mock_slack)


class TestCheckAdminHandler:
    @pytest.mark.asyncio
    async def test_member_replies_admin(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "souzu.slack.handlers.CONFIG"
        ).slack.admin_user_group = "3dprinterteam"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        mock_slack.is_user_in_group = AsyncMock(return_value=True)
        register_admin_check_handler(mock_slack)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {
            "user": {"id": "U_ADMIN", "name": "admin"},
            "channel": {"id": "C_ERR"},
            "message": {"ts": "1111.2222"},
        }

        await handlers["check_admin"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_slack.is_user_in_group.assert_awaited_once_with("U_ADMIN", "3dprinterteam")
        mock_client.chat_postMessage.assert_awaited_once()
        kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert kwargs["channel"] == "C_ERR"
        assert kwargs["thread_ts"] == "1111.2222"
        assert kwargs["text"] == "<@U_ADMIN> is an admin"

    @pytest.mark.asyncio
    async def test_non_member_replies_not_admin(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "souzu.slack.handlers.CONFIG"
        ).slack.admin_user_group = "3dprinterteam"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        mock_slack.is_user_in_group = AsyncMock(return_value=False)
        register_admin_check_handler(mock_slack)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {
            "user": {"id": "U_NORMIE", "name": "normie"},
            "channel": {"id": "C_ERR"},
            "message": {"ts": "1111.2222"},
        }

        await handlers["check_admin"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postMessage.assert_awaited_once()
        kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert kwargs["text"] == "<@U_NORMIE> is not an admin"

    @pytest.mark.asyncio
    async def test_uses_configured_group(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "souzu.slack.handlers.CONFIG"
        ).slack.admin_user_group = "custom-group"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        mock_slack.is_user_in_group = AsyncMock(return_value=False)
        register_admin_check_handler(mock_slack)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = {
            "user": {"id": "U_X"},
            "channel": {"id": "C_ERR"},
            "message": {"ts": "1111.2222"},
        }

        await handlers["check_admin"](ack=mock_ack, body=body, client=mock_client)

        mock_slack.is_user_in_group.assert_awaited_once_with("U_X", "custom-group")
