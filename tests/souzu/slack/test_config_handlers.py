"""Tests for souzu.slack.config_handlers."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from souzu.slack.client import SlackClient
from souzu.slack.config_handlers import (
    COMMAND,
    EDIT_CALLBACK_ID,
    register_config_handlers,
)

BOT_TOKEN = "xoxb-1234567890-abcdefghijkl"
APP_TOKEN = "xapp-1-A000-1234567890-wxyz"


class _Harness:
    def __init__(self, tmp_path: Path, mocker: MockerFixture, is_admin: bool) -> None:
        self.config_file = tmp_path / "souzu.json"
        self.config_file.write_text(
            json.dumps(
                {
                    "printers": {"SERIAL1": {"access_code": "12345678"}},
                    "slack": {
                        "access_token": BOT_TOKEN,
                        "app_token": APP_TOKEN,
                        "error_notification_channel": "C_ERR",
                    },
                }
            )
        )
        config = mocker.patch("souzu.slack.config_handlers.CONFIG")
        config.slack.admin_user_group = "3dprinterteam"
        config.slack.error_notification_channel = "C_ERR"

        self.auth_test = AsyncMock()
        web_client_cls = mocker.patch("souzu.slack.config_handlers.AsyncWebClient")
        web_client_cls.return_value.auth_test = self.auth_test
        self.web_client_cls = web_client_cls

        self.handlers: dict[str, Any] = {}

        def capture(name: str) -> Any:  # noqa: ANN401
            def decorator(func: Any) -> Any:  # noqa: ANN401
                self.handlers[name] = func
                return func

            return decorator

        mock_app = MagicMock()
        mock_app.command = capture
        mock_app.view = capture
        self.slack = MagicMock(spec=SlackClient)
        self.slack.app = mock_app
        self.slack.is_user_in_group = AsyncMock(return_value=is_admin)
        self.slack.post_to_channel = AsyncMock()
        self.request_restart = MagicMock(spec=lambda: None)
        register_config_handlers(self.slack, self.request_restart, self.config_file)

        self.ack = AsyncMock()
        self.respond = AsyncMock()
        self.client = AsyncMock()

    async def command(self, text: str) -> None:
        await self.handlers[COMMAND](
            ack=self.ack,
            command={"text": text, "user_id": "U_ADMIN", "trigger_id": "T1"},
            respond=self.respond,
            client=self.client,
        )

    async def submit(self, text: str) -> None:
        await self.handlers[EDIT_CALLBACK_ID](
            ack=self.ack,
            body={"user": {"id": "U_ADMIN"}},
            view={"state": {"values": {"config_json": {"value": {"value": text}}}}},
        )

    def shown_json(self) -> dict[str, Any]:
        view = self.client.views_open.call_args.kwargs["view"]
        return json.loads(view["blocks"][0]["element"]["initial_value"])


@pytest.fixture
def admin(tmp_path: Path, mocker: MockerFixture) -> _Harness:
    return _Harness(tmp_path, mocker, is_admin=True)


@pytest.fixture
def non_admin(tmp_path: Path, mocker: MockerFixture) -> _Harness:
    return _Harness(tmp_path, mocker, is_admin=False)


def test_skips_when_no_app() -> None:
    mock_slack = MagicMock(spec=SlackClient)
    mock_slack.app = None
    register_config_handlers(mock_slack, None)


class TestCommand:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["", "help", "config bogus", "config edit now"])
    async def test_unknown_subcommand_shows_usage(
        self, admin: _Harness, text: str
    ) -> None:
        await admin.command(text)
        admin.ack.assert_awaited_once()
        assert "Usage" in admin.respond.call_args.args[0]
        admin.slack.is_user_in_group.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["config", "config show", "config edit"])
    async def test_denies_non_admin(self, non_admin: _Harness, text: str) -> None:
        await non_admin.command(text)
        non_admin.slack.is_user_in_group.assert_awaited_once_with(
            "U_ADMIN", "3dprinterteam"
        )
        non_admin.respond.assert_awaited_once_with("Sorry, this is admin-only.")
        non_admin.client.views_open.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["config", "config show"])
    async def test_show_replies_with_redacted_config(
        self, admin: _Harness, text: str
    ) -> None:
        await admin.command(text)
        reply: str = admin.respond.call_args.args[0]
        assert reply.startswith("```")
        assert "C_ERR" in reply
        assert "SERIAL1" in reply
        for secret in (BOT_TOKEN, APP_TOKEN, "12345678"):
            assert secret not in reply

    @pytest.mark.asyncio
    async def test_edit_opens_modal_with_redacted_config(self, admin: _Harness) -> None:
        await admin.command("config edit")
        kwargs = admin.client.views_open.call_args.kwargs
        assert kwargs["trigger_id"] == "T1"
        assert kwargs["view"]["callback_id"] == EDIT_CALLBACK_ID
        shown = admin.shown_json()
        assert shown["slack"]["access_token"] == "<redacted …ijkl>"
        assert shown["printers"]["SERIAL1"]["access_code"] == "<redacted>"

    @pytest.mark.asyncio
    async def test_edit_refuses_config_too_large_for_modal(
        self, admin: _Harness
    ) -> None:
        printers = {f"SERIAL{i}": {"access_code": "12345678"} for i in range(100)}
        admin.config_file.write_text(json.dumps({"printers": printers}))
        await admin.command("config edit")
        admin.client.views_open.assert_not_awaited()
        assert "too large" in admin.respond.call_args.args[0]


class TestSubmission:
    @pytest.mark.asyncio
    async def test_applies_change_keeps_secrets_and_restarts(
        self, admin: _Harness
    ) -> None:
        await admin.command("config edit")
        edited = admin.shown_json()
        edited["timezone"] = "America/Toronto"

        await admin.submit(json.dumps(edited))

        admin.ack.assert_awaited_with()
        written = json.loads(admin.config_file.read_text())
        assert written["timezone"] == "America/Toronto"
        assert written["slack"]["access_token"] == BOT_TOKEN
        assert written["printers"]["SERIAL1"]["access_code"] == "12345678"
        admin.auth_test.assert_not_awaited()
        admin.request_restart.assert_called_once_with()
        channel, text = admin.slack.post_to_channel.call_args.args
        assert channel == "C_ERR"
        assert "<@U_ADMIN>" in text
        assert "timezone" in text

    @pytest.mark.asyncio
    async def test_no_change_does_not_write_or_restart(self, admin: _Harness) -> None:
        await admin.command("config edit")
        before = admin.config_file.read_text()

        await admin.submit(json.dumps(admin.shown_json()))

        admin.ack.assert_awaited_with()
        assert admin.config_file.read_text() == before
        admin.request_restart.assert_not_called()
        admin.slack.post_to_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_input_returns_modal_error(self, admin: _Harness) -> None:
        before = admin.config_file.read_text()

        await admin.submit('{"timezone": "Mars/Olympus"}')

        kwargs = admin.ack.call_args.kwargs
        assert kwargs["response_action"] == "errors"
        assert "Mars/Olympus" in kwargs["errors"]["config_json"]
        assert len(kwargs["errors"]["config_json"]) <= 150
        assert admin.config_file.read_text() == before
        admin.request_restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_denies_non_admin(self, non_admin: _Harness) -> None:
        before = non_admin.config_file.read_text()

        await non_admin.submit('{"timezone": "America/Toronto"}')

        assert non_admin.ack.call_args.kwargs["response_action"] == "errors"
        assert non_admin.config_file.read_text() == before
        non_admin.request_restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_bot_token_is_checked_before_write(self, admin: _Harness) -> None:
        await admin.command("config edit")
        edited = admin.shown_json()
        edited["slack"]["access_token"] = "xoxb-new-token"
        before = admin.config_file.read_text()
        admin.auth_test.side_effect = RuntimeError("invalid_auth")

        await admin.submit(json.dumps(edited))

        admin.web_client_cls.assert_called_once_with(token="xoxb-new-token")
        assert "invalid_auth" in admin.ack.call_args.kwargs["errors"]["config_json"]
        assert admin.config_file.read_text() == before
        admin.request_restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_new_bot_token_is_written(self, admin: _Harness) -> None:
        await admin.command("config edit")
        edited = admin.shown_json()
        edited["slack"]["access_token"] = "xoxb-new-token"

        await admin.submit(json.dumps(edited))

        admin.auth_test.assert_awaited_once()
        written = json.loads(admin.config_file.read_text())
        assert written["slack"]["access_token"] == "xoxb-new-token"
        assert "xoxb-new-token" not in admin.slack.post_to_channel.call_args.args[1]

    @pytest.mark.asyncio
    async def test_without_restart_hook_tells_admin_to_restart(
        self, admin: _Harness
    ) -> None:
        register_config_handlers(admin.slack, None, admin.config_file)
        await admin.command("config edit")
        edited = admin.shown_json()
        edited["timezone"] = "America/Toronto"

        await admin.submit(json.dumps(edited))

        assert "Restart Souzu" in admin.slack.post_to_channel.call_args.args[1]
        admin.request_restart.assert_not_called()
