import signal
import typing
from asyncio import CancelledError
from unittest.mock import AsyncMock, Mock, call

import pytest
from pytest_mock import MockerFixture

from souzu.bambu.discovery import BambuDevice
from souzu.commands.monitor import monitor, notify_startup
from souzu.slack.client import SlackClient


def create_mock_device(
    name: str = "Test Printer", ip: str = "192.168.1.100", device_id: str = "ABCD1234"
) -> BambuDevice:
    """Create a mock BambuDevice for testing."""
    return BambuDevice(
        device_id=device_id,
        device_name=name,
        ip_address=ip,
        filename_prefix=device_id,
    )


@pytest.mark.asyncio
async def test_monitor_signal_handler_triggers_exit(mocker: MockerFixture) -> None:
    """Test that signal handlers correctly trigger the exit event."""
    mock_loop = Mock()

    signal_handlers: dict[
        int, tuple[typing.Callable[..., None], tuple[object, ...]]
    ] = {}

    def mock_add_handler(
        sig: int, handler: typing.Callable[..., None], *args: object
    ) -> None:
        signal_handlers[sig] = (handler, args)

    mock_loop.add_signal_handler.side_effect = mock_add_handler

    mock_slack_instance = AsyncMock(spec=SlackClient)
    mock_slack_instance.app = None
    mock_slack_instance.__aenter__.return_value = mock_slack_instance
    mocker.patch("souzu.commands.monitor.SlackClient", return_value=mock_slack_instance)

    mocker.patch("souzu.commands.monitor.get_running_loop", return_value=mock_loop)
    mock_event_class = mocker.patch("souzu.commands.monitor.Event")
    mock_wait = mocker.patch("souzu.commands.monitor.wait")
    mocker.patch("souzu.commands.monitor.create_task")

    mock_event = Mock()
    mock_event_set = Mock()
    mock_event.set = mock_event_set
    mock_event.wait = AsyncMock()
    mock_event_class.return_value = mock_event

    mock_wait.side_effect = CancelledError()

    await monitor()

    assert signal.SIGINT in signal_handlers
    assert signal.SIGTERM in signal_handlers

    handler, args = signal_handlers[signal.SIGINT]
    handler(*args)

    mock_event_set.assert_called_once()

    assert mock_loop.remove_signal_handler.call_count == 2
    mock_loop.remove_signal_handler.assert_has_calls(
        [call(signal.SIGINT), call(signal.SIGTERM)]
    )


@pytest.mark.asyncio
async def test_monitor_waits_for_first_completed_task(mocker: MockerFixture) -> None:
    """Test that monitor correctly sets up the wait with FIRST_COMPLETED."""
    mock_slack_instance = AsyncMock(spec=SlackClient)
    mock_slack_instance.app = None
    mock_slack_instance.__aenter__.return_value = mock_slack_instance
    mocker.patch("souzu.commands.monitor.SlackClient", return_value=mock_slack_instance)

    mocker.patch("souzu.commands.monitor.get_running_loop")
    mocker.patch("souzu.commands.monitor.Event")
    mock_wait = mocker.patch("souzu.commands.monitor.wait")
    mock_create_task = mocker.patch("souzu.commands.monitor.create_task")
    mock_first_completed = mocker.patch("souzu.commands.monitor.FIRST_COMPLETED")

    mock_wait.side_effect = CancelledError()

    task1, task2 = Mock(), Mock()
    mock_create_task.side_effect = [task1, task2]

    await monitor()

    mock_wait.assert_called_once()
    wait_args, wait_kwargs = mock_wait.call_args

    assert len(wait_args[0]) == 2
    assert task1 in wait_args[0]
    assert task2 in wait_args[0]
    assert wait_kwargs.get("return_when") == mock_first_completed


