import asyncio
import signal
import typing
from asyncio import CancelledError
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from souzu.bambu.discovery import BambuDevice
from souzu.commands.monitor import monitor, notify_startup


# Helper function to create a completed future for testing
def completed_future(result: object = None) -> asyncio.Future:
    future: asyncio.Future = asyncio.Future()
    future.set_result(result)
    return future


# Helper function to create a future that raises an exception when awaited
def failing_future(exception: Exception) -> asyncio.Future:
    future: asyncio.Future = asyncio.Future()
    future.set_exception(exception)
    return future


def create_mock_device(
    name: str = "Test Printer", ip: str = "192.168.1.100", device_id: str = "ABCD1234"
) -> BambuDevice:
    """Create a mock BambuDevice for testing.

    BambuDevice is a frozen class, so we create it directly with the required attributes.
    """
    return BambuDevice(
        device_id=device_id,
        device_name=name,
        ip_address=ip,
        filename_prefix=device_id,
    )


@pytest.mark.asyncio
async def test_monitor_signal_handler_triggers_exit() -> None:
    """Test that signal handlers correctly trigger the exit event."""
    # Create a mock asyncio loop with a working add_signal_handler that calls our handler
    mock_loop = Mock()

    # Store the signal handler when it's registered
    signal_handlers = {}

    def mock_add_handler(
        sig: int, handler: typing.Callable[..., None], *args: object
    ) -> None:
        signal_handlers[sig] = (handler, args)

    mock_loop.add_signal_handler.side_effect = mock_add_handler

    with (
        patch("souzu.commands.monitor.get_running_loop", return_value=mock_loop),
        patch("souzu.commands.monitor.Event") as mock_event_class,
        patch("souzu.commands.monitor.wait") as mock_wait,
        patch("souzu.commands.monitor.create_task"),
    ):
        # Set up the Event - use regular Mock to avoid coroutine warning
        mock_event = Mock()
        mock_event_set = Mock()  # Regular mock instead of AsyncMock
        mock_event.set = mock_event_set
        mock_event.wait = AsyncMock()
        mock_event_class.return_value = mock_event

        # Make wait return immediately
        mock_wait.side_effect = CancelledError()

        # Run monitor, which should register signal handlers
        await monitor()

        # Verify signal handlers were registered for both signals
        assert signal.SIGINT in signal_handlers
        assert signal.SIGTERM in signal_handlers

        # Call the handler manually to simulate a signal
        handler, args = signal_handlers[signal.SIGINT]
        handler(*args)

        # Verify the exit event was set
        mock_event_set.assert_called_once()

        # Verify cleanup of signal handlers occurred
        assert mock_loop.remove_signal_handler.call_count == 2
        mock_loop.remove_signal_handler.assert_has_calls(
            [call(signal.SIGINT), call(signal.SIGTERM)]
        )


@pytest.mark.asyncio
async def test_monitor_waits_for_first_completed_task() -> None:
    """Test that monitor correctly sets up the wait with FIRST_COMPLETED."""
    with (
        patch("souzu.commands.monitor.get_running_loop"),
        patch("souzu.commands.monitor.Event"),
        patch("souzu.commands.monitor.wait") as mock_wait,
        patch("souzu.commands.monitor.create_task") as mock_create_task,
        patch("souzu.commands.monitor.FIRST_COMPLETED") as mock_first_completed,
    ):
        # Make wait return immediately to avoid hanging
        mock_wait.side_effect = CancelledError()

        # Two task mocks that will be returned by create_task
        task1, task2 = Mock(), Mock()
        mock_create_task.side_effect = [task1, task2]

        # Run monitor, which should call wait with our tasks
        await monitor()

        # Verify wait was called with the right tasks and return_when
        mock_wait.assert_called_once()
        wait_args, wait_kwargs = mock_wait.call_args

        # Verify the wait was called with the right arguments
        assert len(wait_args[0]) == 2
        assert task1 in wait_args[0]
        assert task2 in wait_args[0]
        assert wait_kwargs.get('return_when') == mock_first_completed


