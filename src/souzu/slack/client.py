"""Unified Slack client wrapping Bolt's AsyncApp and the SDK's AsyncWebClient."""

import logging
from typing import Any


class SlackApiError(Exception):
    """Raised when a Slack API call fails."""


class SlackClient:
    """Unified Slack client supporting three operating modes.

    - No tokens: all methods are silent no-ops.
    - access_token only: outbound messages work, no event handling.
    - Both tokens: full socket mode with interactive features.
    """

    def __init__(
        self,
        access_token: str | None = None,
        app_token: str | None = None,
    ) -> None:
        self._access_token = access_token
        self._app_token = app_token
        self._web_client: Any | None = None
        self._app: Any | None = None
        self._socket_handler: Any | None = None
        self._bot_user_id: str | None = None

        if access_token and app_token:
            from slack_bolt.adapter.socket_mode.async_handler import (
                AsyncSocketModeHandler,
            )
            from slack_bolt.async_app import AsyncApp

            self._app = AsyncApp(token=access_token)
            self._socket_handler = AsyncSocketModeHandler(self._app, app_token)
            self._web_client = self._app.client
        elif access_token:
            from slack_sdk.web.async_client import AsyncWebClient

            self._web_client = AsyncWebClient(token=access_token)

    async def __aenter__(self) -> "SlackClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start the client. Connects socket mode if available, caches bot_user_id."""
        if self._web_client is not None:
            try:
                response = await self._web_client.auth_test()
                self._bot_user_id = response.get("user_id")
            except Exception as e:
                raise SlackApiError(f"Failed to authenticate with Slack: {e}") from e

        if self._socket_handler is not None:
            await self._socket_handler.connect_async()
        elif self._web_client is not None:
            logging.warning(
                "Slack app_token not configured — running in degraded mode "
                "(outbound messages only, no interactive features)"
            )

    async def stop(self) -> None:
        """Stop the client. Disconnects socket mode if active."""
        if self._socket_handler is not None:
            await self._socket_handler.disconnect_async()

    @property
    def app(self) -> Any | None:  # noqa: ANN401
        """The Bolt AsyncApp, or None if socket mode is not available."""
        return self._app

    @property
    def bot_user_id(self) -> str | None:
        """The bot's Slack user ID, cached after start()."""
        return self._bot_user_id

    async def post_to_channel(
        self,
        channel: str | None,
        text: str,
        blocks: list[Any] | None = None,
    ) -> str | None:
        """Post a message to a channel. Returns the message timestamp."""
        if self._web_client is None:
            return None
        if channel is None:
            logging.debug(f"No channel to post message: {text}")
            return None
        try:
            kwargs: dict[str, Any] = {"channel": channel, "text": text}
            if blocks is not None:
                kwargs["blocks"] = blocks
            response = await self._web_client.chat_postMessage(**kwargs)
        except Exception as e:
            raise SlackApiError(f"Failed to post message to channel: {e}") from e
        if not response.get("ok"):
            raise SlackApiError(
                f"Failed to post message to channel: {response.get('error', 'Unknown error')}"
            )
        return response.get("ts")

    async def post_to_thread(
        self,
        channel: str | None,
        thread_ts: str,
        text: str,
        blocks: list[Any] | None = None,
    ) -> str | None:
        """Post a message to a thread. Returns the message timestamp."""
        if self._web_client is None:
            return None
        if channel is None:
            logging.debug(f"No channel to post message: {text}")
            return None
        try:
            kwargs: dict[str, Any] = {
                "channel": channel,
                "thread_ts": thread_ts,
                "text": text,
            }
            if blocks is not None:
                kwargs["blocks"] = blocks
            response = await self._web_client.chat_postMessage(**kwargs)
        except Exception as e:
            raise SlackApiError(f"Failed to post message to thread: {e}") from e
        if not response.get("ok"):
            raise SlackApiError(
                f"Failed to post message to thread: {response.get('error', 'Unknown error')}"
            )
        return response.get("ts")

    async def edit_message(
        self,
        channel: str | None,
        message_ts: str,
        text: str,
        blocks: list[Any] | None = None,
    ) -> None:
        """Edit an existing message."""
        if self._web_client is None:
            return
        if channel is None:
            logging.debug(f"No channel to edit message: {message_ts}")
            return
        try:
            kwargs: dict[str, Any] = {
                "channel": channel,
                "ts": message_ts,
                "text": text,
            }
            if blocks is not None:
                kwargs["blocks"] = blocks
            response = await self._web_client.chat_update(**kwargs)
        except Exception as e:
            raise SlackApiError(f"Failed to edit message: {e}") from e
        if not response.get("ok"):
            raise SlackApiError(
                f"Failed to edit message: {response.get('error', 'Unknown error')}"
            )
