"""Slack slash command that lets admins show and replace the config."""

import json
import logging
from asyncio import to_thread
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from slack_sdk.web.async_client import AsyncWebClient

from souzu.config import CONFIG, CONFIG_FILE
from souzu.config_admin import (
    ConfigUpdateError,
    changed_paths,
    load_config_dict,
    prepare_update,
    redact_config,
    write_config,
)

if TYPE_CHECKING:
    from souzu.slack.client import SlackClient

COMMAND = "/souzu"
EDIT_CALLBACK_ID = "config_edit"
_BLOCK_ID = "config_json"
_ACTION_ID = "value"
# Slack limit for the text of a plain_text_input element.
_MAX_INPUT_LENGTH = 3000
_MAX_ERROR_LENGTH = 150

_USAGE = (
    f"Usage:\n• `{COMMAND} config` — show the current config, with secrets redacted\n"
    f"• `{COMMAND} config edit` — edit the config"
)
_ADMIN_ONLY = "Sorry, this is admin-only."


def _render(config_dict: dict[str, Any]) -> str:
    return json.dumps(redact_config(config_dict), indent=2)


def _build_edit_view(config_json: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": EDIT_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Souzu config"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": _BLOCK_ID,
                "label": {"type": "plain_text", "text": "Config (JSON)"},
                "hint": {
                    "type": "plain_text",
                    "text": (
                        "Leave a <redacted> placeholder as it is to keep that "
                        "secret. Souzu restarts after you save."
                    ),
                },
                "element": {
                    "type": "plain_text_input",
                    "action_id": _ACTION_ID,
                    "multiline": True,
                    "max_length": _MAX_INPUT_LENGTH,
                    "initial_value": config_json,
                },
            },
        ],
    }


async def _check_bot_token(token: str) -> None:
    try:
        await AsyncWebClient(token=token).auth_test()
    except Exception as e:
        raise ConfigUpdateError(
            f"Slack rejected the new slack.access_token: {e}"
        ) from e


def register_config_handlers(
    slack: "SlackClient",
    request_restart: Callable[[], None] | None,
    config_file: Path = CONFIG_FILE,
) -> None:
    """Register the /souzu slash command and the config edit modal.

    ``request_restart`` is called after a successful update. Pass None when
    nothing will restart the process; the admin is then told to restart it.

    Does nothing if socket mode is not available (slack.app is None).
    """
    if slack.app is None:
        return

    async def _is_admin(user_id: str) -> bool:
        return await slack.is_user_in_group(user_id, CONFIG.slack.admin_user_group)

    @slack.app.command(COMMAND)
    async def handle_command(
        ack: Any,  # noqa: ANN401
        command: Any,  # noqa: ANN401
        respond: Any,  # noqa: ANN401
        client: Any,  # noqa: ANN401
    ) -> None:
        await ack()

        args: list[str] = command.get("text", "").split()
        if not args or args[0] != "config" or args[1:] not in ([], ["show"], ["edit"]):
            await respond(_USAGE)
            return

        if not await _is_admin(command["user_id"]):
            await respond(_ADMIN_ONLY)
            return

        try:
            config_json = _render(await to_thread(load_config_dict, config_file))
        except Exception:
            logging.exception("Failed to load config for Slack command")
            await respond("Failed to read the config file.")
            return

        if args[1:] != ["edit"]:
            await respond(f"```\n{config_json}\n```")
            return

        if len(config_json) > _MAX_INPUT_LENGTH:
            await respond("The config is too large to edit over Slack.")
            return
        try:
            await client.views_open(
                trigger_id=command["trigger_id"],
                view=_build_edit_view(config_json),
            )
        except Exception:
            logging.exception("Failed to open config edit modal")
            await respond("Failed to open the config editor.")

    @slack.app.view(EDIT_CALLBACK_ID)
    async def handle_submission(
        ack: Any,  # noqa: ANN401
        body: Any,  # noqa: ANN401
        view: Any,  # noqa: ANN401
    ) -> None:
        user_id: str = body["user"]["id"]

        async def _reject(message: str) -> None:
            await ack(
                response_action="errors",
                errors={_BLOCK_ID: message[:_MAX_ERROR_LENGTH]},
            )

        # The modal can stay open for a long time, so check again on submit.
        if not await _is_admin(user_id):
            await _reject(_ADMIN_ONLY)
            return

        text: str = view["state"]["values"][_BLOCK_ID][_ACTION_ID]["value"] or ""
        try:
            current = await to_thread(load_config_dict, config_file)
            new = prepare_update(text, current)
            new_token = new["slack"]["access_token"]
            if new_token and new_token != (current.get("slack") or {}).get(
                "access_token"
            ):
                await _check_bot_token(new_token)
            changes = changed_paths(current, new)
            if changes:
                await to_thread(write_config, new, config_file)
        except ConfigUpdateError as e:
            await _reject(str(e))
            return
        except Exception:
            logging.exception("Failed to apply config update from Slack")
            await _reject("Failed to write the config file. See the logs.")
            return

        await ack()
        if not changes:
            return

        logging.info(f"Config updated by {user_id}: {', '.join(changes)}")
        outcome = (
            "Restarting."
            if request_restart is not None
            else "Restart Souzu to apply the change."
        )
        try:
            await slack.post_to_channel(
                CONFIG.slack.error_notification_channel,
                f"Config updated by <@{user_id}> ({', '.join(changes)}). {outcome}",
            )
        except Exception:
            logging.exception("Failed to post config update audit message")
        if request_restart is not None:
            request_restart()
