"""Tests for the job_tracking module."""

from collections.abc import AsyncIterator
from concurrent.futures import CancelledError
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz
from pytest_mock import MockerFixture

from souzu.bambu.camera import P1CameraClient
from souzu.bambu.discovery import BambuDevice
from souzu.bambu.mqtt import BambuMqttConnection, BambuStatusReport
from souzu.job_tracking import (
    JobAction,
    JobState,
    PreviousJobInfo,
    PrinterState,
    PrintJob,
    _format_date_time,
    _format_duration,
    _format_eta,
    _format_time,
    _job_started,
    _round_up,
    _update_thread,
    available_actions,
    build_actions_blocks,
    build_terminal_actions_blocks,
    monitor_printer_status,
)
from souzu.slack.client import SlackApiError, SlackClient


def test_job_state_enum() -> None:
    """Test that JobState is an Enum with expected values."""
    assert JobState.RUNNING.value == "running"
    assert JobState.PAUSED.value == "paused"
    assert JobState("running") == JobState.RUNNING
    assert JobState("paused") == JobState.PAUSED


def test_print_job() -> None:
    """Test PrintJob class initialization and attributes."""
    job = PrintJob(duration=timedelta(minutes=30))
    assert job.duration == timedelta(minutes=30)
    assert job.eta is None
    assert job.state == JobState.RUNNING
    assert job.slack_channel is None
    assert job.slack_thread_ts is None
    assert job.start_message is None
    assert job.owner is None
    now = datetime.now()
    job = PrintJob(
        duration=timedelta(hours=2),
        eta=now,
        state=JobState.PAUSED,
        slack_channel="general",
        slack_thread_ts="1234567890.123456",
        start_message="Print started",
    )
    assert job.duration == timedelta(hours=2)
    assert job.eta == now
    assert job.state == JobState.PAUSED
    assert job.slack_channel == "general"
    assert job.slack_thread_ts == "1234567890.123456"
    assert job.start_message == "Print started"


def test_printer_state() -> None:
    """Test PrinterState class initialization and attributes."""
    state = PrinterState()
    assert state.current_job is None

    job = PrintJob(duration=timedelta(minutes=45))
    state = PrinterState(current_job=job)
    assert state.current_job is job
    assert state.current_job is not None
    assert state.current_job.duration == timedelta(minutes=45)


def test_round_up() -> None:
    """Test the _round_up function."""
    dt = datetime(2023, 1, 1, 12, 30, 45)
    rounded = _round_up(dt, timedelta(minutes=1))
    assert rounded == datetime(2023, 1, 1, 12, 31, 0)

    dt = datetime(2023, 1, 1, 12, 32, 0)
    rounded = _round_up(dt, timedelta(minutes=5))
    assert rounded == datetime(2023, 1, 1, 12, 35, 0)

    dt = datetime(2023, 1, 1, 12, 45, 0)
    rounded = _round_up(dt, timedelta(hours=1))
    assert rounded == datetime(2023, 1, 1, 13, 0, 0)

    dt = datetime(2023, 1, 1, 12, 0, 0)
    rounded = _round_up(dt, timedelta(minutes=5))
    assert rounded == datetime(2023, 1, 1, 12, 0, 0)


def test_format_duration() -> None:
    """Test the _format_duration function."""
    duration = timedelta(seconds=30)
    assert _format_duration(duration) == "1 minute"

    duration = timedelta(minutes=12)
    assert _format_duration(duration) == "15 minutes"

    duration = timedelta(minutes=53)
    assert _format_duration(duration) == "55 minutes"

    duration = timedelta(minutes=58)
    assert _format_duration(duration) == "1 hour"

    duration = timedelta(hours=2, minutes=5)
    assert _format_duration(duration) == "2.5 hours"

    duration = timedelta(hours=1, minutes=15)
    assert _format_duration(duration) == "1.5 hours"

    duration = timedelta(hours=10)
    assert _format_duration(duration) == "10 hours"


def test_format_time() -> None:
    """Test the _format_time function."""
    dt = datetime(2023, 1, 1, 9, 30, 0)
    assert _format_time(dt) == "9:30 AM"

    dt = datetime(2023, 1, 1, 14, 45, 0)
    assert _format_time(dt) == "2:45 PM"

    dt = datetime(2023, 1, 1, 0, 0, 0)
    assert _format_time(dt) == "12:00 AM"

    dt = datetime(2023, 1, 1, 12, 0, 0)
    assert _format_time(dt) == "12:00 PM"


def test_format_date_time() -> None:
    """Test the _format_date_time function."""
    dt = datetime(2023, 1, 2, 9, 30, 0)  # Monday
    assert _format_date_time(dt) == "9:30 AM on Monday"

    dt = datetime(2023, 1, 3, 14, 45, 0)  # Tuesday
    assert _format_date_time(dt) == "2:45 PM on Tuesday"


def test_format_eta_basics(mocker: MockerFixture) -> None:
    """Basic test for _format_eta that at least runs the function."""
    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_datetime = mocker.patch("souzu.job_tracking.datetime")

    test_tz = pytz.timezone("America/New_York")
    mock_config.timezone = test_tz

    now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=test_tz)
    eta = datetime(2023, 1, 1, 13, 0, 0, tzinfo=test_tz)
    mock_datetime.now.return_value = now

    result = _format_eta(eta)
    assert isinstance(result, str)


