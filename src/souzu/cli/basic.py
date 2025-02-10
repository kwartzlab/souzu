import argparse
import asyncio

from souzu.bambu.mqtt import BambuMqttSubscription


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="The IP address of the printer")
    parser.add_argument("device_id", help="The serial number of the printer")
    parser.add_argument("access_code", help="The LAN access code for the printer")
    return parser.parse_args()


async def real_main() -> None:
    args = _parse_args()
    async with BambuMqttSubscription(
        args.host, args.device_id, args.access_code
    ) as subscription:
        async for message in subscription.messages:
            print(message.__dict__)  # noqa: T201


def main() -> None:
    asyncio.run(real_main())


if __name__ == "__main__":
    main()
