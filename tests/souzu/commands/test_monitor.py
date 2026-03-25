"""Tests for the monitor command."""

from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from souzu.slack.client import SlackClient


@pytest.mark.asyncio
async def test_notify_startup_returns_timestamp(mocker: MockerFixture) -> None:
    from souzu.commands.monitor import notify_startup

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "1234.5678"

    result = await notify_startup(mock_slack)
    assert result == "1234.5678"


@pytest.mark.asyncio
async def test_notify_startup_returns_none_on_failure(mocker: MockerFixture) -> None:
    from souzu.commands.monitor import notify_startup

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.side_effect = Exception("API error")

    result = await notify_startup(mock_slack)
    assert result is None