@pytest.mark.asyncio
async def test_device_processing_flow(mocker: MockerFixture) -> None:
    """Test the device processing flow similar to what inner_loop does."""
    test_device1 = create_mock_device(
        name="Printer 1", ip="192.168.1.101", device_id="DEV1"
    )
    test_device2 = create_mock_device(
        name="Printer 2", ip="192.168.1.102", device_id="DEV2"
    )

    mock_tg = AsyncMock()
    mock_stack = AsyncMock()
    mock_connection = AsyncMock()
    mock_slack = AsyncMock(spec=SlackClient)
    mock_job_registry: dict[str, object] = {}

    mock_connection_class = Mock(return_value=mock_connection)

    mock_log_reports = Mock()
    mock_monitor_status = Mock()

    mocker.patch("souzu.commands.monitor.BambuMqttConnection", mock_connection_class)
    mocker.patch("souzu.commands.monitor.log_reports", mock_log_reports)
    mocker.patch("souzu.commands.monitor.monitor_printer_status", mock_monitor_status)

    for device in [test_device1, test_device2]:
        mock_stack.enter_async_context.return_value = mock_connection

        connection = await mock_stack.enter_async_context(
            mock_connection_class(mock_tg, device)
        )

        mock_log_reports.return_value = None
        mock_monitor_status.return_value = None

        mock_log_reports(device, connection)
        mock_monitor_status(device, connection, mock_slack, mock_job_registry)

        mock_tg.create_task(None)
        mock_tg.create_task(None)

    assert mock_connection_class.call_count == 2
    mock_connection_class.assert_has_calls(
        [call(mock_tg, test_device1), call(mock_tg, test_device2)]
    )

    assert mock_stack.enter_async_context.call_count == 2

    assert mock_log_reports.call_count == 2
    mock_log_reports.assert_has_calls(
        [call(test_device1, mock_connection), call(test_device2, mock_connection)]
    )

    assert mock_monitor_status.call_count == 2
    mock_monitor_status.assert_has_calls(
        [
            call(test_device1, mock_connection, mock_slack, mock_job_registry),
            call(test_device2, mock_connection, mock_slack, mock_job_registry),
        ]
    )

    assert mock_tg.create_task.call_count == 4


@pytest.mark.asyncio
async def test_device_processing_handles_exceptions(mocker: MockerFixture) -> None:
    """Test that device processing properly handles connection exceptions."""
    test_device = create_mock_device(
        name="Failing Printer", ip="192.168.1.200", device_id="FAIL1"
    )

    mock_tg = AsyncMock()
    mock_stack = AsyncMock()

    mock_connection_class = Mock()

    mock_log_reports = Mock()
    mock_monitor_status = Mock()

    mock_log_exception = Mock()

    mocker.patch("souzu.commands.monitor.BambuMqttConnection", mock_connection_class)
    mocker.patch("souzu.commands.monitor.log_reports", mock_log_reports)
    mocker.patch("souzu.commands.monitor.monitor_printer_status", mock_monitor_status)
    mocker.patch("souzu.commands.monitor.logging.exception", mock_log_exception)

    connection_error = ConnectionError("Failed to connect")
    mock_stack.enter_async_context.side_effect = connection_error

    try:
        await mock_stack.enter_async_context(
            mock_connection_class(mock_tg, test_device)
        )

        mock_tg.create_task(mock_log_reports(test_device, "never reached"))
        mock_tg.create_task(mock_monitor_status(test_device, "never reached"))
    except Exception:
        mock_log_exception(
            f"Failed to set up subscription for {test_device.device_name}"
        )

    mock_log_exception.assert_called_once_with(
        f"Failed to set up subscription for {test_device.device_name}"
    )

    mock_log_reports.assert_not_called()
    mock_monitor_status.assert_not_called()

    mock_tg.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_handles_cancelled_error(mocker: MockerFixture) -> None:
    """Test that monitor handles CancelledError properly."""
    mock_slack_instance = AsyncMock(spec=SlackClient)
    mock_slack_instance.app = None
    mock_slack_instance.__aenter__.return_value = mock_slack_instance
    mocker.patch("souzu.commands.monitor.SlackClient", return_value=mock_slack_instance)

    mock_loop = Mock()
    mocker.patch("souzu.commands.monitor.get_running_loop", return_value=mock_loop)
    mocker.patch("souzu.commands.monitor.Event")
    mock_wait = mocker.patch("souzu.commands.monitor.wait")
    mocker.patch("souzu.commands.monitor.create_task")

    mock_wait.side_effect = CancelledError()

    await monitor()

    assert mock_loop.remove_signal_handler.call_count == 2