def test_format_eta_different_day(mocker: MockerFixture) -> None:
    """Test the _format_eta function for times on a different day."""
    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_datetime = mocker.patch("souzu.job_tracking.datetime")

    test_tz = pytz.timezone("America/New_York")
    mock_config.timezone = test_tz
    today = datetime(2023, 1, 1, 22, 0, 0, tzinfo=test_tz)

    mock_tomorrow = MagicMock()
    mock_tomorrow.__sub__ = MagicMock(return_value=timedelta(hours=10))
    mock_date = MagicMock()
    mock_tomorrow.date = MagicMock(return_value=mock_date)

    mock_datetime.now.return_value = today

    mock_format_time = mocker.patch("souzu.job_tracking._format_time")
    mock_format_date_time = mocker.patch("souzu.job_tracking._format_date_time")
    mocker.patch("souzu.job_tracking._round_up", return_value=mock_tomorrow)

    mock_format_date_time.return_value = "8:00 AM on Monday"

    result = _format_eta(mock_tomorrow)

    assert mock_format_date_time.called
    assert not mock_format_time.called
    assert result == "8:00 AM on Monday"


@pytest.mark.asyncio
async def test_update_thread_no_thread() -> None:
    """Test the _update_thread function when there's no thread yet."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts=None,
    )

    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(mock_slack, job, device, "Edited message", "Update message")

    mock_slack.post_to_channel.assert_called_once_with(
        "test-channel",
        "Update message",
    )


@pytest.mark.asyncio
async def test_update_thread_with_thread() -> None:
    """Test the _update_thread function when there's an existing thread."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
    )

    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(mock_slack, job, device, "Edited message", "Update message")

    mock_slack.edit_message.assert_called_once()
    edit_args, edit_kwargs = mock_slack.edit_message.call_args
    assert edit_args == ("test-channel", "1234.5678", "Edited message")
    # No owner → blocks should have section + claim button
    assert edit_kwargs["blocks"][0]["type"] == "section"
    assert edit_kwargs["blocks"][1]["type"] == "actions"
    assert edit_kwargs["blocks"][1]["elements"][0]["action_id"] == "claim_print"
    assert len(edit_kwargs["blocks"]) == 2

    mock_slack.post_to_thread.assert_called_once_with(
        "test-channel",
        "1234.5678",
        "Update message",
    )


@pytest.mark.asyncio
async def test_update_thread_preserves_owner_in_blocks() -> None:
    """Test that _update_thread includes claim context when job has an owner."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        owner="U_ALICE",
    )

    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(mock_slack, job, device, "Edited message", "Update message")

    edit_kwargs = mock_slack.edit_message.call_args.kwargs
    assert len(edit_kwargs["blocks"]) == 2
    assert edit_kwargs["blocks"][1]["type"] == "context"
    assert "Claimed by <@U_ALICE>" in str(edit_kwargs["blocks"][1])


@pytest.mark.asyncio
async def test_update_thread_terminal_unclaimed_shows_nobody_claimed() -> None:
    """Test that terminal updates on unclaimed jobs show 'Nobody claimed' context."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
    )

    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(
        mock_slack,
        job,
        device,
        "Edited message",
        "Update message",
        terminal_reason="print completed",
    )

    edit_kwargs = mock_slack.edit_message.call_args.kwargs
    assert len(edit_kwargs["blocks"]) == 2
    assert edit_kwargs["blocks"][1]["type"] == "context"
    assert "Nobody claimed this print" in str(edit_kwargs["blocks"][1])
    assert ":cry:" in str(edit_kwargs["blocks"][1])


@pytest.mark.asyncio
async def test_update_thread_slack_error_edit(mocker: MockerFixture) -> None:
    """Test handling Slack API errors in _update_thread during edit_message."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
    )

    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.edit_message.side_effect = SlackApiError("API error")

    mock_logging = mocker.patch("souzu.job_tracking.logging")

    await _update_thread(mock_slack, job, device, "Edited message", "Update message")

    mock_logging.error.assert_called_once()

    mock_slack.post_to_thread.assert_called_once_with(
        "test-channel",
        "1234.5678",
        "Update message",
    )


@pytest.mark.asyncio
async def test_update_thread_slack_error_thread(mocker: MockerFixture) -> None:
    """Test handling Slack API errors in _update_thread during post_to_thread."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
    )

    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_thread.side_effect = SlackApiError("API error")
    mock_slack.post_to_channel.return_value = "5678.1234"

    mock_logging = mocker.patch("souzu.job_tracking.logging")

    await _update_thread(mock_slack, job, device, "Edited message", "Update message")

    mock_slack.edit_message.assert_called_once()
    mock_logging.error.assert_any_call("Failed to notify thread: API error")


@pytest.mark.asyncio
async def test_update_thread_no_channel(mocker: MockerFixture) -> None:
    """Test the _update_thread function when using the default notification channel."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel=None,
        slack_thread_ts=None,
    )

    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "default-channel"

    await _update_thread(mock_slack, job, device, "Edited message", "Update message")

    mock_slack.post_to_channel.assert_called_once_with(
        "default-channel",
        "Update message",
    )


@pytest.mark.asyncio
async def test_update_thread_posts_actions_message_when_no_actions_ts() -> None:
    """When actions_ts is None and actions are non-empty, post a new actions message."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_thread.side_effect = ["status_ts", "9999.0001"]

    await _update_thread(
        mock_slack,
        job,
        device,
        "Edited",
        "Update",
        actions=[JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO],
    )

    # Should have posted an actions message in-thread
    post_calls = mock_slack.post_to_thread.call_args_list
    assert len(post_calls) == 2  # status update + actions message
    actions_call = post_calls[1]
    assert "blocks" in actions_call.kwargs or (len(actions_call.args) > 3)
    assert job.actions_ts == "9999.0001"


