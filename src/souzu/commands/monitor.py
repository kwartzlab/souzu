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
from souzu.job_tracking import monitor_printer_status
from souzu.logs import log_reports
from souzu.slack.monitor import SlackMessage, watch_thread
from souzu.slack.thread import post_to_channel, post_to_thread


async def notify_startup() -> str | None:
    """Post a startup notification to Slack. Returns message ts, or None on failure."""
    try:
        souzu_version = version("souzu")
    except PackageNotFoundError:
        souzu_version = "unknown"

    try:
        return await post_to_channel(
            CONFIG.slack.error_notification_channel,
            f"Souzu {souzu_version} started",
        )
    except Exception:
        logging.exception("Failed to post startup notification")
        return None


async def inner_loop() -> None:
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
                tg.create_task(monitor_printer_status(device, connection))
            except Exception:
                logging.exception(
                    f"Failed to set up subscription for {device.device_name}"
                )
            queue.task_done()


async def monitor() -> None:
    startup_ts = await notify_startup()

    loop = get_running_loop()
    exit_event = Event()

    def exit_handler(sig: int, frame: FrameType | None) -> None:
        exit_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, exit_handler, sig, None)

    tasks: list[Task[object]] = [
        create_task(inner_loop()),
        create_task(exit_event.wait()),
    ]
    if startup_ts and CONFIG.slack.error_notification_channel:

        async def on_startup_reply(msg: SlackMessage) -> None:
            await post_to_thread(
                CONFIG.slack.error_notification_channel,
                startup_ts,
                f"I saw this message: {msg.text}",
            )

        tasks.append(
            create_task(
                watch_thread(
                    CONFIG.slack.error_notification_channel,
                    startup_ts,
                    on_startup_reply,
                )
            )
        )

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
