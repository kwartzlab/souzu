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
from types import FrameType

from souzu.bambu.discovery import BambuDevice, discover_bambu_devices
from souzu.bambu.mqtt import BambuMqttSubscription
from souzu.config import BAMBU_ACCESS_CODES


async def print_messages(host: str, device_id: str) -> None:
    access_code = BAMBU_ACCESS_CODES.get(device_id)
    if not access_code:
        logging.error(f"No access code for device {device_id}")
        return
    async with BambuMqttSubscription(host, device_id, access_code) as subscription:
        async for message in subscription.messages:
            print(message.__dict__)  # noqa: T201


async def inner_loop() -> None:
    async with TaskGroup() as tg:
        queue = Queue[BambuDevice]()
        tg.create_task(discover_bambu_devices(queue))
        while True:
            device = await queue.get()
            print(f"Found device {device.device_id} at {device.ip_address}")  # noqa: T201
            tg.create_task(print_messages(device.ip_address, device.device_id))


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