@pytest.mark.asyncio
async def test_update_thread_edits_actions_message_when_actions_ts_exists() -> None:
    """When actions_ts exists, edit the actions message."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        actions_ts="8888.0001",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(
        mock_slack,
        job,
        device,
        "Edited",
        "Update",
        actions=[JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO],
    )

    # Should have edited the actions message
    edit_calls = mock_slack.edit_message.call_args_list
    assert len(edit_calls) == 2  # parent edit + actions edit
    actions_edit = edit_calls[1]
    assert actions_edit.args[1] == "8888.0001"


@pytest.mark.asyncio
async def test_update_thread_clears_actions_on_terminal() -> None:
    """When actions is empty and terminal_reason is set, edit to terminal block."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        actions_ts="8888.0001",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(
        mock_slack,
        job,
        device,
        "Edited",
        "Update",
        actions=[],
        terminal_reason="print completed",
    )

    # Should have edited the actions message to terminal
    edit_calls = mock_slack.edit_message.call_args_list
    actions_edit = edit_calls[1]
    assert "No actions available" in str(
        actions_edit.kwargs.get("blocks", actions_edit.args)
    )


@pytest.mark.asyncio
async def test_update_thread_skips_actions_when_empty_and_no_ts() -> None:
    """When actions is empty and no actions_ts exists, do nothing for actions."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(
        mock_slack,
        job,
        device,
        "Edited",
        "Update",
        actions=[],
        terminal_reason="print completed",
    )

    # Only one edit call (parent message), no actions edit
    assert mock_slack.edit_message.call_count == 1


@pytest.mark.asyncio
async def test_update_thread_recovers_when_actions_edit_fails() -> None:
    """When editing the actions message fails, post a new one and update actions_ts."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        actions_ts="stale.0001",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)
    # First edit_message (parent) succeeds; second (actions) fails
    mock_slack.edit_message.side_effect = [None, SlackApiError("stale")]
    mock_slack.post_to_thread.side_effect = ["status_ts", "new_actions.0002"]

    await _update_thread(
        mock_slack,
        job,
        device,
        "Edited",
        "Update",
        actions=[JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO],
    )

    # Should have fallen back to posting a new actions message
    post_calls = mock_slack.post_to_thread.call_args_list
    assert len(post_calls) == 2  # status update + fallback actions post
    assert job.actions_ts == "new_actions.0002"


@pytest.mark.asyncio
async def test_monitor_printer_status(mocker: MockerFixture) -> None:
    """Test monitor_printer_status with a complete print job lifecycle."""
    device = BambuDevice(
        device_id="TEST123",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="test_printer",
    )

    mock_connection = MagicMock()
    mock_slack = AsyncMock(spec=SlackClient)

    report_start = BambuStatusReport(
        gcode_state="RUNNING",
        mc_remaining_time=120,
        mc_percent=0,
    )
    report_paused = BambuStatusReport(
        gcode_state="PAUSE",
        mc_remaining_time=100,
        mc_percent=20,
    )
    report_resumed = BambuStatusReport(
        gcode_state="RUNNING",
        mc_remaining_time=90,
        mc_percent=25,
    )
    report_finished = BambuStatusReport(
        gcode_state="FINISH",
        mc_remaining_time=0,
        mc_percent=100,
    )

    mock_subscription = AsyncMock()

    async def mock_aiter(*args: object) -> AsyncIterator[BambuStatusReport]:
        yield report_start
        yield report_paused
        yield report_resumed
        yield report_finished

    mock_subscription.__aiter__ = mock_aiter
    mock_subscription.__anext__ = mock_aiter().__anext__

    mock_connection.subscribe.return_value.__aenter__.return_value = mock_subscription

    mock_state_dir = AsyncMock()
    mock_state_dir.mkdir = AsyncMock()
    mock_state_file = AsyncMock()
    mock_state_dir.__truediv__ = MagicMock(return_value=mock_state_file)
    mock_state_file.exists = AsyncMock(return_value=False)
    mock_state_file.open = AsyncMock()
    mock_open_cm = AsyncMock()
    mock_state_file.open.return_value.__aenter__ = AsyncMock(return_value=mock_open_cm)
    mock_state_file.open.return_value.__aexit__ = AsyncMock()

    mock_job_started = AsyncMock()
    mock_job_paused = AsyncMock()
    mock_job_resumed = AsyncMock()
    mock_job_completed = AsyncMock()

    mocker.patch("souzu.job_tracking.json")
    mock_serializer = mocker.patch("souzu.job_tracking._STATE_SERIALIZER")
    mock_serializer.unstructure.return_value = {}

    mocker.patch("souzu.job_tracking._STATE_DIR", mock_state_dir)
    mocker.patch("souzu.job_tracking._job_started", mock_job_started)
    mocker.patch("souzu.job_tracking._job_paused", mock_job_paused)
    mocker.patch("souzu.job_tracking._job_resumed", mock_job_resumed)
    mocker.patch("souzu.job_tracking._job_completed", mock_job_completed)

    await monitor_printer_status(device, mock_connection, mock_slack, {})

    mock_state_dir.mkdir.assert_called_once_with(exist_ok=True, parents=True)
    mock_state_file.exists.assert_called_once()

    assert (
        mock_job_started.call_count
        + mock_job_paused.call_count
        + mock_job_resumed.call_count
        + mock_job_completed.call_count
        > 0
    )

    mock_state_file.open.assert_called()
    mock_open_cm.write.assert_called()


