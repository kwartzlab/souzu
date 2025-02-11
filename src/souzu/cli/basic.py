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
from souzu.bambu.mqtt import BambuMqttSubscription
from souzu.slack.thread import post_to_channel


async def log_messages(
    device: BambuDevice, subscription: BambuMqttSubscription
) -> None:
    async with subscription.subscribe() as messages:
        async for _before, after in messages:
            logging.debug(f"{device.device_name}: {pformat(after)}")


async def log_print_started(
    device: BambuDevice, subscription: BambuMqttSubscription
) -> None:
    running_state: bool | None = None
    async with subscription.subscribe() as messages:
        async for _before, after in messages:
            print_running = after.mc_print_stage == 2
            if print_running:
                if running_state is None:
                    if after.mc_remaining_time is not None:
                        logging.info(
                            f"{device.device_name}: Print already running, {after.mc_remaining_time} minutes remaining"
                        )
                    else:
                        logging.info(f"{device.device_name}: Print already running")
                elif running_state is False:
                    logging.info(
                        f"{device.device_name}: Print started, {after.mc_remaining_time} minutes remaining"
                    )
                running_state = True
            else:
                if running_state:
                    logging.info(f"{device.device_name}: Print stopped")
                running_state = False


async def inner_loop() -> None:
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
                tg.create_task(log_print_started(device, subscription))
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
    if args.slack_channel:
        await post_to_channel(args.slack_channel, "Hello from souzu")
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