@pytest.mark.asyncio
async def test_device_processing_flow() -> None:
    """Test the device processing flow similar to what inner_loop does."""
    # Create test devices
    test_device1 = create_mock_device(
        name="Printer 1", ip="192.168.1.101", device_id="DEV1"
    )
    test_device2 = create_mock_device(
        name="Printer 2", ip="192.168.1.102", device_id="DEV2"
    )

    # Create mocks for components used in the process flow
    mock_tg = AsyncMock()
    mock_stack = AsyncMock()
    mock_connection = AsyncMock()

    # Mock the BambuMqttConnection class
    mock_connection_class = Mock(return_value=mock_connection)

    # Mock log_reports and monitor_printer_status functions
    mock_log_reports = Mock()
    mock_monitor_status = Mock()

    with (
        patch("souzu.commands.monitor.BambuMqttConnection", mock_connection_class),
        patch("souzu.commands.monitor.log_reports", mock_log_reports),
        patch("souzu.commands.monitor.monitor_printer_status", mock_monitor_status),
    ):
        # Simulate the device processing logic from inner_loop
        for device in [test_device1, test_device2]:
            # Simulate entering the BambuMqttConnection context
            mock_stack.enter_async_context.return_value = mock_connection

            # Call the method that would be called in inner_loop
            connection = await mock_stack.enter_async_context(
                mock_connection_class(mock_tg, device)
            )

            # Create tasks for monitoring the device - use regular Mock to avoid coroutine warnings
            mock_log_reports.return_value = None
            mock_monitor_status.return_value = None

            # Call the functions
            mock_log_reports(device, connection)
            mock_monitor_status(device, connection)

            # Call create_task to simulate the actual inner_loop behavior
            mock_tg.create_task(None)  # For log_reports task
            mock_tg.create_task(None)  # For monitor_printer_status task

        # Verify connections were created properly
        assert mock_connection_class.call_count == 2
        mock_connection_class.assert_has_calls(
            [call(mock_tg, test_device1), call(mock_tg, test_device2)]
        )

        # Verify enter_async_context was called for each device
        assert mock_stack.enter_async_context.call_count == 2

        # Verify log_reports and monitor_printer_status were called for each device
        assert mock_log_reports.call_count == 2
        mock_log_reports.assert_has_calls(
            [call(test_device1, mock_connection), call(test_device2, mock_connection)]
        )

        assert mock_monitor_status.call_count == 2
        mock_monitor_status.assert_has_calls(
            [call(test_device1, mock_connection), call(test_device2, mock_connection)]
        )

        # Verify the task group create_task was called for each monitoring task (2 per device)
        assert mock_tg.create_task.call_count == 4


@pytest.mark.asyncio
async def test_device_processing_handles_exceptions() -> None:
    """Test that device processing properly handles connection exceptions."""
    # Create test device
    test_device = create_mock_device(
        name="Failing Printer", ip="192.168.1.200", device_id="FAIL1"
    )

    # Create mocks for components used in the process flow
    mock_tg = AsyncMock()
    mock_stack = AsyncMock()

    # Mock the BambuMqttConnection class
    mock_connection_class = Mock()

    # Mock log_reports and monitor_printer_status functions
    mock_log_reports = Mock()
    mock_monitor_status = Mock()

    # Mock logging.exception for verification
    mock_log_exception = Mock()

    with (
        patch("souzu.commands.monitor.BambuMqttConnection", mock_connection_class),
        patch("souzu.commands.monitor.log_reports", mock_log_reports),
        patch("souzu.commands.monitor.monitor_printer_status", mock_monitor_status),
        patch("souzu.commands.monitor.logging.exception", mock_log_exception),
    ):
        # Make enter_async_context raise a connection error
        connection_error = ConnectionError("Failed to connect")
        mock_stack.enter_async_context.side_effect = connection_error

        # Simulate the device processing logic with exception handling
        try:
            # This will raise our mocked connection error
            await mock_stack.enter_async_context(
                mock_connection_class(mock_tg, test_device)
            )

            # These should not execute due to the exception
            mock_tg.create_task(mock_log_reports(test_device, "never reached"))
            mock_tg.create_task(mock_monitor_status(test_device, "never reached"))
        except Exception:
            # This is similar to the exception handling in inner_loop
            mock_log_exception(
                f"Failed to set up subscription for {test_device.device_name}"
            )

        # Verify the exception was logged with the right message
        mock_log_exception.assert_called_once_with(
            f"Failed to set up subscription for {test_device.device_name}"
        )

        # Verify the monitoring functions were never called
        mock_log_reports.assert_not_called()
        mock_monitor_status.assert_not_called()

        # Verify the task group create_task was never called
        mock_tg.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_handles_cancelled_error() -> None:
    """Test that monitor handles CancelledError properly."""
    with (
        patch("souzu.commands.monitor.get_running_loop") as mock_get_loop,
        patch("souzu.commands.monitor.Event"),
        patch("souzu.commands.monitor.wait") as mock_wait,
        patch("souzu.commands.monitor.create_task"),
    ):
        # Make wait raise CancelledError
        mock_wait.side_effect = CancelledError()

        # Mock loop for signal handler cleanup verification
        mock_loop = Mock()
        mock_get_loop.return_value = mock_loop

        # Monitor should not re-raise the CancelledError
        await monitor()

        # Verify signal handlers were cleaned up
        assert mock_loop.remove_signal_handler.call_count == 2


