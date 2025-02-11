import argparse
import signal
from asyncio import (
    FIRST_COMPLETED,
    CancelledError,
    Event,
    create_task,
    get_running_loop,
    run,
    wait,
)
from contextlib import AsyncExitStack
from types import FrameType

from souzu.bambu.mqtt import BambuMqttSubscription


async def print_messages(subscription: BambuMqttSubscription) -> None:
    async for message in subscription.messages:
        print(message.__dict__)  # noqa: T201


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="The IP address of the printer")
    parser.add_argument("device_id", help="The serial number of the printer")
    parser.add_argument("access_code", help="The LAN access code for the printer")
    return parser.parse_args()


async def inner_loop() -> None:
    args = _parse_args()
    async with AsyncExitStack() as stack:
        subscription = await stack.enter_async_context(
            BambuMqttSubscription(args.host, args.device_id, args.access_code)
        )
        await print_messages(subscription)


async def real_main() -> None:
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