@pytest.mark.asyncio
async def test_monitor_printer_status_load_existing_state(
    mocker: MockerFixture,
) -> None:
    """Test monitor_printer_status loading existing state from file."""
    device = BambuDevice(
        device_id="TEST123",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="test_printer",
    )

    mock_connection = MagicMock()
    mock_slack = AsyncMock(spec=SlackClient)
    mock_subscription = AsyncMock()

    report_finish = BambuStatusReport(
        gcode_state="FINISH", mc_remaining_time=0, mc_percent=100
    )

    async def mock_aiter(*args: object) -> AsyncIterator[BambuStatusReport]:
        yield report_finish

    mock_subscription.__aiter__ = mock_aiter
    mock_subscription.__anext__ = mock_aiter().__anext__
    mock_connection.subscribe.return_value.__aenter__.return_value = mock_subscription

    mock_state_dir = AsyncMock()
    mock_state_dir.mkdir = AsyncMock()
    mock_state_file = AsyncMock()
    mock_state_dir.__truediv__ = MagicMock(return_value=mock_state_file)
    mock_state_file.exists = AsyncMock(return_value=True)

    mock_read_cm = AsyncMock()
    mock_state_file.open.return_value.__aenter__ = AsyncMock(return_value=mock_read_cm)
    mock_state_file.open.return_value.__aexit__ = AsyncMock()

    existing_job = PrintJob(
        duration=timedelta(hours=2),
        state=JobState.RUNNING,
    )
    existing_state = PrinterState(current_job=existing_job)

    mock_json = mocker.patch("souzu.job_tracking.json")
    mock_json.loads.return_value = {}

    mock_serializer = mocker.patch("souzu.job_tracking._STATE_SERIALIZER")
    mock_serializer.structure.return_value = existing_state
    mock_serializer.unstructure.return_value = {}

    mock_job_completed = AsyncMock()

    mocker.patch("souzu.job_tracking._STATE_DIR", mock_state_dir)
    mocker.patch("souzu.job_tracking._job_completed", mock_job_completed)

    await monitor_printer_status(device, mock_connection, mock_slack, {})

    mock_state_file.exists.assert_called_once()
    mock_state_file.open.assert_called()
    mock_read_cm.read.assert_called_once()
    mock_json.loads.assert_called_once()
    mock_serializer.structure.assert_called_once()

    assert mock_job_completed.call_count > 0


@pytest.mark.asyncio
async def test_monitor_printer_status_exception_handling(
    mocker: MockerFixture,
) -> None:
    """Test monitor_printer_status handling exceptions."""
    device = BambuDevice(
        device_id="TEST123",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="test_printer",
    )

    mock_connection = MagicMock()
    mock_connection.subscribe.return_value.__aenter__.side_effect = ValueError(
        "Test error"
    )
    mock_slack = AsyncMock(spec=SlackClient)

    mock_logging = mocker.patch("souzu.job_tracking.logging")

    mock_state_dir = AsyncMock()
    mock_state_dir.mkdir = AsyncMock()

    mocker.patch("souzu.job_tracking._STATE_DIR", mock_state_dir)

    await monitor_printer_status(device, mock_connection, mock_slack, {})

    mock_logging.exception.assert_called_once()
    assert "Error while monitoring printer" in mock_logging.exception.call_args[0][0]


@pytest.mark.asyncio
async def test_monitor_printer_status_cancelled_error(mocker: MockerFixture) -> None:
    """Test monitor_printer_status propagating CancelledError."""
    device = BambuDevice(
        device_id="TEST123",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="test_printer",
    )

    mock_connection = MagicMock()
    mock_connection.subscribe.return_value.__aenter__.side_effect = CancelledError()
    mock_slack = AsyncMock(spec=SlackClient)

    mock_state_dir = AsyncMock()
    mock_state_dir.mkdir = AsyncMock()
    mock_state_file = AsyncMock()
    mock_state_file.exists = AsyncMock(return_value=False)
    mock_state_dir.__truediv__ = MagicMock(return_value=mock_state_file)

    mocker.patch("souzu.job_tracking._STATE_DIR", mock_state_dir)

    with pytest.raises(CancelledError):
        await monitor_printer_status(device, mock_connection, mock_slack, {})


def test_job_state_unstructure() -> None:
    """Test that JobState unstructure hook converts enum to string."""
    from souzu.job_tracking import _STATE_SERIALIZER

    result = _STATE_SERIALIZER.unstructure(JobState.RUNNING)

    assert isinstance(result, str)
    assert result == "running"


def test_job_state_structure() -> None:
    """Test that JobState structure hook converts string to enum."""
    from souzu.job_tracking import _STATE_SERIALIZER

    result = _STATE_SERIALIZER.structure("paused", JobState)

    assert isinstance(result, JobState)
    assert result == JobState.PAUSED


def test_printer_state_serialization_round_trip() -> None:
    """Test complete serialization cycle for PrinterState with JobState."""
    import json

    from souzu.job_tracking import _STATE_SERIALIZER

    job = PrintJob(
        duration=timedelta(hours=2),
        eta=datetime(2023, 1, 1, 14, 0, 0),
        state=JobState.RUNNING,
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        start_message="Test job started",
        owner="U123",
        actions_ts="9876.5432",
    )
    state = PrinterState(current_job=job)

    unstructured = _STATE_SERIALIZER.unstructure(state)
    json_str = json.dumps(unstructured)
    json_loaded = json.loads(json_str)
    restructured = _STATE_SERIALIZER.structure(json_loaded, PrinterState)

    assert restructured.current_job is not None
    assert restructured.current_job.duration == job.duration
    assert restructured.current_job.eta == job.eta
    assert restructured.current_job.state == job.state
    assert restructured.current_job.slack_channel == job.slack_channel
    assert restructured.current_job.slack_thread_ts == job.slack_thread_ts
    assert restructured.current_job.start_message == job.start_message
    assert restructured.current_job.owner == job.owner
    assert restructured.current_job.actions_ts == job.actions_ts


