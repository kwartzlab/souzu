import argparse
import signal
from asyncio import (
    FIRST_COMPLETED,
    CancelledError,
    Event,
    TaskGroup,
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
    parser.add_argument(
        "--device",
        nargs=3,
        dest="devices",
        action="append",
        metavar=("host", "device_id", "access_code"),
        help="Device configuration tuple: host, device_id, access_code",
    )
    args = parser.parse_args()
    return args


async def inner_loop() -> None:
    args = _parse_args()
    async with AsyncExitStack() as stack, TaskGroup() as tg:
        for host, device_id, access_code in args.devices:
            subscription = await stack.enter_async_context(
                BambuMqttSubscription(host, device_id, access_code)
            )
            tg.create_task(print_messages(subscription))


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
