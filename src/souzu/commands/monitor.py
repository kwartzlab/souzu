import logging
import signal
from asyncio import (
    ALL_COMPLETED,
    FIRST_COMPLETED,
    CancelledError,
    Event,
    Queue,
    Task,
    TaskGroup,
    create_task,
    get_running_loop,
    wait,
)
from contextlib import AsyncExitStack
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from types import FrameType

from souzu.bambu.discovery import BambuDevice, discover_bambu_devices
from souzu.bambu.mqtt import BambuMqttConnection
from souzu.config import CONFIG
from souzu.job_tracking import JobRegistry, monitor_printer_status
from souzu.logs import log_reports
from souzu.slack.client import SlackClient
from souzu.slack.handlers import register_admin_check_handler, register_job_handlers


def _build_startup_blocks(text: str) -> list[dict[str, object]]:
    """Build Block Kit blocks for the startup message, including the admin probe."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Check admin"},
                    "action_id": "check_admin",
                },
            ],
        },
    ]


async def notify_startup(slack: SlackClient) -> str | None:
    """Post a startup notification to Slack. Returns message ts, or None on failure."""
    try:
        souzu_version = version("souzu")
    except PackageNotFoundError:
        souzu_version = "unknown"

    text = f"Souzu {souzu_version} started"
    try:
        return await slack.post_to_channel(
            CONFIG.slack.error_notification_channel,
            text,
            blocks=_build_startup_blocks(text),
        )
    except Exception:
        logging.exception("Failed to post startup notification")
        return None


async def inner_loop(slack: SlackClient, job_registry: JobRegistry) -> None:
    queue = Queue[BambuDevice]()
    async with TaskGroup() as tg, AsyncExitStack() as stack:
        tg.create_task(discover_bambu_devices(queue, max_time=timedelta(minutes=1)))
        while True:
            device = await queue.get()
            logging.info(f"Found device {device.device_name} at {device.ip_address}")
            try:
                connection = await stack.enter_async_context(
                    BambuMqttConnection(tg, device)
                )
                tg.create_task(log_reports(device, connection))
                tg.create_task(
                    monitor_printer_status(device, connection, slack, job_registry)
                )
            except Exception:
                logging.exception(
                    f"Failed to set up subscription for {device.device_name}"
                )
            queue.task_done()


async def monitor() -> None:
    job_registry: JobRegistry = {}

    async with SlackClient(
        access_token=CONFIG.slack.access_token,
        app_token=CONFIG.slack.app_token,
    ) as slack:
        if slack.app:
            register_job_handlers(slack, job_registry)
            register_admin_check_handler(slack)

        await notify_startup(slack)

        loop = get_running_loop()
        exit_event = Event()

        def exit_handler(sig: int, frame: FrameType | None) -> None:
            exit_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, exit_handler, sig, None)

        tasks: list[Task[object]] = [
            create_task(inner_loop(slack, job_registry)),
            create_task(exit_event.wait()),
        ]

        try:
            done, pending = await wait(tasks, return_when=FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await wait(pending, return_when=ALL_COMPLETED)
        except CancelledError:
            pass
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
