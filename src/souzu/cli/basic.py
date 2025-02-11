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

from prettyprinter import install_extras, pformat

from souzu.bambu.discovery import BambuDevice, discover_bambu_devices
from souzu.bambu.mqtt import BambuMqttSubscription
from souzu.config import BAMBU_ACCESS_CODES


async def print_messages(host: str, device_id: str, device_name: str) -> None:
    access_code = BAMBU_ACCESS_CODES.get(device_id)
    if not access_code:
        logging.error(f"No access code for device {device_id}")
        return
    async with BambuMqttSubscription(host, device_id, access_code) as subscription:
        async for _before, after in subscription.messages:
            logging.info(f"{device_name}: {pformat(after)}")


async def inner_loop() -> None:
    async with TaskGroup() as tg:
        queue = Queue[BambuDevice]()
        tg.create_task(discover_bambu_devices(queue))
        while True:
            device = await queue.get()
            logging.info(f"Found device {device.device_name} at {device.ip_address}")
            tg.create_task(
                print_messages(device.ip_address, device.device_id, device.device_name)
            )


async def real_main() -> None:
    install_extras(frozenset({'attrs'}))
    logging.basicConfig(level=logging.INFO)
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
