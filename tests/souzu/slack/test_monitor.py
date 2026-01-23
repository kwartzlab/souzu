"""Tests for the Slack thread monitoring module."""

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