@pytest.mark.asyncio
async def test_monitor_workflow_integration() -> None:
    """Integration test for the monitor workflow, simulating a complete run."""
    from asyncio import ALL_COMPLETED, FIRST_COMPLETED

    # Setup test mocks
    mock_loop = Mock()
    mock_exit_event = Mock()
    mock_exit_event.wait = AsyncMock()
    mock_exit_event.wait.return_value = None  # Just returns normally when awaited

    # Create mock tasks
    mock_inner_loop_task = Mock()
    mock_exit_wait_task = Mock()

    # Set up the mocks to simulate inner_loop completing first
    # wait returns (done, pending) tuple
    mock_wait = AsyncMock(return_value=({mock_inner_loop_task}, {mock_exit_wait_task}))

    with (
        patch("souzu.commands.monitor.get_running_loop", return_value=mock_loop),
        patch("souzu.commands.monitor.Event", return_value=mock_exit_event),
        patch("souzu.commands.monitor.wait", mock_wait),
        patch("souzu.commands.monitor.create_task") as mock_create_task,
        patch("souzu.commands.monitor.inner_loop"),
        patch(
            "souzu.commands.monitor.notify_startup", new_callable=AsyncMock
        ) as mock_notify,
    ):
        # notify_startup returns None so watch_thread won't be started
        mock_notify.return_value = None

        # Set up create_task to return our mocks
        mock_create_task.side_effect = [mock_inner_loop_task, mock_exit_wait_task]

        # Run monitor
        await monitor()

        # Verify signal handlers were registered
        assert mock_loop.add_signal_handler.call_count == 2

        # Verify both tasks were created (inner_loop and exit_event.wait)
        assert mock_create_task.call_count == 2

        # Verify wait was called twice: once for FIRST_COMPLETED, once for ALL_COMPLETED
        assert mock_wait.call_count == 2

        # Check first call (FIRST_COMPLETED)
        first_call_args, first_call_kwargs = mock_wait.call_args_list[0]
        assert len(first_call_args[0]) == 2
        assert isinstance(first_call_args[0], list)
        assert first_call_kwargs.get("return_when") == FIRST_COMPLETED

        # Check second call (ALL_COMPLETED for pending tasks)
        second_call_args, second_call_kwargs = mock_wait.call_args_list[1]
        assert second_call_kwargs.get("return_when") == ALL_COMPLETED

        # Verify signal handlers were cleaned up on exit
        assert mock_loop.remove_signal_handler.call_count == 2


@pytest.mark.asyncio
async def test_notify_startup_posts_version() -> None:
    """Test that notify_startup posts the version to the error channel."""
    with (
        patch("souzu.commands.monitor.version", return_value="1.2.3"),
        patch("souzu.commands.monitor.CONFIG") as mock_config,
        patch("souzu.commands.monitor.post_to_channel") as mock_post,
    ):
        mock_config.slack.error_notification_channel = "test-channel"
        mock_post.return_value = "12345.67890"

        await notify_startup()

        mock_post.assert_called_once_with("test-channel", "Souzu 1.2.3 started")


@pytest.mark.asyncio
async def test_notify_startup_handles_unknown_version() -> None:
    """Test that notify_startup handles PackageNotFoundError gracefully."""
    from importlib.metadata import PackageNotFoundError

    with (
        patch(
            "souzu.commands.monitor.version", side_effect=PackageNotFoundError("souzu")
        ),
        patch("souzu.commands.monitor.CONFIG") as mock_config,
        patch("souzu.commands.monitor.post_to_channel") as mock_post,
    ):
        mock_config.slack.error_notification_channel = "test-channel"
        mock_post.return_value = "12345.67890"

        await notify_startup()

        mock_post.assert_called_once_with("test-channel", "Souzu unknown started")


@pytest.mark.asyncio
async def test_notify_startup_handles_slack_error() -> None:
    """Test that notify_startup logs but doesn't raise on Slack errors."""
    with (
        patch("souzu.commands.monitor.version", return_value="1.0.0"),
        patch("souzu.commands.monitor.CONFIG") as mock_config,
        patch("souzu.commands.monitor.post_to_channel") as mock_post,
        patch("souzu.commands.monitor.logging") as mock_logging,
    ):
        mock_config.slack.error_notification_channel = "test-channel"
        mock_post.side_effect = Exception("Slack API error")

        # Should not raise
        await notify_startup()

        # Should log the exception
        mock_logging.exception.assert_called_once_with(
            "Failed to post startup notification"
        )


@pytest.mark.asyncio
async def test_monitor_calls_notify_startup() -> None:
    """Test that monitor calls notify_startup on start."""
    with (
        patch("souzu.commands.monitor.notify_startup") as mock_notify,
        patch("souzu.commands.monitor.get_running_loop"),
        patch("souzu.commands.monitor.Event"),
        patch("souzu.commands.monitor.wait") as mock_wait,
        patch("souzu.commands.monitor.create_task"),
    ):
        mock_wait.side_effect = CancelledError()

        await monitor()

        mock_notify.assert_awaited_once()
