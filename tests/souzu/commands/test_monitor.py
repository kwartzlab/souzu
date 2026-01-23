"""Tests for the monitor command."""

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
