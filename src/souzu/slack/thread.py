from slack_sdk.web.async_client import AsyncWebClient

from souzu.config import SLACK_ACCESS_TOKEN

_CLIENT = AsyncWebClient(token=SLACK_ACCESS_TOKEN)


async def post_to_channel(channel_id: str, message: str) -> None:
    await _CLIENT.chat_postMessage(
        channel=channel_id,
        text=message,
    )
