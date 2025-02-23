import logging

from slack_sdk.web.async_client import AsyncWebClient

from souzu.config import CONFIG

_CLIENT = (
    AsyncWebClient(token=CONFIG.slack.access_token)
    if CONFIG.slack.access_token
    else None
)


class SlackApiError(Exception):
    pass


async def post_to_channel(channel: str | None, message: str) -> str | None:
    """
    Post a message to a channel.

    Return the timestamp of the message (if available) to use for editing or threading.
    """
    try:
        if _CLIENT is None:
            raise SlackApiError("No Slack API token configured")
        if channel is None:
            logging.debug(f"No channel to post message: {message}")
            return None
        response = await _CLIENT.chat_postMessage(
            channel=channel,
            text=message,
        )
    except Exception as e:
        raise SlackApiError(f"Failed to post message to channel: {e}") from e
    if not response.get('ok'):
        raise SlackApiError(
            f"Failed to post message to channel: {response.get('error', 'Unknown error')}"
        )
    return response.get('ts')


async def post_to_thread(
    channel: str | None, thread_ts: str, message: str
) -> str | None:
    """
    Post a message to a thread.

    Return the timestamp of the message (if available) to use for editing.
    """
    try:
        if _CLIENT is None:
            raise SlackApiError("No Slack API token configured")
        if channel is None:
            logging.debug(f"No channel to post message: {message}")
            return None
        response = await _CLIENT.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=message,
        )
    except Exception as e:
        raise SlackApiError(f"Failed to post message to thread: {e}") from e
    if not response.get('ok'):
        raise SlackApiError(
            f"Failed to post message to thread: {response.get('error', 'Unknown error')}"
        )
    return response.get('ts')


async def edit_message(channel: str | None, message_ts: str, message: str) -> None:
    """
    Edit a message.
    """
    try:
        if _CLIENT is None:
            raise SlackApiError("No Slack API token configured")
        if channel is None:
            logging.debug(f"No channel to edit message: {message_ts}")
            return
        response = await _CLIENT.chat_update(
            channel=channel,
            ts=message_ts,
            text=message,
        )
    except Exception as e:
        raise SlackApiError(f"Failed to edit message: {e}") from e
    if not response.get('ok'):
        raise SlackApiError(
            f"Failed to edit message: {response.get('error', 'Unknown error')}"
        )
