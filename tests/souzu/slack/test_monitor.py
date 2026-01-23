"""Tests for the Slack thread monitoring module."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from souzu.slack.monitor import SlackMessage


def test_slack_message_creation() -> None:
    """Test creating a SlackMessage instance."""
    msg = SlackMessage(ts="1234.5678", text="Hello world", user="U12345")
    assert msg.ts == "1234.5678"
    assert msg.text == "Hello world"
    assert msg.user == "U12345"


def test_slack_message_is_frozen() -> None:
    """Test that SlackMessage is immutable."""
    msg = SlackMessage(ts="1234.5678", text="Hello world", user="U12345")
    with pytest.raises(AttributeError):
        msg.text = "Changed"  # type: ignore[misc]


@pytest.fixture
def mock_slack_client() -> AsyncMock:
    """Create a mock Slack client for testing."""
    mock_client = AsyncMock()
    mock_client.conversations_replies = AsyncMock()
    return mock_client


@pytest.mark.asyncio
async def test_fetch_replies_returns_empty_when_no_client() -> None:
    """Test that _fetch_replies returns empty list when client is None."""
    from souzu.slack.monitor import _fetch_replies

    with patch("souzu.slack.monitor._CLIENT", None):
        result = await _fetch_replies("C123", "1234.5678")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_replies_filters_parent_message(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that _fetch_replies filters out the parent message."""
    from souzu.slack.monitor import _fetch_replies

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent message", "user": "U111"},
            {"ts": "1234.5679", "text": "Reply 1", "user": "U222"},
        ]
    }

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        result = await _fetch_replies("C123", "1234.5678")

    assert len(result) == 1
    assert result[0].ts == "1234.5679"
    assert result[0].text == "Reply 1"
    assert result[0].user == "U222"


@pytest.mark.asyncio
async def test_fetch_replies_filters_already_seen(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that _fetch_replies filters messages at or before after_ts."""
    from souzu.slack.monitor import _fetch_replies

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent", "user": "U111"},
            {"ts": "1234.5679", "text": "Old reply", "user": "U222"},
            {"ts": "1234.5680", "text": "New reply", "user": "U333"},
        ]
    }

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        result = await _fetch_replies("C123", "1234.5678", after_ts="1234.5679")

    assert len(result) == 1
    assert result[0].ts == "1234.5680"
    assert result[0].text == "New reply"


@pytest.mark.asyncio
async def test_fetch_replies_handles_missing_fields(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that _fetch_replies handles messages with missing optional fields."""
    from souzu.slack.monitor import _fetch_replies

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678"},  # Parent, missing text and user
            {"ts": "1234.5679"},  # Reply, missing text and user
        ]
    }

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        result = await _fetch_replies("C123", "1234.5678")

    assert len(result) == 1
    assert result[0].text == ""
    assert result[0].user == ""


@pytest.mark.asyncio
async def test_watch_thread_calls_callback_for_new_replies(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that watch_thread calls on_reply for each new message."""
    from souzu.slack.monitor import watch_thread

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent", "user": "U111"},
            {"ts": "1234.5679", "text": "Reply 1", "user": "U222"},
            {"ts": "1234.5680", "text": "Reply 2", "user": "U333"},
        ]
    }

    received_messages: list[SlackMessage] = []

    async def on_reply(msg: SlackMessage) -> None:
        received_messages.append(msg)

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        task = asyncio.create_task(
            watch_thread(
                "C123",
                "1234.5678",
                on_reply,
                poll_interval=timedelta(seconds=0.01),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(received_messages) >= 2
    assert received_messages[0].text == "Reply 1"
    assert received_messages[1].text == "Reply 2"


@pytest.mark.asyncio
async def test_watch_thread_skips_already_seen_messages(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that watch_thread respects last_seen_ts."""
    from souzu.slack.monitor import watch_thread

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent", "user": "U111"},
            {"ts": "1234.5679", "text": "Old reply", "user": "U222"},
            {"ts": "1234.5680", "text": "New reply", "user": "U333"},
        ]
    }

    received_messages: list[SlackMessage] = []

    async def on_reply(msg: SlackMessage) -> None:
        received_messages.append(msg)

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        task = asyncio.create_task(
            watch_thread(
                "C123",
                "1234.5678",
                on_reply,
                last_seen_ts="1234.5679",
                poll_interval=timedelta(seconds=0.01),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(received_messages) >= 1
    assert all(msg.text == "New reply" for msg in received_messages)


@pytest.mark.asyncio
async def test_watch_thread_continues_on_api_error(
    mock_slack_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that watch_thread logs and continues on API errors."""
    import logging

    from souzu.slack.monitor import watch_thread

    caplog.set_level(logging.ERROR)
    call_count = 0

    async def mock_replies(*args: object, **kwargs: object) -> dict[str, list[object]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("API error")
        return {"messages": [{"ts": "1234.5678", "text": "Parent", "user": "U111"}]}

    mock_slack_client.conversations_replies.side_effect = mock_replies

    received_messages: list[SlackMessage] = []

    async def on_reply(msg: SlackMessage) -> None:
        received_messages.append(msg)

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        task = asyncio.create_task(
            watch_thread(
                "C123",
                "1234.5678",
                on_reply,
                poll_interval=timedelta(seconds=0.01),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert "Error polling thread" in caplog.text
    assert call_count >= 2


@pytest.mark.asyncio
async def test_watch_thread_continues_on_callback_error(
    mock_slack_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that watch_thread logs and continues when callback raises."""
    import logging

    from souzu.slack.monitor import watch_thread

    caplog.set_level(logging.ERROR)

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent", "user": "U111"},
            {"ts": "1234.5679", "text": "Reply 1", "user": "U222"},
            {"ts": "1234.5680", "text": "Reply 2", "user": "U333"},
        ]
    }

    received_messages: list[SlackMessage] = []

    async def on_reply(msg: SlackMessage) -> None:
        if msg.ts == "1234.5679":
            raise Exception("Callback error")
        received_messages.append(msg)

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        task = asyncio.create_task(
            watch_thread(
                "C123",
                "1234.5678",
                on_reply,
                poll_interval=timedelta(seconds=0.01),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert "Error in on_reply callback" in caplog.text
    assert any(msg.text == "Reply 2" for msg in received_messages)
