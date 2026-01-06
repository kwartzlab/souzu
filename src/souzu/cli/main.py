import argparse
import logging
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


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
    update_subparser = subparsers.add_parser("update", help="Update souzu")
    update_subparser.add_argument(
        "--restart", action="store_true", help="Restart the monitor service"
    )
    subparsers.add_parser("install", help="Install systemd user service")

    compact_subparser = subparsers.add_parser(
        "compact", help="Compact a log file by removing duplicate reports"
    )
    compact_subparser.add_argument(
        "input_file", type=Path, help="Path to the log file to compact"
    )
    compact_subparser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Path to the output file (default: input_file.compact.log)",
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    # Commands are lazily imported to isolate failures - a broken dependency in
    # one command (e.g., monitor) won't prevent other commands (e.g., update)
    # from running. This is critical for the auto-update mechanism.
    if args.command == "update":
        try:
            from souzu.commands.update import update

            update(args.restart)
        except Exception as e:
            print(f"Error updating: {e}", file=sys.stderr)  # noqa: T201
            exit(1)
    elif args.command == "monitor":
        from asyncio import run

        from prettyprinter import install_extras

        from souzu.commands.monitor import monitor

        install_extras(frozenset({"attrs"}))
        run(monitor())
    elif args.command == "install":
        try:
            from souzu.commands.install import install

            install()
        except Exception as e:
            print(f"Error installing: {e}", file=sys.stderr)  # noqa: T201
            exit(1)
    elif args.command == "compact":
        from asyncio import run

        from souzu.commands.compact import compact

        try:
            run(compact(args.input_file, args.output))
        except Exception as e:
            print(f"Error compacting log file: {e}", file=sys.stderr)  # noqa: T201
            exit(1)
    else:
        raise NotImplementedError(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
