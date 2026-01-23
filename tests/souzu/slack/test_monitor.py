"""Tests for the Slack thread monitoring module."""

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
