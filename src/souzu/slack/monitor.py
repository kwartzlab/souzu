"""Slack thread monitoring via polling."""

from typing import Any

from attrs import frozen
from slack_sdk.web.async_client import AsyncWebClient

from souzu.config import CONFIG

_CLIENT = (
    AsyncWebClient(token=CONFIG.slack.access_token)
    if CONFIG.slack.access_token
    else None
)


@frozen
class SlackMessage:
    """A message from a Slack thread."""

    ts: str
    text: str
    user: str


async def _fetch_replies(
    channel: str,
    thread_ts: str,
    after_ts: str | None = None,
) -> list[SlackMessage]:
    """
    Fetch replies from a Slack thread, optionally filtering to messages after a timestamp.

    Returns messages in chronological order (oldest first).
    """
    if _CLIENT is None:
        return []

    response = await _CLIENT.conversations_replies(
        channel=channel,
        ts=thread_ts,
    )

    messages: list[SlackMessage] = []
    msg: dict[str, Any]
    for msg in response.get("messages", []):
        if msg["ts"] == thread_ts:
            continue
        if after_ts is not None and msg["ts"] <= after_ts:
            continue
        messages.append(
            SlackMessage(
                ts=msg["ts"],
                text=msg.get("text", ""),
                user=msg.get("user", ""),
            )
        )

    return messages