def test_job_action_enum() -> None:
    assert JobAction.PAUSE.value == "pause"
    assert JobAction.RESUME.value == "resume"
    assert JobAction.CANCEL.value == "cancel"
    assert JobAction.PHOTO.value == "photo"


def test_available_actions_running() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING, owner="U123")
    actions = available_actions(job)
    assert actions == [JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO]


def test_available_actions_paused() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.PAUSED, owner="U123")
    actions = available_actions(job)
    assert actions == [JobAction.RESUME, JobAction.CANCEL, JobAction.PHOTO]


def test_available_actions_none() -> None:
    assert available_actions(None) == []


def test_available_actions_unclaimed() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING)
    assert available_actions(job) == []


def test_available_actions_unclaimed_paused() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.PAUSED)
    assert available_actions(job) == []


def test_print_job_actions_ts_default() -> None:
    job = PrintJob(duration=timedelta(hours=1))
    assert job.actions_ts is None


def test_build_actions_blocks_running() -> None:
    """Running job gets Pause, Cancel (with confirm), and Photo buttons."""
    actions = [JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO]
    blocks = build_actions_blocks(actions)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "actions"
    elements = blocks[0]["elements"]
    assert len(elements) == 3
    assert elements[0]["action_id"] == "print_pause"
    assert elements[0]["text"]["text"] == "Pause"
    assert "style" not in elements[0]
    assert elements[1]["action_id"] == "print_cancel"
    assert elements[1]["style"] == "danger"
    assert "confirm" in elements[1]
    assert elements[2]["action_id"] == "print_photo"


def test_build_actions_blocks_paused() -> None:
    """Paused job gets Resume, Cancel (with confirm), and Photo buttons."""
    actions = [JobAction.RESUME, JobAction.CANCEL, JobAction.PHOTO]
    blocks = build_actions_blocks(actions)
    elements = blocks[0]["elements"]
    assert elements[0]["action_id"] == "print_resume"
    assert elements[1]["action_id"] == "print_cancel"
    assert "confirm" in elements[1]


def test_build_actions_blocks_empty() -> None:
    blocks = build_actions_blocks([])
    assert blocks == []


def test_build_actions_blocks_cancel_confirm_dialog() -> None:
    """Cancel button has a confirmation dialog with expected text."""
    actions = [JobAction.CANCEL]
    blocks = build_actions_blocks(actions)
    cancel_btn = blocks[0]["elements"][0]
    confirm = cancel_btn["confirm"]
    assert confirm["title"]["text"] == "Cancel print?"
    assert "cannot be undone" in confirm["text"]["text"]
    assert confirm["confirm"]["text"] == "Cancel print"
    assert confirm["deny"]["text"] == "Keep printing"


def test_build_terminal_actions_blocks() -> None:
    blocks = build_terminal_actions_blocks("print completed")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "context"
    assert "print completed" in blocks[0]["elements"][0]["text"]


def test_printer_state_connection_default() -> None:
    """Test that PrinterState.connection defaults to None."""
    state = PrinterState()
    assert state.connection is None


def test_printer_state_connection_excluded_from_serialization() -> None:
    """Test that connection is excluded from serialization round-trip."""
    import json
    from unittest.mock import MagicMock

    from souzu.bambu.mqtt import BambuMqttConnection
    from souzu.job_tracking import _STATE_SERIALIZER

    mock_conn = MagicMock(spec=BambuMqttConnection)
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING)
    state = PrinterState(current_job=job, connection=mock_conn)

    unstructured = _STATE_SERIALIZER.unstructure(state)
    assert "connection" not in unstructured

    json_str = json.dumps(unstructured)
    json_loaded = json.loads(json_str)
    restored = _STATE_SERIALIZER.structure(json_loaded, PrinterState)
    assert restored.connection is None


@pytest.mark.asyncio
async def test_job_started_no_actions_for_unclaimed(mocker: MockerFixture) -> None:
    """Test that _job_started does not post an actions message for unclaimed jobs."""
    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 1, 1, 12, 0, 0, tzinfo=pytz.UTC
    )

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "1111.0001"

    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 60

    state = PrinterState()
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    job_registry: dict[str, PrinterState] = {}

    await _job_started(mock_slack, report, state, device, job_registry)

    assert state.current_job is not None
    assert state.current_job.actions_ts is None
    mock_slack.post_to_thread.assert_not_called()


class TestPrinterStateCameraClient:
    def _make_device(self) -> BambuDevice:
        return BambuDevice(
            device_id="SERIAL123",
            device_name="Test Printer",
            ip_address="192.168.1.100",
            filename_prefix="test",
        )

    def test_returns_p1_camera_client_when_connection_exists(
        self,
    ) -> None:
        device = self._make_device()
        mock_conn = MagicMock(spec=BambuMqttConnection)
        mock_conn.device = device
        mock_conn.access_code = "12345678"
        state = PrinterState(connection=mock_conn)

        client = state.camera_client()

        assert isinstance(client, P1CameraClient)

    def test_returns_none_when_no_connection(self) -> None:
        state = PrinterState(connection=None)
        assert state.camera_client() is None


