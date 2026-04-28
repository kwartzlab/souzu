"""Slack interactive event handlers bridging Slack events to domain logic."""

import logging
from typing import TYPE_CHECKING, Any

from aiomqtt import MqttError

from souzu.config import CONFIG
from souzu.job_tracking import (
    JobAction,
    JobRegistry,
    PrinterState,
    PrintJob,
    available_actions,
    build_actions_blocks,
)

_ACTION_NAMES: dict[JobAction, str] = {
    JobAction.PAUSE: "Pause",
    JobAction.RESUME: "Resume",
    JobAction.CANCEL: "Cancel",
}

if TYPE_CHECKING:
    from souzu.slack.client import SlackClient


def can_control_job(user_id: str, job: PrintJob) -> bool:
    """Whether this user is allowed to control this job."""
    return job.owner is not None and job.owner == user_id


def register_job_handlers(slack: "SlackClient", job_registry: JobRegistry) -> None:
    """Register interactive handlers on the Bolt app for job-related actions.

    Does nothing if socket mode is not available (slack.app is None).
    """
    if slack.app is None:
        return

    @slack.app.action("claim_print")
    async def handle_claim(ack: Any, body: Any, client: Any) -> None:  # noqa: ANN401
        await ack()

        user_id: str = body["user"]["id"]
        user_name: str = body["user"].get("name", user_id)
        message: dict[str, Any] = body.get("message", {})
        thread_ts: str | None = message.get("ts")

        if thread_ts is None or thread_ts not in job_registry:
            logging.warning(f"Claim action for unknown job: {thread_ts}")
            return

        state: PrinterState = job_registry[thread_ts]
        if state.current_job is None:
            return

        job = state.current_job
        if job.owner is not None:
            await client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text=f"This print was already claimed by <@{job.owner}>.",
                thread_ts=thread_ts,
            )
            return

        job.owner = user_id
        logging.info(f"Print claimed by {user_name} ({user_id})")

        channel_id = body["channel"]["id"]
        message_text = message.get("text", "Print job")

        # Update the parent message to show claim and remove the button
        from souzu.job_tracking import _build_status_blocks

        claimed_blocks = _build_status_blocks(message_text, user_id)
        await client.chat_update(
            channel=channel_id,
            ts=thread_ts,
            text=message_text,
            blocks=claimed_blocks,
        )

        # Update the actions message now that the job is claimed
        actions = available_actions(job)
        if actions and job.actions_ts is not None:
            action_blocks = build_actions_blocks(actions)
            try:
                await client.chat_update(
                    channel=channel_id,
                    ts=job.actions_ts,
                    text="Actions",
                    blocks=action_blocks,
                )
            except Exception:
                logging.warning("Failed to update actions message after claim")
        elif actions:
            try:
                actions_ts = await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="Actions",
                    blocks=build_actions_blocks(actions),
                )
                job.actions_ts = actions_ts.get("ts")
            except Exception:
                logging.warning("Failed to post actions message after claim")

        # Post in-thread so the claimant gets notified of replies
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f"<@{user_id}> claimed this print.",
        )

    def _make_action_handler(
        bound_action_id: str,
    ) -> Any:  # noqa: ANN401
        async def handle_action(
            ack: Any,  # noqa: ANN401
            body: Any,  # noqa: ANN401
            client: Any,  # noqa: ANN401
        ) -> None:
            await ack()

            user_id: str = body["user"]["id"]
            message: dict[str, Any] = body.get("message", {})
            parent_ts: str | None = message.get("thread_ts")

            if parent_ts is None or parent_ts not in job_registry:
                return

            state = job_registry[parent_ts]
            if state.current_job is None:
                return

            job = state.current_job

            action_value = bound_action_id.removeprefix("print_")
            try:
                action = JobAction(action_value)
            except ValueError:
                return

            async def _ephemeral(text: str) -> None:
                try:
                    await client.chat_postEphemeral(
                        channel=body["channel"]["id"],
                        user=user_id,
                        text=text,
                        thread_ts=parent_ts,
                    )
                except Exception:
                    logging.exception("Failed to post ephemeral message")

            if action not in available_actions(job):
                await _ephemeral("This action isn't available right now.")
                return

            if not can_control_job(user_id, job):
                await _ephemeral("Sorry, this isn't your print.")
                return

            # Photo: capture and upload a camera snapshot
            if action == JobAction.PHOTO:
                camera = state.camera_client()
                if camera is None:
                    await _ephemeral(
                        "Couldn't capture a photo — the camera may be unavailable."
                    )
                    return

                try:
                    jpeg_data = await camera.capture_frame()
                except Exception:
                    logging.exception("Failed to capture camera frame")
                    await _ephemeral(
                        "Couldn't capture a photo — the camera may be unavailable."
                    )
                    return

                try:
                    channel = job.slack_channel or body["channel"]["id"]
                    thread = job.slack_thread_ts or parent_ts
                    await client.files_upload_v2(
                        channel=channel,
                        thread_ts=thread,
                        content=jpeg_data,
                        filename="snapshot.jpg",
                    )
                except Exception:
                    logging.exception("Failed to upload camera snapshot to Slack")
                    await _ephemeral("Captured a photo but failed to upload it.")
                return

            # Dispatch MQTT command
            if state.connection is None:
                await _ephemeral("Printer is offline.")
                return

            command_method = {
                JobAction.PAUSE: state.connection.pause,
                JobAction.RESUME: state.connection.resume,
                JobAction.CANCEL: state.connection.stop,
            }.get(action)

            if command_method is None:
                return

            try:
                await command_method()
            except (RuntimeError, MqttError):
                logging.exception(f"Failed to send {action.value} command to printer")
                await _ephemeral("Failed to send command to printer.")
                return

            # Post non-ephemeral audit trail message
            action_name = _ACTION_NAMES.get(action, action.value.capitalize())
            channel = job.slack_channel or body["channel"]["id"]
            thread = job.slack_thread_ts or parent_ts
            try:
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread,
                    text=f"{action_name} requested by <@{user_id}>",
                )
            except Exception:
                logging.exception("Failed to post audit trail message")

        return handle_action

    for action_id in [f"print_{action.value}" for action in JobAction]:
        slack.app.action(action_id)(_make_action_handler(action_id))


def register_admin_check_handler(slack: "SlackClient") -> None:
    """Register the check_admin button handler used on the startup notification.

    Does nothing if socket mode is not available (slack.app is None).
    """
    if slack.app is None:
        return

    @slack.app.action("check_admin")
    async def handle_check_admin(
        ack: Any,  # noqa: ANN401
        body: Any,  # noqa: ANN401
        client: Any,  # noqa: ANN401
    ) -> None:
        await ack()

        user_id: str = body["user"]["id"]
        channel_id: str = body["channel"]["id"]
        message_ts: str = body["message"]["ts"]

        group_handle = CONFIG.slack.admin_user_group
        is_admin = await slack.is_user_in_group(user_id, group_handle)
        verb = "is" if is_admin else "is not"
        try:
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
                text=f"<@{user_id}> {verb} an admin",
            )
        except Exception:
            logging.exception("Failed to post admin check result")
