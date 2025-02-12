from slack_sdk.web.async_client import AsyncWebClient

from souzu.config import SLACK_ACCESS_TOKEN

_CLIENT = AsyncWebClient(token=SLACK_ACCESS_TOKEN)


async def post_to_channel(channel: str | None, message: str) -> None:
    if channel is None:
        return
    await _CLIENT.chat_postMessage(
        channel=channel,
        text=message,
    )
