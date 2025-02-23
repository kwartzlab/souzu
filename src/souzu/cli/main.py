import argparse
import logging
from asyncio import (
    run,
)
from importlib.metadata import PackageNotFoundError, version

from prettyprinter import install_extras

from souzu.commands.monitor import monitor
from souzu.commands.update import update


def _parse_args() -> argparse.Namespace:
    try:
        souzu_version = version("souzu")
    except PackageNotFoundError:
        souzu_version = "unknown"

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {souzu_version}"
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True
    subparsers.add_parser("monitor", help="Monitor printers on the local network")
    subparsers.add_parser("update", help="Update souzu")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    install_extras(frozenset({'attrs'}))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    if args.command == "monitor":
        run(monitor())
    elif args.command == "update":
        update_successful = update()
        if not update_successful:
            exit(1)
    else:
        raise NotImplementedError(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
