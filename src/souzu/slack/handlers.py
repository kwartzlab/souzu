"""Slack interactive event handlers bridging Slack events to domain logic."""

import logging
from typing import TYPE_CHECKING, Any

from souzu.job_tracking import JobRegistry, PrinterState

if TYPE_CHECKING:
    from souzu.slack.client import SlackClient


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
