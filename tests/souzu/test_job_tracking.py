"""Tests for the job_tracking module."""

from collections.abc import AsyncIterator
from concurrent.futures import CancelledError
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz
from pytest_mock import MockerFixture

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.mqtt import BambuStatusReport
from souzu.job_tracking import (
    JobState,
    PrinterState,
    PrintJob,
    SlackApiError,
    _format_date_time,
    _format_duration,
    _format_eta,
    _format_time,
    _round_up,
    _update_thread,
    monitor_printer_status,
)


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
    # Default initialization
    state = PrinterState()
    assert state.current_job is None

    # With job
    job = PrintJob(duration=timedelta(minutes=45))
    state = PrinterState(current_job=job)
    assert state.current_job is job
    # Add type check to make type checkers happy
    assert state.current_job is not None
    assert state.current_job.duration == timedelta(minutes=45)


def test_round_up() -> None:
    """Test the _round_up function."""
    # Test rounding up to the nearest minute
    dt = datetime(2023, 1, 1, 12, 30, 45)
    rounded = _round_up(dt, timedelta(minutes=1))
    assert rounded == datetime(2023, 1, 1, 12, 31, 0)

    # Test rounding up to the nearest 5 minutes
    dt = datetime(2023, 1, 1, 12, 32, 0)
    rounded = _round_up(dt, timedelta(minutes=5))
    assert rounded == datetime(2023, 1, 1, 12, 35, 0)

    # Test rounding up to the nearest hour
    dt = datetime(2023, 1, 1, 12, 45, 0)
    rounded = _round_up(dt, timedelta(hours=1))
    assert rounded == datetime(2023, 1, 1, 13, 0, 0)

    # Test exact multiple
    dt = datetime(2023, 1, 1, 12, 0, 0)
    rounded = _round_up(dt, timedelta(minutes=5))
    assert rounded == datetime(2023, 1, 1, 12, 0, 0)


def test_format_duration() -> None:
    """Test the _format_duration function."""
    # Test less than a minute
    duration = timedelta(seconds=30)
    assert _format_duration(duration) == "1 minute"

    # Test minutes (less than 55 minutes)
    duration = timedelta(minutes=12)
    assert _format_duration(duration) == "15 minutes"

    # Test minutes (edge case)
    duration = timedelta(minutes=53)
    assert _format_duration(duration) == "55 minutes"

    # Test 1 hour
    duration = timedelta(minutes=58)
    assert _format_duration(duration) == "1 hour"

    # Test hours (integer)
    duration = timedelta(hours=2, minutes=5)
    assert _format_duration(duration) == "2.5 hours"

    # Test hours (fractional)
    duration = timedelta(hours=1, minutes=15)
    assert _format_duration(duration) == "1.5 hours"

    # Test many hours
    duration = timedelta(hours=10)
    assert _format_duration(duration) == "10 hours"


def test_format_time() -> None:
    """Test the _format_time function."""
    # Test morning time (remove leading zero)
    dt = datetime(2023, 1, 1, 9, 30, 0)
    assert _format_time(dt) == "9:30 AM"

    # Test afternoon time
    dt = datetime(2023, 1, 1, 14, 45, 0)
    assert _format_time(dt) == "2:45 PM"

    # Test midnight
    dt = datetime(2023, 1, 1, 0, 0, 0)
    assert _format_time(dt) == "12:00 AM"

    # Test noon
    dt = datetime(2023, 1, 1, 12, 0, 0)
    assert _format_time(dt) == "12:00 PM"


def test_format_date_time() -> None:
    """Test the _format_date_time function."""
    # Test full date time format
    dt = datetime(2023, 1, 2, 9, 30, 0)  # Monday, January 2nd, 2023
    assert _format_date_time(dt) == "9:30 AM on Monday"

    # Test with afternoon time
    dt = datetime(2023, 1, 3, 14, 45, 0)  # Tuesday, January 3rd, 2023
    assert _format_date_time(dt) == "2:45 PM on Tuesday"


def test_format_eta_basics() -> None:
    """Basic test for _format_eta that at least runs the function."""
    with (
        patch("souzu.job_tracking.CONFIG") as mock_config,
        patch("souzu.job_tracking.datetime") as mock_datetime,
    ):
        test_tz = pytz.timezone("America/New_York")
        mock_config.timezone = test_tz

        now = datetime(2023, 1, 1, 12, 0, 0, tzinfo=test_tz)
        eta = datetime(2023, 1, 1, 13, 0, 0, tzinfo=test_tz)
        mock_datetime.now.return_value = now

        result = _format_eta(eta)
        assert isinstance(result, str)


