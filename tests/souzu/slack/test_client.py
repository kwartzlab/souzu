"""Tests for souzu.slack.client."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from souzu.slack.client import SlackApiError, SlackClient


class TestNoTokenMode:
    """Tests for SlackClient with no tokens."""

    def test_app_is_none(self) -> None:
        client = SlackClient()
        assert client.app is None

    @pytest.mark.asyncio
    async def test_post_to_channel_returns_none(self) -> None:
        client = SlackClient()
        async with client:
            result = await client.post_to_channel("C123", "hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_post_to_thread_returns_none(self) -> None:
        client = SlackClient()
        async with client:
            result = await client.post_to_thread("C123", "1234.5678", "hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_edit_message_is_noop(self) -> None:
        client = SlackClient()
        async with client:
            await client.edit_message("C123", "1234.5678", "updated")

    @pytest.mark.asyncio
    async def test_bot_user_id_is_none(self) -> None:
        client = SlackClient()
        async with client:
            assert client.bot_user_id is None


class TestAccessTokenOnlyMode:
    """Tests for SlackClient with access_token only."""

    @pytest.fixture
    def mock_web_client(self, mocker: MockerFixture) -> MagicMock:
        mock_cls = mocker.patch(
            "slack_sdk.web.async_client.AsyncWebClient",
        )
        mock_instance = MagicMock()
        mock_instance.auth_test = AsyncMock(return_value={"user_id": "U_BOT"})
        mock_instance.chat_postMessage = AsyncMock(
            return_value={"ok": True, "ts": "1234.5678"}
        )
        mock_instance.chat_update = AsyncMock(return_value={"ok": True})
        mock_cls.return_value = mock_instance
        return mock_instance

    def test_app_is_none(self, mock_web_client: MagicMock) -> None:
        client = SlackClient(access_token="xoxb-test")
        assert client.app is None

    @pytest.mark.asyncio
    async def test_start_caches_bot_user_id(self, mock_web_client: MagicMock) -> None:
        client = SlackClient(access_token="xoxb-test")
        async with client:
            assert client.bot_user_id == "U_BOT"
        mock_web_client.auth_test.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_raises_on_auth_failure(
        self, mock_web_client: MagicMock
    ) -> None:
        mock_web_client.auth_test = AsyncMock(side_effect=RuntimeError("auth failed"))
        client = SlackClient(access_token="xoxb-test")
        with pytest.raises(SlackApiError, match="Failed to authenticate"):
            await client.start()

    @pytest.mark.asyncio
    async def test_post_to_channel_success(self, mock_web_client: MagicMock) -> None:
        client = SlackClient(access_token="xoxb-test")
        async with client:
            ts = await client.post_to_channel("C123", "hello")
        assert ts == "1234.5678"
        mock_web_client.chat_postMessage.assert_awaited_once_with(
            channel="C123", text="hello"
        )

    @pytest.mark.asyncio
    async def test_post_to_channel_with_blocks(
        self, mock_web_client: MagicMock
    ) -> None:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
        client = SlackClient(access_token="xoxb-test")
        async with client:
            ts = await client.post_to_channel("C123", "hello", blocks=blocks)
        assert ts == "1234.5678"
        mock_web_client.chat_postMessage.assert_awaited_once_with(
            channel="C123", text="hello", blocks=blocks
        )

    @pytest.mark.asyncio
    async def test_post_to_channel_none_channel(
        self, mock_web_client: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = SlackClient(access_token="xoxb-test")
        async with client:
            with caplog.at_level(logging.DEBUG):
                result = await client.post_to_channel(None, "hello")
        assert result is None
        assert "No channel to post message" in caplog.text
        mock_web_client.chat_postMessage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_post_to_channel_api_error(self, mock_web_client: MagicMock) -> None:
        mock_web_client.chat_postMessage = AsyncMock(
            return_value={"ok": False, "error": "channel_not_found"}
        )
        client = SlackClient(access_token="xoxb-test")
        async with client:
            with pytest.raises(SlackApiError, match="channel_not_found"):
                await client.post_to_channel("C123", "hello")

    @pytest.mark.asyncio
    async def test_post_to_thread_success(self, mock_web_client: MagicMock) -> None:
        client = SlackClient(access_token="xoxb-test")
        async with client:
            ts = await client.post_to_thread("C123", "1111.2222", "reply")
        assert ts == "1234.5678"
        mock_web_client.chat_postMessage.assert_awaited_once_with(
            channel="C123", thread_ts="1111.2222", text="reply"
        )

    @pytest.mark.asyncio
    async def test_edit_message_success(self, mock_web_client: MagicMock) -> None:
        client = SlackClient(access_token="xoxb-test")
        async with client:
            await client.edit_message("C123", "1234.5678", "updated")
        mock_web_client.chat_update.assert_awaited_once_with(
            channel="C123", ts="1234.5678", text="updated"
        )

    @pytest.mark.asyncio
    async def test_start_logs_degraded_mode_warning(
        self, mock_web_client: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = SlackClient(access_token="xoxb-test")
        with caplog.at_level(logging.WARNING):
            async with client:
                pass
        assert "degraded mode" in caplog.text


class TestFullMode:
    """Tests for SlackClient with both access_token and app_token."""

    @pytest.fixture
    def mock_app(self, mocker: MockerFixture) -> MagicMock:
        mock_app_cls = mocker.patch("slack_bolt.async_app.AsyncApp")
        mock_app_instance = MagicMock()
        mock_app_instance.client = MagicMock()
        mock_app_instance.client.auth_test = AsyncMock(
            return_value={"user_id": "U_BOT"}
        )
        mock_app_cls.return_value = mock_app_instance
        return mock_app_instance

    @pytest.fixture
    def mock_socket_handler(self, mocker: MockerFixture) -> MagicMock:
        mock_handler_cls = mocker.patch(
            "slack_bolt.adapter.socket_mode.async_handler.AsyncSocketModeHandler"
        )
        mock_handler_instance = MagicMock()
        mock_handler_instance.connect_async = AsyncMock()
        mock_handler_instance.disconnect_async = AsyncMock()
        mock_handler_cls.return_value = mock_handler_instance
        return mock_handler_instance

    def test_app_is_not_none(
        self, mock_app: MagicMock, mock_socket_handler: MagicMock
    ) -> None:
        client = SlackClient(access_token="xoxb-test", app_token="xapp-test")
        assert client.app is not None

    @pytest.mark.asyncio
    async def test_start_connects_socket_mode(
        self, mock_app: MagicMock, mock_socket_handler: MagicMock
    ) -> None:
        client = SlackClient(access_token="xoxb-test", app_token="xapp-test")
        async with client:
            mock_socket_handler.connect_async.assert_awaited_once()
            assert client.bot_user_id == "U_BOT"
        mock_socket_handler.disconnect_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_disconnects_on_exception(
        self, mock_app: MagicMock, mock_socket_handler: MagicMock
    ) -> None:
        client = SlackClient(access_token="xoxb-test", app_token="xapp-test")
        with pytest.raises(RuntimeError, match="boom"):
            async with client:
                raise RuntimeError("boom")
        mock_socket_handler.disconnect_async.assert_awaited_once()
