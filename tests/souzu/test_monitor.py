import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_mock import MockerFixture

from souzu.commands.monitor import monitor


# Creates a completed future for testing async code without actual waiting
def completed_future(result: object = None) -> asyncio.Future:
    future: asyncio.Future = asyncio.Future()
    future.set_result(result)
    return future


@pytest.mark.asyncio
async def test_inner_loop_mock_function(mocker: MockerFixture) -> None:
    """Test inner_loop by mocking the function itself."""
    # Create mock inner_loop that returns a completed future
    mock_inner_loop = mocker.patch("souzu.commands.monitor.inner_loop")
    mock_inner_loop.return_value = completed_future(None)

    # Set up other necessary mocks
    mocker.patch("souzu.commands.monitor.get_running_loop")
    mocker.patch("souzu.commands.monitor.Event")
    mock_create_task = mocker.patch("souzu.commands.monitor.create_task")
    mock_wait = AsyncMock()
    mocker.patch("souzu.commands.monitor.wait", mock_wait)

    # Run the monitor function
    await monitor()

    # Verify inner_loop was called
    assert mock_inner_loop.called
    # Verify create_task was called with our coroutine
    assert mock_create_task.call_count > 0


@pytest.mark.asyncio
async def test_monitor_signal_handlers(mocker: MockerFixture) -> None:
    """Test monitor function sets up signal handlers."""
    # Mock necessary components
    mock_loop = mocker.patch("souzu.commands.monitor.get_running_loop")
    mocker.patch("souzu.commands.monitor.Event")
    mocker.patch("souzu.commands.monitor.wait")
    mocker.patch("souzu.commands.monitor.create_task")

    # Create mock inner_loop that returns a completed future
    mock_inner_loop = mocker.patch("souzu.commands.monitor.inner_loop")
    mock_inner_loop.return_value = completed_future(None)

    # Run the monitor function
    await monitor()

    # Verify signal handlers were set
    assert mock_loop.return_value.add_signal_handler.call_count > 0


@pytest.mark.asyncio
async def test_monitor() -> None:
    """Test monitor function signal handling and task creation."""
    # Mock loop and signal handlers
    mock_loop = MagicMock()
    mock_wait = AsyncMock()
    mock_create_task = AsyncMock()
    mock_exit_event = AsyncMock()
    mock_exit_event.wait = AsyncMock()

    with (
        patch("souzu.commands.monitor.get_running_loop", return_value=mock_loop),
        patch("souzu.commands.monitor.Event", return_value=mock_exit_event),
        patch("souzu.commands.monitor.wait", mock_wait),
        patch("souzu.commands.monitor.create_task", mock_create_task),
    ):
        # Call the function but cancel to simulate exiting
        mock_wait.side_effect = Exception("Test exit")

        with pytest.raises(Exception, match="Test exit"):
            await monitor()

        # Verify signal handlers were set up
        assert mock_loop.add_signal_handler.call_count == 2

        # Verify tasks were created
        assert mock_create_task.call_count == 2


@pytest.mark.asyncio
async def test_monitor_handles_exceptions(mocker: MockerFixture) -> None:
    """Test monitor function handles other exceptions."""
    # Mock necessary components
    mock_loop = mocker.patch("souzu.commands.monitor.get_running_loop")
    mocker.patch("souzu.commands.monitor.Event")

    # Create mock inner_loop that returns a future that raises when awaited
    mock_inner_loop = mocker.patch("souzu.commands.monitor.inner_loop")
    future: asyncio.Future = asyncio.Future()
    future.set_exception(ValueError("Test exception"))
    mock_inner_loop.return_value = future

    # Create awaitable mocks for asyncio functions
    mock_wait = AsyncMock()
    mocker.patch("souzu.commands.monitor.wait", mock_wait)

    # Create mock task objects
    mock_task1 = MagicMock()
    mock_task2 = MagicMock()
    mock_create_task = mocker.patch("souzu.commands.monitor.create_task")
    mock_create_task.side_effect = [mock_task1, mock_task2]

    # This should not raise an exception because the exception is handled
    # in the finally block of monitor()
    await monitor()

    # Verify wait was called with the right arguments
    mock_wait.assert_called_once()

    # Verify cleanup happened
    assert mock_loop.return_value.remove_signal_handler.call_count > 0
