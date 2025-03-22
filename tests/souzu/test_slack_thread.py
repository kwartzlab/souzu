"""Tests for the slack thread module."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from souzu.slack.thread import (
    SlackApiError,
    edit_message,
    post_to_channel,
    post_to_thread,
)


@pytest.fixture
def mock_slack_client() -> AsyncMock:
    """Create a mock Slack client for testing."""
    mock_client = AsyncMock()
    mock_client.chat_postMessage = AsyncMock()
    mock_client.chat_update = AsyncMock()
    return mock_client


@pytest.mark.asyncio
async def test_post_to_channel_success(mock_slack_client: AsyncMock) -> None:
    """Test posting to a channel successfully."""
    mock_response = {"ok": True, "ts": "1234.5678"}
    mock_slack_client.chat_postMessage.return_value = mock_response

    with patch("souzu.slack.thread._CLIENT", mock_slack_client):
        result = await post_to_channel("test-channel", "Hello, world!")

    assert result == "1234.5678"
    mock_slack_client.chat_postMessage.assert_called_once_with(
        channel="test-channel", text="Hello, world!"
    )


@pytest.mark.asyncio
async def test_post_to_channel_no_token() -> None:
    """Test posting to a channel with no API token configured."""
    with patch("souzu.slack.thread._CLIENT", None):
        with pytest.raises(SlackApiError, match="No Slack API token configured"):
            await post_to_channel("test-channel", "Hello, world!")


@pytest.mark.asyncio
async def test_post_to_channel_no_channel(caplog: pytest.LogCaptureFixture) -> None:
    """Test posting to a channel with no channel specified."""
    caplog.set_level(logging.DEBUG)

    mock_client = AsyncMock()
    with patch("souzu.slack.thread._CLIENT", mock_client):
        result = await post_to_channel(None, "Hello, world!")
    assert result is None
    assert "No channel to post message: Hello, world!" in caplog.text
    mock_client.chat_postMessage.assert_not_called()


@pytest.mark.asyncio
async def test_post_to_channel_api_error(mock_slack_client: AsyncMock) -> None:
    """Test handling an API error when posting to a channel."""
    mock_response = {"ok": False, "error": "channel_not_found"}
    mock_slack_client.chat_postMessage.return_value = mock_response

    with patch("souzu.slack.thread._CLIENT", mock_slack_client):
        with pytest.raises(
            SlackApiError, match="Failed to post message to channel: channel_not_found"
        ):
            await post_to_channel("test-channel", "Hello, world!")


@pytest.mark.asyncio
async def test_post_to_channel_exception(mock_slack_client: AsyncMock) -> None:
    """Test handling an exception when posting to a channel."""
    mock_slack_client.chat_postMessage.side_effect = Exception("Network error")

    with patch("souzu.slack.thread._CLIENT", mock_slack_client):
        with pytest.raises(
            SlackApiError, match="Failed to post message to channel: Network error"
        ):
            await post_to_channel("test-channel", "Hello, world!")


@pytest.mark.asyncio
async def test_post_to_thread_success(mock_slack_client: AsyncMock) -> None:
    """Test posting to a thread successfully."""
    # Set up mock response
    mock_response = {"ok": True, "ts": "1234.5678"}
    mock_slack_client.chat_postMessage.return_value = mock_response

    # Patch the _CLIENT
    with patch("souzu.slack.thread._CLIENT", mock_slack_client):
        # Call the function
        result = await post_to_thread("test-channel", "9876.5432", "Hello, thread!")

    # Verify results
    assert result == "1234.5678"
    mock_slack_client.chat_postMessage.assert_called_once_with(
        channel="test-channel", thread_ts="9876.5432", text="Hello, thread!"
    )


@pytest.mark.asyncio
async def test_post_to_thread_no_token() -> None:
    """Test posting to a thread with no API token configured."""
    # Patch the _CLIENT to None to simulate no token
    with patch("souzu.slack.thread._CLIENT", None):
        # Call the function and expect an exception
        with pytest.raises(SlackApiError, match="No Slack API token configured"):
            await post_to_thread("test-channel", "9876.5432", "Hello, thread!")


@pytest.mark.asyncio
async def test_post_to_thread_no_channel(caplog: pytest.LogCaptureFixture) -> None:
    """Test posting to a thread with no channel specified."""
    # Set up the logger
    caplog.set_level(logging.DEBUG)

    # Patch the _CLIENT
    mock_client = AsyncMock()
    with patch("souzu.slack.thread._CLIENT", mock_client):
        # Call the function
        result = await post_to_thread(None, "9876.5432", "Hello, thread!")

    # Verify results
    assert result is None
    assert "No channel to post message: Hello, thread!" in caplog.text
    mock_client.chat_postMessage.assert_not_called()


@pytest.mark.asyncio
async def test_post_to_thread_api_error(mock_slack_client: AsyncMock) -> None:
    """Test handling an API error when posting to a thread."""
    # Set up mock response
    mock_response = {"ok": False, "error": "thread_not_found"}
    mock_slack_client.chat_postMessage.return_value = mock_response

    # Patch the _CLIENT
    with patch("souzu.slack.thread._CLIENT", mock_slack_client):
        # Call the function and expect an exception
        with pytest.raises(
            SlackApiError, match="Failed to post message to thread: thread_not_found"
        ):
            await post_to_thread("test-channel", "9876.5432", "Hello, thread!")


@pytest.mark.asyncio
async def test_post_to_thread_exception(mock_slack_client: AsyncMock) -> None:
    """Test handling an exception when posting to a thread."""
    # Set up mock to raise an exception
    mock_slack_client.chat_postMessage.side_effect = Exception("Network error")

    # Patch the _CLIENT
    with patch("souzu.slack.thread._CLIENT", mock_slack_client):
        # Call the function and expect an exception
        with pytest.raises(
            SlackApiError, match="Failed to post message to thread: Network error"
        ):
            await post_to_thread("test-channel", "9876.5432", "Hello, thread!")


@pytest.mark.asyncio
async def test_edit_message_success(mock_slack_client: AsyncMock) -> None:
    """Test editing a message successfully."""
    # Set up mock response
    mock_response = {"ok": True}
    mock_slack_client.chat_update.return_value = mock_response

    # Patch the _CLIENT
    with patch("souzu.slack.thread._CLIENT", mock_slack_client):
        # Call the function
        await edit_message("test-channel", "1234.5678", "Updated message")

    # Verify results
    mock_slack_client.chat_update.assert_called_once_with(
        channel="test-channel", ts="1234.5678", text="Updated message"
    )


@pytest.mark.asyncio
async def test_edit_message_no_token() -> None:
    """Test editing a message with no API token configured."""
    # Patch the _CLIENT to None to simulate no token
    with patch("souzu.slack.thread._CLIENT", None):
        # Call the function and expect an exception
        with pytest.raises(SlackApiError, match="No Slack API token configured"):
            await edit_message("test-channel", "1234.5678", "Updated message")


@pytest.mark.asyncio
async def test_edit_message_no_channel(caplog: pytest.LogCaptureFixture) -> None:
    """Test editing a message with no channel specified."""
    # Set up the logger
    caplog.set_level(logging.DEBUG)

    # Patch the _CLIENT
    mock_client = AsyncMock()
    with patch("souzu.slack.thread._CLIENT", mock_client):
        # Call the function
        await edit_message(None, "1234.5678", "Updated message")

    # Verify results
    assert "No channel to edit message: 1234.5678" in caplog.text
    mock_client.chat_update.assert_not_called()


@pytest.mark.asyncio
async def test_edit_message_api_error(mock_slack_client: AsyncMock) -> None:
    """Test handling an API error when editing a message."""
    # Set up mock response
    mock_response = {"ok": False, "error": "message_not_found"}
    mock_slack_client.chat_update.return_value = mock_response

    # Patch the _CLIENT
    with patch("souzu.slack.thread._CLIENT", mock_slack_client):
        # Call the function and expect an exception
        with pytest.raises(
            SlackApiError, match="Failed to edit message: message_not_found"
        ):
            await edit_message("test-channel", "1234.5678", "Updated message")


@pytest.mark.asyncio
async def test_edit_message_exception(mock_slack_client: AsyncMock) -> None:
    """Test handling an exception when editing a message."""
    # Set up mock to raise an exception
    mock_slack_client.chat_update.side_effect = Exception("Network error")

    # Patch the _CLIENT
    with patch("souzu.slack.thread._CLIENT", mock_slack_client):
        # Call the function and expect an exception
        with pytest.raises(
            SlackApiError, match="Failed to edit message: Network error"
        ):
            await edit_message("test-channel", "1234.5678", "Updated message")