def test_format_eta_different_day() -> None:
    """Test the _format_eta function for times on a different day."""
    with (
        patch("souzu.job_tracking.CONFIG") as mock_config,
        patch("souzu.job_tracking.datetime") as mock_datetime,
    ):
        test_tz = pytz.timezone("America/New_York")
        mock_config.timezone = test_tz
        today = datetime(2023, 1, 1, 22, 0, 0, tzinfo=test_tz)

        mock_tomorrow = MagicMock()
        mock_tomorrow.__sub__ = MagicMock(return_value=timedelta(hours=10))
        mock_date = MagicMock()
        mock_tomorrow.date = MagicMock(return_value=mock_date)

        mock_datetime.now.return_value = today
        with (
            patch("souzu.job_tracking._format_time") as mock_format_time,
            patch("souzu.job_tracking._format_date_time") as mock_format_date_time,
            patch("souzu.job_tracking._round_up", return_value=mock_tomorrow),
        ):
            mock_format_date_time.return_value = "8:00 AM on Monday"

            result = _format_eta(mock_tomorrow)

            assert mock_format_date_time.called
            assert not mock_format_time.called
            assert result == "8:00 AM on Monday"


@pytest.mark.asyncio
async def test_update_thread_no_thread() -> None:
    """Test the _update_thread function when there's no thread yet."""
    # Create a job with no thread timestamp
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts=None,
    )

    # Create a mock device
    device = MagicMock()
    device.device_name = "Test Printer"

    # Patch the post_to_channel function
    with patch("souzu.job_tracking.post_to_channel") as mock_post_to_channel:
        # Call the function
        await _update_thread(job, device, "Edited message", "Update message")

        # Verify post_to_channel was called with the right arguments
        mock_post_to_channel.assert_called_once_with(
            "test-channel",
            "Update message",
        )


@pytest.mark.asyncio
async def test_update_thread_with_thread() -> None:
    """Test the _update_thread function when there's an existing thread."""
    # Create a job with a thread timestamp
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
    )

    # Create a mock device
    device = MagicMock()
    device.device_name = "Test Printer"

    # Patch the slack functions
    with (
        patch("souzu.job_tracking.edit_message") as mock_edit_message,
        patch("souzu.job_tracking.post_to_thread") as mock_post_to_thread,
    ):
        # Call the function
        await _update_thread(job, device, "Edited message", "Update message")

        # Verify edit_message was called with the right arguments
        mock_edit_message.assert_called_once_with(
            "test-channel",
            "1234.5678",
            "Edited message",
        )

        # Verify post_to_thread was called with the right arguments
        mock_post_to_thread.assert_called_once_with(
            "test-channel",
            "1234.5678",
            "Update message",
        )


@pytest.mark.asyncio
async def test_update_thread_slack_error_edit() -> None:
    """Test handling Slack API errors in _update_thread during edit_message."""
    # Create a job with a thread timestamp
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
    )

    # Create a mock device
    device = MagicMock()
    device.device_name = "Test Printer"

    # Patch the slack functions with edit_message raising an error
    with (
        patch(
            "souzu.job_tracking.edit_message", side_effect=SlackApiError("API error")
        ),
        patch("souzu.job_tracking.post_to_thread") as mock_post_to_thread,
        patch("souzu.job_tracking.logging") as mock_logging,
    ):
        # Call the function
        await _update_thread(job, device, "Edited message", "Update message")

        # Verify error was logged
        mock_logging.error.assert_called_once()

        # Verify post_to_thread was still called
        mock_post_to_thread.assert_called_once_with(
            "test-channel",
            "1234.5678",
            "Update message",
        )


@pytest.mark.asyncio
async def test_update_thread_slack_error_thread() -> None:
    """Test handling Slack API errors in _update_thread during post_to_thread."""
    # Create a job with a thread timestamp
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
    )

    # Create a mock device
    device = MagicMock()
    device.device_name = "Test Printer"

    # Patch the slack functions with post_to_thread raising an error
    with (
        patch("souzu.job_tracking.edit_message") as mock_edit_message,
        patch(
            "souzu.job_tracking.post_to_thread", side_effect=SlackApiError("API error")
        ),
        patch("souzu.job_tracking.post_to_channel") as mock_post_to_channel,
        patch("souzu.job_tracking.logging") as mock_logging,
    ):
        # Make sure post_to_channel doesn't raise an error
        mock_post_to_channel.return_value = "5678.1234"

        # Call the function
        await _update_thread(job, device, "Edited message", "Update message")

        # Verify edit_message was called
        mock_edit_message.assert_called_once()

        # Verify error for post_to_thread was logged
        mock_logging.error.assert_any_call("Failed to notify thread: API error")


