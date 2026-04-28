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

    async def _api_call(
        self,
        method: str,
        channel: str | None,
        error_context: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call a Slack API method with common error handling.

        Returns the API response dict, or None if no client or channel.
        """
        if self._web_client is None:
            return None
        if channel is None:
            logging.debug(f"No channel for {error_context}: {params}")
            return None
        params["channel"] = channel
        try:
            api_method = getattr(self._web_client, method)
            response = await api_method(**params)
        except Exception as e:
            raise SlackApiError(f"Failed to {error_context}: {e}") from e
        if not response.get("ok"):
            raise SlackApiError(
                f"Failed to {error_context}: {response.get('error', 'Unknown error')}"
            )
        return response

    async def post_to_channel(
        self,
        channel: str | None,
        text: str,
        blocks: list[Any] | None = None,
    ) -> str | None:
        """Post a message to a channel. Returns the message timestamp."""
        params: dict[str, Any] = {"text": text}
        if blocks is not None:
            params["blocks"] = blocks
        response = await self._api_call(
            "chat_postMessage", channel, "post message to channel", params
        )
        return response.get("ts") if response else None

    async def post_to_thread(
        self,
        channel: str | None,
        thread_ts: str,
        text: str,
        blocks: list[Any] | None = None,
    ) -> str | None:
        """Post a message to a thread. Returns the message timestamp."""
        params: dict[str, Any] = {"thread_ts": thread_ts, "text": text}
        if blocks is not None:
            params["blocks"] = blocks
        response = await self._api_call(
            "chat_postMessage", channel, "post message to thread", params
        )
        return response.get("ts") if response else None

    async def is_user_in_group(self, user_id: str, group_handle: str) -> bool:
        """Whether the given user is a member of the user group with the given handle.

        Returns False if the client has no token, the group doesn't exist, or the
        Slack API call fails.
        """
        if self._web_client is None:
            return False
        try:
            groups_response = await self._web_client.usergroups_list()
        except Exception:
            logging.exception("Failed to list Slack user groups")
            return False
        if not groups_response.get("ok"):
            logging.warning(
                f"usergroups.list failed: {groups_response.get('error', 'unknown')}"
            )
            return False
        group_id: str | None = None
        for group in groups_response.get("usergroups", []):
            if group.get("handle") == group_handle:
                group_id = group.get("id")
                break
        if group_id is None:
            logging.warning(f"Slack user group with handle {group_handle!r} not found")
            return False
        try:
            users_response = await self._web_client.usergroups_users_list(
                usergroup=group_id
            )
        except Exception:
            logging.exception(f"Failed to list users in Slack group {group_handle!r}")
            return False
        if not users_response.get("ok"):
            logging.warning(
                f"usergroups.users.list failed: "
                f"{users_response.get('error', 'unknown')}"
            )
            return False
        return user_id in users_response.get("users", [])

    async def edit_message(
        self,
        channel: str | None,
        message_ts: str,
        text: str,
        blocks: list[Any] | None = None,
    ) -> None:
        """Edit an existing message."""
        params: dict[str, Any] = {"ts": message_ts, "text": text}
        if blocks is not None:
            params["blocks"] = blocks
        await self._api_call("chat_update", channel, "edit message", params)
