"""Tests for the monitor command."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_notify_startup_returns_timestamp() -> None:
    """Test that notify_startup returns the message timestamp."""
    from souzu.commands.monitor import notify_startup

    with (
        patch(
            "souzu.commands.monitor.post_to_channel", new_callable=AsyncMock
        ) as mock_post,
        patch("souzu.commands.monitor.CONFIG") as mock_config,
    ):
        mock_post.return_value = "1234.5678"
        mock_config.slack.error_notification_channel = "C123"

        result = await notify_startup()

    assert result == "1234.5678"


@pytest.mark.asyncio
async def test_notify_startup_returns_none_on_failure() -> None:
    """Test that notify_startup returns None when posting fails."""
    from souzu.commands.monitor import notify_startup

    with (
        patch(
            "souzu.commands.monitor.post_to_channel", new_callable=AsyncMock
        ) as mock_post,
        patch("souzu.commands.monitor.CONFIG") as mock_config,
    ):
        mock_post.side_effect = Exception("API error")
        mock_config.slack.error_notification_channel = "C123"

        result = await notify_startup()

    assert result is None


@pytest.mark.asyncio
async def test_monitor_starts_thread_watcher_when_startup_succeeds() -> None:
    """Test that monitor starts watching the startup thread."""
    from souzu.commands.monitor import monitor

    watch_thread_called = False
    watch_thread_args: tuple[str, str] | None = None

    async def mock_watch_thread(
        channel: str,
        thread_ts: str,
        on_reply: object,
        **kwargs: object,
    ) -> None:
        nonlocal watch_thread_called, watch_thread_args
        watch_thread_called = True
        watch_thread_args = (channel, thread_ts)
        await asyncio.sleep(10)  # Run until cancelled

    async def mock_inner_loop() -> None:
        # Simulate inner_loop completing quickly
        pass

    with (
        patch(
            "souzu.commands.monitor.notify_startup", new_callable=AsyncMock
        ) as mock_startup,
        patch("souzu.commands.monitor.inner_loop", side_effect=mock_inner_loop),
        patch("souzu.commands.monitor.watch_thread", side_effect=mock_watch_thread),
        patch("souzu.commands.monitor.CONFIG") as mock_config,
    ):
        mock_startup.return_value = "1234.5678"
        mock_config.slack.error_notification_channel = "C123"

        await asyncio.wait_for(monitor(), timeout=0.1)

    assert watch_thread_called
    assert watch_thread_args == ("C123", "1234.5678")


@pytest.mark.asyncio
async def test_monitor_skips_thread_watcher_when_startup_fails() -> None:
    """Test that monitor doesn't start watcher when startup returns None."""
    from souzu.commands.monitor import monitor

    watch_thread_called = False

    async def mock_watch_thread(*args: object, **kwargs: object) -> None:
        nonlocal watch_thread_called
        watch_thread_called = True
        await asyncio.sleep(10)

    async def mock_inner_loop() -> None:
        # Simulate inner_loop completing quickly
        pass

    with (
        patch(
            "souzu.commands.monitor.notify_startup", new_callable=AsyncMock
        ) as mock_startup,
        patch("souzu.commands.monitor.inner_loop", side_effect=mock_inner_loop),
        patch("souzu.commands.monitor.watch_thread", side_effect=mock_watch_thread),
        patch("souzu.commands.monitor.CONFIG") as mock_config,
    ):
        mock_startup.return_value = None
        mock_config.slack.error_notification_channel = "C123"

        await asyncio.wait_for(monitor(), timeout=0.1)

    assert not watch_thread_called