def test_previous_job_info_serialization_round_trip() -> None:
    """Test round-trip serialization of PrinterState with previous_job populated."""
    import json

    from souzu.job_tracking import _STATE_SERIALIZER, PreviousJobInfo

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2, minutes=15),
        ended_at=datetime(2026, 4, 16, 12, 30, 0),
    )
    state = PrinterState(current_job=None, previous_job=previous)

    unstructured = _STATE_SERIALIZER.unstructure(state)
    json_str = json.dumps(unstructured)
    json_loaded = json.loads(json_str)
    restructured = _STATE_SERIALIZER.structure(json_loaded, PrinterState)

    assert restructured.previous_job is not None
    assert restructured.previous_job.slack_channel == previous.slack_channel
    assert restructured.previous_job.slack_thread_ts == previous.slack_thread_ts
    assert restructured.previous_job.actions_ts == previous.actions_ts
    assert restructured.previous_job.duration == previous.duration
    assert restructured.previous_job.ended_at == previous.ended_at


def test_previous_job_info_default_none() -> None:
    """PrinterState.previous_job defaults to None."""
    state = PrinterState()
    assert state.previous_job is None


def test_previous_job_info_excluded_from_serialization_when_none() -> None:
    """When previous_job is None, round-trip preserves it as None."""
    import json

    from souzu.job_tracking import _STATE_SERIALIZER

    state = PrinterState()
    unstructured = _STATE_SERIALIZER.unstructure(state)
    json_str = json.dumps(unstructured)
    restored = _STATE_SERIALIZER.structure(json.loads(json_str), PrinterState)
    assert restored.previous_job is None


class TestShouldAdopt:
    def _make_previous(
        self,
        duration: timedelta = timedelta(hours=2),
        ended_at: datetime | None = None,
    ) -> "PreviousJobInfo":
        from souzu.job_tracking import PreviousJobInfo

        return PreviousJobInfo(
            slack_channel="C_PRINTS",
            slack_thread_ts="1111.0001",
            actions_ts=None,
            duration=duration,
            ended_at=ended_at or datetime(2026, 4, 16, 12, 0, 0),
        )

    def test_adopts_within_time_and_duration_window(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous()
        # 5 mins later, same duration → should adopt
        now = prev.ended_at + timedelta(minutes=5)
        assert _should_adopt(prev, timedelta(hours=2), now) is True

    def test_rejects_when_outside_time_window(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous()
        # 11 mins later → outside 10 min window
        now = prev.ended_at + timedelta(minutes=11)
        assert _should_adopt(prev, timedelta(hours=2), now) is False

    def test_adopts_at_time_window_boundary(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous()
        # Exactly 10 mins later → still within window
        now = prev.ended_at + timedelta(minutes=10)
        assert _should_adopt(prev, timedelta(hours=2), now) is True

    def test_adopts_within_plus_ten_percent_duration(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous(duration=timedelta(hours=2))
        now = prev.ended_at + timedelta(minutes=1)
        # +10% → 2h12m
        assert _should_adopt(prev, timedelta(hours=2, minutes=12), now) is True

    def test_adopts_within_minus_ten_percent_duration(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous(duration=timedelta(hours=2))
        now = prev.ended_at + timedelta(minutes=1)
        # -10% → 1h48m
        assert _should_adopt(prev, timedelta(hours=1, minutes=48), now) is True

    def test_rejects_when_duration_more_than_ten_percent_off(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous(duration=timedelta(hours=2))
        now = prev.ended_at + timedelta(minutes=1)
        # +20% → 2h24m
        assert _should_adopt(prev, timedelta(hours=2, minutes=24), now) is False
        # -20% → 1h36m
        assert _should_adopt(prev, timedelta(hours=1, minutes=36), now) is False

    def test_rejects_when_previous_duration_zero(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous(duration=timedelta(0))
        now = prev.ended_at + timedelta(minutes=1)
        assert _should_adopt(prev, timedelta(hours=1), now) is False


class TestBuildPreviousJobInfo:
    def test_returns_info_for_unclaimed_job_with_thread(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        ended_at = datetime(2026, 4, 16, 12, 0, 0)
        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel="C_PRINTS",
            slack_thread_ts="1111.0001",
            actions_ts="1111.0002",
        )
        info = _build_previous_job_info(job, ended_at)
        assert info is not None
        assert info.slack_channel == "C_PRINTS"
        assert info.slack_thread_ts == "1111.0001"
        assert info.actions_ts == "1111.0002"
        assert info.duration == timedelta(hours=2)
        assert info.ended_at == ended_at

    def test_returns_none_when_owner_set(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel="C_PRINTS",
            slack_thread_ts="1111.0001",
            owner="U_ALICE",
        )
        assert _build_previous_job_info(job, datetime(2026, 4, 16, 12, 0, 0)) is None

    def test_returns_none_when_no_thread_ts(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel="C_PRINTS",
            slack_thread_ts=None,
        )
        assert _build_previous_job_info(job, datetime(2026, 4, 16, 12, 0, 0)) is None

    def test_returns_none_when_no_channel(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel=None,
            slack_thread_ts="1111.0001",
        )
        assert _build_previous_job_info(job, datetime(2026, 4, 16, 12, 0, 0)) is None

    def test_actions_ts_can_be_none(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel="C_PRINTS",
            slack_thread_ts="1111.0001",
            actions_ts=None,
        )
        info = _build_previous_job_info(job, datetime(2026, 4, 16, 12, 0, 0))
        assert info is not None
        assert info.actions_ts is None


@pytest.mark.asyncio
async def test_job_failed_cancel_sets_previous_job(mocker: MockerFixture) -> None:
    """A cancel on an unclaimed job populates state.previous_job."""
    from souzu.job_tracking import _job_failed

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC)
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())
    mocker.patch(
        "souzu.job_tracking.CANCELLED_ERROR_CODES",
        new={0x12345678},
    )

    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
    )
    state = PrinterState(current_job=job)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.print_error = 0x12345678

    await _job_failed(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is not None
    assert state.previous_job.slack_thread_ts == "1111.0001"
    assert state.previous_job.actions_ts == "1111.0002"
    assert state.previous_job.duration == timedelta(hours=2)
    assert state.previous_job.ended_at == fixed_now


@pytest.mark.asyncio
async def test_job_failed_cancel_does_not_set_previous_job_when_claimed(
    mocker: MockerFixture,
) -> None:
    """A cancel on a claimed job leaves previous_job as None."""
    from souzu.job_tracking import _job_failed

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC
    )
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())
    mocker.patch(
        "souzu.job_tracking.CANCELLED_ERROR_CODES",
        new={0x12345678},
    )

    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        owner="U_ALICE",
    )
    state = PrinterState(current_job=job)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.print_error = 0x12345678

    await _job_failed(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is None


@pytest.mark.asyncio
async def test_job_failed_non_cancel_clears_previous_job(
    mocker: MockerFixture,
) -> None:
    """A non-cancel failure explicitly clears any prior previous_job."""
    from souzu.job_tracking import PreviousJobInfo, _job_failed

    mocker.patch("souzu.job_tracking.CONFIG")
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())
    mocker.patch(
        "souzu.job_tracking.CANCELLED_ERROR_CODES",
        new={0x12345678},
    )
    mocker.patch(
        "souzu.job_tracking.parse_error_code",
        return_value="some error",
    )

    job = PrintJob(duration=timedelta(hours=2))
    stale_previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="9999.0001",
        actions_ts=None,
        duration=timedelta(hours=1),
        ended_at=datetime(2026, 4, 16, 11, 0, 0),
    )
    state = PrinterState(current_job=job, previous_job=stale_previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.print_error = 0xDEADBEEF

    await _job_failed(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is None


@pytest.mark.asyncio
async def test_job_tracking_lost_sets_previous_job(mocker: MockerFixture) -> None:
    """Tracking lost on an unclaimed job populates state.previous_job."""
    from souzu.job_tracking import _job_tracking_lost

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC)
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())

    job = PrintJob(
        duration=timedelta(hours=1, minutes=30),
        slack_channel="C_PRINTS",
        slack_thread_ts="2222.0001",
        actions_ts="2222.0002",
    )
    state = PrinterState(current_job=job)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)

    await _job_tracking_lost(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is not None
    assert state.previous_job.slack_thread_ts == "2222.0001"
    assert state.previous_job.duration == timedelta(hours=1, minutes=30)
    assert state.previous_job.ended_at == fixed_now


@pytest.mark.asyncio
async def test_job_tracking_lost_does_not_set_previous_job_when_claimed(
    mocker: MockerFixture,
) -> None:
    """Tracking lost on a claimed job leaves previous_job as None."""
    from souzu.job_tracking import _job_tracking_lost

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC
    )
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())

    job = PrintJob(
        duration=timedelta(hours=1, minutes=30),
        slack_channel="C_PRINTS",
        slack_thread_ts="2222.0001",
        owner="U_ALICE",
    )
    state = PrinterState(current_job=job)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)

    await _job_tracking_lost(AsyncMock(spec=SlackClient), report, state, device)

    assert state.previous_job is None


