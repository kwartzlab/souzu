import json
import logging
from collections.abc import AsyncIterable
from concurrent.futures import CancelledError
from datetime import timedelta
from math import ceil

from anyio import Path as AsyncPath
from attrs import define
from cattrs import Converter
from xdg_base_dirs import xdg_state_home

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.errors import parse_error_code
from souzu.bambu.mqtt import BambuMqttConnection, BambuStatusReport
from souzu.config import SLACK_PRINT_NOTIFICATION_CHANNEL
from souzu.slack.thread import (
    SlackApiError,
    edit_message,
    post_to_channel,
    post_to_thread,
)

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


@define
class PrintJob:
    duration: timedelta
    slack_channel: str | None = None
    slack_thread_ts: str | None = None


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
        elif hours.is_integer():
            return f"{int(hours)} hours"
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
        if report.gcode_state == 'RUNNING' and report.mc_remaining_time:
            return PrintJob(duration=timedelta(minutes=report.mc_remaining_time))
    raise CancelledError("No print job found")


async def _wait_for_job_completion(
    reports: AsyncIterable[BambuStatusReport],
) -> str | None:
    """
    Wait until the print job completes or errors.

    If the print job completes, return None.
    If the print job has an error, return a human-readable error message, or the error code, or some other string.
    """
    async for report in reports:
        if report.gcode_state == 'FAILED':
            return parse_error_code(report.print_error)
        elif report.gcode_state == 'FINISH':
            return None
    raise CancelledError("Job completion not found")


async def _notify_job_started(job: PrintJob, device: BambuDevice) -> None:
    try:
        thread_ts = await post_to_channel(
            SLACK_PRINT_NOTIFICATION_CHANNEL,
            f":progress_bar: {device.device_name}: Print started, {_pretty_print(job.duration)} remaining",
        )
        job.slack_channel = SLACK_PRINT_NOTIFICATION_CHANNEL
        job.slack_thread_ts = thread_ts
    except SlackApiError as e:
        logging.error(f"Failed to notify channel: {e}")


async def _update_thread(
    job: PrintJob, device: BambuDevice, edited_message: str, update_message: str
) -> None:
    if job.slack_thread_ts is None:
        try:
            await post_to_channel(
                job.slack_channel or SLACK_PRINT_NOTIFICATION_CHANNEL,
                update_message,
            )
        except SlackApiError as e:
            logging.error(f"Failed to notify channel: {e}")
        return

    try:
        await post_to_thread(
            job.slack_channel or SLACK_PRINT_NOTIFICATION_CHANNEL,
            job.slack_thread_ts,
            update_message,
        )
    except SlackApiError as e:
        logging.error(f"Failed to notify thread: {e}")
        if job.slack_thread_ts:
            # we tried to post to thread, we can try posting to the channel instead
            try:
                await post_to_channel(
                    job.slack_channel or SLACK_PRINT_NOTIFICATION_CHANNEL,
                    update_message,
                )
            except SlackApiError as e:
                logging.error(f"Failed to notify channel as fallback: {e}")
    try:
        await edit_message(
            job.slack_channel or SLACK_PRINT_NOTIFICATION_CHANNEL,
            job.slack_thread_ts,
            edited_message,
        )
    except SlackApiError as e:
        logging.error(f"Failed to edit message: {e}")


async def _notify_job_completed(job: PrintJob, device: BambuDevice) -> None:
    # TODO detect paused prints
    await _update_thread(
        job,
        device,
        f"~{device.device_name}: Print started, {_pretty_print(job.duration)} remaining~\n:white_check_mark: Finished!",
        f":white_check_mark: {device.device_name}: Print finished!",
    )


async def _notify_job_error(job: PrintJob, device: BambuDevice, error: str) -> None:
    await _update_thread(
        job,
        device,
        f"~{device.device_name}: Print started, {_pretty_print(job.duration)} remaining~\n:x: Failed!",
        f":x: {device.device_name}: Print failed!\nMessage from printer: {error}",
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
                        error = await _wait_for_job_completion(reports)
                        if error:
                            await _notify_job_error(state.current_job, device, error)
                        else:
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
