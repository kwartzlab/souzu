"""Slack thread monitoring via polling."""

from attrs import frozen


@frozen
class SlackMessage:
    """A message from a Slack thread."""

    ts: str
    text: str
    user: str
