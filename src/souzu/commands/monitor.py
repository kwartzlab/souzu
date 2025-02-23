import logging
import signal
from asyncio import (
    FIRST_COMPLETED,
    CancelledError,
    Event,
    Queue,
    TaskGroup,
    create_task,
    get_running_loop,
    wait,
)
from contextlib import AsyncExitStack
from datetime import timedelta
from types import FrameType

from xdg_base_dirs import xdg_cache_home

from souzu.bambu.discovery import BambuDevice, discover_bambu_devices
from souzu.bambu.mqtt import BambuMqttConnection
from souzu.job_tracking import monitor_printer_status
from souzu.logs import log_reports

LOG_DIRECTORY = xdg_cache_home() / "souzu/logs"


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
                tg.create_task(
                    log_reports(
                        device,
                        connection,
                        LOG_DIRECTORY / f"{device.filename_prefix}.log",
                    )
                )
                tg.create_task(monitor_printer_status(device, connection))
            except Exception:
                logging.exception(
                    f"Failed to set up subscription for {device.device_name}"
                )
            queue.task_done()


async def monitor() -> None:
    loop = get_running_loop()
    exit_event = Event()

    def exit_handler(sig: int, frame: FrameType | None) -> None:
        exit_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, exit_handler, sig, None)

    try:
        await wait(
            [
                create_task(inner_loop()),
                create_task(exit_event.wait()),
            ],
            return_when=FIRST_COMPLETED,
        )
    except CancelledError:
        pass
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