@pytest.mark.asyncio
async def test_job_completed_clears_previous_job(mocker: MockerFixture) -> None:
    """Successful completion clears any prior previous_job."""
    from souzu.job_tracking import PreviousJobInfo, _job_completed

    mocker.patch("souzu.job_tracking.CONFIG")
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())

    job = PrintJob(duration=timedelta(hours=2))
    stale_previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="9999.0001",
        actions_ts=None,
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 11, 0, 0),
    )
    state = PrinterState(current_job=job, previous_job=stale_previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)

    await _job_completed(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is None


@pytest.mark.asyncio
async def test_adopt_thread_edits_top_level_message_and_posts_restart_reply(
    mocker: MockerFixture,
) -> None:
    from souzu.job_tracking import PreviousJobInfo, _adopt_thread

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 4, 16, 12, 5, 0, tzinfo=pytz.UTC
    )

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    job = PrintJob(
        duration=timedelta(hours=2),
        eta=datetime(2026, 4, 16, 14, 5, 0, tzinfo=pytz.UTC),
        start_message="Test Printer: Print started, 2 hours, done around 2:05 PM",
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    mock_slack = AsyncMock(spec=SlackClient)

    await _adopt_thread(mock_slack, previous, job, device)

    # Top-level message edited with new start text + claim button
    edit_calls = mock_slack.edit_message.call_args_list
    assert len(edit_calls) == 2  # parent + actions
    parent_edit = edit_calls[0]
    assert parent_edit.args[0] == "C_PRINTS"
    assert parent_edit.args[1] == "1111.0001"
    assert ":progress_bar:" in parent_edit.args[2]
    assert "Print started" in parent_edit.args[2]
    parent_blocks = parent_edit.kwargs["blocks"]
    assert any(
        b.get("type") == "actions"
        and any(e.get("action_id") == "claim_print" for e in b.get("elements", []))
        for b in parent_blocks
    )

    # Restart reply posted in-thread
    post_calls = mock_slack.post_to_thread.call_args_list
    assert len(post_calls) == 1
    assert post_calls[0].args[0] == "C_PRINTS"
    assert post_calls[0].args[1] == "1111.0001"
    assert ":repeat:" in post_calls[0].args[2]
    assert "Test Printer" in post_calls[0].args[2]

    # Actions message edited to "awaiting claim" placeholder
    actions_edit = edit_calls[1]
    assert actions_edit.args[1] == "1111.0002"
    assert "awaiting claim" in str(actions_edit.kwargs.get("blocks", actions_edit.args))


@pytest.mark.asyncio
async def test_adopt_thread_skips_actions_edit_when_no_actions_ts(
    mocker: MockerFixture,
) -> None:
    from souzu.job_tracking import PreviousJobInfo, _adopt_thread

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts=None,
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    job = PrintJob(
        duration=timedelta(hours=2),
        eta=datetime(2026, 4, 16, 14, 5, 0, tzinfo=pytz.UTC),
        start_message="Test Printer: Print started, 2 hours, done around 2:05 PM",
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    mock_slack = AsyncMock(spec=SlackClient)

    await _adopt_thread(mock_slack, previous, job, device)

    # Only parent edit; no actions edit
    assert mock_slack.edit_message.call_count == 1


@pytest.mark.asyncio
async def test_adopt_thread_logs_and_continues_on_edit_error(
    mocker: MockerFixture,
) -> None:
    from souzu.job_tracking import PreviousJobInfo, _adopt_thread

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    job = PrintJob(
        duration=timedelta(hours=2),
        eta=datetime(2026, 4, 16, 14, 5, 0, tzinfo=pytz.UTC),
        start_message="Test Printer: Print started, 2 hours, done around 2:05 PM",
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.edit_message.side_effect = SlackApiError("nope")
    mock_slack.post_to_thread.side_effect = SlackApiError("nope")

    mock_logging = mocker.patch("souzu.job_tracking.logging")

    await _adopt_thread(mock_slack, previous, job, device)

    # Should not raise; should log multiple errors
    assert mock_logging.error.call_count >= 2


@pytest.mark.asyncio
async def test_job_started_adopts_when_heuristic_matches(
    mocker: MockerFixture,
) -> None:
    """When previous_job matches the heuristic, _job_started adopts the thread."""
    from souzu.job_tracking import PreviousJobInfo, _job_started

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 12, 5, 0, tzinfo=pytz.UTC)
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now

    mock_adopt = AsyncMock()
    mocker.patch("souzu.job_tracking._adopt_thread", new=mock_adopt)

    mock_slack = AsyncMock(spec=SlackClient)

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    state = PrinterState(previous_job=previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 120  # 2 hours
    job_registry: dict[str, PrinterState] = {}

    await _job_started(mock_slack, report, state, device, job_registry)

    # Adoption was used — no fresh post_to_channel
    mock_slack.post_to_channel.assert_not_called()
    # _adopt_thread was called with the previous info
    assert mock_adopt.call_count == 1
    # Current job carries forward the adopted thread metadata
    assert state.current_job is not None
    assert state.current_job.slack_channel == "C_PRINTS"
    assert state.current_job.slack_thread_ts == "1111.0001"
    assert state.current_job.actions_ts == "1111.0002"
    # previous_job is consumed
    assert state.previous_job is None
    # Job registry is updated
    assert "1111.0001" in job_registry


@pytest.mark.asyncio
async def test_job_started_falls_back_to_fresh_when_outside_heuristic(
    mocker: MockerFixture,
) -> None:
    """When previous_job exists but heuristic rejects, post a fresh thread."""
    from souzu.job_tracking import PreviousJobInfo, _job_started

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 13, 0, 0, tzinfo=pytz.UTC)  # 1h after end
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now

    mock_adopt = AsyncMock()
    mocker.patch("souzu.job_tracking._adopt_thread", new=mock_adopt)

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "fresh.thread.ts"

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    state = PrinterState(previous_job=previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 120
    job_registry: dict[str, PrinterState] = {}

    await _job_started(mock_slack, report, state, device, job_registry)

    # Fresh thread used; adoption not called
    mock_adopt.assert_not_called()
    mock_slack.post_to_channel.assert_called_once()
    # previous_job is still consumed
    assert state.previous_job is None
    # Current job has the new thread ts
    assert state.current_job is not None
    assert state.current_job.slack_thread_ts == "fresh.thread.ts"


@pytest.mark.asyncio
async def test_job_started_consumes_previous_job_even_when_no_adoption(
    mocker: MockerFixture,
) -> None:
    """Confirm previous_job is cleared when heuristic mismatches (duration)."""
    from souzu.job_tracking import PreviousJobInfo, _job_started

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 12, 5, 0, tzinfo=pytz.UTC)
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now

    mocker.patch("souzu.job_tracking._adopt_thread", new=AsyncMock())

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "fresh.thread.ts"

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),  # 120 mins
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    state = PrinterState(previous_job=previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 60  # 50% of previous → mismatch
    job_registry: dict[str, PrinterState] = {}

    await _job_started(mock_slack, report, state, device, job_registry)

    assert state.previous_job is None


@pytest.mark.asyncio
async def test_job_started_with_no_previous_job_uses_fresh_thread(
    mocker: MockerFixture,
) -> None:
    """Existing fresh-thread behavior is preserved when no previous_job exists."""
    from souzu.job_tracking import _job_started

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC
    )

    mock_adopt = AsyncMock()
    mocker.patch("souzu.job_tracking._adopt_thread", new=mock_adopt)

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "1234.5678"

    state = PrinterState()
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 60

    await _job_started(mock_slack, report, state, device, {})

    mock_adopt.assert_not_called()
    mock_slack.post_to_channel.assert_called_once()
    assert state.current_job is not None
    assert state.current_job.slack_thread_ts == "1234.5678"