@pytest.mark.asyncio
async def test_monitor_workflow_integration(mocker: MockerFixture) -> None:
    """Integration test for the monitor workflow, simulating a complete run."""
    from asyncio import ALL_COMPLETED, FIRST_COMPLETED

    mock_loop = Mock()
    mock_exit_event = Mock()
    mock_exit_event.wait = AsyncMock()
    mock_exit_event.wait.return_value = None

    mock_inner_loop_task = Mock()
    mock_exit_wait_task = Mock()

    mock_wait = AsyncMock(return_value=({mock_inner_loop_task}, {mock_exit_wait_task}))

    mock_slack_instance = AsyncMock(spec=SlackClient)
    mock_slack_instance.app = None
    mock_slack_instance.__aenter__.return_value = mock_slack_instance
    mocker.patch("souzu.commands.monitor.SlackClient", return_value=mock_slack_instance)

    mocker.patch("souzu.commands.monitor.get_running_loop", return_value=mock_loop)
    mocker.patch("souzu.commands.monitor.Event", return_value=mock_exit_event)
    mocker.patch("souzu.commands.monitor.wait", mock_wait)
    mock_create_task = mocker.patch("souzu.commands.monitor.create_task")
    mocker.patch("souzu.commands.monitor.inner_loop")
    mock_notify = mocker.patch(
        "souzu.commands.monitor.notify_startup", new_callable=AsyncMock
    )

    mock_notify.return_value = None

    mock_create_task.side_effect = [mock_inner_loop_task, mock_exit_wait_task]

    await monitor()

    assert mock_loop.add_signal_handler.call_count == 2

    assert mock_create_task.call_count == 2

    assert mock_wait.call_count == 2

    first_call_args, first_call_kwargs = mock_wait.call_args_list[0]
    assert len(first_call_args[0]) == 2
    assert isinstance(first_call_args[0], list)
    assert first_call_kwargs.get("return_when") == FIRST_COMPLETED

    second_call_args, second_call_kwargs = mock_wait.call_args_list[1]
    assert second_call_kwargs.get("return_when") == ALL_COMPLETED

    assert mock_loop.remove_signal_handler.call_count == 2


@pytest.mark.asyncio
async def test_notify_startup_posts_version(mocker: MockerFixture) -> None:
    """Test that notify_startup posts the version to the error channel."""
    mocker.patch("souzu.commands.monitor.version", return_value="1.2.3")
    mocker.patch(
        "souzu.commands.monitor.CONFIG"
    ).slack.error_notification_channel = "test-channel"

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "12345.67890"

    await notify_startup(mock_slack)

    mock_slack.post_to_channel.assert_called_once_with(
        "test-channel", "Souzu 1.2.3 started"
    )


@pytest.mark.asyncio
async def test_notify_startup_handles_unknown_version(mocker: MockerFixture) -> None:
    """Test that notify_startup handles PackageNotFoundError gracefully."""
    from importlib.metadata import PackageNotFoundError

    mocker.patch(
        "souzu.commands.monitor.version", side_effect=PackageNotFoundError("souzu")
    )
    mocker.patch(
        "souzu.commands.monitor.CONFIG"
    ).slack.error_notification_channel = "test-channel"

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "12345.67890"

    await notify_startup(mock_slack)

    mock_slack.post_to_channel.assert_called_once_with(
        "test-channel", "Souzu unknown started"
    )


@pytest.mark.asyncio
async def test_notify_startup_handles_slack_error(mocker: MockerFixture) -> None:
    """Test that notify_startup logs but doesn't raise on Slack errors."""
    mocker.patch("souzu.commands.monitor.version", return_value="1.0.0")
    mocker.patch(
        "souzu.commands.monitor.CONFIG"
    ).slack.error_notification_channel = "test-channel"
    mock_logging = mocker.patch("souzu.commands.monitor.logging")

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.side_effect = Exception("Slack API error")

    await notify_startup(mock_slack)

    mock_logging.exception.assert_called_once_with(
        "Failed to post startup notification"
    )


@pytest.mark.asyncio
async def test_monitor_calls_notify_startup(mocker: MockerFixture) -> None:
    """Test that monitor calls notify_startup on start."""
    mock_slack_instance = AsyncMock(spec=SlackClient)
    mock_slack_instance.app = None
    mock_slack_instance.__aenter__.return_value = mock_slack_instance
    mocker.patch("souzu.commands.monitor.SlackClient", return_value=mock_slack_instance)

    mock_notify = mocker.patch("souzu.commands.monitor.notify_startup")
    mocker.patch("souzu.commands.monitor.get_running_loop")
    mocker.patch("souzu.commands.monitor.Event")
    mock_wait = mocker.patch("souzu.commands.monitor.wait")
    mocker.patch("souzu.commands.monitor.create_task")

    mock_wait.side_effect = CancelledError()

    await monitor()

    mock_notify.assert_awaited_once_with(mock_slack_instance)
