import argparse
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
    run,
    wait,
)
from contextlib import AsyncExitStack
from datetime import timedelta
from types import FrameType

from prettyprinter import install_extras
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    return parser.parse_args()


async def real_main() -> None:
    args = _parse_args()
    install_extras(frozenset({'attrs'}))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
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


def main() -> None:
    run(real_main())


if __name__ == "__main__":
    main()