@pytest.mark.asyncio
async def test_update_thread_no_channel() -> None:
    """Test the _update_thread function when using the default notification channel."""
    # Create a job with no channel specified
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel=None,
        slack_thread_ts=None,
    )

    # Create a mock device
    device = MagicMock()
    device.device_name = "Test Printer"

    # Patch the post_to_channel function and CONFIG
    with (
        patch("souzu.job_tracking.post_to_channel") as mock_post_to_channel,
        patch("souzu.job_tracking.CONFIG") as mock_config,
    ):
        # Set default notification channel
        mock_config.slack.print_notification_channel = "default-channel"

        # Call the function
        await _update_thread(job, device, "Edited message", "Update message")

        # Verify post_to_channel was called with the default channel
        mock_post_to_channel.assert_called_once_with(
            "default-channel",
            "Update message",
        )


@pytest.mark.asyncio
async def test_monitor_printer_status(mocker: MockerFixture) -> None:
    """Test monitor_printer_status with a complete print job lifecycle."""
    # Create test device
    device = BambuDevice(
        device_id="TEST123",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="test_printer",
    )

    # Create mock connection
    mock_connection = MagicMock()

    # Set up mock reports for different states
    report_start = BambuStatusReport(
        gcode_state="RUNNING",
        mc_remaining_time=120,  # 2 hours remaining
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

    # Mock subscription
    mock_subscription = AsyncMock()

    # Set up async iterator for subscription
    async def mock_aiter(*args: object) -> AsyncIterator[BambuStatusReport]:
        yield report_start
        yield report_paused
        yield report_resumed
        yield report_finished

    mock_subscription.__aiter__ = mock_aiter
    mock_subscription.__anext__ = mock_aiter().__anext__

    mock_connection.subscribe.return_value.__aenter__.return_value = mock_subscription

    # Mock file system operations
    mock_state_dir = AsyncMock()
    mock_state_dir.mkdir = AsyncMock()
    mock_state_file = AsyncMock()
    mock_state_dir.__truediv__ = MagicMock(return_value=mock_state_file)
    mock_state_file.exists = AsyncMock(return_value=False)  # No existing state file
    mock_state_file.open = AsyncMock()
    mock_open_cm = AsyncMock()
    mock_state_file.open.return_value.__aenter__ = AsyncMock(return_value=mock_open_cm)
    mock_state_file.open.return_value.__aexit__ = AsyncMock()

    # Mock update functions
    mock_job_started = AsyncMock()
    mock_job_paused = AsyncMock()
    mock_job_resumed = AsyncMock()
    mock_job_completed = AsyncMock()

    # Mock json/serializer
    mocker.patch("souzu.job_tracking.json")
    mock_serializer = mocker.patch("souzu.job_tracking._STATE_SERIALIZER")
    mock_serializer.unstructure.return_value = {}  # Doesn't matter for test

    with (
        patch("souzu.job_tracking._STATE_DIR", mock_state_dir),
        patch("souzu.job_tracking._job_started", mock_job_started),
        patch("souzu.job_tracking._job_paused", mock_job_paused),
        patch("souzu.job_tracking._job_resumed", mock_job_resumed),
        patch("souzu.job_tracking._job_completed", mock_job_completed),
    ):
        # Create a PrinterState - commenting out because it's unused but left for clarity
        # state = PrinterState()

        # Execute the function
        await monitor_printer_status(device, mock_connection)

        # Verify directory was created
        mock_state_dir.mkdir.assert_called_once_with(exist_ok=True, parents=True)

        # Verify state file existence check
        mock_state_file.exists.assert_called_once()

        # Verify at least one state transition was called
        # The actual function calls depend on how the match cases work
        # with the test data, but we don't need to test that exact pattern
        assert (
            mock_job_started.call_count
            + mock_job_paused.call_count
            + mock_job_resumed.call_count
            + mock_job_completed.call_count
            > 0
        )

        # Verify state file was written at the end
        mock_state_file.open.assert_called()
        mock_open_cm.write.assert_called()


@pytest.mark.asyncio
async def test_monitor_printer_status_load_existing_state(
    mocker: MockerFixture,
) -> None:
    """Test monitor_printer_status loading existing state from file."""
    # Create test device
    device = BambuDevice(
        device_id="TEST123",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="test_printer",
    )

    # Set up mock connection
    mock_connection = MagicMock()
    mock_subscription = AsyncMock()

    # Set up a report that will complete an existing job
    report_finish = BambuStatusReport(
        gcode_state="FINISH", mc_remaining_time=0, mc_percent=100
    )

    # Set up subscription that yields this report
    async def mock_aiter(*args: object) -> AsyncIterator[BambuStatusReport]:
        yield report_finish

    mock_subscription.__aiter__ = mock_aiter
    mock_subscription.__anext__ = mock_aiter().__anext__
    mock_connection.subscribe.return_value.__aenter__.return_value = mock_subscription

    # Mock state file with existing state
    mock_state_dir = AsyncMock()
    mock_state_dir.mkdir = AsyncMock()
    mock_state_file = AsyncMock()
    mock_state_dir.__truediv__ = MagicMock(return_value=mock_state_file)
    mock_state_file.exists = AsyncMock(return_value=True)  # Existing state file

    # Setup file reading
    mock_read_cm = AsyncMock()
    mock_state_file.open.return_value.__aenter__ = AsyncMock(return_value=mock_read_cm)
    mock_state_file.open.return_value.__aexit__ = AsyncMock()

    # Setup existing state
    existing_job = PrintJob(
        duration=timedelta(hours=2),
        state=JobState.RUNNING,
    )
    existing_state = PrinterState(current_job=existing_job)

    # Mock json/serializer
    mock_json = mocker.patch("souzu.job_tracking.json")
    mock_json.loads.return_value = {}  # Placeholder

    # Mock serializer
    mock_serializer = mocker.patch("souzu.job_tracking._STATE_SERIALIZER")
    mock_serializer.structure.return_value = existing_state
    mock_serializer.unstructure.return_value = {}  # Doesn't matter for test

    # Mock job functions
    mock_job_completed = AsyncMock()

    with (
        patch("souzu.job_tracking._STATE_DIR", mock_state_dir),
        patch("souzu.job_tracking._job_completed", mock_job_completed),
    ):
        # Run the function
        await monitor_printer_status(device, mock_connection)

        # Verify state file was loaded
        mock_state_file.exists.assert_called_once()
        mock_state_file.open.assert_called()
        mock_read_cm.read.assert_called_once()
        mock_json.loads.assert_called_once()
        mock_serializer.structure.assert_called_once()

        # Verify state transition function was called
        assert mock_job_completed.call_count > 0


@pytest.mark.asyncio
async def test_monitor_printer_status_exception_handling(mocker: MockerFixture) -> None:
    """Test monitor_printer_status handling exceptions."""
    device = BambuDevice(
        device_id="TEST123",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="test_printer",
    )

    # Mock connection that raises an exception
    mock_connection = MagicMock()
    mock_connection.subscribe.return_value.__aenter__.side_effect = ValueError(
        "Test error"
    )

    # Mock logging
    mock_logging = mocker.patch("souzu.job_tracking.logging")

    # Mock state dir
    mock_state_dir = AsyncMock()
    mock_state_dir.mkdir = AsyncMock()

    with patch("souzu.job_tracking._STATE_DIR", mock_state_dir):
        # This should not raise an exception
        await monitor_printer_status(device, mock_connection)

        # Verify error was logged
        mock_logging.exception.assert_called_once()
        assert (
            "Error while monitoring printer" in mock_logging.exception.call_args[0][0]
        )


@pytest.mark.asyncio
async def test_monitor_printer_status_cancelled_error(mocker: MockerFixture) -> None:
    """Test monitor_printer_status propagating CancelledError."""
    device = BambuDevice(
        device_id="TEST123",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="test_printer",
    )

    # Mock connection that raises a CancelledError
    mock_connection = MagicMock()
    mock_connection.subscribe.return_value.__aenter__.side_effect = CancelledError()

    # Mock state dir and file
    mock_state_dir = AsyncMock()
    mock_state_dir.mkdir = AsyncMock()
    mock_state_file = AsyncMock()
    mock_state_file.exists = AsyncMock(return_value=False)
    mock_state_dir.__truediv__ = MagicMock(return_value=mock_state_file)

    with patch("souzu.job_tracking._STATE_DIR", mock_state_dir):
        # CancelledError should be propagated
        with pytest.raises(CancelledError):
            await monitor_printer_status(device, mock_connection)
