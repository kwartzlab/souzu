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

from prettyprinter import install_extras, pformat

from souzu.bambu.discovery import BambuDevice, discover_bambu_devices
from souzu.bambu.mqtt import BambuMqttSubscription, BambuStatusReport
from souzu.slack.thread import post_to_channel


async def log_messages(
    device: BambuDevice, subscription: BambuMqttSubscription
) -> None:
    try:
        async with subscription.subscribe() as messages:
            async for _before, after in messages:
                logging.debug(f"{device.device_name}: {pformat(after)}")
    except Exception:
        logging.exception(f"Logger task failed for {device.device_name}")


def is_printing(state: BambuStatusReport) -> bool:
    return state.print_type != 'idle' and bool(state.mc_remaining_time)


async def report_print_started(
    device: BambuDevice, subscription: BambuMqttSubscription, slack_channel: str | None
) -> None:
    try:
        async with subscription.subscribe() as messages:
            while True:
                # wait for print to start
                async for _before, after in messages:
                    if is_printing(after):
                        logging.info(
                            f"{device.device_name}: Print started, {after.mc_remaining_time} minutes remaining"
                        )
                        if slack_channel is not None:
                            await post_to_channel(
                                slack_channel,
                                f"{device.device_name}: Print started, {after.mc_remaining_time} minutes remaining",
                            )
                        break
                async for _before, after in messages:
                    if not is_printing(after):
                        logging.info(f"{device.device_name}: Print stopped")
                        if slack_channel is not None:
                            await post_to_channel(
                                slack_channel,
                                f"{device.device_name}: Print stopped",
                            )
                        break
    except Exception:
        logging.exception(f"Print monitor task failed for {device.device_name}")


async def inner_loop(slack_channel: str | None) -> None:
    queue = Queue[BambuDevice]()
    async with TaskGroup() as tg, AsyncExitStack() as stack:
        tg.create_task(discover_bambu_devices(queue, max_time=timedelta(minutes=1)))
        while True:
            device = await queue.get()
            logging.info(f"Found device {device.device_name} at {device.ip_address}")
            try:
                subscription = BambuMqttSubscription(tg, device)
                await stack.enter_async_context(subscription)
                tg.create_task(log_messages(device, subscription))
                tg.create_task(
                    report_print_started(device, subscription, slack_channel)
                )
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
    parser.add_argument("--slack-channel", help="Slack channel to post to")
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
                create_task(inner_loop(args.slack_channel)),
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
