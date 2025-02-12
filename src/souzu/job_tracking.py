import json
import logging
from collections.abc import AsyncIterable
from concurrent.futures import CancelledError
from datetime import timedelta
from math import ceil

from anyio import Path as AsyncPath
from attrs import define, frozen
from cattrs import Converter
from xdg_base_dirs import xdg_state_home

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.mqtt import BambuMqttConnection, BambuStatusReport
from souzu.config import SLACK_PRINT_NOTIFICATION_CHANNEL
from souzu.slack.thread import post_to_channel

_ONE_MINUTE = 60
_FIVE_MINUTES = 5 * 60
_HALF_HOUR = 30 * 60
_FIFTY_FIVE_MINUTES = 55 * 60
_ONE_HOUR = 60 * 60
_EIGHT_HOURS = 8 * 60 * 60


_STATE_DIR = AsyncPath(xdg_state_home() / 'souzu')


_STATE_SERIALIZER = Converter()
_STATE_SERIALIZER.register_unstructure_hook(timedelta, lambda td: td.total_seconds())
_STATE_SERIALIZER.register_structure_hook(
    timedelta, lambda td, _: timedelta(seconds=td)
)


@frozen
class PrintJob:
    duration: timedelta


@define
class PrinterState:
    current_job: PrintJob | None = None


def _pretty_print(duration: timedelta) -> str:
    """
    Return a human-readable string representing the duration of the print job.

    Add a fudge factor to account for estimation error.
    """

    if duration.total_seconds() < _ONE_MINUTE:
        return "1 minute"
    elif duration.total_seconds() < _FIFTY_FIVE_MINUTES:
        # round up to next 5 minutes
        minutes = ceil(duration.total_seconds() / _FIVE_MINUTES) * 5
        return f"{minutes} minutes"
    elif duration.total_seconds() < _EIGHT_HOURS:
        # round up to next half hour
        hours = ceil(duration.total_seconds() / _HALF_HOUR) / 2
        if hours == 1:
            return "1 hour"
        elif hours == int(hours):
            return f"{hours:d} hours"
        else:
            return f"{hours:.1f} hours"
    else:
        # round up to next hour
        hours = int(ceil(duration.total_seconds() / _ONE_HOUR))
        return f"{hours} hours"


async def _wait_for_job(reports: AsyncIterable[BambuStatusReport]) -> PrintJob:
    """
    Consume messages from the queue until a job is running with estimated time available.
    """
    async for report in reports:
        if report.print_type != 'idle' and report.mc_remaining_time:
            return PrintJob(duration=timedelta(minutes=report.mc_remaining_time))
    raise CancelledError("No print job found")


async def _wait_for_job_completion(reports: AsyncIterable[BambuStatusReport]) -> None:
    async for message in reports:
        if message.print_type == 'idle':
            return


async def _notify_job_started(job: PrintJob, device: BambuDevice) -> None:
    await post_to_channel(
        SLACK_PRINT_NOTIFICATION_CHANNEL,
        f"{device.device_name}: Print started, {_pretty_print(job.duration)} remaining",
    )


async def _notify_job_completed(job: PrintJob, device: BambuDevice) -> None:
    # TODO detect failed prints and report them
    # TODO detect paused prints
    await post_to_channel(
        SLACK_PRINT_NOTIFICATION_CHANNEL,
        f"{device.device_name}: Print finished",
    )


async def monitor_printer_status(
    device: BambuDevice, connection: BambuMqttConnection
) -> None:
    """
    Subscribe to events from the given printer and report on print status.

    This function operates as a state machine, with state persisted to a file.
    """
    try:
        await _STATE_DIR.mkdir(exist_ok=True, parents=True)
        state_file = _STATE_DIR / f'job.{device.device_id}.json'
        if await state_file.exists():
            async with await state_file.open('r') as f:
                state_str = json.loads(await f.read())
                logging.info(f"Loading state file {state_file}")
                state = _STATE_SERIALIZER.structure(state_str, PrinterState)
        else:
            state = PrinterState()

        try:
            async with connection.subscribe() as reports:
                while True:
                    if state.current_job is None:
                        state.current_job = await _wait_for_job(reports)
                        await _notify_job_started(state.current_job, device)
                    else:
                        await _wait_for_job_completion(reports)
                        await _notify_job_completed(state.current_job, device)
                        state.current_job = None
        finally:
            serialized = json.dumps(_STATE_SERIALIZER.unstructure(state))
            async with await state_file.open('w') as f:
                await f.write(serialized)
                logging.info(f"Saved state file {state_file}")
    except CancelledError:
        raise
    except Exception:
        logging.exception(f"Error while monitoring printer {device.device_id}")
