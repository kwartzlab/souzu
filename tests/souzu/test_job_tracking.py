"""Tests for the job_tracking module."""

from collections.abc import AsyncIterator
from concurrent.futures import CancelledError
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz
from pytest_mock import MockerFixture

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.mqtt import BambuStatusReport
from souzu.job_tracking import (
    JobState,
    PrinterState,
    PrintJob,
    _format_date_time,
    _format_duration,
    _format_eta,
    _format_time,
    _round_up,
    _update_thread,
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

    mock_slack.edit_message.assert_called_once_with(
        "test-channel",
        "1234.5678",
        "Edited message",
    )

    mock_slack.post_to_thread.assert_called_once_with(
        "test-channel",
        "1234.5678",
        "Update message",
    )


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
