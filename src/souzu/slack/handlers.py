"""Slack interactive event handlers bridging Slack events to domain logic."""

import logging
from typing import TYPE_CHECKING, Any

from souzu.job_tracking import (
    JobAction,
    JobRegistry,
    PrinterState,
    PrintJob,
    available_actions,
)

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
            logging.info(f"Action handler invoked: {bound_action_id}")

            user_id: str = body["user"]["id"]
            message: dict[str, Any] = body.get("message", {})
            parent_ts: str | None = message.get("thread_ts")

            logging.info(
                f"Action lookup: parent_ts={parent_ts}, "
                f"registry_keys={list(job_registry.keys())}"
            )

            if parent_ts is None or parent_ts not in job_registry:
                logging.warning(
                    f"Action {bound_action_id}: no match for parent_ts={parent_ts}"
                )
                return

            state = job_registry[parent_ts]
            if state.current_job is None:
                logging.warning(f"Action {bound_action_id}: no current job")
                return

            job = state.current_job

            action_value = bound_action_id.removeprefix("print_")
            try:
                action = JobAction(action_value)
            except ValueError:
                logging.warning(f"Action {bound_action_id}: invalid action value")
                return

            actions = available_actions(job)
            logging.info(
                f"Action {bound_action_id}: job.state={job.state}, "
                f"available={actions}, owner={job.owner}, user={user_id}"
            )

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

            if action not in actions:
                await _ephemeral("This action isn't available right now.")
                return

            if not can_control_job(user_id, job):
                await _ephemeral("Sorry, this isn't your print.")
                return

            logging.info(f"Action {bound_action_id}: sending stub response")
            await _ephemeral("Sorry, this isn't implemented yet, but stay tuned!")

        return handle_action

    for action_id in [f"print_{action.value}" for action in JobAction]:
        slack.app.action(action_id)(_make_action_handler(action_id))
