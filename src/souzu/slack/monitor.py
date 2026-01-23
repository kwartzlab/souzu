"""Slack thread monitoring via polling."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
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


OnReplyCallback = Callable[[SlackMessage], Awaitable[None]]


async def watch_thread(
    channel: str,
    thread_ts: str,
    on_reply: OnReplyCallback,
    last_seen_ts: str | None = None,
    poll_interval: timedelta = timedelta(minutes=5),
) -> None:
    """
    Poll a Slack thread for new replies and invoke callback for each.

    Runs until cancelled. Handles API errors by logging and continuing.

    Args:
        channel: Slack channel ID
        thread_ts: Parent message timestamp (thread ID)
        on_reply: Called for each new reply, in chronological order
        last_seen_ts: Skip replies at or before this timestamp
        poll_interval: Time between polls (default 5 minutes)
    """
    seen_ts = last_seen_ts

    while True:
        try:
            replies = await _fetch_replies(channel, thread_ts, after_ts=seen_ts)
        except Exception:
            logging.exception(f"Error polling thread {thread_ts}")
            replies = []

        for msg in replies:
            try:
                await on_reply(msg)
            except Exception:
                logging.exception(f"Error in on_reply callback for message {msg.ts}")
            seen_ts = msg.ts

        await asyncio.sleep(poll_interval.total_seconds())
